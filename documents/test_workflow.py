from contextlib import closing

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Document, Version, WorkflowRecord


class WorkflowTests(TestCase):
    def user(self, username):
        return get_user_model().objects.create_user(username)

    def test_registry_lifecycle_audit_and_owner_boundary(self):
        owner = self.user("author")
        outsider = self.user("outsider")
        recipient = self.user("recipient")
        document = Document.objects.create(owner=owner, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.txt", size=10)
        create = reverse("workflow_create", args=[version.pk])
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(create).status_code, 404)
        self.client.force_login(owner)
        data = {"title": "Договор", "kind": "ДОГ", "purpose": "Поставка", "recipient": recipient.pk}
        self.assertContains(self.client.post(create, {**data, "purpose": ""}), "Назначение")
        self.assertFalse(WorkflowRecord.objects.exists())
        for expected in (1, 2):
            self.client.force_login(owner)
            detail = self.client.post(create, data).url
            self.assertEqual(self.client.post(detail, {"state": "registered"}).status_code, 400)
            self.assertEqual(self.client.post(detail, {"state": "withdrawn"}).status_code, 400)
            self.assertRedirects(self.client.post(detail, {"state": "review"}), detail)
            self.client.force_login(recipient)
            for next_state in ("signed", "registered", "archived"):
                self.assertRedirects(self.client.post(detail, {"state": next_state}), detail)
            record = WorkflowRecord.objects.get(pk=int(detail.rstrip("/").split("/")[-1]))
            self.assertEqual(record.registration_number, f"ДОГ-{timezone.localdate().year}-{expected:06d}")
            self.assertEqual(record.events.count(), 5)
            self.assertEqual(list(record.events.order_by("pk").values_list("actor_id", flat=True)),
                             [owner.pk, owner.pk, recipient.pk, recipient.pk, recipient.pk])
        self.assertTrue(Version.objects.filter(pk=version.pk).exists())
        self.client.force_login(owner)
        rejected = self.client.post(create, data).url
        self.assertRedirects(self.client.post(rejected, {"state": "review"}), rejected)
        self.client.force_login(recipient)
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

        from django.core.files.uploadedfile import SimpleUploadedFile

        from .storage import stored_file

        author = self.user("sender")
        reader = self.user("receiver")
        stranger = self.user("stranger")
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
        edit_url = reverse("workflow_edit", args=[record.pk])
        updates = {"title": "Обновлено", "purpose": "Проверка"}
        self.assertEqual(self.client.post(detail, {"state": "signed"}).status_code, 403)
        self.assertEqual(self.client.post(edit_url, updates).status_code, 403)
        self.client.force_login(reader)
        self.assertContains(self.client.get(reverse("workflow_records")), "Входящий от sender")
        self.assertRedirects(self.client.post(edit_url, updates), detail)
        self.assertContains(self.client.get(detail), "Обновлено")
        with TemporaryDirectory() as directory, self.settings(
            DATA_DIR=Path(directory), FILE_UPLOAD_TEMP_DIR=Path(directory),
        ):
            path = stored_file(version.key)
            path.parent.mkdir()
            path.write_bytes(b"test")
            response = self.client.get(file_url)
            self.assertEqual(b"".join(response.streaming_content), b"test")
            response.close()
            self.assertRedirects(self.client.post(edit_url, {
                **updates, "file": SimpleUploadedFile("revision.txt", b"updated"),
            }), detail)
            record.refresh_from_db()
            self.assertEqual(record.version.number, 2)
            self.assertEqual(path.read_bytes(), b"test")
            initial_event = record.events.order_by("pk").first()
            initial_file = reverse("workflow_event_file", args=[record.pk, initial_event.pk])
            self.assertContains(self.client.get(detail), "Версия 2 · revision.txt")
            with closing(self.client.get(initial_file)) as result:
                self.assertEqual(b"".join(result.streaming_content), b"test")
            with closing(self.client.get(file_url)) as result:
                self.assertEqual(b"".join(result.streaming_content), b"updated")
        self.assertEqual(self.client.get(reverse("download_version", args=[version.pk])).status_code, 404)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.assertEqual(self.client.get(initial_file).status_code, 404)

    def test_upload_creates_card_without_archive(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile

        actor = self.user("uploader")
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

    def test_admin_panel_and_workflow_permissions_are_separate(self):
        from django.core.management import call_command

        author = self.user("author")
        colleague = self.user("colleague")
        department = Group.objects.create(name="Бухгалтерия")
        author.groups.add(department)
        colleague.groups.add(department)
        version = Version.objects.create(
            document=Document.objects.create(owner=author, title="Test"), number=1, name="test.txt", size=4,
        )
        record = WorkflowRecord.objects.create(version=version, title="Test", kind="ЗАЯ", purpose="Check")
        detail = reverse("workflow_detail", args=[record.pk])
        admin = reverse("account_admin:index")
        self.client.force_login(colleague)
        self.assertEqual(self.client.get(reverse("workflow_records")).status_code, 200)
        self.assertEqual(self.client.get(reverse("workflow_upload")).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(admin, follow=True).status_code, 403)
        alex = self.user("alex")
        call_command("grant_admin", alex.username)
        alex.refresh_from_db()
        self.assertTrue(alex.is_superuser and alex.is_staff)
        self.client.force_login(alex)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertRedirects(self.client.post(reverse("workflow_edit", args=[record.pk]), {
            "title": "Изменено администратором", "purpose": "Check",
        }), detail)
        for page in (admin, reverse("account_admin:auth_user_changelist"), reverse("account_admin:auth_group_changelist")):
            self.assertEqual(self.client.get(page).status_code, 200)

    def test_create_and_send_delivers_to_recipient(self):
        sender = self.user("sender")
        recipient = self.user("recipient")
        document = Document.objects.create(owner=sender, title="Поручение")
        version = Version.objects.create(document=document, number=1, name="task.txt", size=4)
        create = reverse("workflow_create", args=[version.pk])
        data = {"title": "Поручение", "kind": "ЗАЯ", "purpose": "Проверка", "action": "send"}
        self.client.force_login(sender)
        self.assertContains(self.client.post(create, data), "Укажи получателя перед отправкой.")
        self.assertFalse(WorkflowRecord.objects.exists())
        detail = self.client.post(create, {**data, "recipient": recipient.pk}).url
        record = WorkflowRecord.objects.get()
        self.assertEqual(record.state, WorkflowRecord.State.REVIEW)
        self.assertEqual(list(record.events.order_by("pk").values_list("state", flat=True)), ["created", "review"])
        self.client.force_login(recipient)
        self.assertContains(self.client.get(reverse("workflow_records")), detail)
        self.assertContains(self.client.get(detail), "<dt>Кому направлен</dt><dd>recipient</dd>")

    def test_comments_require_card_access_and_do_not_change_state(self):
        author, recipient, outsider = (self.user(name) for name in ("author", "recipient", "outsider"))
        document = Document.objects.create(owner=author, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.DOCX", size=10)
        self.client.force_login(author)
        detail = self.client.post(reverse("workflow_create", args=[version.pk]), {
            "title": "Договор", "kind": "ДОГ", "purpose": "Проверка", "action": "send",
            "recipient": recipient.pk,
        }).url
        record = WorkflowRecord.objects.get()
        url = reverse("workflow_comment", args=[record.pk])
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(url, {"comment": "Посторонний"}).status_code, 404)
        self.client.force_login(recipient)
        self.assertEqual(self.client.post(url, {"comment": "   "}).status_code, 200)
        self.assertFalse(record.events.filter(is_comment=True).exists())
        self.assertRedirects(self.client.post(url, {"comment": "Получил, <проверяю>"}), detail + "#document-comments")
        record.refresh_from_db()
        self.assertEqual(record.state, WorkflowRecord.State.REVIEW)
        self.assertEqual(record.events.get(is_comment=True).actor, recipient)
        self.client.force_login(author)
        page = self.client.get(detail)
        self.assertContains(page, "Получил, &lt;проверяю&gt;")
        self.assertContains(page, 'data-format="word"')
