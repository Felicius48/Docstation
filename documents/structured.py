"""Extract the text, tables and images already stored in PDF files."""
import re
from pathlib import Path

from .spreadsheets import read_csv, write_workbook


def tables_on(page):
    if page.chars:
        for table in page.find_tables():
            rows = table.extract()
            if any(cell and cell.strip() for row in rows for cell in row):
                yield table.bbox, rows


def pdf_tables(source, destination):
    import pdfplumber

    with pdfplumber.open(source) as pdf:
        write_workbook(
            ((f"Таблица {index}", rows) for index, rows in enumerate(iter_pdf_tables(pdf), 1)),
            destination,
        )


def iter_pdf_tables(pdf):
    for page in pdf.pages:
        try:
            for _, rows in tables_on(page):
                yield rows
        finally:
            page.close()


def escape_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>~-])", r"\\\1", text)


def write_markdown_table(output, rows, width):
    for index, row in enumerate(rows):
        cells = [escape_markdown(" ".join((cell or "").splitlines()).strip()) for cell in row]
        cells.extend([""] * (width - len(cells)))
        output.write("| " + " | ".join(cells) + " |\n")
        if index == 0:
            output.write("| " + " | ".join(["---"] * width) + " |\n")


def csv_markdown(source, destination):
    with source.open(encoding="utf-8-sig", newline="") as handle:
        width = max(map(len, read_csv(handle)), default=0)
        handle.seek(0)
        with destination.open("w", encoding="utf-8") as output:
            if width:
                write_markdown_table(output, read_csv(handle), width)


def pdf_markdown(source, destination):
    import pdfplumber
    from pypdf import PdfReader

    found_text = False
    images = destination.parent / "images"
    with source.open("rb") as handle, pdfplumber.open(source) as pdf, destination.open(
        "w", encoding="utf-8",
    ) as markdown:
        image_pages = PdfReader(handle).pages
        for number, page in enumerate(pdf.pages, 1):
            tables = list(tables_on(page))
            outside_tables = page
            for box, _ in tables:
                outside_tables = outside_tables.outside_bbox(box)
            text = outside_tables.extract_text()
            if text:
                markdown.write(escape_markdown(text) + "\n\n")
            for _, rows in tables:
                write_markdown_table(markdown, rows, max(map(len, rows)))
                markdown.write("\n")
            found_text = found_text or bool(text or tables)
            for index, item in enumerate(image_pages[number - 1].images, 1):
                if item.image is None:
                    continue
                images.mkdir(exist_ok=True)
                suffix = Path(item.name).suffix.lower()
                stem = f"page-{number:04d}-{index:03d}"
                if suffix in {".jpg", ".jpeg", ".png"}:
                    name = stem + suffix
                    (images / name).write_bytes(item.data)
                else:
                    name = stem + ".png"
                    item.image.convert("RGBA").save(images / name)
                markdown.write(f"![Страница {number}](images/{name})\n\n")
            page.close()
    return found_text
