import json
import tempfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .archive import VersionBusyError, delete_versions, save_version
from .archive_filters import ArchiveFilters
from .jobs import can_retry, enqueue, requeue
from .models import Batch, CompressionPreview, Document, Job, JobInput, Result, Version
from .operations import TARGETS, TOOLS, WORKSPACE_SECTIONS, compression_strength, file_rules, validate_submission, workspace_section
from .previews import create_preview, requeue_preview
from .storage import stored_file, zip_files
from .uploads import request_file
from .file_responses import download, private_file_response


@login_required
def workspace(request):
    selected = None
    version = request.GET.get("version")
    if version is not None:
        if not version.isascii() or not version.isdecimal() or len(version) > 18:
            raise Http404
        selected = get_object_or_404(Version, pk=version, document__owner=request.user)
    section = workspace_section(selected.name) if selected else "office"
    title, _, _, operations = WORKSPACE_SECTIONS[section]
    return render(request, "documents/workspace.html", {
        "selected": selected,
        "section": section, "sections": WORKSPACE_SECTIONS, "section_title": title,
        "tools": [(key, TOOLS[key][0]) for key in operations], "targets": TARGETS,
        "recent": Document.objects.filter(owner=request.user)[:4],
        "file_rules": file_rules(settings.MAX_BATCH_SIZE, settings.MAX_FILE_SIZE),
    })


@login_required
def archive(request):
    filters = ArchiveFilters(request.GET or None)
    documents = Document.objects.filter(owner=request.user).annotate(
        version_count=Count("versions"),
    ).order_by("-updated_at", "-pk")
    if filters.is_valid():
        data = filters.cleaned_data
        if data["q"]:
            documents = documents.filter(title__icontains=data["q"])
        dates = {}
        for field, lookup in (("start", "date__gte"), ("end", "date__lte")):
            if data[field]:
                dates[lookup] = data[field]
        versions = Version.objects.filter(document_id=OuterRef("pk"))
        if data["format"]:
            ext = data["format"]
            suffixes = {"jpg": ("jpg", "jpeg"), "tiff": ("tiff", "tif")}.get(ext, (ext,))
            versions = versions.filter(Q.create(
                [("name__iendswith", "." + suffix) for suffix in suffixes], connector=Q.OR,
            ))
        if data["operation"]:
            jobs = JobInput.objects.filter(
                version_id=OuterRef("pk"), job__owner=request.user,
                job__operation=data["operation"], job__state=Job.State.DONE,
                **{f"job__created_at__{lookup}": date for lookup, date in dates.items()},
            )
            versions = versions.filter(Exists(jobs))
        else:
            documents = documents.filter(**{f"updated_at__{lookup}": date for lookup, date in dates.items()})
        if data["format"] or data["operation"]:
            documents = documents.filter(Exists(versions))
    elif filters.is_bound:
        documents = documents.none()
    return render(request, "documents/archive.html", {
        "page": Paginator(documents, 30).get_page(request.GET.get("page")),
        "filters": filters,
        "filtered": any(request.GET.get(key) for key in filters.fields),
    })


@login_required
def document_detail(request, pk):
    document = get_object_or_404(Document, owner=request.user, pk=pk)
    versions = document.versions.prefetch_related(
        Prefetch("job_inputs", queryset=JobInput.objects.select_related("job").prefetch_related("job__results")),
    )
    return render(request, "documents/detail.html", {
        "document": document, "versions": versions,
    })


@login_required
@require_POST
def new_batch(request):
    batch = Batch.objects.create(owner=request.user)
    return JsonResponse({
        "upload": reverse("upload", args=[batch.pk]),
        "submit": reverse("submit", args=[batch.pk]),
    })


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
    response = JsonResponse({
        "state": preview.state, "strength": preview.strength,
        "original": preview.source_size, "size": preview.result_size,
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def add_version(request, pk):
    try:
        upload = request_file(request, "Добавь обновлённый файл.")
        save_version(request.user, upload, document_id=pk)
    except Document.DoesNotExist as exc:
        raise Http404 from exc
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Новая версия сохранена.")
    return redirect("document_detail", pk)


@login_required
@require_POST
def submit(request, pk):
    try:
        data = json.loads(request.body)
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


@login_required
def history(request):
    batches = Batch.objects.filter(owner=request.user, jobs__isnull=False).annotate(
        job_count=Count("jobs"),
    ).order_by("-pk")
    return render(request, "documents/history.html", {
        "page": Paginator(batches, 30).get_page(request.GET.get("page")),
    })


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(Batch, owner=request.user, pk=pk)
    jobs = list(batch.jobs.order_by("pk").prefetch_related("inputs__version", "results"))
    template = (
        "documents/batch_results.html" if request.GET.get("fragment") == "jobs" else "documents/batch.html"
    )
    return render(request, template, {
        "batch": batch,
        "jobs": [(job, TOOLS.get(job.operation, ("Операция из архива",))[0], can_retry(job))
                 for job in jobs],
        "active": any(job.state in (Job.State.QUEUED, Job.State.RUNNING) for job in jobs),
        "ready": any(job.state == Job.State.DONE for job in jobs),
    })


@login_required
@require_POST
def retry(request, pk):
    try:
        batch_id = requeue(request.user, pk)
    except Job.DoesNotExist as exc:
        raise Http404 from exc
    return redirect("batch_detail", batch_id)


@login_required
def download_version(request, pk, preview=False):
    version = get_object_or_404(Version, document__owner=request.user, pk=pk)
    return download(version.key, version.name, preview=preview)


@login_required
def download_result(request, pk, preview=False):
    result = get_object_or_404(Result, job__owner=request.user, pk=pk)
    return download(result.key, result.name, preview=preview)


@login_required
def download_batch(request, pk):
    batch = get_object_or_404(Batch, owner=request.user, pk=pk)
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
def delete(request, pk, version_pk=None):
    try:
        deleted = delete_versions(request.user, pk, version_pk)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    except VersionBusyError as exc:
        messages.error(request, str(exc))
        return redirect("document_detail", pk)
    messages.success(request, "Удалено из архива.")
    return redirect("archive") if deleted else redirect("document_detail", pk)
