import uuid

from django.conf import settings
from django.db import models

WORKFLOW_TEXT_LIMIT = 4000


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    patronymic = models.CharField("Отчество", max_length=150, blank=True)
    position = models.CharField("Должность", max_length=120, blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    avatar = models.BinaryField(blank=True, default=bytes)

    class Meta:
        verbose_name = "профиль"
        verbose_name_plural = "профиль"


class Document(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]


class Version(models.Model):
    marks = models.JSONField(default=list, blank=True)
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
        CANCELLED = "cancelled", "Отменено"

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


class WorkflowRecord(models.Model):
    class State(models.TextChoices):
        CREATED = "created", "Создан"
        SENT = "sent", "Отправлено"
        RECEIVED = "received", "Получено"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Готово"
        REWORK = "rework", "На переделке"
    version = models.ForeignKey(Version, on_delete=models.CASCADE, null=True, blank=True)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_tasks")
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="received_workflow_cards",
    )
    title = models.CharField(max_length=255)
    purpose = models.TextField(max_length=WORKFLOW_TEXT_LIMIT)
    due_date = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=12, choices=State.choices, default=State.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkflowEvent(models.Model):
    reply_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    record = models.ForeignKey(WorkflowRecord, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    version = models.ForeignKey(Version, on_delete=models.CASCADE, null=True, blank=True)
    screenshots = models.ManyToManyField(Version, blank=True, related_name="screenshot_events")
    is_comment = models.BooleanField(default=False)
    previous_state = models.CharField(max_length=12, choices=WorkflowRecord.State.choices, blank=True)
    state = models.CharField(max_length=12, choices=WorkflowRecord.State.choices)
    comment = models.TextField(blank=True, max_length=WORKFLOW_TEXT_LIMIT)
    created_at = models.DateTimeField(auto_now_add=True)
