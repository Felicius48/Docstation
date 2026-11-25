from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import OuterRef, Prefetch, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .file_responses import download
from .models import (
    WORKFLOW_TEXT_LIMIT,
    Document,
    Version,
    WorkflowEvent,
    WorkflowRecord,
)
from .storage import remove_files
from .uploads import TOO_LARGE, request_file
from .version_storage import save_version
from .workflow_forms import CommentForm, TaskForm

WORKFLOW_PAGE_SIZE = 30
State = WorkflowRecord.State
TASK_TRANSITIONS = {
    State.SENT: State.RECEIVED,
    State.RECEIVED: State.IN_PROGRESS,
    State.REWORK: State.IN_PROGRESS,
    State.IN_PROGRESS: State.DONE,
}
def visible_records(user):
    if user.is_superuser:
        return WorkflowRecord.objects.all()
    return WorkflowRecord.objects.filter(
        Q(sender=user)
        | Q(recipients=user, events__state=State.SENT, events__previous_state=State.CREATED),
    ).distinct()


def card_records(user):
    recipients = get_user_model().objects.select_related("profile").defer("profile__avatar")
    return (
        visible_records(user)
        .select_related("sender__profile", "version__document")
        .defer("sender__profile__avatar")
        .prefetch_related(
            Prefetch("recipients", queryset=recipients),
        )
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
@never_cache
def task_list(request, sent=False):
    writing = request.method == "POST"
    form = TaskForm(request.user, request.POST if writing else None)
    if writing:
        response = create_task(request, form)
        if response is not None:
            return response
    records = card_records(request.user).order_by("-updated_at", "-pk")
    if sent:
        records = records.filter(sender=request.user)
    else:
        records = records.filter(recipients=request.user)
    today = timezone.localdate()
    task_filter = request.GET.get("filter")
    if task_filter == "overdue":
        records = records.filter(due_date__lt=today).exclude(state=State.DONE)
    elif task_filter == "today":
        records = records.filter(due_date=today).exclude(state=State.DONE)
    elif task_filter == "waiting":
        states = [State.SENT, State.RECEIVED, State.REWORK]
        if sent:
            states.append(State.IN_PROGRESS)
        records = records.filter(state__in=states)
    return render(
        request,
        "documents/task_rows.html" if not writing and request.headers.get("X-Fragment") == "tasks" else "documents/task_list.html",
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
        state__in=(State.SENT, State.REWORK), recipients=request.user
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


def create_task(request, form):
    if not form.is_valid():
        return None
    versions = []
    try:
        with transaction.atomic():
            uploads = request.FILES.getlist("file")
            if getattr(request, "file_too_large", False):
                raise ValueError(TOO_LARGE)
            if sum(upload.size for upload in uploads) > settings.MAX_FILE_SIZE:
                raise ValueError("Общий размер вложений превышает 50 МБ.")
            for upload in uploads:
                versions.append(save_version(request.user, upload))
            uploaded = versions[0] if versions else None
            title = form.cleaned_data["title"] or (uploaded.name if uploaded else "")
            if not title:
                form.add_error("title", "Укажи название задачи или выбери файл для отправки.")
                return None
            record = WorkflowRecord.objects.create(
                version=uploaded,
                sender=request.user,
                title=title,
                purpose=form.cleaned_data["purpose"],
                due_date=form.cleaned_data["due_date"],
                state=State.SENT,
            )
            record.recipients.add(form.cleaned_data["recipient"])
            record.events.create(actor=request.user, state=State.CREATED, version=uploaded)
            record.events.create(actor=request.user, previous_state=State.CREATED, state=State.SENT, version=uploaded)
            for version in versions[1:]:
                record.events.create(actor=request.user, state=State.SENT, version=version)
            return redirect("workflow_detail", record.pk)
    except ValueError as exc:
        remove_files(version.key for version in versions)
        form.add_error(None, str(exc))
    except BaseException:
        remove_files(version.key for version in versions)
        raise
    return None


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def workflow_detail(request, pk):
    records = card_records(request.user)
    if request.method == "POST":
        with transaction.atomic():
            record = get_object_or_404(records, pk=pk)
            if request.user not in record.recipients.all():
                raise PermissionDenied
            target = request.POST.get("state")
            comment = request.POST.get("comment", "").strip()
            if not target or target != TASK_TRANSITIONS.get(record.state):
                return HttpResponseBadRequest("Недопустимый переход статуса.")
            if len(comment) > WORKFLOW_TEXT_LIMIT:
                return HttpResponseBadRequest("Слишком длинный комментарий.")
            previous = record.state
            record.state = target
            record.save(update_fields=["state", "updated_at"])
            record.events.create(
                actor=request.user,
                previous_state=previous,
                state=target,
                comment=comment,
                version=record.version,
            )
        return redirect("workflow_detail", pk)
    latest = WorkflowEvent.objects.filter(record_id=OuterRef("pk")).order_by("-pk").values("pk")[:1]
    if "after" in request.GET:
        revision = get_object_or_404(visible_records(request.user).annotate(revision=latest).values("revision"), pk=pk)
        if request.GET["after"] == str(revision["revision"] or 0):
            return HttpResponse(status=204)
    record = get_object_or_404(records.annotate(revision=latest), pk=pk)
    return render_record(request, record)


def can_return_task(user, record):
    return (
        record.state == State.DONE
        and record.sender_id == user.pk
    )


def can_replace_file(user, record):
    return record.state in TASK_TRANSITIONS and (
            record.sender_id == user.pk or user in record.recipients.all()
        )


def save_revision(record, request):
    file = request_file(request, "Выбери файл документа.")
    return save_version(record.sender, file, document=record.version.document if record.version else None)


def render_record(request, record, comment_form=None):
    events = list(record.events.select_related("actor", "version").order_by("pk"))
    attachments = [event for event in events if event.state == State.SENT and event.version_id
                   and not event.previous_state and not event.is_comment]
    attachment_ids = {event.pk for event in attachments}
    return render(
        request,
        "documents/workflow_detail.html",
        {
            "comment_form": comment_form if comment_form is not None else CommentForm(),
            "comments": [
                event for event in events if event.is_comment or (event.previous_state and event.comment)
            ],
            "can_return_task": can_return_task(request.user, record),
            "can_replace_file": can_replace_file(request.user, record),
            "record": record,
            "attachments": attachments,
            "events": [
                event for event in events if event.pk not in attachment_ids
                and (not event.is_comment or event.version_id or event.previous_state)
            ],
            "transitions": [
                (state, state.label) for state in [TASK_TRANSITIONS.get(record.state)] if state
            ]
            if request.user in record.recipients.all()
            else [],
        },
    )


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
    if version is None:
        raise Http404
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
    document_id = record.version.document_id if record.version else None
    version_ids = set(record.events.exclude(version_id=None).values_list("version_id", flat=True))
    if record.version_id:
        version_ids.add(record.version_id)
    record.events.all().delete()
    record.delete()

    # Older events did not store their version, so recover only an unshared document's history.
    if (
        document_id is not None
        and not WorkflowRecord.objects.filter(version__document_id=document_id).exists()
        and not WorkflowEvent.objects.filter(version__document_id=document_id).exists()
    ):
        version_ids.update(Version.objects.filter(document_id=document_id).values_list("pk", flat=True))

    unused = Version.objects.filter(
        pk__in=version_ids,
        workflowrecord__isnull=True,
        workflowevent__isnull=True,
        job_inputs__isnull=True,
    )
    files = list(unused.values_list("key", "document_id"))
    unused.delete()
    Document.objects.filter(
        pk__in={document_id for _, document_id in files},
        versions__isnull=True,
    ).delete()
    transaction.on_commit(lambda: remove_files(key for key, _ in files), robust=True)
    messages.success(request, "Задача удалена.")
    return redirect("task_list")


@login_required
@never_cache
@require_safe
def notifications(request):
    events = (
        WorkflowEvent.objects.filter(Q(record__sender=request.user) | Q(record__recipients=request.user))
        .exclude(actor=request.user)
        .exclude(state=State.CREATED)
        .exclude(state=State.SENT, previous_state="", is_comment=False)
        .select_related("record")
        .distinct()
        .order_by("-pk")
    )
    if request.GET.get("fragment") == "toast":
        after = request.GET.get("after")
        if after is not None:
            if not after.isascii() or not after.isdecimal() or len(after) > 19 or int(after) >= 2**63:
                return HttpResponseBadRequest("Недопустимый номер уведомления.")
            events = events.filter(pk__gt=int(after)).order_by("pk")
        return JsonResponse({"notifications": [
            {
                "id": event.pk,
                "title": event.record.title,
                "label": notification_label(event),
                "url": reverse("workflow_detail", args=[event.record_id]),
            }
            for event in events[:20]
        ]})
    page = Paginator(events, 30).get_page(request.GET.get("page"))
    return render(request, "documents/notifications.html", {
        "page": page,
        "notifications": [(event, notification_label(event)) for event in page],
    })


def notification_label(event):
    if event.previous_state == State.CREATED and event.state == State.SENT:
        return "Новая задача"
    if event.previous_state:
        return event.get_state_display()
    if event.version_id:
        return "Новая версия файла"
    return "Новый комментарий"
