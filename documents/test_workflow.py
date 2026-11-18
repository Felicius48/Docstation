from contextlib import closing

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Document, DocumentType, UserProfile, Version, WorkflowRecord


class WorkflowTests(TestCase):
    def user(self, username):
        return get_user_model().objects.create_user(username)

    def post_edit(self, url, data):
        record = WorkflowRecord.objects.get(pk=url.rstrip("/").split("/")[-2])
        return self.client.post(url, {"revision": record.updated_at.isoformat(), **data})

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
        data = {
            "title": "Договор",
            "mode": "approval",
            "kind": "ДОГ",
            "purpose": "Поставка",
            "recipients": recipient.pk,
        }
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
                record = WorkflowRecord.objects.get(pk=detail.rstrip("/").split("/")[-1])
                self.assertRedirects(
                    self.client.post(
                        detail,
                        {
                            "state": next_state,
                            "revision": record.updated_at.isoformat(),
                        },
                    ),
                    detail,
                )
            record = WorkflowRecord.objects.get(pk=int(detail.rstrip("/").split("/")[-1]))
            self.assertEqual(record.registration_number, f"ДОГ-{timezone.localdate().year}-{expected:06d}")
            self.assertEqual(record.events.count(), 5)
            self.assertEqual(
                list(record.events.order_by("pk").values_list("actor_id", flat=True)),
                [owner.pk, owner.pk, recipient.pk, recipient.pk, recipient.pk],
            )
        self.assertTrue(Version.objects.filter(pk=version.pk).exists())
        self.client.force_login(owner)
        rejected = self.client.post(create, data).url
        self.assertRedirects(self.client.post(rejected, {"state": "review"}), rejected)
        self.client.force_login(recipient)
        self.assertEqual(self.client.post(rejected, {"state": "rejected"}).status_code, 400)
        self.assertRedirects(
            self.client.post(rejected, {"state": "rejected", "comment": "Нужны правки"}), rejected
        )
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
        detail = self.client.post(
            reverse("workflow_create", args=[version.pk]),
            {
                "title": "Поручение",
                "mode": "approval",
                "kind": "ЗАЯ",
                "purpose": "Проверка",
            },
        ).url
        record = WorkflowRecord.objects.get()
        file_url = reverse("workflow_file", args=[record.pk])
        self.assertRedirects(self.client.post(detail, {"action": "assign", "recipients": reader.pk}), detail)
        self.assertContains(self.client.get(detail), "receiver")
        self.client.force_login(reader)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.client.force_login(author)
        self.assertRedirects(self.client.post(detail, {"state": "review"}), detail)
        edit_url = reverse("workflow_edit", args=[record.pk])
        updates = {"title": "Обновлено", "purpose": "Проверка"}
        self.assertEqual(self.client.post(detail, {"state": "signed"}).status_code, 403)
        self.assertEqual(self.post_edit(edit_url, updates).status_code, 403)
        self.client.force_login(reader)
        self.assertContains(self.client.get(reverse("workflow_records")), "Входящий от sender")
        self.assertRedirects(self.post_edit(edit_url, updates), detail)
        self.assertContains(self.client.get(detail), "Обновлено")
        with (
            TemporaryDirectory() as directory,
            self.settings(
                DATA_DIR=Path(directory),
                FILE_UPLOAD_TEMP_DIR=Path(directory),
            ),
        ):
            path = stored_file(version.key)
            path.parent.mkdir()
            path.write_bytes(b"test")
            response = self.client.get(file_url)
            self.assertEqual(b"".join(response.streaming_content), b"test")
            response.close()
            self.assertRedirects(
                self.post_edit(
                    edit_url,
                    {
                        **updates,
                        "file": SimpleUploadedFile("revision.txt", b"updated"),
                    },
                ),
                detail,
            )
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
                response = self.client.post(
                    create,
                    {
                        "title": "Поручение",
                        "mode": "approval",
                        "kind": "ЗАЯ",
                        "purpose": "Проверка",
                        "file": SimpleUploadedFile("task.txt", b"test"),
                    },
                )
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
            document=Document.objects.create(owner=author, title="Test"),
            number=1,
            name="test.txt",
            size=4,
        )
        record = WorkflowRecord.objects.create(version=version, title="Test", kind_id="ЗАЯ", purpose="Check")
        detail = reverse("workflow_detail", args=[record.pk])
        admin = reverse("account_admin:index")
        self.client.force_login(colleague)
        self.assertNotContains(self.client.get(reverse("task_list")), f'href="{admin}"')
        self.assertEqual(self.client.get(reverse("workflow_records")).status_code, 200)
        self.assertEqual(self.client.get(reverse("workflow_upload")).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(admin, follow=True).status_code, 403)
        alex = self.user("alex")
        call_command("grant_admin", alex.username)
        alex.refresh_from_db()
        self.assertTrue(alex.is_superuser and alex.is_staff)
        self.client.force_login(alex)
        self.assertContains(self.client.get(reverse("task_list")), f'href="{admin}"', count=1)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertRedirects(
            self.post_edit(
                reverse("workflow_edit", args=[record.pk]),
                {
                    "title": "Изменено администратором",
                    "purpose": "Check",
                },
            ),
            detail,
        )
        for page in (
            admin,
            reverse("account_admin:auth_user_changelist"),
            reverse("account_admin:auth_group_changelist"),
        ):
            self.assertEqual(self.client.get(page).status_code, 200)

    def test_create_and_send_delivers_to_recipient(self):
        sender = self.user("sender")
        recipient = self.user("recipient")
        document = Document.objects.create(owner=sender, title="Поручение")
        version = Version.objects.create(document=document, number=1, name="task.txt", size=4)
        create = reverse("workflow_create", args=[version.pk])
        data = {
            "title": "Поручение",
            "mode": "approval",
            "kind": "ЗАЯ",
            "purpose": "Проверка",
            "action": "send",
        }
        self.client.force_login(sender)
        self.assertContains(self.client.post(create, data), "Укажи получателя перед отправкой.")
        self.assertFalse(WorkflowRecord.objects.exists())
        detail = self.client.post(create, {**data, "recipients": recipient.pk}).url
        record = WorkflowRecord.objects.get()
        self.assertEqual(record.state, WorkflowRecord.State.REVIEW)
        self.assertEqual(
            list(record.events.order_by("pk").values_list("state", flat=True)), ["created", "review"]
        )
        self.client.force_login(recipient)
        self.assertContains(self.client.get(reverse("workflow_records")), detail)
        self.assertContains(self.client.get(detail), "<dt>Кому направлен</dt><dd>ФИО не указано</dd>")

    def test_comments_require_card_access_and_do_not_change_state(self):
        author, recipient, outsider = (self.user(name) for name in ("author", "recipient", "outsider"))
        document = Document.objects.create(owner=author, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.DOCX", size=10)
        self.client.force_login(author)
        detail = self.client.post(
            reverse("workflow_create", args=[version.pk]),
            {
                "title": "Договор",
                "mode": "approval",
                "kind": "ДОГ",
                "purpose": "Проверка",
                "action": "send",
                "recipients": recipient.pk,
            },
        ).url
        record = WorkflowRecord.objects.get()
        url = reverse("workflow_comment", args=[record.pk])
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(url, {"comment": "Посторонний"}).status_code, 404)
        self.client.force_login(recipient)
        self.assertEqual(self.client.post(url, {"comment": "   "}).status_code, 200)
        self.assertFalse(record.events.filter(is_comment=True).exists())
        self.assertRedirects(
            self.client.post(url, {"comment": "Получил, <проверяю>"}), detail + "#document-comments"
        )
        record.refresh_from_db()
        self.assertEqual(record.state, WorkflowRecord.State.REVIEW)
        self.assertEqual(record.events.get(is_comment=True).actor, recipient)
        self.client.force_login(author)
        page = self.client.get(detail)
        self.assertContains(page, "Получил, &lt;проверяю&gt;")
        self.assertContains(page, 'data-format="word"')
        self.assertContains(page, "/static/documents/file-icons/doc.png", count=3)

    def test_edit_mode_delivers_without_approval_transitions(self):
        sender, recipient = (self.user(name) for name in ("sender", "recipient"))
        document = Document.objects.create(owner=sender, title="Черновик")
        version = Version.objects.create(document=document, number=1, name="draft.txt", size=4)
        create = reverse("workflow_create", args=[version.pk])
        self.client.force_login(sender)
        self.assertContains(self.client.get(create), "Редактирование и комментарии")
        detail = self.client.post(
            create,
            {
                "title": "Черновик",
                "kind": "ЗАЯ",
                "purpose": "Правки",
                "mode": "edit",
                "recipients": recipient.pk,
            },
        ).url
        record = WorkflowRecord.objects.get()
        self.assertEqual(self.client.post(detail, {"state": "review"}).status_code, 400)
        self.assertContains(
            self.client.get(detail),
            '<button class="button primary" name="state" value="shared">Отправить получателям</button>',
            html=True,
        )
        self.assertRedirects(self.client.post(detail, {"state": "shared"}), detail)
        self.client.force_login(recipient)
        self.assertContains(self.client.get(reverse("workflow_records")), detail)
        self.assertContains(self.client.get(detail), "На редактировании")
        self.assertNotContains(self.client.get(detail), "Изменить статус")
        self.assertEqual(self.client.post(detail, {"state": "signed"}).status_code, 400)
        edit = reverse("workflow_edit", args=[record.pk])
        self.assertRedirects(self.post_edit(edit, {"title": "Уточнено", "purpose": "Правки"}), detail)
        comment = reverse("workflow_comment", args=[record.pk])
        self.assertRedirects(self.client.post(comment, {"comment": "Готово"}), detail + "#document-comments")
        record.refresh_from_db()
        self.assertEqual(record.state, WorkflowRecord.State.SHARED)
        self.assertEqual(record.title, "Уточнено")

    def test_comment_attachment_replaces_current_file_and_preserves_previous_version(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile

        from .storage import stored_file

        author, recipient, outsider = (self.user(name) for name in ("author", "recipient", "outsider"))
        document = Document.objects.create(owner=author, title="Заявка")
        version = Version.objects.create(document=document, number=1, name="initial.txt", size=5)
        create = reverse("workflow_create", args=[version.pk])
        self.client.force_login(author)
        detail = self.client.post(
            create,
            {
                "title": "Заявка",
                "kind": "ЗАЯ",
                "purpose": "Правки",
                "mode": "edit",
                "recipients": recipient.pk,
                "action": "send",
            },
        ).url
        record = WorkflowRecord.objects.get()
        comment_url = reverse("workflow_comment", args=[record.pk])
        with (
            TemporaryDirectory() as directory,
            self.settings(
                DATA_DIR=Path(directory),
                FILE_UPLOAD_TEMP_DIR=Path(directory),
            ),
        ):
            initial = stored_file(version.key)
            initial.parent.mkdir()
            initial.write_bytes(b"first")
            self.assertEqual(
                self.client.post(
                    comment_url,
                    {
                        "file": SimpleUploadedFile("unauthorized.txt", b"blocked"),
                    },
                ).status_code,
                403,
            )
            self.client.force_login(recipient)
            self.assertRedirects(
                self.client.post(
                    comment_url,
                    {
                        "comment": "Исправил текст",
                        "file": SimpleUploadedFile("revision.txt", b"second"),
                    },
                ),
                detail + "#document-comments",
            )
            record.refresh_from_db()
            change = record.events.get(is_comment=True)
            self.assertEqual(record.version.number, 2)
            self.assertEqual(change.version_id, record.version_id)
            self.assertEqual(initial.read_bytes(), b"first")
            download_url = reverse("workflow_event_file", args=[record.pk, change.pk])
            preview_url = reverse("workflow_event_preview", args=[record.pk, change.pk])
            self.assertContains(self.client.get(detail), 'data-file-preview="' + preview_url + '"', count=2)
            for url in (download_url, preview_url, reverse("workflow_file", args=[record.pk])):
                with closing(self.client.get(url)) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(b"".join(response.streaming_content), b"second")
            self.client.force_login(outsider)
            self.assertEqual(self.client.get(download_url).status_code, 404)
            self.assertEqual(self.client.get(preview_url).status_code, 404)

    def test_admin_document_types_control_new_cards_without_hiding_existing_ones(self):
        admin = get_user_model().objects.create_superuser("admin", password="test-password")
        employee = self.user("employee")
        document = Document.objects.create(owner=employee, title="Отчёт")
        version = Version.objects.create(document=document, number=1, name="report.txt", size=4)
        add_type = reverse("account_admin:documents_documenttype_add")
        self.client.force_login(employee)
        self.assertEqual(self.client.get(add_type, follow=True).status_code, 403)
        self.client.force_login(admin)
        response = self.client.post(
            add_type, {"code": "ОТЧ", "name": "Отчёт", "is_active": "on", "_save": "1"}
        )
        self.assertEqual(response.status_code, 302)
        self.client.force_login(employee)
        create = reverse("workflow_create", args=[version.pk])
        data = {"mode": "approval", "title": "Отчёт", "kind": "ОТЧ", "purpose": "Проверка"}
        self.assertContains(self.client.get(create), 'value="ОТЧ"')
        detail = self.client.post(create, data).url
        self.client.force_login(admin)
        change_type = reverse("account_admin:documents_documenttype_change", args=["ОТЧ"])
        self.assertEqual(
            self.client.post(change_type, {"name": "Отчёт отдела", "_save": "1"}).status_code, 302
        )
        self.assertFalse(DocumentType.objects.get(pk="ОТЧ").is_active)
        self.client.force_login(employee)
        self.assertContains(self.client.get(detail), "Отчёт отдела")
        self.assertNotContains(self.client.get(create), 'value="ОТЧ"')
        self.assertEqual(self.client.post(create, data).status_code, 200)
        self.assertEqual(WorkflowRecord.objects.count(), 1)

    def test_document_is_delivered_to_multiple_selected_users(self):
        author, first, second, outsider = (
            self.user(name) for name in ("author", "first", "second", "outsider")
        )
        first.first_name, first.last_name = "Алексей", "Иванов"
        first.save(update_fields=["first_name", "last_name"])
        UserProfile.objects.create(user=first, patronymic="Сергеевич")
        second.first_name, second.last_name = "Мария", "Петрова"
        second.save(update_fields=["first_name", "last_name"])
        UserProfile.objects.create(user=second, patronymic="Олеговна")
        document = Document.objects.create(owner=author, title="Заявка")
        version = Version.objects.create(document=document, number=1, name="plan.txt", size=4)
        self.client.force_login(author)
        create = reverse("workflow_create", args=[version.pk])
        page = self.client.get(create)
        self.assertContains(page, "Поиск по ФИО")
        self.assertContains(page, "Иванов Алексей Сергеевич")
        self.assertNotContains(page, "(first)")
        detail = self.client.post(
            create,
            {
                "title": "Заявка",
                "kind": "ЗАЯ",
                "purpose": "Проверка",
                "mode": "edit",
                "recipients": [first.pk, second.pk],
                "action": "send",
            },
        ).url
        record = WorkflowRecord.objects.get()
        self.assertSetEqual(set(record.recipients.values_list("pk", flat=True)), {first.pk, second.pk})
        edit = reverse("workflow_edit", args=[record.pk])
        for recipient in (first, second):
            self.client.force_login(recipient)
            self.assertContains(self.client.get(reverse("workflow_records")), detail)
            self.assertContains(self.client.get(detail), "Иванов Алексей Сергеевич, Петрова Мария Олеговна")
            self.assertRedirects(
                self.post_edit(
                    edit,
                    {
                        "title": recipient.username,
                        "purpose": "Проверка",
                    },
                ),
                detail,
            )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_admin_deletes_branch_without_removing_shared_file_or_reusing_number(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from .storage import stored_file

        admin = get_user_model().objects.create_superuser("admin", password="test-password")
        employee = self.user("employee")
        document = Document.objects.create(owner=employee, title="Договор")
        shared = Version.objects.create(document=document, number=1, name="shared.txt", size=4)
        private = Version.objects.create(document=document, number=2, name="private.txt", size=4)
        first = WorkflowRecord.objects.create(
            version=private,
            title="Договор",
            kind_id="ДОГ",
            purpose="Проверка",
            state="signed",
        )
        other = WorkflowRecord.objects.create(
            version=shared,
            title="Вторая ветка",
            kind_id="ДОГ",
            purpose="Проверка",
            state="signed",
        )
        event = first.events.create(actor=employee, state="created", version=shared, comment="История")
        delete = reverse("workflow_delete", args=[first.pk])
        self.client.force_login(employee)
        self.assertNotContains(self.client.get(reverse("workflow_detail", args=[first.pk])), delete)
        self.assertEqual(self.client.post(delete, {"confirm": "delete"}).status_code, 403)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(delete).status_code, 405)
        self.assertEqual(self.client.post(delete).status_code, 400)
        first_url = reverse("workflow_detail", args=[first.pk])
        self.assertRedirects(self.client.post(first_url, {"state": "registered"}), first_url)

        with TemporaryDirectory() as directory, self.settings(DATA_DIR=Path(directory)):
            removed_path, shared_path = stored_file(private.key), stored_file(shared.key)
            removed_path.parent.mkdir()
            removed_path.write_bytes(b"private")
            shared_path.write_bytes(b"shared")
            with self.captureOnCommitCallbacks(execute=True):
                self.assertRedirects(
                    self.client.post(delete, {"confirm": "delete"}), reverse("workflow_records")
                )
            self.assertFalse(removed_path.exists())
            self.assertTrue(shared_path.exists())
        self.assertFalse(WorkflowRecord.objects.filter(pk=first.pk).exists())
        self.assertFalse(first.events.filter(pk=event.pk).exists())
        self.assertTrue(Version.objects.filter(pk=shared.pk).exists())
        self.assertFalse(Version.objects.filter(pk=private.pk).exists())
        other_url = reverse("workflow_detail", args=[other.pk])
        self.assertRedirects(self.client.post(other_url, {"state": "registered"}), other_url)
        other.refresh_from_db()
        self.assertEqual(other.registration_number, f"ДОГ-{timezone.localdate().year}-000002")

    def test_admin_deletes_legacy_history_without_explicit_event_versions(self):
        admin = get_user_model().objects.create_superuser("admin", password="test-password")
        document = Document.objects.create(owner=admin, title="Старая карточка")
        Version.objects.create(document=document, number=1, name="first.txt", size=3)
        latest = Version.objects.create(document=document, number=2, name="second.txt", size=3)
        record = WorkflowRecord.objects.create(
            version=latest,
            title=document.title,
            kind_id="ЗАЯ",
            purpose="Проверка",
        )
        record.events.create(actor=admin, state="created")
        self.client.force_login(admin)
        self.assertRedirects(
            self.client.post(
                reverse("workflow_delete", args=[record.pk]),
                {"confirm": "delete"},
            ),
            reverse("workflow_records"),
        )
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertFalse(Version.objects.filter(document_id=document.pk).exists())

    def test_stale_form_cannot_overwrite_or_sign_an_updated_card(self):
        author, first, second = (self.user(name) for name in ("author", "first", "second"))
        document = Document.objects.create(owner=author, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.txt", size=4)
        self.client.force_login(author)
        detail = self.client.post(
            reverse("workflow_create", args=[version.pk]),
            {
                "title": "Договор",
                "mode": "approval",
                "kind": "ДОГ",
                "purpose": "Проверка",
                "recipients": [first.pk, second.pk],
                "action": "send",
            },
        ).url
        record = WorkflowRecord.objects.get()
        old = record.updated_at.isoformat()
        edit = reverse("workflow_edit", args=[record.pk])
        self.client.force_login(first)
        self.assertContains(
            self.client.get(detail), f'<input type="hidden" name="revision" value="{old}">', count=2, html=True
        )
        self.assertRedirects(self.post_edit(edit, {"title": "Новая редакция", "purpose": "Проверка"}), detail)
        self.client.force_login(second)
        self.assertEqual(
            self.client.post(
                edit,
                {
                    "title": "Договор",
                    "purpose": "Проверка",
                    "revision": old,
                },
            ).status_code,
            409,
        )
        self.assertEqual(self.client.post(detail, {"state": "signed", "revision": old}).status_code, 409)
        record.refresh_from_db()
        self.assertEqual(record.title, "Новая редакция")
        self.assertEqual(record.state, WorkflowRecord.State.REVIEW)

    def test_simple_task_delivery_and_forward_only_statuses(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile

        sender, recipient, stranger = (self.user(name) for name in ("sender", "recipient", "stranger"))
        self.client.force_login(sender)
        send = reverse("task_list")
        sent = reverse("task_sent")
        for url in (send, sent):
            page = self.client.get(url)
            self.assertContains(page, "<h1>Задачи</h1>")
            self.assertContains(page, 'aria-controls="task-dialog"')
            self.assertContains(page, "Что нужно сделать")
        self.assertEqual(self.client.get("/tasks/send/").status_code, 404)
        invalid = self.client.post(sent, {"recipient": recipient.pk, "purpose": "Проверить реквизиты"})
        self.assertContains(invalid, "data-invalid")
        self.assertContains(invalid, "Выбери файл для отправки.")
        self.assertContains(invalid, "Проверить реквизиты")
        self.assertFalse(WorkflowRecord.objects.exists())
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "files").mkdir()
            with self.settings(DATA_DIR=data, FILE_UPLOAD_TEMP_DIR=data):
                response = self.client.post(
                    send,
                    {
                        "recipient": recipient.pk,
                        "purpose": "Проверить реквизиты",
                        "file": SimpleUploadedFile("invoice.txt", b"invoice"),
                    },
                )
                record = WorkflowRecord.objects.get()
                detail = reverse("workflow_detail", args=[record.pk])
                self.assertRedirects(response, detail)
                self.assertContains(self.client.get(reverse("task_sent")), "Отправлено")
                self.assertEqual(self.client.post(detail, {"state": "received"}).status_code, 403)
                notifications = reverse("task_notifications")
                self.assertEqual(self.client.get(notifications).json(), {"tasks": []})
                self.client.force_login(stranger)
                self.assertEqual(self.client.get(detail).status_code, 404)
                self.assertEqual(self.client.get(notifications).json(), {"tasks": []})
                self.client.force_login(recipient)
                self.assertContains(self.client.get(reverse("task_list")), "Проверить реквизиты")
                self.assertEqual(self.client.get(notifications).json()["tasks"][0]["url"], detail)
                with closing(self.client.get(reverse("workflow_file", args=[record.pk]))) as download:
                    self.assertEqual(b"".join(download.streaming_content), b"invoice")
                self.assertEqual(self.client.post(detail, {"state": "done"}).status_code, 400)
                for state in ("received", "in_progress", "done"):
                    self.assertRedirects(self.client.post(detail, {"state": state}), detail)
                self.assertEqual(self.client.post(detail, {"state": "received"}).status_code, 400)
                self.assertEqual(self.client.get(notifications).json(), {"tasks": []})
                self.assertRedirects(
                    self.client.post(
                        reverse("workflow_comment", args=[record.pk]),
                        {
                            "comment": "Какой срок?",
                        },
                    ),
                    detail + "#document-comments",
                )
                self.client.force_login(sender)
                self.assertContains(self.client.get(reverse("task_sent")), "Готово")
                self.assertContains(self.client.get(detail), "Какой срок?")
                comments = reverse("workflow_comment", args=[record.pk])
                self.client.force_login(recipient)
                self.assertEqual(self.client.post(comments, {"action": "return"}).status_code, 403)
                self.client.force_login(sender)
                self.assertContains(self.client.get(detail), "Вернуть на переделку")
                self.assertRedirects(
                    self.client.post(
                        comments,
                        {
                            "action": "return",
                            "comment": "Используй обновлённые реквизиты",
                            "file": SimpleUploadedFile("revision.txt", b"revision"),
                        },
                    ),
                    detail + "#document-comments",
                )
                record.refresh_from_db()
                self.assertEqual(record.state, WorkflowRecord.State.REWORK)
                self.assertEqual(record.version.number, 2)
                event = record.events.get(
                    previous_state=WorkflowRecord.State.DONE, state=WorkflowRecord.State.REWORK
                )
                self.assertEqual(event.version, record.version)
                self.client.force_login(recipient)
                self.assertEqual(self.client.get(notifications).json()["tasks"][0]["state"], "rework")
                attachment = reverse("workflow_event_file", args=[record.pk, event.pk])
                with closing(self.client.get(attachment)) as response:
                    self.assertEqual(b"".join(response.streaming_content), b"revision")
                self.assertEqual(self.client.post(detail, {"state": "done"}).status_code, 400)
                self.assertRedirects(self.client.post(detail, {"state": "in_progress"}), detail)
                self.assertRedirects(self.client.post(detail, {"state": "done"}), detail)
