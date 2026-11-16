from contextlib import nullcontext

from django import forms
from django.contrib import messages
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
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .file_responses import download
from .models import Document, DocumentType, RegistrationSequence, Version, WorkflowEvent, WorkflowRecord
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
        Q(version__document__owner=user) | Q(recipients=user, events__state__in=SENT_STATES, events__previous_state=State.CREATED),
    ).distinct()


class RecipientPicker(forms.CheckboxSelectMultiple):
    template_name = "documents/recipient_picker.html"


class RecipientForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["recipients"]
        labels = {"recipients": "Кому направлен документ"}
        widgets = {"recipients": RecipientPicker()}

    def __init__(self, owner, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = get_user_model().objects.filter(is_active=True).exclude(
            pk=owner.pk,
        ).order_by("last_name", "first_name", "pk")
        self.fields["recipients"].label_from_instance = lambda user: user.get_full_name() or "ФИО не указано"


class DetailsForm(forms.ModelForm):
    class Meta:
        model = WorkflowRecord
        fields = ["title", "purpose", "description"]
        labels = {"title": "Название", "purpose": "Назначение", "description": "Описание"}
        widgets = {"purpose": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 3})}


class RecordForm(RecipientForm):
    def __init__(self, owner, *args, **kwargs):
        super().__init__(owner, *args, **kwargs)
        self.fields["kind"].queryset = DocumentType.objects.filter(is_active=True)

    class Meta(RecipientForm.Meta):
        model = WorkflowRecord
        fields = ["mode", "title", "kind", "purpose", "description", "recipients"]
        labels = {**DetailsForm.Meta.labels, **RecipientForm.Meta.labels, "kind": "Тип", "mode": "Режим работы"}
        widgets = {**DetailsForm.Meta.widgets, **RecipientForm.Meta.widgets, "mode": forms.RadioSelect()}
        help_texts = {"mode": "Выберите режим до создания карточки."}


@login_required
@require_safe
def workflow_records(request):
    records = visible_records(request.user).select_related("kind", "version__document__owner").prefetch_related("recipients").order_by("-updated_at", "-pk")
    query = request.GET.get("q", "").strip()[:255]
    kind = request.GET.get("kind", "")
    state = request.GET.get("state", "")
    if query:
        records = records.filter(Q(title__icontains=query) | Q(purpose__icontains=query)
                                 | Q(description__icontains=query) | Q(registration_number__icontains=query))
    if kind:
        records = records.filter(kind_id=kind)
    if state in State.values:
        records = records.filter(state=state)
    return render(request, "documents/workflow_list.html", {
        "page": Paginator(records, 30).get_page(request.GET.get("page")), "query": query,
        "kinds": DocumentType.objects.values_list("code", "name"), "states": State.choices, "kind": kind, "state": state,
    })


@login_required
@require_http_methods(["GET", "POST"])
def workflow_create(request, pk=None):
    uploaded = None
    writing = request.method == "POST"
    data = request.POST if writing else None
    try:
        with transaction.atomic() if writing else nullcontext():
            version = get_object_or_404(
                Version.objects.select_related("document"), pk=pk, document__owner=request.user,
            ) if pk is not None else None
            form = RecordForm(request.user, data,
                              initial={"title": version.document.title} if version else None)
            if writing and form.is_valid():
                send_now = data.get("action") == "send"
                if send_now and not form.cleaned_data["recipients"]:
                    form.add_error("recipients", "Укажи получателя перед отправкой.")
                else:
                    if version is None:
                        uploaded = save_version(request.user, request_file(request, "Выбери файл для ЭДО."))
                    record = form.save(commit=False)
                    record.version = version or uploaded
                    record.save()
                    form.save_m2m()
                    record.events.create(actor=request.user, state=record.state, version=record.version)
                    if send_now:
                        record.state = send_state(record.mode)
                        record.save(update_fields=["state", "updated_at"])
                        record.events.create(
                            actor=request.user, previous_state=State.CREATED, state=record.state,
                            version=record.version,
                        )
                    return redirect("workflow_detail", record.pk)
    except ValueError as exc:
        if uploaded:
            remove_files([uploaded.key])
        form.add_error(None, str(exc))
    except BaseException:
        if uploaded:
            remove_files([uploaded.key])
        raise
    return render(request, "documents/workflow_create.html", {"form": form, "version": version})


@login_required
@require_http_methods(["GET", "POST"])
def workflow_detail(request, pk):
    records = visible_records(request.user).select_related("version__document__owner").prefetch_related("recipients")
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
                    names = ", ".join(user.get_username() for user in form.cleaned_data["recipients"])
                    comment = f"Получатели: {names}" if names else "Получатели сняты."
                    record.events.create(actor=request.user, state=record.state, comment=comment[:4000])
                return redirect("workflow_detail", pk)
            target = request.POST.get("state")
            comment = request.POST.get("comment", "").strip()
            if target not in available_transitions(record):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if target in SENT_STATES and not any(user.is_active for user in record.recipients.all()):
                return HttpResponseBadRequest("Укажи получателя перед отправкой.")
            if len(comment) > 4000 or (target in (State.WITHDRAWN, State.REJECTED) and not comment):
                return HttpResponseBadRequest("Укажи причину отклонения или отзыва, до 4000 символов.")
            previous = record.state
            record.state = target
            if target == State.REGISTERED:
                now = timezone.now()
                prefix = f"{record.kind_id}-{timezone.localtime(now).year}-"
                sequence, _ = RegistrationSequence.objects.get_or_create(prefix=prefix)
                sequence.last_number += 1
                sequence.save(update_fields=["last_number"])
                record.registration_number = f"{prefix}{sequence.last_number:06d}"
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
    return user in record.recipients.all()


def can_assign(user, record):
    return user.is_superuser or (record.state == State.CREATED and record.version.document.owner_id == user.pk)


class CommentForm(forms.Form):
    comment = forms.CharField(
        label="Комментарий", max_length=4000, required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите комментарий к документу"}),
    )


def can_replace_file(user, record):
    return can_edit(user, record) and record.state in FILE_EDIT_STATES


def save_revision(record, request):
    file = request_file(request, "Выбери файл документа.")
    return save_version(record.version.document.owner, file, document=record.version.document)


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
        "can_send": editable and record.state == State.CREATED and any(user.is_active for user in record.recipients.all()),
        "send_state": send_state(record.mode), "can_replace_file": can_replace_file(request.user, record),
        "record": record, "events": [event for event in events if not event.is_comment or event.version_id],
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
            record = get_object_or_404(visible_records(request.user).select_related("version__document__owner").prefetch_related("recipients"), pk=pk)
            if not can_edit(request.user, record):
                raise PermissionDenied
            form = DetailsForm(request.POST, instance=record)
            if not form.is_valid():
                return render_record(request, record, details_form=form)
            changes = [form.fields[name].label for name in form.changed_data]
            if request.FILES or getattr(request, "file_too_large", False):
                if record.state not in FILE_EDIT_STATES:
                    return HttpResponseBadRequest("Замена файла доступна до завершения согласования.")
                uploaded = save_revision(record, request)
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
def workflow_file(request, pk, preview=False):
    record = get_object_or_404(visible_records(request.user).select_related("version"), pk=pk)
    return download(record.version.key, record.version.name, preview=preview)


@login_required
@require_http_methods(["POST"])
def workflow_comment(request, pk):
    uploaded = None
    try:
        with transaction.atomic():
            record = get_object_or_404(
                visible_records(request.user).select_related("version__document__owner").prefetch_related("recipients"), pk=pk,
            )
            form = CommentForm(request.POST)
            attaching = bool(request.FILES) or getattr(request, "file_too_large", False)
            if attaching and not can_replace_file(request.user, record):
                raise PermissionDenied
            if not form.is_valid():
                return render_record(request, record, comment_form=form)
            comment = form.cleaned_data["comment"]
            if not comment and not attaching:
                form.add_error("comment", "Добавь комментарий или прикрепи новую версию.")
                return render_record(request, record, comment_form=form)
            if attaching:
                uploaded = save_revision(record, request)
                record.version = uploaded
                record.save(update_fields=["version", "updated_at"])
            record.events.create(
                actor=request.user, state=record.state, is_comment=True, comment=comment, version=uploaded,
            )
    except ValueError as exc:
        if uploaded:
            remove_files([uploaded.key])
        form.add_error(None, str(exc))
        return render_record(request, record, comment_form=form)
    except BaseException:
        if uploaded:
            remove_files([uploaded.key])
        raise
    return redirect(reverse("workflow_detail", args=[pk]) + "#document-comments")


@login_required
@require_safe
def workflow_event_file(request, pk, event_pk, preview=False):
    record = get_object_or_404(visible_records(request.user), pk=pk)
    event = get_object_or_404(record.events.select_related("version"), pk=event_pk, version__isnull=False)
    return download(event.version.key, event.version.name, preview=preview)


@login_required
@require_POST
@transaction.atomic
def workflow_delete(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.POST.get("confirm") != "delete":
        return HttpResponseBadRequest("Подтверди удаление ветки.")

    record = get_object_or_404(WorkflowRecord.objects.select_related("version"), pk=pk)
    document_id = record.version.document_id
    version_ids = set(record.events.exclude(version_id=None).values_list("version_id", flat=True))
    version_ids.add(record.version_id)
    record.events.all().delete()
    record.delete()

    # Older events did not store their version, so recover only an unshared document's history.
    if (not WorkflowRecord.objects.filter(version__document_id=document_id).exists()
            and not WorkflowEvent.objects.filter(version__document_id=document_id).exists()):
        version_ids.update(Version.objects.filter(document_id=document_id).values_list("pk", flat=True))

    unused = Version.objects.filter(
        pk__in=version_ids, workflowrecord__isnull=True, workflowevent__isnull=True,
        job_inputs__isnull=True, approval_rounds__isnull=True,
    )
    files = list(unused.values_list("key", "document_id"))
    unused.delete()
    Document.objects.filter(
        pk__in={document_id for _, document_id in files}, versions__isnull=True, approvals__isnull=True,
    ).delete()
    transaction.on_commit(lambda: remove_files(key for key, _ in files), robust=True)
    messages.success(request, "Ветка ЭДО удалена.")
    return redirect("workflow_records")
