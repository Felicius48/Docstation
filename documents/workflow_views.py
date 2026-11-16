from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .file_responses import download
from .models import Version, WorkflowRecord
from .storage import remove_files
from .uploads import request_file
from .version_storage import save_version

State = WorkflowRecord.State
Mode = WorkflowRecord.Mode
SENT_STATES = (State.SHARED, State.REVIEW)
FILE_EDIT_STATES = (State.CREATED, *SENT_STATES)
TRANSITIONS = {
    State.REVIEW: (State.SIGNED, State.REJECTED, State.WITHDRAWN),
    State.SIGNED: (State.REGISTERED,),
    State.REGISTERED: (State.ARCHIVED,),
}


def send_state(mode):
    return State.SHARED if mode == Mode.EDIT else State.REVIEW


def available_transitions(record):
    if record.state == State.CREATED:
        return send_state(record.mode), State.WITHDRAWN
    return TRANSITIONS.get(record.state, ()) if record.mode == Mode.APPROVAL else ()


def visible_records(user):
    if user.is_superuser:
        return WorkflowRecord.objects.all()
    return WorkflowRecord.objects.filter(
        Q(version__document__owner=user) | Q(recipient=user, events__state__in=SENT_STATES, events__previous_state=State.CREATED),
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


class DetailsForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["title", "purpose", "description"]
        labels = {"title": "Название", "purpose": "Назначение", "description": "Описание"}
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 3})}


class RecordForm(RecipientForm):
    class Meta(RecipientForm.Meta):
        model = WorkflowRecord
        fields = ["mode", "title", "kind", "purpose", "description", "recipient"]
        labels = {**DetailsForm.Meta.labels, **RecipientForm.Meta.labels, "kind": "Тип", "mode": "Режим работы"}
        widgets = {**DetailsForm.Meta.widgets, "mode": forms.RadioSelect()}
        help_texts = {"mode": "Выберите режим до создания карточки."}


@login_required
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
@require_http_methods(["GET", "POST"])
def workflow_create(request, pk=None):
    version = get_object_or_404(Version.objects.select_related("document"), pk=pk,
                                document__owner=request.user) if pk is not None else None
    form = RecordForm(request.user, request.POST if request.method == "POST" else None,
                      initial={"title": version.document.title} if version else None)
    if request.method == "POST" and form.is_valid():
        send_now = request.POST.get("action") == "send"
        if send_now and form.cleaned_data["recipient"] is None:
            form.add_error("recipient", "Укажи получателя перед отправкой.")
            return render(request, "documents/workflow_create.html", {"form": form, "version": version})
        uploaded = None
        try:
            with transaction.atomic():
                if version is None:
                    uploaded = save_version(request.user, request_file(request, "Выбери файл для ЭДО."))
                record = form.save(commit=False)
                record.version = version or uploaded
                record.save()
                record.events.create(actor=request.user, state=record.state, version=record.version)
                if send_now:
                    record.state = send_state(record.mode)
                    record.save(update_fields=["state", "updated_at"])
                    record.events.create(
                        actor=request.user, previous_state=State.CREATED, state=record.state, version=record.version,
                    )
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
@require_http_methods(["GET", "POST"])
def workflow_detail(request, pk):
    records = visible_records(request.user).select_related("version__document__owner", "recipient")
    if request.method == "POST":
        with transaction.atomic():
            record = get_object_or_404(records, pk=pk)
            if not can_edit(request.user, record):
                raise PermissionDenied
            if request.POST.get("action") == "assign" and can_assign(request.user, record):
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
            if target not in available_transitions(record):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if target in SENT_STATES and (record.recipient is None or not record.recipient.is_active):
                return HttpResponseBadRequest("Укажи получателя перед отправкой.")
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
            record.events.create(
                actor=request.user, previous_state=previous, state=target, comment=comment, version=record.version,
            )
        return redirect("workflow_detail", pk)
    record = get_object_or_404(records, pk=pk)
    return render_record(request, record)


def can_edit(user, record):
    if user.is_superuser:
        return True
    if record.state == State.CREATED:
        return record.version.document.owner_id == user.pk
    return record.recipient_id == user.pk


def can_assign(user, record):
    return user.is_superuser or (record.state == State.CREATED and record.version.document.owner_id == user.pk)


class CommentForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий", max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите комментарий к документу"}),
    )


def render_record(request, record, form=None, details_form=None, comment_form=None):
    editable = can_edit(request.user, record)
    if form is None and can_assign(request.user, record):
        form = RecipientForm(record.version.document.owner, instance=record)
    if details_form is None and editable:
        details_form = DetailsForm(instance=record)
    events = list(record.events.select_related("actor", "version").order_by("pk"))
    return render(request, "documents/workflow_detail.html", {
        "comment_form": comment_form if comment_form is not None else CommentForm(),
        "comments": [event for event in events if event.is_comment or (event.previous_state and event.comment)],
        "recipient_form": form, "details_form": details_form,
        "can_send": editable and record.state == State.CREATED and record.recipient is not None and record.recipient.is_active,
        "send_state": send_state(record.mode), "can_replace_file": record.state in FILE_EDIT_STATES,
        "record": record, "events": [event for event in events if not event.is_comment],
        "transitions": [
            (state, state.label) for state in available_transitions(record) if state not in SENT_STATES
        ] if editable else [],
    })


@login_required
@require_http_methods(["POST"])
def workflow_edit(request, pk):
    uploaded = None
    try:
        with transaction.atomic():
            record = get_object_or_404(visible_records(request.user).select_related("version__document__owner", "recipient"), pk=pk)
            if not can_edit(request.user, record):
                raise PermissionDenied
            form = DetailsForm(request.POST, instance=record)
            if not form.is_valid():
                return render_record(request, record, details_form=form)
            changes = [form.fields[name].label for name in form.changed_data]
            if request.FILES or getattr(request, "file_too_large", False):
                if record.state not in FILE_EDIT_STATES:
                    return HttpResponseBadRequest("Замена файла доступна до завершения согласования.")
                file = request_file(request, "Выбери файл документа.")
                uploaded = save_version(record.version.document.owner, file, document=record.version.document)
                record.version = uploaded
                changes.append(f"Файл (версия {uploaded.number})")
            if changes:
                form.save()
                record.events.create(
                    actor=request.user, state=record.state, version=uploaded,
                    comment="Изменено: " + ", ".join(changes),
                )
    except ValueError as exc:
        form.add_error(None, str(exc))
        return render_record(request, record, details_form=form)
    except BaseException:
        if uploaded:
            remove_files([uploaded.key])
        raise
    return redirect("workflow_detail", pk)


@login_required
@require_safe
def workflow_file(request, pk):
    record = get_object_or_404(visible_records(request.user).select_related("version"), pk=pk)
    return download(record.version.key, record.version.name)


@login_required
@require_http_methods(["POST"])
def workflow_comment(request, pk):
    with transaction.atomic():
        record = get_object_or_404(
            visible_records(request.user).select_related("version__document__owner", "recipient"), pk=pk,
        )
        form = CommentForm(request.POST)
        if not form.is_valid():
            return render_record(request, record, comment_form=form)
        record.events.create(
            actor=request.user, state=record.state, is_comment=True, comment=form.cleaned_data["comment"],
        )
    return redirect(reverse("workflow_detail", args=[pk]) + "#document-comments")


@login_required
@require_safe
def workflow_event_file(request, pk, event_pk):
    record = get_object_or_404(visible_records(request.user), pk=pk)
    event = get_object_or_404(record.events.select_related("version"), pk=event_pk, version__isnull=False)
    return download(event.version.key, event.version.name)
