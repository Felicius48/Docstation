from contextlib import closing

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import WorkflowRecord


class WorkflowTests(TestCase):
    def user(self, username):
        return get_user_model().objects.create_user(username)

    def test_task_folders_show_only_my_tasks_in_creation_order(self):
        user, colleague, other = (self.user(name) for name in ("me", "colleague", "other"))

        def send(sender, recipient, title):
            task = WorkflowRecord.objects.create(sender=sender, title=title, state=WorkflowRecord.State.SENT)
            task.recipients.add(recipient)
            task.events.create(actor=sender, state=WorkflowRecord.State.SENT, previous_state=WorkflowRecord.State.CREATED)
            return task

        older = send(colleague, user, "Входящая задача")
        newer = send(user, colleague, "Отправленная задача")
        send(colleague, other, "Чужая задача")
        older.save(update_fields=["updated_at"])
        self.client.force_login(user)
        for route, expected in (
            ("task_list", [newer, older]),
            ("task_incoming", [older]),
            ("task_sent", [newer]),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route), headers={"X-Fragment": "tasks"})
                self.assertEqual(list(response.context["page"]), expected)

    def test_switching_between_older_tasks_keeps_the_same_folder_page(self):
        from .workflow_views import WORKFLOW_PAGE_SIZE

        sender = self.user("sender")
        self.client.force_login(sender)
        tasks = WorkflowRecord.objects.bulk_create([
            WorkflowRecord(sender=sender, title=f"Task {i}")
            for i in range(WORKFLOW_PAGE_SIZE + 2)
        ])
        tasks[0].save(update_fields=["updated_at"])
        for selected in tasks[:2]:
            page = self.client.get(reverse("workflow_detail", args=[selected.pk]))
            self.assertEqual(list(page.context["nearby_tasks"]), [tasks[1], tasks[0]])
            sidebar = page.content.decode().split('aria-label="Задачи в разделе"', 1)[1].split("</nav>", 1)[0]
            self.assertLess(sidebar.index("Task 1"), sidebar.index("Task 0"))
            self.assertIn(f'href="{reverse("workflow_detail", args=[selected.pk])}" aria-current="page"', sidebar)

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

    def test_screenshots_without_text_are_visible_and_private(self):
        from io import BytesIO
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from .storage import stored_file

        image = BytesIO()
        Image.new("RGB", (20, 12)).save(image, "PNG")

        def shot(name):
            return SimpleUploadedFile(name, image.getvalue(), "image/png")

        sender, recipient, stranger = (self.user(name) for name in ("sender", "recipient", "stranger"))
        self.client.force_login(sender)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "files").mkdir()
            with self.settings(DATA_DIR=root, FILE_UPLOAD_TEMP_DIR=root):
                create = reverse("task_list")
                fields = {"recipient": recipient.pk}
                self.assertContains(self.client.post(create, {**fields, "title": "Пусто"}), "прикрепите скриншот")
                for invalid in ('{}', '[]', '[[["rect",-1,0,500,800]]]', '[[["unknown",0,0,1,1]]]'):
                    self.assertContains(self.client.post(create, {
                        **fields, "file": shot("screen.png"), "file_marks": invalid,
                    }), "Не удалось прочитать отметки")
                self.assertFalse(WorkflowRecord.objects.exists())
                shape = '[[["arrow",5,10,950,890]]]'
                response = self.client.post(create, {**fields, "file": shot("screen.png"), "file_marks": shape})
                self.assertEqual(response.status_code, 302)
                record = WorkflowRecord.objects.get()
                self.assertEqual((record.title, record.purpose), ("screen.png", ""))
                self.assertEqual(record.version.marks, [["arrow", 5, 10, 950, 890]])
                with closing(self.client.get(reverse("workflow_file", args=[record.pk]))) as download:
                    self.assertEqual(b"".join(download.streaming_content), image.getvalue())
                original = record.version_id
                self.client.force_login(recipient)
                self.assertContains(self.client.get(response.url), 'alt="screen.png"')
                comments = reverse("workflow_comment", args=[record.pk])
                record.state = WorkflowRecord.State.DONE
                record.save()
                self.assertContains(self.client.post(comments, {
                    "screenshot": SimpleUploadedFile("invalid.png", b"invalid"),
                }), "корректный скриншот")
                self.assertEqual(self.client.post(comments, {
                    "screenshot": [shot("first.png"), shot("second.png")],
                    "screenshot_marks": '[[],[["rect",10,20,460,650]]]',
                }).status_code, 302)
                event = record.events.get(is_comment=True)
                images = list(event.screenshots.all())
                self.assertEqual(event.screenshots.get(name="second.png").marks, [["rect", 10, 20, 460, 650]])
                self.assertEqual(len(images), 2)
                self.assertEqual(event.comment, "")
                record.refresh_from_db()
                self.assertEqual(record.version_id, original)
                self.assertEqual(record.state, WorkflowRecord.State.DONE)
                url = reverse("comment_screenshot", args=[record.pk, event.pk, images[0].pk])
                self.assertContains(self.client.get(response.url), f'src="{url}"')
                with closing(self.client.get(url)) as result:
                    self.assertEqual(result["Content-Type"], "image/png")
                    self.assertEqual(b"".join(result.streaming_content), image.getvalue())
                self.client.force_login(stranger)
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.get(url + "?download").status_code, 404)
                sender.is_superuser = True
                sender.save()
                self.client.force_login(sender)
                with self.captureOnCommitCallbacks(execute=True):
                    self.client.post(reverse("workflow_delete", args=[record.pk]), {"confirm": "delete"})
                self.assertFalse(any(stored_file(file.key).exists() for file in images))

    def test_reply_targets_only_comments_in_the_same_task(self):
        sender, recipient = self.user("writer"), self.user("reader")
        self.client.force_login(sender)
        def task(title):
            response = self.client.post(reverse("task_list"), {
                "recipient": recipient.pk, "title": title, "purpose": "Проверить",
            })
            self.assertEqual(response.status_code, 302)
            return WorkflowRecord.objects.get(title=title)

        record = task("Первый")
        other = task("Второй")
        endpoint = reverse("workflow_comment", args=[record.pk])
        parent = record.events.create(actor=sender, state=record.state, is_comment=True, comment="Вопрос о сумме")
        foreign = other.events.create(actor=sender, state=other.state, is_comment=True, comment="Другой вопрос")
        self.client.force_login(recipient)
        for invalid in (foreign.pk, record.events.get(state="created").pk):
            self.assertContains(self.client.post(endpoint, {"reply_to": invalid, "comment": "Ответ"}), "Исходный комментарий недоступен")
        self.assertEqual(self.client.post(endpoint, {"reply_to": parent.pk, "comment": "Проверено"}).status_code, 302)
        reply = record.events.get(comment="Проверено")
        self.assertEqual(reply.reply_to_id, parent.pk)
        url = reverse("workflow_detail", args=[record.pk])
        for params in ({}, {"after": parent.pk}):
            detail = self.client.get(url, params)
            html = detail.content.decode()
            self.assertLess(html.index(f'id="comment-{reply.pk}"'), html.index(f'id="comment-{parent.pk}"'))
        self.assertContains(detail, f'href="#comment-{parent.pk}"')
        self.assertNotContains(detail, "Другой вопрос")
