import difflib
import shutil
import tempfile
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache

from .models import Job, Result
from .operations import extension
from .storage import remove_files, stored_file
from .version_storage import save_version
from .workflow_views import card_records, can_replace_file


@login_required
@require_http_methods(["GET", "POST"])
def attach_result(request, pk):
    result = get_object_or_404(Result, pk=pk, job__owner=request.user, job__state=Job.State.DONE)
    records = card_records(request.user).filter(
        Q(sender=request.user) | Q(recipients=request.user)
    ).distinct()
    uploaded = None
    error = ""
    if request.method == "POST":
        try:
            with transaction.atomic():
                task_id = request.POST.get("task", "")
                if not task_id.isascii() or not task_id.isdecimal() or len(task_id) > 18:
                    raise ValueError("Выбери задачу.")
                record = get_object_or_404(records, pk=task_id)
                if not can_replace_file(request.user, record):
                    raise Http404
                if result.size > settings.MAX_FILE_SIZE:
                    raise ValueError("Результат превышает допустимый размер версии.")
                with tempfile.TemporaryDirectory(dir=settings.DATA_DIR / "uploads") as directory:
                    path = Path(directory) / "result"
                    shutil.copyfile(stored_file(result.key), path)
                    with path.open("rb") as handle:
                        upload = File(handle, name=result.name)
                        uploaded = save_version(
                            record.sender, upload, document=record.version.document if record.version else None,
                            result_path=path,
                        )
                record.version = uploaded
                record.save(update_fields=["version", "updated_at"])
                record.events.create(actor=request.user, state=record.state, version=uploaded, is_comment=True)
            return redirect("workflow_detail", record.pk)
        except (ValueError, FileNotFoundError) as exc:
            if uploaded:
                remove_files([uploaded.key])
            error = str(exc) if isinstance(exc, ValueError) else "Файл результата недоступен."
        except BaseException:
            if uploaded:
                remove_files([uploaded.key])
            raise
    tasks = [record for record in records.distinct().order_by("-updated_at") if can_replace_file(request.user, record)]
    return render(request, "documents/attach_result.html", {"result": result, "tasks": tasks, "error": error})


def read_text(version, *, allow_cp1251=False):
    limit = 65536
    kind = extension(version.name)
    path = stored_file(version.key)
    if kind in {"docx", "odt"}:
        entry = "word/document.xml" if kind == "docx" else "content.xml"
        namespace = (
            "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            if kind == "docx"
            else "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        )
        with ZipFile(path) as archive, archive.open(entry) as file:
            data = file.read(8 * limit + 1)
        if len(data) > 8 * limit or b"<!DOCTYPE" in data or b"\0" in data:
            raise ValueError("Unsupported document XML")
        paragraphs = ElementTree.fromstring(data).iter(f"{{{namespace}}}p")
        text = "\n".join(
            "".join(node.text or "" for node in paragraph.iter(f"{{{namespace}}}t"))
            if kind == "docx" else "".join(paragraph.itertext())
            for paragraph in paragraphs
        )
    else:
        with path.open("rb") as file:
            data = file.read(4 * limit + 1)
        if len(data) > 4 * limit:
            raise ValueError("Text too large")
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                if not allow_cp1251:
                    raise
                text = data.decode("cp1251")
    if len(text) > limit:
        raise ValueError("Text too large")
    return text.splitlines()


@login_required
@never_cache
def compare_versions(request, pk):
    record = get_object_or_404(card_records(request.user), pk=pk)
    unique = {}
    for event in record.events.filter(version__document_id=record.version.document_id if record.version else None).select_related("version").order_by("pk"):
        unique.setdefault(event.version_id, event)
    events = list(unique.values())
    by_id = {str(event.pk): event for event in events}
    selected = []
    rows = []
    allow_cp1251 = request.GET.get("encoding") == "cp1251"
    mode = ""
    if len(events) >= 2:
        selected = [
            by_id.get(request.GET.get("left"), events[-2]),
            by_id.get(request.GET.get("right"), events[-1]),
        ]
        suffixes = {extension(event.version.name) for event in selected}
        if suffixes == {"pdf"}:
            mode = "pdf"
        elif suffixes <= {"txt", "md", "csv", "docx", "odt"}:
            mode = "text"
            excerpts = []
            for event in selected:
                try:
                    excerpts.append(read_text(event.version, allow_cp1251=allow_cp1251))
                except FileNotFoundError as exc:
                    raise Http404 from exc
                except UnicodeDecodeError:
                    mode = "encoding"
                    break
                except (ValueError, BadZipFile, KeyError, ElementTree.ParseError, RuntimeError):
                    mode = "limit"
                    break
            if mode == "text":
                diff = difflib.SequenceMatcher(None, *excerpts)
                for kind, a, b, c, d in diff.get_opcodes():
                    rows.append((kind, "\n".join(excerpts[0][a:b]), "\n".join(excerpts[1][c:d])))
    return render(request, "documents/compare_versions.html", {
        "record": record, "events": events, "selected": selected, "mode": mode, "rows": rows,
        "allow_cp1251": allow_cp1251,
    })
