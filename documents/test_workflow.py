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
