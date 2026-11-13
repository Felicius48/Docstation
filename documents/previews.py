import logging
import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .conversion import compress_pdf
from .models import CompressionPreview, Job
from .operations import extension

logger = logging.getLogger(__name__)
LIFETIME = timedelta(minutes=30)


def preview_source(key):
    return settings.DATA_DIR / "work" / f"compression-{key}.pdf"


@transaction.atomic
def create_preview(owner, name, size, strength, source):
    if extension(name) != "pdf" or not 0 < size <= settings.MAX_FILE_SIZE:
        raise ValueError("Выбери PDF размером до 50 МБ.")
    if CompressionPreview.objects.filter(owner=owner, updated_at__gte=timezone.now() - LIFETIME).count() >= 100:
        raise ValueError("Слишком много файлов для расчёта размера. Попробуй позже.")
    preview = CompressionPreview(owner=owner, strength=strength, source_size=size)
    path = preview_source(preview.pk)
    try:
        os.link(source, path)
        preview.save(force_insert=True)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return preview


def requeue_preview(previews, strength):
    previews.exclude(strength=strength).update(
        strength=strength, state=Job.State.QUEUED, result_size=None, updated_at=timezone.now(),
    )


def expire_previews():
    cutoff = timezone.now() - LIFETIME
    with transaction.atomic():
        expired = list(CompressionPreview.objects.filter(updated_at__lt=cutoff).values_list("pk", flat=True))
        CompressionPreview.objects.filter(pk__in=expired).delete()
    for key in expired:
        preview_source(key).unlink(missing_ok=True)


def recover_previews(directory):
    CompressionPreview.objects.filter(state=Job.State.RUNNING).update(state=Job.State.QUEUED)
    with transaction.atomic():
        active = {str(key) for key in CompressionPreview.objects.values_list("pk", flat=True)}
        for path in directory.glob("compression-*.pdf"):
            if path.stem.removeprefix("compression-") not in active:
                path.unlink(missing_ok=True)


def process_preview():
    queued = CompressionPreview.objects.filter(state=Job.State.QUEUED)
    preview = queued.order_by("updated_at").first()
    if preview is None:
        return False
    current = CompressionPreview.objects.filter(pk=preview.pk, strength=preview.strength)
    if not queued.filter(pk=preview.pk, strength=preview.strength).update(state=Job.State.RUNNING):
        return True
    state, size = Job.State.FAILED, None
    try:
        source = preview_source(preview.pk)
        with TemporaryDirectory(prefix="job-preview-", dir=source.parent) as directory:
            result = compress_pdf(source, Path(directory) / "result.pdf", {"strength": preview.strength})
            size = result.stat().st_size
        state = Job.State.DONE
    except Exception:
        logger.warning("Compression preview %s failed", preview.pk, exc_info=True)
    current.filter(state=Job.State.RUNNING).update(state=state, result_size=size, updated_at=timezone.now())
    return True
