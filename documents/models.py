import uuid

from django.conf import settings
from django.db import models


class Document(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]


class Version(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="unique_version_number"),
        ]


class Batch(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded = models.PositiveSmallIntegerField(default=0)
    submitted = models.BooleanField(default=False)


class Job(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Обрабатывается"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="jobs")
    operation = models.CharField(max_length=30)
    options = models.JSONField(default=dict)
    state = models.CharField(max_length=10, choices=State, default=State.QUEUED, db_index=True)
    error = models.CharField(max_length=350, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]


class JobInput(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="inputs")
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name="job_inputs")
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["job", "position"], name="unique_job_input_position"),
        ]


class Result(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="results")
    name = models.CharField(max_length=255)
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    size = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["pk"]


class CompressionPreview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    strength = models.PositiveSmallIntegerField()
    source_size = models.PositiveBigIntegerField()
    result_size = models.PositiveBigIntegerField(null=True)
    state = models.CharField(max_length=10, choices=Job.State, default=Job.State.QUEUED)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "updated_at"])]


class RegistrationPolicy(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    public = models.BooleanField(default=False)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField()


class RegistrationInvitation(models.Model):
    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expires_at = models.DateTimeField()


class Approval(models.Model):
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="approvals")
    title = models.CharField(max_length=255)


class ApprovalRound(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Черновик"
        OPEN = "open", "На согласовании"
        RETURNED = "returned", "На доработке"
        APPROVED = "approved", "Согласован"
        WITHDRAWN = "withdrawn", "Отозван"

    approval = models.ForeignKey(Approval, on_delete=models.CASCADE, related_name="rounds")
    version = models.ForeignKey(Version, on_delete=models.PROTECT, related_name="approval_rounds")
    state = models.CharField(max_length=10, choices=State, default=State.DRAFT)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    sent_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["pk"]


class ApprovalDecision(models.Model):
    class Choice(models.TextChoices):
        APPROVE = "approve", "Согласовано"
        RETURN = "return", "Возвращено на доработку"

    round = models.ForeignKey(ApprovalRound, on_delete=models.CASCADE, related_name="decisions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=7, choices=Choice, blank=True)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["round", "reviewer"], name="unique_approval_reviewer")]


class WorkflowRecord(models.Model):
    class Kind(models.TextChoices):
        CONTRACT = "ДОГ", "Договор"
        ACT = "АКТ", "Акт"
        ORDER = "ПРК", "Приказ"
        REQUEST = "ЗАЯ", "Заявка"
        INCOMING = "ВХ", "Входящая корреспонденция"
        OUTGOING = "ИСХ", "Исходящая корреспонденция"

    class State(models.TextChoices):
        CREATED = "created", "Создан"
        REVIEW = "review", "На согласовании"
        SIGNED = "signed", "Подписан"
        REGISTERED = "registered", "Зарегистрирован"
        ARCHIVED = "archived", "В архиве"
        REJECTED = "rejected", "Отклонён"
        WITHDRAWN = "withdrawn", "Отозван"

    version = models.ForeignKey(Version, on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    kind = models.CharField(max_length=3, choices=Kind.choices)
    purpose = models.TextField(max_length=4000)
    description = models.TextField(blank=True, max_length=4000)
    state = models.CharField(max_length=12, choices=State.choices, default=State.CREATED)
    registration_number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkflowEvent(models.Model):
    record = models.ForeignKey(WorkflowRecord, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    previous_state = models.CharField(max_length=12, choices=WorkflowRecord.State.choices, blank=True)
    state = models.CharField(max_length=12, choices=WorkflowRecord.State.choices)
    comment = models.TextField(blank=True, max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)
