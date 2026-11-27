import json
import tempfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .file_responses import download, private_file_response
from .jobs import can_retry, enqueue, queue_status, requeue
from .models import Batch, CompressionPreview, Job, Result, Version
from .operations import (
    TARGETS,
    TOOLS,
    WORKSPACE_SECTIONS,
    compression_strength,
    file_rules,
    validate_submission,
)
from .previews import create_preview, requeue_preview
from .storage import stored_file, zip_files
from .uploads import request_file
from .version_storage import save_version


@login_required
def workspace(request):
    selected = None
    version = request.GET.get("version")
    if version is not None:
        if not version.isascii() or not version.isdecimal() or len(version) > 18:
            raise Http404
        selected = get_object_or_404(Version, pk=version, document__owner=request.user)
    section = "office"
    return render(
        request,
        "documents/workspace.html",
        {
            "page": history_page(request),
            "selected": selected,
            "section": section,
            "sections": WORKSPACE_SECTIONS,
            "section_title": WORKSPACE_SECTIONS[section][0],
            "targets": TARGETS,
            "max_file_mb": settings.MAX_FILE_SIZE / 1_000_000,
            "file_rules": file_rules(settings.MAX_BATCH_SIZE, settings.MAX_FILE_SIZE),
        },
    )


@login_required
@require_POST
def new_batch(request):
    batch = Batch.objects.create(owner=request.user)
    return JsonResponse(
        {
            "upload": reverse("upload", args=[batch.pk]),
            "submit": reverse("submit", args=[batch.pk]),
        }
    )


@login_required
@require_POST
def upload(request, pk):
    try:
        upload = request_file(request, "Добавь файл.")
        version = save_version(request.user, upload, batch_id=pk)
    except Batch.DoesNotExist as exc:
        raise Http404 from exc
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"id": version.pk, "name": version.name}, status=201)


@login_required
@require_POST
def new_compression_preview(request):
    try:
        strength = compression_strength(request.POST.get("strength"))
        version_id = request.POST.get("version", "")
        if version_id:
            if not version_id.isascii() or not version_id.isdecimal() or len(version_id) > 18:
                raise Http404
            version = get_object_or_404(Version, document__owner=request.user, pk=version_id)
            name, size, source = version.name, version.size, stored_file(version.key)
        else:
            file = request_file(request, "Добавь PDF.")
            name, size, source = file.name, file.size, file.temporary_file_path()
        preview = create_preview(request.user, name, size, strength, source)
    except FileNotFoundError as exc:
        raise Http404 from exc
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"url": reverse("compression_preview", args=[preview.pk])}, status=201)


@login_required
@require_http_methods(["GET", "POST"])
def compression_preview(request, pk):
    previews = CompressionPreview.objects.filter(owner=request.user, pk=pk)
    if request.method == "POST":
        try:
            strength = compression_strength(request.POST.get("strength"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        requeue_preview(previews, strength)
    preview = get_object_or_404(previews)
    response = JsonResponse(
        {
            "state": preview.state,
            "strength": preview.strength,
            "original": preview.source_size,
            "size": preview.result_size,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def submit(request, pk):
    payload_limit = 2 * 1024 * 1024
    body = request.read(payload_limit + 1)
    if len(body) > payload_limit:
        return JsonResponse({"error": "Слишком большой запрос."}, status=413)
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return JsonResponse({"error": "Проверь параметры операции."}, status=400)
    try:
        operation, options, ids = validate_submission(data, settings.MAX_BATCH_SIZE)
        enqueue(request.user, pk, operation, options, ids)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"url": reverse("batch_detail", args=[pk])})


def visible_to(queryset, user, owner="owner"):
    return queryset if user.is_superuser else queryset.filter(**{owner: user})


def history_page(request):
    batches = (
        visible_to(Batch.objects.filter(jobs__isnull=False), request.user)
        .select_related("owner")
        .annotate(
            job_count=Count("jobs"),
        )
        .order_by("-pk")
    )
    return Paginator(batches, 10).get_page(request.GET.get("page"))


@login_required
def history(request):
    return redirect(reverse("workspace", query=request.GET, fragment="history"))


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(visible_to(Batch.objects.all(), request.user), pk=pk)
    jobs = list(batch.jobs.order_by("pk").prefetch_related("inputs__version", "results"))
    results = [result for job in jobs if job.state == Job.State.DONE for result in job.results.all()]
    template = (
        "documents/batch_results.html" if request.GET.get("fragment") == "jobs" else "documents/batch.html"
    )
    return render(
        request,
        template,
        {
            "batch": batch,
            "jobs": [(job, TOOLS.get(job.operation, "Старая операция"), can_retry(job)) for job in jobs],
            "active": any(job.state in (Job.State.QUEUED, Job.State.RUNNING) for job in jobs),
            "queue": queue_status(batch.owner),
            "results": results,
        },
    )


@login_required
@require_POST
def retry(request, pk):
    job = get_object_or_404(visible_to(Job.objects.select_related("owner"), request.user), pk=pk)
    try:
        batch_id = requeue(job.owner, pk)
    except Job.DoesNotExist as exc:
        raise Http404 from exc
    except ValueError as exc:
        messages.error(request, str(exc))
        batch_id = job.batch_id
    return redirect("batch_detail", batch_id)


@login_required
def download_version(request, pk, preview=False):
    version = get_object_or_404(visible_to(Version.objects.all(), request.user, "document__owner"), pk=pk)
    return download(version.key, version.name, preview=preview)


@login_required
def download_result(request, pk, preview=False):
    result = get_object_or_404(visible_to(Result.objects.all(), request.user, "job__owner"), pk=pk)
    return download(result.key, result.name, preview=preview)


@login_required
def download_batch(request, pk):
    batch = get_object_or_404(visible_to(Batch.objects.all(), request.user), pk=pk)
    results = list(Result.objects.filter(job__batch=batch, job__state=Job.State.DONE))
    if not results:
        raise Http404
    archive = tempfile.TemporaryFile(dir=settings.DATA_DIR / "work")
    try:
        zip_files(archive, [(stored_file(result.key), result.name) for result in results])
        archive.seek(0)
    except FileNotFoundError as exc:
        archive.close()
        raise Http404 from exc
    except BaseException:
        archive.close()
        raise
    return private_file_response(archive, f"docstation-{pk}.zip")


@login_required
@require_POST
def cancel_job(request, pk):
    jobs = visible_to(Job.objects.all(), request.user)
    job = get_object_or_404(jobs, pk=pk)
    jobs.filter(pk=pk, state=Job.State.QUEUED).update(state=Job.State.CANCELLED)
    return redirect("batch_detail", job.batch_id)
