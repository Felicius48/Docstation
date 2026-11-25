import json
from io import BytesIO
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

from .jobs import claim, process
from .models import Job, WorkflowRecord
from .storage import stored_file


class DocumentExtensionsTests(TestCase):
    @skipUnless(which("qpdf"), "Requires qpdf")
    def test_edit_pdf_through_queue_and_attach_to_task(self):
        sender, recipient = [get_user_model().objects.create_user(name) for name in ("sender", "recipient")]
        self.client.force_login(sender)

        def pdf(name, widths):
            writer = PdfWriter()
            for width in widths:
                writer.add_blank_page(width, 100)
            output = BytesIO()
            writer.write(output)
            return SimpleUploadedFile(name, output.getvalue())

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("files", "uploads", "work"):
                (root / name).mkdir()
            with self.settings(DATA_DIR=root, FILE_UPLOAD_TEMP_DIR=root / "uploads"):
                task = self.client.post(reverse("task_list"), {
                    "recipient": recipient.pk, "due_date": timezone.localdate().isoformat(),
                    "purpose": "Проверить", "file": pdf("old.pdf", [50]),
                })
                self.assertEqual(task.status_code, 302)
                self.assertContains(self.client.get(reverse("task_sent"), {"filter": "today"}), "old.pdf")

                endpoints = self.client.post(reverse("new_batch")).json()
                versions = [
                    self.client.post(endpoints["upload"], {"file": pdf(name, widths)}).json()["id"]
                    for name, widths in [("a.pdf", [100, 200]), ("b.pdf", [300])]
                ]
                response = self.client.post(endpoints["submit"], json.dumps({
                    "operation": "edit_pdf", "versions": versions, "layout": [[1, 1, 90], [0, 2, 0]],
                }), content_type="application/json")
                self.assertEqual(response.status_code, 200, response.content)
                job = Job.objects.get()
                self.assertContains(self.client.get(response.json()["url"]), "Воркер недоступен.")
                self.client.post(reverse("cancel_job", args=[job.pk]))
                job.refresh_from_db()
                self.assertEqual(job.state, Job.State.CANCELLED)
                self.assertIsNone(claim())
                self.client.post(reverse("retry", args=[job.pk]))
                process(claim())
                job.refresh_from_db()
                self.assertEqual(job.state, Job.State.DONE, job.error)
                result = job.results.get()
                doc = PdfReader(stored_file(result.key))
                self.assertEqual([page.mediabox.width for page in doc.pages], [300, 200])
                self.assertEqual(doc.pages[0].rotation, 90)

                record = WorkflowRecord.objects.get()
                attach = reverse("attach_result", args=[result.pk])
                self.assertContains(self.client.get(attach), "old.pdf")
                self.assertRedirects(self.client.post(attach, {"task": record.pk}), task.url)
                self.assertTrue(stored_file(result.key).exists())
                record.refresh_from_db()
                self.assertEqual(record.version.number, 2)
                compare = reverse("compare_versions", args=[record.pk])
                self.client.force_login(recipient)
                self.assertContains(self.client.get(compare), "data-compare-pdf", count=2)
                self.assertEqual(self.client.get(attach).status_code, 404)

    def test_text_comparison_highlights_replaced_lines(self):
        sender, recipient = [get_user_model().objects.create_user(name) for name in ("sender", "recipient")]
        self.client.force_login(sender)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("files", "uploads"):
                (root / name).mkdir()
            with self.settings(DATA_DIR=root, FILE_UPLOAD_TEMP_DIR=root / "uploads"):
                response = self.client.post(reverse("task_list"), {
                    "recipient": recipient.pk, "purpose": "Проверить",
                    "file": SimpleUploadedFile("old.txt", b"deleted\nshared"),
                })
                self.assertEqual(response.status_code, 302)
                record = WorkflowRecord.objects.get()
                response = self.client.post(reverse("workflow_comment", args=[record.pk]), {
                    "comment": "Уточнение", "file": SimpleUploadedFile("new.txt", b"added\nshared"),
                })
                self.assertEqual(response.status_code, 302)
                comparison = self.client.get(reverse("compare_versions", args=[record.pk]))
                self.assertContains(comparison, "<del>deleted</del>", html=True)
                self.assertContains(comparison, "<ins>added</ins>", html=True)

                versions = record.version.document.versions.order_by("number")
                original, updated = versions
                stored_file(original.key).write_bytes("кот".encode("cp1251"))
                stored_file(updated.key).write_bytes("дом".encode("cp1251"))
                url = reverse("compare_versions", args=[record.pk])
                self.assertContains(self.client.get(url), "Не удалось определить кодировку")
                comparison = self.client.get(url, {"encoding": "cp1251"})
                self.assertContains(comparison, "<del>кот</del>", html=True)
                self.assertContains(comparison, "<ins>дом</ins>", html=True)
