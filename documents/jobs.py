import logging
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F

from .conversion import ConversionError, convert
from .models import Batch, Job, JobInput, Result, Version
from .operations import GROUPED, TOOLS, extension, supports
from .storage import remove_files, stored_file, zip_files

logger = logging.getLogger(__name__)


@transaction.atomic
def enqueue(owner, batch_id, operation, options, ids):
    batch = Batch.objects.get(pk=batch_id, owner=owner)
    if batch.submitted:
        return
    versions = Version.objects.filter(document__owner=owner, pk__in=ids).in_bulk()
    if len(versions) != len(ids):
        raise Version.DoesNotExist

    ordered = [versions[pk] for pk in ids]
    compatible = [supports(operation, version.name, options) for version in ordered]
    if operation in GROUPED:
        if not all(compatible):
            raise ValueError("Все файлы должны подходить для этой операции.")
        groups = [(ordered, True)]
    else:
        groups = [([version], valid) for version, valid in zip(ordered, compatible)]

    selections = options.get("selections")
    if selections is not None and selections.keys() != {str(pk) for pk in ids}:
        raise ValueError("Выбери страницы для каждого PDF.")
    jobs = Job.objects.bulk_create([
        Job(
            owner=owner, batch=batch, operation=operation,
            options=options if selections is None else {
                "pages": selections[str(group[0].pk)], "mode": options["mode"],
            },
            state=Job.State.QUEUED if valid else Job.State.FAILED,
            error="" if valid else "Этот формат не подходит для выбранной операции.",
        )
        for group, valid in groups
    ])
    JobInput.objects.bulk_create([
        JobInput(job=job, version=version, position=index)
        for job, (group, _) in zip(jobs, groups)
        for index, version in enumerate(group)
    ])
    batch.submitted = True
    batch.save(update_fields=["submitted"])


def can_retry(job):
    if job.state != Job.State.FAILED or job.operation not in TOOLS:
        return False
    inputs = job.inputs.all()
    return bool(inputs) and all(supports(job.operation, item.version.name, job.options) for item in inputs)


@transaction.atomic
def requeue(owner, job_id):
    job = Job.objects.prefetch_related("inputs__version").get(pk=job_id, owner=owner)
    if can_retry(job):
        job.state = Job.State.QUEUED
        job.error = ""
        job.attempts = 0
        job.save(update_fields=["state", "error", "attempts"])
    return job.batch_id


def claim():
    job = Job.objects.filter(state=Job.State.QUEUED).order_by("pk").first()
    if job is not None and Job.objects.filter(pk=job.pk, state=Job.State.QUEUED).update(
        state=Job.State.RUNNING, attempts=F("attempts") + 1,
    ):
        job.state = Job.State.RUNNING
        job.attempts += 1
        return job
    return None


def process(job):
    created = []
    try:
        with tempfile.TemporaryDirectory(prefix="job-", dir=settings.DATA_DIR / "work") as directory:
            work = Path(directory)
            inputs = list(job.inputs.select_related("version"))
            if not inputs:
                raise ConversionError("Исходный файл больше не доступен.")
            paths = []
            for item in inputs:
                suffix = extension(item.version.name)
                path = work / f"input-{item.position}.{suffix}"
                source = stored_file(item.version.key)
                try:
                    os.link(source, path)
                except OSError:
                    shutil.copyfile(source, path)
                paths.append(path)

            outputs = convert(job.operation, paths, job.options, work)
            if not outputs:
                raise ConversionError("Готовых файлов нет.")
            stem = Path(inputs[0].version.name).stem[:180]
            separate = job.operation == "extract" and job.options.get("mode") == "separate"
            if len(outputs) > 1 and not separate:
                bundle = work / "pages.zip"
                zip_files(bundle, [(output, output.name) for output in outputs])
                outputs = [bundle]
            with transaction.atomic():
                for output in outputs:
                    name = f"{stem}{output.suffix}"
                    if separate:
                        name = f"{stem}-{output.name}"
                    elif name == inputs[0].version.name or job.operation in {"merge", "extract"}:
                        name = f"{stem}-{job.operation}{output.suffix}"
                    result = Result.objects.create(job=job, name=name, size=output.stat().st_size)
                    created.append(result.key)
                    output.replace(stored_file(result.key))
                Job.objects.filter(pk=job.pk).update(state=Job.State.DONE, error="")
    except Exception as exc:
        remove_files(created)
        if isinstance(exc, (ConversionError, ValueError)):
            message = str(exc)[:350]
        else:
            logger.exception("Job %s failed", job.pk)
            message = "Файл повреждён или его не удалось обработать."
        Job.objects.filter(pk=job.pk).update(state=Job.State.FAILED, error=message)


def recover(directory):
    for scratch in directory.glob("job-*"):
        if scratch.is_dir():
            shutil.rmtree(scratch)
    # Database and filesystem commits are separate. Reconcile interrupted moves
    # while uploads and deletes cannot change the set of referenced keys.
    with transaction.atomic():
        Job.objects.filter(state__in=[Job.State.QUEUED, Job.State.RUNNING]).exclude(
            operation__in=TOOLS,
        ).update(state=Job.State.FAILED, error="Эта операция больше недоступна.")
        interrupted = Job.objects.filter(state=Job.State.RUNNING)
        interrupted.filter(attempts__gte=2).update(
            state=Job.State.FAILED, error="Обработка дважды прервалась. Задачу можно повторить вручную.",
        )
        interrupted.update(state=Job.State.QUEUED)
        live = {str(key) for key in Version.objects.values_list("key", flat=True).iterator()}
        live.update(str(key) for key in Result.objects.values_list("key", flat=True).iterator())
        orphaned = []
        for path in (settings.DATA_DIR / "files").iterdir():
            if path.name not in live and path.is_file():
                try:
                    key = UUID(path.name)
                except ValueError:
                    continue
                if str(key) == path.name:
                    orphaned.append(key)
    remove_files(orphaned)
