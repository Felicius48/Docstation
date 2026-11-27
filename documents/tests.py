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
from openpyxl import Workbook, load_workbook
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .conversion import command, structured
from .models import Batch, Document, Job, RegistrationPolicy, Version
from .spreadsheets import convert_spreadsheet
from .storage import stored_file


class PdfWordScanTests(TestCase):
    @skipUnless(which("pandoc"), "Pandoc is required for DOCX verification")
    def test_word_paragraphs_and_russian_hyphenation(self):
        from xml.etree import ElementTree

        from .pdf_paragraphs import reflow
        from .structured import escape_markdown

        uncertain = set()
        lines = [
            "1.1 Сторона принимает обяза-",
            "тельство и предоставляет счёт-",
            "фактуру.",
            "1.2 Проверить редкое слово абв-",
            "ёжз.",
        ]
        text = reflow(((line, 0, i * 12, i * 12 + 10, 1) for i, line in enumerate(lines)), uncertain)
        self.assertEqual(uncertain, {"абв-ёжз"})
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "result.md"
            source.write_text(escape_markdown(text), encoding="utf-8")
            command(["pandoc", source, "--from=gfm", "-o", "result.docx"], root)
            with ZipFile(root / "result.docx") as doc:
                xml = ElementTree.fromstring(doc.read("word/document.xml"))
            paragraphs = ["".join(p.itertext()) for p in xml.findall(".//{*}body/{*}p")]
            self.assertEqual(
                paragraphs,
                [
                    "1.1 Сторона принимает обязательство и предоставляет счёт-фактуру.",
                    "1.2 Проверить редкое слово абв-ёжз.",
                ],
            )

    def test_recognized_and_image_only_pages_are_reported_separately(self):
        from .structured import pdf_markdown

        with TemporaryDirectory() as directory:
            work = Path(directory)
            pdf = PdfWriter()
            for _ in range(2):
                pdf.add_blank_page(width=72, height=72)
            source = work / "scan.pdf"
            pdf.write(source)

            def recognize(page, path, uncertain):
                Image.new("RGB", (10, 10), "white").save(path)
                return "Договор 123" if page.page_number == 1 else ""

            output = work / "document.md"
            pdf_markdown(source, output, recognize)
            text = output.read_text()
            self.assertIn("Договор 123", text)
            self.assertIn("Распознанные страницы: 1.", text)
            self.assertIn("Страницы в виде изображений: 2.", text)
            self.assertTrue((work / "images/scan-2.png").exists())


class SpreadsheetSecurityTests(TestCase):
    def test_xlsx_text_is_safe_when_exported_to_csv(self):
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.xlsx", Path(directory) / "result.csv"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["=1+2", -42, "001"])
            sheet["A1"].data_type = "s"
            workbook.save(source)
            convert_spreadsheet(source, "csv", output)
            self.assertEqual(output.read_text(encoding="utf-8-sig"), '"\'=1+2","-42","001"\n')


class MarkdownSecurityTests(TestCase):
    @skipUnless(which("pandoc"), "Pandoc is required for EPUB conversion")
    def test_epub_extracts_book_images_without_reading_other_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "cover.png"
            Image.new("RGB", (6, 6), "red").save(image)
            (root / "book.md").write_text("![Cover](cover.png)", encoding="utf-8")
            book = root / "book.epub"
            command(["pandoc", "book.md", "-o", book], root)
            private = root / "private.sqlite3"
            marker = b"DOCSTATION_PRIVATE_TEST_DATA"
            private.write_bytes(marker)
            source = root / "input.epub"
            with ZipFile(book) as original, ZipFile(source, "w") as edited:
                for item in original.infolist():
                    content = original.read(item)
                    if item.filename.endswith("ch001.xhtml"):
                        content = content.replace(
                            b"</body>",
                            f'<p><img src="{private.as_uri()}" alt="private"/></p></body>'.encode(),
                        )
                    edited.writestr(item, content)
            work = root / "work"
            work.mkdir()
            (result,) = structured("markdown", source, work)
            with ZipFile(result) as archive:
                markdown = archive.read("document.md").decode()
                self.assertIn("private", markdown)
                self.assertTrue(any(name.startswith("images/") for name in archive.namelist()))
                self.assertFalse(any(marker in archive.read(name) for name in archive.namelist()))


class RegistrationTests(TestCase):
    credentials = {"username": "new-owner", "password1": "Local!Pine8Road", "password2": "Local!Pine8Road"}

    def test_unclaimed_registration_is_closed(self):
        RegistrationPolicy.objects.update(public=False, owner=None)
        response = self.client.post(reverse("login"), {**self.credentials, "action": "register"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    def test_login_throttles_repeated_attempts(self):
        for _ in range(10):
            self.assertEqual(
                self.client.post(reverse("login"), {"username": "missing", "password": "bad"}).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(reverse("login"), {"username": "MISSING", "password": "bad"}).status_code, 429
        )

    def test_registration_creates_an_account_and_starts_a_session(self):
        url = reverse("login")
        RegistrationPolicy.objects.update(public=True)
        page = self.client.get(url)
        self.assertContains(page, "Создать аккаунт")
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        response = self.client.post(
            url,
            {
                **self.credentials,
                "action": "register",
                "next": "https://example.com/",
            },
        )
        self.assertRedirects(response, reverse("workspace"))
        user = get_user_model().objects.get(username=self.credentials["username"])
        self.assertTrue(user.check_password(self.credentials["password1"]))
        self.assertFalse(user.is_staff)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.client.post(reverse("logout"))
        self.assertRedirects(
            self.client.post(
                url,
                {
                    "username": user.username,
                    "password": self.credentials["password1"],
                    "next": reverse("history"),
                },
            ),
            reverse("history"), fetch_redirect_response=False,
        )

    def test_registration_requires_csrf_and_rejects_invalid_credentials(self):
        url = reverse("login")
        registration = {**self.credentials, "action": "register"}
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, registration).status_code, 403)
        RegistrationPolicy.objects.update(public=True)
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
        owner = get_user_model().objects.create_superuser("owner", password="Local!Admin42")
        RegistrationPolicy.objects.update(owner=owner, public=True)
        self.client.force_login(owner)
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
        self.assertEqual(
            Client().post(invitation_url, {**registration, "username": "another"}).status_code, 403
        )
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

    @override_settings(MAX_ARCHIVE_BYTES=5, MAX_PENDING_JOBS=1)
    def test_archive_and_queue_quotas_reject_excess_work(self):
        batch = self.new_batch()
        version = self.upload(batch, "test.txt", b"12345").json()["id"]
        self.assertEqual(self.upload(batch, "extra.txt", b"6").status_code, 400)
        self.assertEqual(Version.objects.count(), 1)
        self.assertEqual(self.submit(batch, "markdown", [version]).status_code, 200)
        self.assertEqual(self.submit(self.new_batch(), "markdown", [version]).status_code, 400)
        self.assertEqual(Job.objects.count(), 1)

    def new_batch(self):
        response = self.client.post(reverse("new_batch"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def upload(self, batch, name, contents):
        return self.client.post(batch["upload"], {"file": SimpleUploadedFile(name, contents)})

    def submit(self, batch, operation, versions, **options):
        return self.client.post(
            batch["submit"],
            {"operation": operation, "versions": versions, **options},
            content_type="application/json",
        )

    def download(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        with closing(response):
            return b"".join(response.streaming_content)

    def test_versions_and_results_stay_private(self):
        batch = self.new_batch()
        first = self.upload(batch, "notes.txt", "Первая версия".encode()).json()["id"]
        self.assertEqual(self.download(reverse("download_version", args=[first])), "Первая версия".encode())

        submission = self.submit(batch, "markdown", [first])
        self.assertEqual(submission.status_code, 200)
        self.assertEqual(self.submit(batch, "markdown", [first]).json(), submission.json())
        self.assertEqual(Job.objects.count(), 1)
        call_command("worker", "--once", stdout=StringIO())
        job = Job.objects.get()
        result = job.results.get()
        detail = self.client.get(submission.json()["url"])
        self.assertContains(detail, reverse("download_result", args=[result.pk]))
        self.assertContains(detail, reverse("download_version", args=[first]))
        self.assertNotContains(detail, "В ЭДО")
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

    def test_submit_accepts_a_full_pdf_selection_with_a_bounded_request(self):
        batch = self.new_batch()
        document = Document.objects.create(owner=self.owner, title="Пачка")
        versions = Version.objects.bulk_create(
            [Version(document=document, number=index, name="pages.pdf", size=1) for index in range(1, 51)]
        )
        ids = [version.pk for version in versions]
        pages = list(range(1, 2001))
        response = self.submit(batch, "extract", ids, selections={str(pk): pages for pk in ids})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Job.objects.count(), 50)
        response = self.client.post(
            batch["submit"], b" " * (2 * 1024 * 1024 + 1), content_type="application/json"
        )
        self.assertEqual(response.status_code, 413)

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
        orphan.write_bytes(b"interrupted result")
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
        preview = self.client.post(
            url,
            {
                "strength": "0",
                "file": SimpleUploadedFile("scan.pdf", contents),
            },
        )
        self.assertEqual(preview.status_code, 201)
        preview_url = preview.json()["url"]
        call_command("worker", "--once", stdout=StringIO())
        ready = self.client.get(preview_url)
        self.assertEqual(ready.json()["size"], len(contents))
        self.assertEqual(ready["Cache-Control"], "no-store")
        self.assertEqual(self.client.post(preview_url, {"strength": "0"}).json(), ready.json())
        queued = self.client.post(preview_url, {"strength": "100"})
        self.assertEqual(
            queued.json(),
            {
                "state": Job.State.QUEUED,
                "strength": 100,
                "original": len(contents),
                "size": None,
            },
        )
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
                submission = self.submit(
                    batch, "extract", [version], mode=mode, selections={str(version): [1, 3]}
                )
                self.assertEqual(submission.status_code, 200)
                call_command("worker", "--once", stdout=StringIO())
                job = Job.objects.get(inputs__version=version)
                self.assertEqual(job.state, Job.State.DONE, job.error)
                results = list(job.results.all())
                self.assertEqual(len(results), len(expected_widths))
                for result, widths in zip(results, expected_widths, strict=True):
                    data = self.download(reverse("download_result", args=[result.pk]))
                    self.assertEqual([page.mediabox.width for page in PdfReader(BytesIO(data)).pages], widths)
                fragment = self.client.get(reverse("batch_detail", args=[job.batch_id]), {"fragment": "jobs"})
                if mode == "separate":
                    self.assertContains(fragment, "Скачать всё в ZIP")
                    self.assertEqual(
                        [item.name for item in results], ["source-page-0001.pdf", "source-page-0003.pdf"]
                    )
                    bundle = self.download(reverse("download_batch", args=[job.batch_id]))
                    with ZipFile(BytesIO(bundle)) as archive:
                        self.assertEqual(archive.namelist(), [item.name for item in results])
                else:
                    self.assertContains(fragment, "Скачать PDF")
                    self.assertNotContains(fragment, "Скачать всё в ZIP")

    def test_submission_rejects_invalid_ids_and_preserves_grouped_order(self):
        batch = self.new_batch()
        image = BytesIO()
        Image.new("RGB", (20, 10)).save(image, format="PNG")
        first, second = [
            self.upload(batch, name, image.getvalue()).json()["id"] for name in ("first.png", "second.png")
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
        self.assertEqual(
            list(job.inputs.order_by("position").values_list("version_id", flat=True)), [second, first]
        )
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
        self.assertContains(self.client.get(reverse("batch_detail", args=[result.job.batch_id])), urls[1])
        self.client.force_login(get_user_model().objects.create_user("preview-other"))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)

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
        version = self.upload(batch, "data.csv", 'Имя;Код\n"строка | 1";001\nтекст;=1+1\n'.encode()).json()[
            "id"
        ]
        submission = self.submit(batch, "markdown", [version])
        url = submission.json()["url"]
        fragment = self.client.get(url, {"fragment": "jobs"})
        self.assertContains(fragment, 'id="batch-results"')
        self.assertContains(fragment, 'data-poll="true"')
        self.assertNotContains(fragment, "<html")

        self.assertEqual(self.submit(self.new_batch(), "convert", [version], target="xlsx").status_code, 200)
        call_command("worker", "--once", stdout=StringIO())
        markdown_job = Job.objects.get(operation="markdown")
        with ZipFile(
            BytesIO(self.download(reverse("download_result", args=[markdown_job.results.get().pk])))
        ) as archive:
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
        self.assertContains(self.client.get(submission.json()["url"]), "Старая операция")
        self.assertTrue(self.download(reverse("download_result", args=[completed.results.get().pk])))

    def test_pages_render_for_authenticated_user(self):
        for route in ("workspace", "task_list"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        version = self.upload(self.new_batch(), "notes.txt", b"example").json()["id"]

        workspace = self.client.get(reverse("workspace"))
        sections = workspace.context["file_rules"]["sections"]
        self.assertEqual(set(sections["images"]["tools"]), {"convert", "images_pdf"})
        self.assertNotIn("pdf", sections["images"]["formats"])
        self.assertEqual(set(sections["office"]["tools"]), {"convert"})
        self.assertIn("merge", sections["pdf"]["tools"])
        self.assertEqual(set(sections["markdown"]["tools"]), {"markdown"})
        selected = self.client.get(reverse("workspace"), {"version": version})
        self.assertEqual(selected.context["section"], "office")
        self.assertContains(selected, 'id="tool-cards"')
        self.assertContains(selected, 'id="history"')
        self.assertRedirects(self.client.get(reverse("history")), reverse("workspace") + "#history")
