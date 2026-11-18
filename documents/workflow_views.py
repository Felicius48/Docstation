from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .file_responses import download
from .models import (
    WORKFLOW_TEXT_LIMIT,
    Document,
    DocumentType,
    RegistrationSequence,
    Version,
    WorkflowEvent,
    WorkflowRecord,
)
from .storage import remove_files
from .uploads import request_file
from .version_storage import save_version
from .workflow_forms import CommentForm, DetailsForm, RecipientForm, RecordForm, TaskForm

WORKFLOW_PAGE_SIZE = 30
State = WorkflowRecord.State
Mode = WorkflowRecord.Mode
SENT_STATES = (State.SHARED, State.REVIEW, State.SENT)
FILE_EDIT_STATES = (State.CREATED, State.SHARED, State.REVIEW)
TASK_TRANSITIONS = {
    State.SENT: State.RECEIVED,
    State.RECEIVED: State.IN_PROGRESS,
    State.REWORK: State.IN_PROGRESS,
    State.IN_PROGRESS: State.DONE,
}
TRANSITIONS = {
    State.REVIEW: (State.SIGNED, State.REJECTED, State.WITHDRAWN),
    State.SIGNED: (State.REGISTERED,),
    State.REGISTERED: (State.ARCHIVED,),
}


def send_state(mode):
    return State.SHARED if mode == Mode.EDIT else State.REVIEW


def available_transitions(record):
    if record.mode == Mode.TASK:
        next_state = TASK_TRANSITIONS.get(record.state)
        return (next_state,) if next_state else ()
    if record.state == State.CREATED:
        return send_state(record.mode), State.WITHDRAWN
    return TRANSITIONS.get(record.state, ()) if record.mode == Mode.APPROVAL else ()


def visible_records(user):
    if user.is_superuser:
        return WorkflowRecord.objects.all()
    return WorkflowRecord.objects.filter(
        Q(version__document__owner=user)
        | Q(recipients=user, events__state__in=SENT_STATES, events__previous_state=State.CREATED),
    ).distinct()


def card_records(user):
    recipients = get_user_model().objects.select_related("profile").defer("profile__avatar")
    return (
        visible_records(user)
        .select_related("kind", "version__document__owner")
        .prefetch_related(
            Prefetch("recipients", queryset=recipients),
        )
    )


@login_required
@require_safe
def workflow_records(request):
    records = card_records(request.user).exclude(mode=Mode.TASK).order_by("-updated_at", "-pk")
    query = request.GET.get("q", "").strip()[:255]
    kind = request.GET.get("kind", "")
    state = request.GET.get("state", "")
    if query:
        records = records.filter(
            Q(title__icontains=query)
            | Q(purpose__icontains=query)
            | Q(description__icontains=query)
            | Q(registration_number__icontains=query)
        )
    if kind:
        records = records.filter(kind_id=kind)
    if state in State.values:
        records = records.filter(state=state)
    return render(
        request,
        "documents/workflow_list.html",
        {
            "page": Paginator(records, WORKFLOW_PAGE_SIZE).get_page(request.GET.get("page")),
            "query": query,
            "kinds": DocumentType.objects.values_list("code", "name"),
            "states": [
                (state, label)
                for state, label in State.choices
                if state not in (*TASK_TRANSITIONS, State.DONE)
            ],
            "kind": kind,
            "state": state,
        },
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def task_list(request, sent=False):
    writing = request.method == "POST"
    form = TaskForm(request.user, request.POST if writing else None)
    if writing:
        response = create_workflow_record(request, form, task=True)
        if response is not None:
            return response
    records = card_records(request.user).filter(mode=Mode.TASK).order_by("-updated_at", "-pk")
    if sent:
        records = records.filter(version__document__owner=request.user)
    else:
        records = records.filter(recipients=request.user)
    return render(
        request,
        "documents/task_list.html",
        {
            "page": Paginator(records, WORKFLOW_PAGE_SIZE).get_page(request.GET.get("page")),
            "sent": sent,
            "form": form,
        },
    )


@login_required
@require_safe
@never_cache
def task_notifications(request):
    records = WorkflowRecord.objects.filter(
        mode=Mode.TASK, state__in=(State.SENT, State.REWORK), recipients=request.user
    )
    return JsonResponse(
        {
            "tasks": [
                {"id": pk, "title": title, "state": state, "url": reverse("workflow_detail", args=[pk])}
                for pk, title, state in records.order_by("-updated_at", "-pk").values_list(
                    "pk", "title", "state"
                )[:5]
            ]
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def workflow_create(request, pk=None):
    version = (
        get_object_or_404(
            Version.objects.select_related("document"),
            pk=pk,
            document__owner=request.user,
        )
        if pk is not None
        else None
    )
    writing = request.method == "POST"
    form = RecordForm(
        request.user,
        request.POST if writing else None,
        initial={"title": version.document.title} if version else None,
    )
    if writing:
        response = create_workflow_record(request, form, version)
        if response is not None:
            return response
    return render(request, "documents/workflow_create.html", {"form": form, "version": version})


def create_workflow_record(request, form, version=None, *, task=False):
    if not form.is_valid():
        return None
    send_now = task or request.POST.get("action") == "send"
    if not task and send_now and not form.cleaned_data["recipients"]:
        form.add_error("recipients", "Укажи получателя перед отправкой.")
        return None
    uploaded = None
    try:
        with transaction.atomic():
            if version is None:
                uploaded = save_version(
                    request.user,
                    request_file(request, "Выбери файл для отправки." if task else "Выбери файл для ЭДО."),
                )
            record = (
                WorkflowRecord(
                    title=uploaded.name,
                    purpose=form.cleaned_data["purpose"],
                    mode=Mode.TASK,
                )
                if task
                else form.save(commit=False)
            )
            record.version = version or uploaded
            record.save()
            if task:
                record.recipients.add(form.cleaned_data["recipient"])
            else:
                form.save_m2m()
            record.events.create(actor=request.user, state=record.state, version=record.version)
            if send_now:
                record.state = State.SENT if task else send_state(record.mode)
                record.save(update_fields=["state", "updated_at"])
                record.events.create(
                    actor=request.user,
                    previous_state=State.CREATED,
                    state=record.state,
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
    return None


@login_required
@require_http_methods(["GET", "POST"])
def workflow_detail(request, pk):
    records = card_records(request.user)
    if request.method == "POST":
        with transaction.atomic():
            record = get_object_or_404(records, pk=pk)
            if not can_change_status(request.user, record):
                raise PermissionDenied
            if request.POST.get("action") == "assign" and can_assign(request.user, record):
                form = RecipientForm(record.version.document.owner, request.POST, instance=record)
                if not form.is_valid():
                    return render_record(request, record, form)
                if form.has_changed():
                    form.save()
                    names = ", ".join(user.get_username() for user in form.cleaned_data["recipients"])
                    comment = f"Получатели: {names}" if names else "Получатели сняты."
                    record.events.create(
                        actor=request.user, state=record.state, comment=comment[:WORKFLOW_TEXT_LIMIT]
                    )
                return redirect("workflow_detail", pk)
            target = request.POST.get("state")
            comment = request.POST.get("comment", "").strip()
            if target not in available_transitions(record):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if target == State.SIGNED and request.POST.get("revision") != record.updated_at.isoformat():
                return stale_card_response()
            if target in SENT_STATES and not any(user.is_active for user in record.recipients.all()):
                return HttpResponseBadRequest("Укажи получателя перед отправкой.")
            if len(comment) > WORKFLOW_TEXT_LIMIT or (
                target in (State.WITHDRAWN, State.REJECTED) and not comment
            ):
                return HttpResponseBadRequest(
                    f"Укажи причину отклонения или отзыва, до {WORKFLOW_TEXT_LIMIT} символов."
                )
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
                actor=request.user,
                previous_state=previous,
                state=target,
                comment=comment,
                version=record.version,
            )
        return redirect("workflow_detail", pk)
    record = get_object_or_404(records, pk=pk)
    return render_record(request, record)


def stale_card_response():
    return HttpResponse("Карточка изменилась. Открой её заново перед сохранением.", status=409)


def can_change_status(user, record):
    return user in record.recipients.all() if record.mode == Mode.TASK else can_edit(user, record)


def can_edit(user, record):
    if record.mode == Mode.TASK:
        return False
    if user.is_superuser:
        return True
    if record.state == State.CREATED:
        return record.version.document.owner_id == user.pk
    return user in record.recipients.all()


def can_assign(user, record):
    return record.mode != Mode.TASK and (
        user.is_superuser or (record.state == State.CREATED and record.version.document.owner_id == user.pk)
    )


def can_return_task(user, record):
    return (
        record.mode == Mode.TASK
        and record.state == State.DONE
        and record.version.document.owner_id == user.pk
    )


def can_replace_file(user, record):
    if record.mode == Mode.TASK:
        return record.state in TASK_TRANSITIONS and (
            record.version.document.owner_id == user.pk or user in record.recipients.all()
        )
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
    return render(
        request,
        "documents/workflow_detail.html",
        {
            "comment_form": comment_form if comment_form is not None else CommentForm(),
            "comments": [
                event for event in events if event.is_comment or (event.previous_state and event.comment)
            ],
            "recipient_form": form,
            "details_form": details_form,
            "can_send": editable
            and record.state == State.CREATED
            and any(user.is_active for user in record.recipients.all()),
            "can_return_task": can_return_task(request.user, record),
            "send_state": send_state(record.mode),
            "can_replace_file": can_replace_file(request.user, record),
            "record": record,
            "workflow_text_limit": WORKFLOW_TEXT_LIMIT,
            "events": [
                event for event in events if not event.is_comment or event.version_id or event.previous_state
            ],
            "transitions": [
                (state, state.label) for state in available_transitions(record) if state not in SENT_STATES
            ]
            if can_change_status(request.user, record)
            else [],
        },
    )


@login_required
@require_POST
def workflow_edit(request, pk):
    uploaded = None
    try:
        with transaction.atomic():
            record = get_object_or_404(card_records(request.user), pk=pk)
            if not can_edit(request.user, record):
                raise PermissionDenied
            if request.POST.get("revision") != record.updated_at.isoformat():
                return stale_card_response()
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
                    actor=request.user,
                    state=record.state,
                    version=uploaded,
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
def workflow_file(request, pk, event_pk=None, preview=False):
    record = get_object_or_404(visible_records(request.user).select_related("version"), pk=pk)
    version = (
        record.version
        if event_pk is None
        else get_object_or_404(
            record.events.select_related("version"),
            pk=event_pk,
            version__isnull=False,
        ).version
    )
    return download(version.key, version.name, preview=preview)


@login_required
@require_POST
def workflow_comment(request, pk):
    uploaded = None
    try:
        with transaction.atomic():
            record = get_object_or_404(card_records(request.user), pk=pk)
            form = CommentForm(request.POST)
            returning = request.POST.get("action") == "return"
            if returning and not can_return_task(request.user, record):
                raise PermissionDenied
            attaching = bool(request.FILES) or getattr(request, "file_too_large", False)
            if attaching and not returning and not can_replace_file(request.user, record):
                raise PermissionDenied
            if not form.is_valid():
                return render_record(request, record, comment_form=form)
            comment = form.cleaned_data["comment"]
            if not (comment or attaching or returning):
                form.add_error("comment", "Добавь комментарий или прикрепи новую версию.")
                return render_record(request, record, comment_form=form)
            if attaching:
                uploaded = save_revision(record, request)
                record.version = uploaded
            if returning:
                record.state = State.REWORK
            if attaching or returning:
                record.save(update_fields=["version", "state", "updated_at"])
            record.events.create(
                actor=request.user,
                state=record.state,
                previous_state=State.DONE if returning else "",
                is_comment=not returning or bool(comment),
                comment=comment,
                version=uploaded,
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
    if (
        not WorkflowRecord.objects.filter(version__document_id=document_id).exists()
        and not WorkflowEvent.objects.filter(version__document_id=document_id).exists()
    ):
        version_ids.update(Version.objects.filter(document_id=document_id).values_list("pk", flat=True))

    unused = Version.objects.filter(
        pk__in=version_ids,
        workflowrecord__isnull=True,
        workflowevent__isnull=True,
        job_inputs__isnull=True,
        approval_rounds__isnull=True,
    )
    files = list(unused.values_list("key", "document_id"))
    unused.delete()
    Document.objects.filter(
        pk__in={document_id for _, document_id in files},
        versions__isnull=True,
        approvals__isnull=True,
    ).delete()
    transaction.on_commit(lambda: remove_files(key for key, _ in files), robust=True)
    messages.success(request, "Ветка ЭДО удалена.")
    return redirect("workflow_records")
