from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .file_responses import download
from .models import Version, WorkflowRecord
from .storage import remove_files
from .uploads import request_file
from .version_storage import save_version

State = WorkflowRecord.State
TRANSITIONS = {
    State.CREATED: (State.REVIEW, State.WITHDRAWN),
    State.REVIEW: (State.SIGNED, State.REJECTED, State.WITHDRAWN),
    State.SIGNED: (State.REGISTERED,),
    State.REGISTERED: (State.ARCHIVED,),
}


def visible_records(user):
    if user.has_perm("documents.manage_workflowrecord"):
        return WorkflowRecord.objects.all()
    return WorkflowRecord.objects.filter(
        Q(version__document__owner=user) | Q(recipient=user, events__state=State.REVIEW),
    ).distinct()


class RecipientForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["recipient"]
        labels = {"recipient": "Кому направлен документ"}

    def __init__(self, owner, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipient"].queryset = get_user_model().objects.filter(is_active=True).exclude(
            pk=owner.pk,
        ).order_by("username")


class RecordForm(RecipientForm):
    class Meta(RecipientForm.Meta):
        model = WorkflowRecord
        fields = ["title", "kind", "purpose", "description", "recipient"]
        labels = {**RecipientForm.Meta.labels, "title": "Название", "kind": "Тип", "purpose": "Назначение", "description": "Описание"}
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 3})}


@login_required
@permission_required("documents.view_workflowrecord", raise_exception=True)
@require_safe
def workflow_records(request):
    records = visible_records(request.user).select_related("recipient", "version__document__owner").order_by("-updated_at", "-pk")
    query = request.GET.get("q", "").strip()[:255]
    kind = request.GET.get("kind", "")
    state = request.GET.get("state", "")
    if query:
        records = records.filter(Q(title__icontains=query) | Q(purpose__icontains=query)
                                 | Q(description__icontains=query) | Q(registration_number__icontains=query))
    if kind in WorkflowRecord.Kind.values:
        records = records.filter(kind=kind)
    if state in State.values:
        records = records.filter(state=state)
    return render(request, "documents/workflow_list.html", {
        "page": Paginator(records, 30).get_page(request.GET.get("page")), "query": query,
        "kinds": WorkflowRecord.Kind.choices, "states": State.choices, "kind": kind, "state": state,
    })


@login_required
@permission_required(("documents.view_workflowrecord", "documents.add_workflowrecord"), raise_exception=True)
@require_http_methods(["GET", "POST"])
def workflow_create(request, pk=None):
    version = get_object_or_404(Version.objects.select_related("document"), pk=pk,
                                document__owner=request.user) if pk is not None else None
    form = RecordForm(request.user, request.POST if request.method == "POST" else None,
                      initial={"title": version.document.title} if version else None)
    if request.method == "POST" and form.is_valid():
        uploaded = None
        try:
            with transaction.atomic():
                if version is None:
                    uploaded = save_version(request.user, request_file(request, "Выбери файл для ЭДО."))
                record = form.save(commit=False)
                record.version = version or uploaded
                record.save()
                record.events.create(actor=request.user, state=record.state)
        except ValueError as exc:
            if uploaded:
                remove_files([uploaded.key])
            form.add_error(None, str(exc))
        except BaseException:
            if uploaded:
                remove_files([uploaded.key])
            raise
        else:
            return redirect("workflow_detail", record.pk)
    return render(request, "documents/workflow_create.html", {"form": form, "version": version})


@login_required
@permission_required("documents.view_workflowrecord", raise_exception=True)
@require_http_methods(["GET", "POST"])
def workflow_detail(request, pk):
    records = visible_records(request.user).select_related("version__document__owner", "recipient")
    if request.method == "POST":
        with transaction.atomic():
            record = get_object_or_404(records, pk=pk)
            if not can_edit(request.user, record):
                raise PermissionDenied
            if request.POST.get("action") == "assign" and record.state == State.CREATED:
                form = RecipientForm(record.version.document.owner, request.POST, instance=record)
                if not form.is_valid():
                    return render_record(request, record, form)
                if form.has_changed():
                    form.save()
                    recipient = record.recipient
                    comment = f"Получатель: {recipient.get_username()}" if recipient else "Получатель снят."
                    record.events.create(actor=request.user, state=record.state, comment=comment)
                return redirect("workflow_detail", pk)
            target = request.POST.get("state")
            comment = request.POST.get("comment", "").strip()
            if target not in TRANSITIONS.get(record.state, ()):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if len(comment) > 4000 or (target in (State.WITHDRAWN, State.REJECTED) and not comment):
                return HttpResponseBadRequest("Укажи причину отклонения или отзыва, до 4000 символов.")
            previous = record.state
            record.state = target
            if target == State.REGISTERED:
                now = timezone.now()
                prefix = f"{record.kind}-{timezone.localtime(now).year}-"
                count = WorkflowRecord.objects.filter(registration_number__startswith=prefix).count()
                record.registration_number = f"{prefix}{count + 1:06d}"
                record.registered_at = now
            record.save(update_fields=["state", "updated_at", "registration_number", "registered_at"])
            record.events.create(actor=request.user, previous_state=previous, state=target, comment=comment)
        return redirect("workflow_detail", pk)
    record = get_object_or_404(records, pk=pk)
    return render_record(request, record)


def can_edit(user, record):
    return user.has_perm("documents.change_workflowrecord") and (
        record.version.document.owner_id == user.pk or user.has_perm("documents.manage_workflowrecord")
    )


def render_record(request, record, form=None):
    author = can_edit(request.user, record)
    if form is None and author and record.state == State.CREATED:
        form = RecipientForm(record.version.document.owner, instance=record)
    return render(request, "documents/workflow_detail.html", {
        "recipient_form": form,
        "record": record, "events": record.events.select_related("actor").order_by("pk"),
        "transitions": [(state, state.label) for state in TRANSITIONS.get(record.state, ())] if author else [],
    })


@login_required
@permission_required("documents.view_workflowrecord", raise_exception=True)
@require_safe
def workflow_file(request, pk):
    record = get_object_or_404(visible_records(request.user).select_related("version"), pk=pk)
    return download(record.version.key, record.version.name)
