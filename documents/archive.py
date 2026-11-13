import os
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum

from .models import ApprovalRound, Batch, Document, Job, Result, Version
from .operations import SUPPORTED, extension
from .storage import remove_files, stored_file
from .uploads import TOO_LARGE


def version_name(upload):
    name = Path(upload.name.replace("\\", "/")).name
    return "".join(character for character in name if character.isprintable())[:255]


def check_archive_quota(user, incoming_size):
    originals = Version.objects.filter(document__owner=user).aggregate(total=Sum("size", default=0))
    results = Result.objects.filter(job__owner=user).aggregate(total=Sum("size", default=0))
    if originals["total"] + results["total"] + incoming_size > settings.MAX_ARCHIVE_BYTES:
        raise ValueError("Лимит архива достигнут. Удали ненужные документы.")


def save_version(user, upload, batch_id=None, document_id=None):
    name = version_name(upload)
    if extension(name) not in SUPPORTED:
        raise ValueError("Этот формат пока не поддерживается.")
    if upload.size > settings.MAX_FILE_SIZE:
        raise ValueError(TOO_LARGE)
    if not upload.size:
        raise ValueError("Файл пуст.")

    key = None
    try:
        with transaction.atomic():
            if batch_id is not None:
                batch = Batch.objects.get(owner=user, pk=batch_id, submitted=False)
                if batch.uploaded >= settings.MAX_BATCH_SIZE:
                    raise ValueError("В одной пачке может быть до 50 файлов.")
                batch.uploaded += 1
                batch.save(update_fields=["uploaded"])
            check_archive_quota(user, upload.size)
            if document_id is not None:
                document = Document.objects.get(owner=user, pk=document_id)
                number = (document.versions.aggregate(last=Max("number"))["last"] or 0) + 1
                document.save(update_fields=["updated_at"])
            else:
                document = Document.objects.create(owner=user, title=name)
                number = 1
            version = Version.objects.create(document=document, number=number, name=name, size=upload.size)
            key = version.key
            # The configured Django handler already placed the upload on the same volume.
            os.replace(upload.temporary_file_path(), stored_file(key))
    except BaseException:
        if key:
            remove_files([key])
        raise
    return version


class VersionBusyError(Exception):
    pass


@transaction.atomic
def delete_versions(user, document_id, version_id):
    document = Document.objects.get(owner=user, pk=document_id)
    versions = document.versions.all()
    if version_id is not None:
        versions = versions.filter(pk=version_id)
        if not versions.exists():
            raise Version.DoesNotExist

    jobs = Job.objects.filter(inputs__version__in=versions)
    if jobs.filter(state=Job.State.RUNNING).exists():
        raise VersionBusyError("Дождись завершения обработки.")
    if ApprovalRound.objects.filter(version__in=versions).exists():
        raise VersionBusyError("Версия хранится в истории согласования.")
    keys = list(versions.values_list("key", flat=True))
    keys.extend(Result.objects.filter(job__in=jobs).values_list("key", flat=True))
    jobs.delete()
    versions.delete()
    deleted = not document.versions.exists()
    if deleted:
        document.delete()
    else:
        document.save(update_fields=["updated_at"])
    transaction.on_commit(lambda: remove_files(keys))
    return deleted
