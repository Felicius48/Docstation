from contextlib import closing
from io import BytesIO, StringIO
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from unittest import skipUnless
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .models import Batch, Document, Job, Version
from .storage import stored_file


class RegistrationTests(TestCase):
    credentials = {"username": "new-owner", "password1": "Local!Pine8Road", "password2": "Local!Pine8Road"}

    def test_registration_creates_an_account_and_starts_a_session(self):
        url = reverse("login")
        page = self.client.get(url)
        self.assertContains(page, "Создать аккаунт")
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        response = self.client.post(url, {
            **self.credentials, "action": "register", "next": "https://example.com/",
        })
        self.assertRedirects(response, reverse("workspace"))
        user = get_user_model().objects.get(username=self.credentials["username"])
        self.assertTrue(user.check_password(self.credentials["password1"]))
        self.assertFalse(user.is_staff)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.client.post(reverse("logout"))
        self.assertRedirects(self.client.post(url, {
            "username": user.username, "password": self.credentials["password1"], "next": reverse("archive"),
        }), reverse("archive"))

    def test_registration_requires_csrf_and_rejects_invalid_credentials(self):
        url = reverse("login")
        registration = {**self.credentials, "action": "register"}
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, registration).status_code, 403)
        mismatch = self.client.post(url, {**registration, "password2": "different"})
        self.assertContains(mismatch, "password2_error")
        self.assertFalse(get_user_model().objects.exists())
        weak = self.client.post(url, {**registration, "password1": "123", "password2": "123"})
        self.assertContains(weak, "password2_error")
        self.assertFalse(get_user_model().objects.exists())
        get_user_model().objects.create_user(self.credentials["username"])
        duplicate = self.client.post(url, registration)
        self.assertContains(duplicate, "username_error")
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_owner_closes_registration_and_invitation_is_single_use(self):
        login_url = reverse("login")
        settings_url = reverse("registration_settings")
        self.client.post(login_url, {**self.credentials, "action": "register"})
        self.assertRedirects(self.client.post(settings_url, {"action": "close"}), settings_url)
        response = self.client.post(settings_url, {"action": "invite"})
        invitation_url = response.context["invitation_url"].removeprefix("http://testserver")
        guest = Client()
        registration = {**self.credentials, "username": "invited", "action": "register"}
        self.assertNotContains(guest.get(login_url), "Создать аккаунт")
        self.assertEqual(guest.post(login_url, registration).status_code, 403)
        page = guest.get(invitation_url)
        self.assertContains(page, "Создать аккаунт")
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        self.assertRedirects(guest.post(invitation_url, registration), reverse("workspace"))
        self.assertEqual(guest.post(settings_url, {"action": "open"}).status_code, 404)
        self.assertEqual(Client().post(invitation_url, {**registration, "username": "another"}).status_code, 403)
        self.assertRedirects(self.client.post(settings_url, {"action": "open"}), settings_url)
        self.assertContains(Client().get(login_url), "Создать аккаунт")


class ArchiveWorkflowTests(TransactionTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ("uploads", "files", "work"):
            (root / name).mkdir()
        self.upload_dir = root / "uploads"
        self.enterContext(override_settings(DATA_DIR=root, FILE_UPLOAD_TEMP_DIR=self.upload_dir))
        self.owner = get_user_model().objects.create_user("owner")
        self.client.force_login(self.owner)

    def new_batch(self):
        response = self.client.post(reverse("new_batch"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def upload(self, batch, name, contents):
        return self.client.post(batch["upload"], {"file": SimpleUploadedFile(name, contents)})

    def submit(self, batch, operation, versions, **options):
        return self.client.post(
            batch["submit"], {"operation": operation, "versions": versions, **options},
            content_type="application/json",
        )

    def download(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        with closing(response):
            return b"".join(response.streaming_content)

    def test_versions_stay_private_and_deleting_one_removes_its_results(self):
        batch = self.new_batch()
        first = self.upload(batch, "notes.txt", "Первая версия".encode()).json()["id"]
        document = Document.objects.get()
        response = self.client.post(reverse("add_version", args=[document.pk]), {
            "file": SimpleUploadedFile("notes.txt", "Вторая версия".encode()),
        })
        self.assertEqual(response.status_code, 302)
        second = document.versions.first()
        self.assertEqual(second.number, 2)
        self.assertEqual(self.download(reverse("download_version", args=[first])), "Первая версия".encode())

        submission = self.submit(batch, "markdown", [first])
        self.assertEqual(submission.status_code, 200)
        self.assertEqual(self.submit(batch, "markdown", [first]).json(), submission.json())
        self.assertEqual(Job.objects.count(), 1)
        call_command("worker", "--once", stdout=StringIO())
        job = Job.objects.get()
        result = job.results.get()
        self.assertContains(self.client.get(reverse("archive")), "notes.txt")
        detail = self.client.get(reverse("document_detail", args=[document.pk]))
        self.assertContains(detail, reverse("download_result", args=[result.pk]))
        with ZipFile(BytesIO(self.download(reverse("download_result", args=[result.pk])))) as markdown:
            self.assertEqual(markdown.read("document.md").decode(), "Первая версия")

        other = get_user_model().objects.create_user("other")
        self.client.force_login(other)
        for url in (
            reverse("download_version", args=[first]),
            reverse("download_result", args=[result.pk]),
            submission.json()["url"],
        ):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.submit(self.new_batch(), "markdown", [first]).status_code, 404)
        self.client.force_login(self.owner)

        first_key = Version.objects.get(pk=first).key
        self.client.post(reverse("delete_version", args=[document.pk, first]))
        self.assertFalse(stored_file(first_key).exists())
        self.assertFalse(stored_file(result.key).exists())
        self.assertEqual(self.download(reverse("download_version", args=[second.pk])), "Вторая версия".encode())

    def test_worker_recovers_pending_jobs_and_exports_pdf(self):
        image = BytesIO()
        Image.new("RGB", (180, 90), "blue").save(image, format="PNG")
        batch = self.new_batch()
        version = self.upload(batch, "picture.png", image.getvalue()).json()["id"]
        submission = self.submit(batch, "convert", [version], target="pdf")
        self.assertEqual(submission.status_code, 200)
        batch_url = submission.json()["url"]
        self.assertNotContains(self.client.get(batch_url), 'role="progressbar"')
        Job.objects.update(state=Job.State.RUNNING, attempts=1)
        self.assertContains(self.client.get(batch_url, {"fragment": "jobs"}), 'role="progressbar"')
        orphan = stored_file(uuid4())
        orphan.write_bytes(b'interrupted result')
        call_command("worker", "--once", stdout=StringIO())
        self.assertFalse(orphan.exists())
        job = Job.objects.get()
        self.assertEqual(job.state, Job.State.DONE, job.error)
        self.assertNotContains(self.client.get(batch_url, {"fragment": "jobs"}), 'role="progressbar"')
        result = job.results.get()
        pdf = self.download(reverse("download_result", args=[result.pk]))
        self.assertEqual(len(PdfReader(BytesIO(pdf)).pages), 1)
        with ZipFile(BytesIO(self.download(reverse("download_batch", args=[job.batch_id])))) as bundle:
            self.assertEqual(bundle.read("picture.pdf"), pdf)

        next_batch = self.new_batch()
        pdf_version = self.upload(next_batch, "picture.pdf", pdf).json()["id"]
        self.submit(next_batch, "convert", [pdf_version], target="png")
        call_command("worker", "--once", stdout=StringIO())
        latest = Job.objects.first()
        self.assertEqual(latest.state, Job.State.DONE, latest.error)
        download = self.download(reverse("download_result", args=[latest.results.get().pk]))
        with Image.open(BytesIO(download)) as rendered:
            self.assertEqual(rendered.format, "PNG")
            red, green, blue = rendered.getpixel((20, 20))
            self.assertGreater(blue, 240)
            self.assertLess(max(red, green), 15)

        markdown_batch = self.new_batch()
        self.assertEqual(self.submit(markdown_batch, "markdown", [pdf_version]).status_code, 200)
        call_command("worker", "--once", stdout=StringIO())
        markdown_job = Job.objects.get(operation="markdown")
        self.assertEqual(markdown_job.state, Job.State.DONE, markdown_job.error)
        archive = self.download(reverse("download_result", args=[markdown_job.results.get().pk]))
        with ZipFile(BytesIO(archive)) as markdown:
            image_name = "images/page-0001-001.jpg"
            self.assertIn(image_name, markdown.read("document.md").decode())
            with Image.open(BytesIO(markdown.read(image_name))) as embedded:
                self.assertEqual(embedded.format, "JPEG")
                self.assertEqual(embedded.size, (180, 90))

    @skipUnless(which("gs"), "Ghostscript is required for PDF compression")
    def test_compression_preview_matches_download_and_stays_out_of_archive(self):
        source = BytesIO()
        Image.effect_noise((800, 800), 80).convert("RGB").save(source, format="PDF", resolution=300)
        contents = source.getvalue()
        url = reverse("new_compression_preview")
        preview = self.client.post(url, {
            "strength": "0", "file": SimpleUploadedFile("scan.pdf", contents),
        })
        self.assertEqual(preview.status_code, 201)
        preview_url = preview.json()["url"]
        call_command("worker", "--once", stdout=StringIO())
        ready = self.client.get(preview_url)
        self.assertEqual(ready.json()["size"], len(contents))
        self.assertEqual(ready["Cache-Control"], "no-store")
        self.assertEqual(self.client.post(preview_url, {"strength": "0"}).json(), ready.json())
        queued = self.client.post(preview_url, {"strength": "100"})
        self.assertEqual(queued.json(), {
            "state": Job.State.QUEUED, "strength": 100, "original": len(contents), "size": None,
        })
        call_command("worker", "--once", stdout=StringIO())
        measured = self.client.get(preview_url).json()
        self.assertEqual(measured["state"], Job.State.DONE)
        self.assertLess(measured["size"], len(contents))
        self.assertFalse(Document.objects.exists())
        self.assertFalse(Job.objects.exists())

        self.client.force_login(get_user_model().objects.create_user("another"))
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        self.assertEqual(self.client.post(preview_url, {"strength": "50"}).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(preview_url).json(), measured)
        batch = self.new_batch()
        version = self.upload(batch, "scan.pdf", contents).json()["id"]
        self.assertEqual(self.submit(batch, "compress", [version], strength="100").status_code, 200)
        call_command("worker", "--once", stdout=StringIO())
        result = Job.objects.get().results.get()
        self.assertEqual(result.size, measured["size"])
        self.assertEqual(len(self.download(reverse("download_result", args=[result.pk]))), measured["size"])

    @skipUnless(which("qpdf"), "qpdf is required for PDF extraction")
    def test_extract_selected_pages_as_individual_or_combined_pdfs(self):
        source = BytesIO()
        with PdfWriter() as pdf:
            for width in (100, 200, 300):
                pdf.add_blank_page(width=width, height=100)
            pdf.write(source)

        for mode, expected_widths in (("combined", [[100, 300]]), ("separate", [[100], [300]])):
            with self.subTest(mode=mode):
                batch = self.new_batch()
                version = self.upload(batch, "source.pdf", source.getvalue()).json()["id"]
                submission = self.submit(batch, "extract", [version], mode=mode, selections={str(version): [1, 3]})
                self.assertEqual(submission.status_code, 200)
                call_command("worker", "--once", stdout=StringIO())
                job = Job.objects.get(inputs__version=version)
                self.assertEqual(job.state, Job.State.DONE, job.error)
                results = list(job.results.all())
                self.assertEqual(len(results), len(expected_widths))
                for result, widths in zip(results, expected_widths, strict=True):
                    data = self.download(reverse("download_result", args=[result.pk]))
                    self.assertEqual([page.mediabox.width for page in PdfReader(BytesIO(data)).pages], widths)
                if mode == "separate":
                    self.assertEqual([item.name for item in results], ["source-page-0001.pdf", "source-page-0003.pdf"])
                    bundle = self.download(reverse("download_batch", args=[job.batch_id]))
                    with ZipFile(BytesIO(bundle)) as archive:
                        self.assertEqual(archive.namelist(), [item.name for item in results])

    def test_submission_rejects_invalid_ids_and_preserves_grouped_order(self):
        batch = self.new_batch()
        image = BytesIO()
        Image.new("RGB", (20, 10)).save(image, format="PNG")
        first, second = [
            self.upload(batch, name, image.getvalue()).json()["id"]
            for name in ("first.png", "second.png")
        ]
        for ids in ([True], [first, first], [2**63]):
            with self.subTest(ids=ids):
                response = self.submit(batch, "images_pdf", ids)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "Выбери от 1 до 50 файлов.")
        response = self.submit(batch, "merge", [first])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Для объединения нужны хотя бы два PDF.")
        self.assertFalse(Job.objects.exists())

        self.assertEqual(self.submit(batch, "images_pdf", [second, first]).status_code, 200)
        job = Job.objects.get()
        self.assertEqual(list(job.inputs.order_by("position").values_list("version_id", flat=True)), [second, first])
        call_command("worker", "--once", stdout=StringIO())
        data = self.download(reverse("download_result", args=[job.results.get().pk]))
        self.assertEqual(len(PdfReader(BytesIO(data)).pages), 2)

    def test_preview_is_private_and_limited_to_supported_formats(self):
        batch = self.new_batch()
        image = BytesIO()
        Image.new("RGB", (10, 10)).save(image, format="PNG")
        version = self.upload(batch, "sample.PNG", image.getvalue()).json()["id"]
        unsupported = self.upload(batch, "notes.docx", b"sample").json()["id"]
        self.submit(batch, "convert", [version], target="pdf")
        call_command("worker", "--once", stdout=StringIO())
        result = Job.objects.get().results.get()
        urls = [reverse("preview_version", args=[version]), reverse("preview_result", args=[result.pk])]
        for url, mime in zip(urls, ("image/png", "application/pdf"), strict=True):
            with closing(self.client.get(url)) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], mime)
                self.assertTrue(response["Content-Disposition"].startswith("inline"))
                self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.client.get(reverse("preview_version", args=[unsupported])).status_code, 404)
        document = Version.objects.get(pk=version).document
        detail = self.client.get(reverse("document_detail", args=[document.pk]))
        for url in urls:
            self.assertContains(detail, url)
        self.assertContains(self.client.get(reverse("batch_detail", args=[result.job.batch_id])), urls[1])
        self.client.force_login(get_user_model().objects.create_user("preview-other"))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_archive_filters_match_the_same_version_and_completed_job(self):
        from django.utils import timezone

        batch = self.new_batch()
        pdf = self.upload(batch, "match.PDF", b"%PDF-test").json()["id"]
        other = self.upload(batch, "queued.pdf", b"%PDF-test").json()["id"]
        self.submit(batch, "compress", [pdf, other], strength=50)
        job = Job.objects.get(inputs__version_id=pdf)
        job.state = Job.State.DONE
        job.save(update_fields=["state"])
        day = timezone.localtime(job.created_at).date().isoformat()
        url = reverse("archive")
        filters = {"format": "pdf", "operation": "compress", "start": day, "end": day}
        response = self.client.get(url, filters)
        self.assertContains(response, "match.PDF")
        self.assertNotContains(response, "queued.pdf")
        self.assertNotContains(self.client.get(url, {**filters, "format": "png"}), "match.PDF")
        self.assertNotContains(self.client.get(url, {**filters, "start": "2000-01-01", "end": "2000-01-01"}), "match.PDF")
        self.assertContains(self.client.get(url, {"start": day, "end": "2000-01-01"}), "Начальная дата")

    def test_new_approval_can_upload_its_first_version(self):
        from .models import Approval, ApprovalRound

        reviewer = get_user_model().objects.create_user("upload-reviewer")
        url = reverse("approval_upload")
        data = {"title": "Новый документ", "reviewers": [reviewer.pk]}
        self.assertContains(self.client.get(reverse("approvals")), url)
        self.assertContains(self.client.post(url, data), "Выбери файл")
        self.assertFalse(Document.objects.exists())
        self.assertContains(self.client.post(url, {
            **data, "file": SimpleUploadedFile("unknown.xyz", b"data"),
        }), "формат пока не поддерживается")
        self.assertFalse(Document.objects.exists())
        response = self.client.post(url, {
            **data, "file": SimpleUploadedFile("notes.txt", b"draft"),
        })
        approval = Approval.objects.get()
        cycle = approval.rounds.get()
        self.assertRedirects(response, reverse("approval_detail", args=[approval.pk]))
        self.assertEqual(approval.document.owner, self.owner)
        self.assertEqual(cycle.version.number, 1)
        self.assertEqual(cycle.state, ApprovalRound.State.DRAFT)
        self.assertEqual(cycle.decisions.get().reviewer, reviewer)
        self.assertEqual(self.download(reverse("approval_file", args=[cycle.pk])), b"draft")
        card = self.client.get(reverse("approval_detail", args=[approval.pk]))
        preview_url = reverse("approval_preview", args=[cycle.pk])
        self.assertContains(card, 'data-inline-preview="' + preview_url + '"')
        self.assertContains(card, "text/plain")
        with closing(self.client.get(preview_url)) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Type"].startswith("text/plain"))
            self.assertEqual(b"".join(response.streaming_content), b"draft")
        self.client.force_login(reviewer)
        self.assertEqual(self.client.get(preview_url).status_code, 404)
        self.client.force_login(self.owner)
        self.client.post(reverse("approval_detail", args=[approval.pk]), {"round": cycle.pk, "action": "send"})
        self.client.force_login(reviewer)
        self.assertContains(self.client.get(reverse("approval_detail", args=[approval.pk])), preview_url)
        with closing(self.client.get(preview_url)) as response:
            self.assertEqual(b"".join(response.streaming_content), b"draft")


    def test_discard_draft_releases_archived_version(self):
        from .models import Approval

        reviewer = get_user_model().objects.create_user("discard-reviewer")
        self.client.post(reverse("approval_upload"), {
            "title": "Черновик", "reviewers": [reviewer.pk],
            "file": SimpleUploadedFile("draft.txt", b"draft"),
        })
        approval = Approval.objects.get()
        cycle = approval.rounds.get()
        version = cycle.version
        url = reverse("approval_detail", args=[approval.pk])
        data = {"round": cycle.pk, "action": "discard"}
        self.assertContains(self.client.get(url), "Удалить черновик")
        self.client.force_login(reviewer)
        self.assertEqual(self.client.post(url, data).status_code, 404)
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(url, data), reverse("approval_status", args=["draft"]))
        self.assertFalse(Approval.objects.exists())
        self.assertEqual(self.download(reverse("download_version", args=[version.pk])), b"draft")
        self.client.post(reverse("delete_version", args=[version.document_id, version.pk]))
        self.assertFalse(Version.objects.filter(pk=version.pk).exists())

    def test_approval_return_revision_and_file_access(self):
        from .models import Approval, ApprovalRound

        first = get_user_model().objects.create_user("reviewer-a")
        second = get_user_model().objects.create_user("reviewer-b")
        outsider = get_user_model().objects.create_user("outsider")
        reviewers = [first.pk, second.pk]
        version = self.upload(self.new_batch(), "policy.pdf", b"%PDF-test").json()["id"]
        document = Version.objects.get(pk=version).document
        self.client.post(reverse("approval_new", args=[version]), {
            "title": "Policy", "version": version, "reviewers": reviewers,
        })
        approval = Approval.objects.get()
        url = reverse("approval_detail", args=[approval.pk])
        cycle = approval.rounds.get()
        file_url = reverse("approval_file", args=[cycle.pk])

        def act(user, cycle, action, **extra):
            self.client.force_login(user)
            return self.client.post(url, {"round": cycle.pk, "action": action, **extra})

        self.client.force_login(first)
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.assertEqual(act(self.owner, cycle, "send").status_code, 302)
        self.client.force_login(first)
        self.assertEqual(self.download(file_url), b"%PDF-test")
        self.assertEqual(act(first, cycle, "approve").status_code, 302)
        self.assertEqual(act(first, cycle, "approve").status_code, 400)
        self.assertContains(act(second, cycle, "return"), "Укажите причину")
        self.assertEqual(act(second, cycle, "return", comment="Исправить").status_code, 302)
        cycle.refresh_from_db()
        self.assertEqual(cycle.state, ApprovalRound.State.RETURNED)

        self.client.force_login(self.owner)
        card = self.client.get(url)
        self.assertContains(card, "Согласовали 1 из 2")
        self.assertContains(card, "Причина возврата")
        self.assertEqual(act(second, cycle, "upload_revision",
                             file=SimpleUploadedFile("policy.pdf", b"%PDF-revised")).status_code, 400)
        self.assertEqual(document.versions.count(), 1)
        self.assertEqual(act(self.owner, cycle, "upload_revision",
                             file=SimpleUploadedFile("policy.pdf", b"%PDF-revised")).status_code, 302)
        revised = document.versions.first()
        self.assertEqual(self.client.get(url).context["form"].initial["version"], revised)
        self.assertEqual(act(self.owner, cycle, "revise", version=revised.pk, reviewers=reviewers).status_code, 302)
        latest = approval.rounds.last()
        self.assertRedirects(act(self.owner, latest, "discard"), url)
        self.assertEqual(list(approval.rounds.values_list("pk", flat=True)), [cycle.pk])
        self.assertEqual(act(self.owner, cycle, "discard").status_code, 400)
        self.assertEqual(act(self.owner, cycle, "revise", version=revised.pk, reviewers=reviewers).status_code, 302)
        latest = approval.rounds.last()
        self.assertEqual(act(self.owner, latest, "send").status_code, 302)
        self.assertEqual(act(first, latest, "approve").status_code, 302)
        self.assertEqual(act(second, latest, "approve").status_code, 302)
        latest.refresh_from_db()
        self.assertEqual(latest.state, ApprovalRound.State.APPROVED)
        card = self.client.get(url)
        self.assertContains(card, "Исправить")
        self.assertContains(card, "Версия добавлена в архив", count=2)
        self.assertContains(card, "Создан черновик", count=2)
        history = card.content.decode().split('class="approval-timeline"', 1)[1]
        self.assertLess(history.index("Согласовано"), history.index("Исправить"))
        self.assertLess(history.index("Исправить"), history.index("Версия 2"))
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.assertEqual(self.client.get(reverse("approval_preview", args=[latest.pk])).status_code, 404)
        self.client.force_login(self.owner)
        self.client.post(reverse("delete_version", args=[document.pk, version]))
        self.assertTrue(Version.objects.filter(pk=version).exists())

    def test_batch_limit_and_partial_failures(self):
        batch = self.new_batch()
        image = BytesIO()
        Image.new("RGB", (20, 10)).save(image, format="PNG")
        ids = []
        for index in range(49):
            response = self.upload(batch, f"{index}.png", image.getvalue())
            self.assertEqual(response.status_code, 201)
            ids.append(response.json()["id"])
        ids.append(self.upload(batch, "notes.txt", b"text").json()["id"])
        self.assertEqual(self.upload(batch, "extra.txt", b"text").status_code, 400)
        with override_settings(MAX_FILE_SIZE=4):
            response = self.upload(self.new_batch(), "large.txt", b"12345")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], "Файл больше 50 МБ.")
            self.assertEqual(list(self.upload_dir.iterdir()), [])

        # Unsupported input should fail without affecting the compatible files.
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.submit(batch, "convert", ids, target="pdf").status_code, 200)
        inserts = [query for query in queries if query["sql"].startswith('INSERT INTO "documents_job')]
        self.assertEqual(len(inserts), 2)
        call_command("worker", "--once", stdout=StringIO())
        jobs = Batch.objects.get(uploaded=50).jobs
        self.assertEqual(jobs.filter(state=Job.State.DONE).count(), 49)
        self.assertEqual(jobs.filter(state=Job.State.FAILED).count(), 1)

    def test_csv_converts_to_markdown_and_xlsx_preserving_values(self):
        batch = self.new_batch()
        version = self.upload(batch, "data.csv", 'Имя;Код\n"строка | 1";001\nтекст;=1+1\n'.encode()).json()["id"]
        submission = self.submit(batch, "markdown", [version])
        url = submission.json()["url"]
        fragment = self.client.get(url, {"fragment": "jobs"})
        self.assertContains(fragment, 'id="batch-results"')
        self.assertContains(fragment, 'data-poll="true"')
        self.assertNotContains(fragment, "<html")

        self.assertEqual(self.submit(self.new_batch(), "convert", [version], target="xlsx").status_code, 200)
        call_command("worker", "--once", stdout=StringIO())
        markdown_job = Job.objects.get(operation="markdown")
        with ZipFile(BytesIO(self.download(reverse("download_result", args=[markdown_job.results.get().pk])))) as archive:
            self.assertIn(r"| строка \| 1 | 001 |", archive.read("document.md").decode())
        spreadsheet_job = Job.objects.get(operation="convert")
        data = self.download(reverse("download_result", args=[spreadsheet_job.results.get().pk]))
        with closing(load_workbook(BytesIO(data), read_only=True)) as workbook:
            self.assertEqual(workbook.active["B2"].value, "001")
            self.assertEqual(workbook.active["B3"].data_type, "s")
            self.assertEqual(workbook.active["B3"].value, "=1+1")

    def test_repeated_worker_interruptions_allow_manual_retry(self):
        batch = self.new_batch()
        version = self.upload(batch, "notes.txt", b"recovery").json()["id"]
        self.assertEqual(self.submit(batch, "markdown", [version]).status_code, 200)
        Job.objects.update(state=Job.State.RUNNING, attempts=2)
        call_command("worker", "--once", stdout=StringIO())
        job = Job.objects.get()
        self.assertEqual(job.state, Job.State.FAILED)
        self.assertEqual(self.client.post(reverse("retry", args=[job.pk])).status_code, 302)
        call_command("worker", "--once", stdout=StringIO())
        job.refresh_from_db()
        self.assertEqual(job.state, Job.State.DONE)

    def test_retired_operation_keeps_previous_results(self):
        batch = self.new_batch()
        version = self.upload(batch, "notes.txt", b"saved result").json()["id"]
        self.assertEqual(self.submit(batch, "ocr_pdf", [version]).status_code, 400)
        submission = self.submit(batch, "markdown", [version])
        call_command("worker", "--once", stdout=StringIO())
        completed = Job.objects.get()
        completed.operation = "ocr_pdf"
        completed.save(update_fields=["operation"])
        queued = Job.objects.create(owner=self.owner, batch=completed.batch, operation="ocr_text")

        call_command("worker", "--once", stdout=StringIO())
        queued.refresh_from_db()
        self.assertEqual(queued.state, Job.State.FAILED)
        self.assertEqual(self.client.post(reverse("retry", args=[queued.pk])).status_code, 302)
        queued.refresh_from_db()
        self.assertEqual(queued.state, Job.State.FAILED)
        self.assertContains(self.client.get(submission.json()["url"]), "Операция из архива")
        self.assertTrue(self.download(reverse("download_result", args=[completed.results.get().pk])))

    def test_pages_render_for_authenticated_user(self):
        for route in ("workspace", "archive", "history"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        version = self.upload(self.new_batch(), "notes.txt", b"example").json()["id"]
        document = Version.objects.get(pk=version).document
        self.assertContains(self.client.get(reverse("document_detail", args=[document.pk])), "notes.txt")

        workspace = self.client.get(reverse("workspace"))
        sections = workspace.context["file_rules"]["sections"]
        self.assertEqual(set(sections["images"]["tools"]), {"convert", "images_pdf"})
        self.assertNotIn("pdf", sections["images"]["formats"])
        self.assertEqual(set(sections["office"]["tools"]), {"convert"})
        self.assertIn("merge", sections["pdf"]["tools"])
        self.assertEqual(set(sections["markdown"]["tools"]), {"markdown"})
        selected = self.client.get(reverse("workspace"), {"version": version})
        self.assertEqual(selected.context["section"], "markdown")
        self.assertContains(selected, 'value="markdown"')
