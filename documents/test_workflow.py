from contextlib import closing

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import WorkflowRecord


class WorkflowTests(TestCase):
    def user(self, username):
        return get_user_model().objects.create_user(username)

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
            self.assertContains(page, "Сообщение")
        self.assertEqual(self.client.get("/tasks/send/").status_code, 404)
        invalid = self.client.post(sent, {"recipient": recipient.pk, "purpose": "Проверить реквизиты"})
        self.assertContains(invalid, "data-invalid")
        self.assertContains(invalid, "Укажи название задачи или выбери файл для отправки.")
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
                self.assertEqual(self.client.get(detail, {"after": 0}).status_code, 404)
                self.assertEqual(self.client.get(notifications).json(), {"tasks": []})
                self.client.force_login(recipient)
                self.assertContains(self.client.get(reverse("task_list")), "Проверить реквизиты")
                revision = record.events.latest("pk").pk
                self.assertEqual(self.client.get(detail, {"after": revision}).status_code, 204)
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
                changed = self.client.get(detail, {"after": revision})
                self.assertContains(changed, "revision.txt")
                self.assertContains(changed, "Какой срок?")
                self.assertContains(changed, f'data-revision="{record.events.latest("pk").pk}"')
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

    def test_task_without_file_and_private_notifications(self):
        sender, recipient, stranger = (self.user(name) for name in ("sender", "recipient", "stranger"))
        self.client.force_login(sender)
        response = self.client.post(reverse("task_list"), {
            "recipient": recipient.pk, "title": "Позвонить клиенту", "purpose": "Уточнить сроки",
        })
        record = WorkflowRecord.objects.get()
        detail = reverse("workflow_detail", args=[record.pk])
        self.assertRedirects(response, detail)
        self.assertIsNone(record.version)
        self.assertEqual(record.sender, sender)
        self.assertEqual(self.client.get(reverse("workflow_file", args=[record.pk])).status_code, 404)

        notifications = reverse("notifications")
        self.assertEqual(self.client.get(notifications, {"fragment": "toast"}).json(), {"notifications": []})
        self.client.force_login(stranger)
        self.assertNotContains(self.client.get(notifications), record.title)
        self.client.force_login(recipient)
        self.assertContains(self.client.get(notifications), record.title)
        self.assertContains(self.client.get(notifications), "Новая задача")
        self.assertRedirects(self.client.post(detail, {"state": "received"}), detail)
        self.assertRedirects(self.client.post(detail, {"state": "in_progress"}), detail)
        self.assertRedirects(self.client.post(detail, {"state": "done"}), detail)
        self.client.force_login(sender)
        feed = self.client.get(notifications, {"fragment": "toast"}).json()["notifications"]
        self.assertEqual([item["label"] for item in feed], ["Готово", "В работе", "Получено"])

    def test_toast_cursor_preserves_overflow_across_requests(self):
        sender, recipient = (self.user(name) for name in ("sender", "recipient"))
        self.client.force_login(sender)
        self.client.post(reverse("task_list"), {
            "title": "Звонок", "recipient": recipient.pk, "purpose": "Позвонить",
        })
        record = WorkflowRecord.objects.get()
        previous = record.events.latest("pk").pk
        ids = [
            record.events.create(actor=recipient, state=record.state, is_comment=True, comment=str(i)).pk
            for i in range(23)
        ]
        def poll(after):
            data = self.client.get(reverse("notifications"), {"fragment": "toast", "after": after}).json()
            return [event["id"] for event in data["notifications"]]
        self.assertEqual(poll(previous), ids[:20])
        self.assertEqual(poll(ids[0]), ids[1:21])
        self.assertEqual(poll(ids[19]), ids[20:])
        self.assertEqual(poll(ids[-1]), [])

    def test_task_message_with_multiple_attachments(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile

        sender, recipient, stranger = (self.user(name) for name in ("sender", "recipient", "stranger"))
        self.client.force_login(sender)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "files").mkdir()
            with self.settings(DATA_DIR=root, FILE_UPLOAD_TEMP_DIR=root):
                response = self.client.post(reverse("task_list"), {
                    "recipient": recipient.pk, "purpose": "Проверь оба файла",
                    "file": [
                        SimpleUploadedFile("first.txt", b"first"),
                        SimpleUploadedFile("second.txt", b"second"),
                    ],
                })
                self.assertEqual(response.status_code, 302)
                record = WorkflowRecord.objects.get()
                extra = record.events.get(version__name="second.txt")
                attachment = reverse("workflow_event_file", args=[record.pk, extra.pk])
                self.client.force_login(stranger)
                self.assertEqual(self.client.get(attachment).status_code, 404)
                self.client.force_login(recipient)
                detail = self.client.get(response.url)
                self.assertContains(detail, "first.txt")
                self.assertContains(detail, "second.txt")
                with closing(self.client.get(attachment)) as downloaded:
                    self.assertEqual(b"".join(downloaded.streaming_content), b"second")
                feed = self.client.get(reverse("notifications"), {"fragment": "toast"}).json()
                self.assertEqual(len(feed["notifications"]), 1)

    def test_rich_message_formats_text_and_removes_unsafe_markup(self):
        from .rich_text import PREFIX

        sender, recipient = (self.user(name) for name in ("sender", "recipient"))
        self.client.force_login(sender)
        response = self.client.post(reverse("task_list"), {
            "recipient": recipient.pk, "title": "Форматирование",
            "purpose": PREFIX + '<b>Текст</b><a href="javascript:void(0)">Ссылка</a>',
        })
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.url)
        self.assertContains(page, "<b>Текст</b>", html=True)
        self.assertNotContains(page, "javascript:")
        comment = reverse("workflow_comment", args=[WorkflowRecord.objects.get().pk])
        posted = self.client.post(comment, {"comment": PREFIX + '<ul><li>Пункт</li></ul><img src=x>'})
        self.assertEqual(posted.status_code, 302)
        page = self.client.get(response.url)
        self.assertContains(page, "<ul><li>Пункт</li></ul>", html=True)
        self.assertNotContains(page, "src=x")

    def test_browser_multipart_line_endings_and_literal_html(self):
        from .rich_text import normalize
        from .templatetags.rich_text import message_summary, rich_message

        old = "docstation-rich:v1\r\n<b>Важно</b>"
        self.assertEqual(normalize(old), "docstation-rich:v1\n<b>Важно</b>")
        self.assertEqual(str(rich_message(old)), "<b>Важно</b>")
        self.assertEqual(message_summary(old), "Важно")
        self.assertEqual(str(rich_message("<b>пример</b>")), "&lt;b&gt;пример&lt;/b&gt;")
