from contextlib import closing

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Document, Version, WorkflowRecord


class WorkflowTests(TestCase):
    def test_registry_lifecycle_audit_and_owner_boundary(self):
        users = get_user_model().objects
        owner = users.create_user("author")
        outsider = users.create_user("outsider")
        document = Document.objects.create(owner=owner, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.txt", size=10)
        create = reverse("workflow_create", args=[version.pk])
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(create).status_code, 404)
        self.client.force_login(owner)
        data = {"title": "Договор", "kind": "ДОГ", "purpose": "Поставка"}
        self.assertContains(self.client.post(create, {**data, "purpose": ""}), "Назначение")
        self.assertFalse(WorkflowRecord.objects.exists())
        for expected in (1, 2):
            detail = self.client.post(create, data).url
            self.assertEqual(self.client.post(detail, {"state": "registered"}).status_code, 400)
            self.assertEqual(self.client.post(detail, {"state": "withdrawn"}).status_code, 400)
            for next_state in ("review", "signed", "registered", "archived"):
                self.assertRedirects(self.client.post(detail, {"state": next_state}), detail)
            record = WorkflowRecord.objects.get(pk=int(detail.rstrip("/").split("/")[-1]))
            self.assertEqual(record.registration_number, f"ДОГ-{timezone.localdate().year}-{expected:06d}")
            self.assertEqual(record.events.count(), 5)
            self.assertTrue(all(event.actor == owner for event in record.events.all()))
        self.assertTrue(Version.objects.filter(pk=version.pk).exists())
        rejected = self.client.post(create, data).url
        self.assertRedirects(self.client.post(rejected, {"state": "review"}), rejected)
        self.assertEqual(self.client.post(rejected, {"state": "rejected"}).status_code, 400)
        self.assertRedirects(self.client.post(rejected, {"state": "rejected", "comment": "Нужны правки"}), rejected)
        self.assertContains(self.client.get(rejected), "Нужны правки")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.post(detail, {"state": "review"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workflow_records")), detail)

    def test_recipient_sees_only_sent_card_and_its_file(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from .storage import stored_file

        users = get_user_model().objects
        author = users.create_user("sender")
        reader = users.create_user("receiver")
        stranger = users.create_user("stranger")
        document = Document.objects.create(owner=author, title="Поручение")
        version = Version.objects.create(document=document, number=1, name="task.txt", size=4)
        self.client.force_login(author)
        detail = self.client.post(reverse("workflow_create", args=[version.pk]), {
            "title": "Поручение", "kind": "ЗАЯ", "purpose": "Проверка",
        }).url
        record = WorkflowRecord.objects.get()
        file_url = reverse("workflow_file", args=[record.pk])
        self.assertRedirects(self.client.post(detail, {"action": "assign", "recipient": reader.pk}), detail)
        self.assertContains(self.client.get(detail), "receiver")
        self.client.force_login(reader)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.client.force_login(author)
        self.assertRedirects(self.client.post(detail, {"state": "review"}), detail)
        self.client.force_login(reader)
        self.assertContains(self.client.get(reverse("workflow_records")), "Входящий от sender")
        self.assertEqual(self.client.post(detail, {"state": "signed"}).status_code, 403)
        with TemporaryDirectory() as directory, self.settings(DATA_DIR=Path(directory)):
            path = stored_file(version.key)
            path.parent.mkdir()
            path.write_bytes(b"test")
            response = self.client.get(file_url)
            self.assertEqual(b"".join(response.streaming_content), b"test")
            response.close()
        self.assertEqual(self.client.get(reverse("download_version", args=[version.pk])).status_code, 404)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(file_url).status_code, 404)

    def test_upload_creates_card_without_archive(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile

        actor = get_user_model().objects.create_user("uploader")
        self.client.force_login(actor)
        for retired in ("/archive/", "/approvals/"):
            self.assertEqual(self.client.get(retired).status_code, 404)
        create = reverse("workflow_upload")
        self.assertContains(self.client.get(reverse("workflow_records")), create)
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "uploads").mkdir()
            (data / "files").mkdir()
            with self.settings(DATA_DIR=data, FILE_UPLOAD_TEMP_DIR=data / "uploads"):
                response = self.client.post(create, {
                    "title": "Поручение", "kind": "ЗАЯ", "purpose": "Проверка",
                    "file": SimpleUploadedFile("task.txt", b"test"),
                })
                record = WorkflowRecord.objects.get()
                self.assertRedirects(response, reverse("workflow_detail", args=[record.pk]))
                with closing(self.client.get(reverse("workflow_file", args=[record.pk]))) as result:
                    self.assertEqual(b"".join(result.streaming_content), b"test")
