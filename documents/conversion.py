import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import closing

from .operations import IMAGES, extension, parse_pages
from .spreadsheets import convert_spreadsheet, read_csv, write_csv
from .storage import zip_files

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    pass


def command(args, cwd, *, capture=False):
    with tempfile.TemporaryFile(dir=cwd) as log:
        try:
            result = subprocess.run(
                [str(arg) for arg in args],
                cwd=cwd,
                stdout=subprocess.PIPE if capture else log,
                stderr=log if capture else subprocess.STDOUT,
                timeout=600,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ConversionError(f"Установи инструмент {args[0]} или запусти обработчик в Docker.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("Истекло время обработки файла.") from exc
        if result.returncode:
            log_size = log.seek(0, os.SEEK_END)
            log.seek(max(0, log_size - 1800))
            logger.warning(
                "%s exited %s: %s", args[0], result.returncode, log.read().decode(errors="replace")
            )
            raise ConversionError("Инструмент не смог прочитать или обработать этот файл.")
        if capture:
            return result.stdout


def office(source, target, work):
    output_dir = work / "office"
    output_dir.mkdir(exist_ok=True)
    command(
        [
            "soffice",
            f"-env:UserInstallation={(work / 'lo-profile').as_uri()}",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            output_dir,
            source,
        ],
        work,
    )
    output = output_dir / f"{source.stem}.{target}"
    if not output.is_file():
        raise ConversionError("LibreOffice не создал выходной файл.")
    return output


def image_frames(paths):
    from PIL import Image, ImageOps, ImageSequence

    for path in paths:
        if extension(path.name) == "heic":
            from pillow_heif import register_heif_opener

            register_heif_opener(thumbnails=False)
        with Image.open(path) as source:
            for frame in ImageSequence.Iterator(source):
                yield ImageOps.exif_transpose(frame)


def opaque(frame):
    from PIL import Image

    if frame.mode == "RGB" and "transparency" not in frame.info:
        return frame
    rgba = frame.convert("RGBA")
    background = Image.new("RGB", frame.size, "white")
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def merge_pdfs(paths, output):
    args_file = output.parent / "pdf-merge.args"
    with args_file.open("w", encoding="utf-8") as handle:
        for arg in ("--warning-exit-0", "--empty", "--pages", *paths, "--", output):
            handle.write(f"{arg}\n")
    command(["qpdf", f"@{args_file}"], output.parent)


def images_pdf(paths, output):
    pages = []
    for index, frame in enumerate(image_frames(paths)):
        page = output.parent / f"photo-{index}.pdf"
        opaque(frame).save(page, resolution=150)
        pages.append(page)
    if not pages:
        raise ConversionError("В файле нет изображений.")
    if len(pages) == 1:
        pages[0].replace(output)
    else:
        merge_pdfs(pages, output)


def image_export(source, target, output):
    format_name = {"jpg": "JPEG", "tiff": "TIFF"}.get(target, target.upper())
    if target == "tiff":
        frames = list(image_frames([source]))
        frames[0].save(
            output, format=format_name, save_all=True, append_images=frames[1:], compression="tiff_deflate"
        )
        return [output]
    outputs = []
    for index, frame in enumerate(image_frames([source]), 1):
        path = output if index == 1 else output.with_stem(f"{output.stem}-{index}")
        if target == "jpg":
            frame = opaque(frame)
        frame.save(path, format=format_name)
        outputs.append(path)
    return outputs


def pdf_export(source, target, work):
    if target == "txt" and shutil.which("pdftotext"):
        output = work / "result.txt"
        command(["pdftotext", "-layout", source, output], work)
        return [output]
    import pypdfium2 as pdfium

    outputs = []
    with pdfium.PdfDocument(source) as pdf:
        if target == "txt":
            output = work / "result.txt"
            with output.open("w", encoding="utf-8") as handle:
                for page in pdf:
                    with closing(page.get_textpage()) as textpage:
                        handle.write(textpage.get_text_range())
                        handle.write("\n\n")
                    page.close()
            return [output]
        for index, pdf_page in enumerate(pdf, 1):
            output = work / f"page-{index:04d}.{target}"
            with closing(pdf_page) as page:
                scale = min(2, 3000 / max(page.get_size()))
                with closing(page.render(scale=scale)) as bitmap:
                    image = bitmap.to_pil()
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    image.save(output)
            outputs.append(output)
    return outputs


def pdf_pages(operation, paths, options, work):
    result = work / "result.pdf"
    if operation == "merge":
        merge_pdfs(paths, result)
        return [result]
    if operation == "extract":
        page_count = command(["qpdf", "--warning-exit-0", "--show-npages", paths[0]], work, capture=True)
        pages = options["pages"]
        if isinstance(pages, str):
            pages = [index + 1 for index in parse_pages(pages, int(page_count))]
        elif max(pages) > int(page_count):
            raise ConversionError("В PDF нет одной из выбранных страниц.")
        selection = ",".join(map(str, pages))
        command(["qpdf", "--warning-exit-0", "--empty", "--pages", paths[0], selection, "--", result], work)
        if options.get("mode") == "separate":
            command(["qpdf", "--warning-exit-0", "--split-pages", result, work / "split-%d.pdf"], work)
            repeated = len(set(pages)) != len(pages)
            return [
                path.rename(
                    work / (f"page-{number:04d}-{index:04d}.pdf" if repeated else f"page-{number:04d}.pdf")
                )
                for index, (number, path) in enumerate(
                    zip(pages, sorted(work.glob("split-*.pdf")), strict=True), 1
                )
            ]
        return [result]

    command(["qpdf", "--warning-exit-0", "--split-pages", paths[0], work / "split-%d.pdf"], work)
    outputs = []
    for index, page in enumerate(sorted(work.glob("split-*.pdf")), 1):
        outputs.append(page.rename(work / f"page-{index:04d}.pdf"))
    return outputs


def compress_pdf(source, output, options):
    if "strength" in options:
        strength = options["strength"]
        if strength == 0:
            return source
        dpi = 300 - round(strength * 2.4)
        settings = ["-dPDFSETTINGS=/ebook"]
        for color, resolution in (("Color", dpi), ("Gray", dpi), ("Mono", dpi * 2)):
            settings.extend(
                [
                    f"-dDownsample{color}Images=true",
                    f"-d{color}ImageResolution={resolution}",
                    f"-d{color}ImageDownsampleThreshold=1.0",
                ]
            )
    else:
        profile = {"small": "screen", "balanced": "ebook", "high": "printer"}[options["quality"]]
        settings = [f"-dPDFSETTINGS=/{profile}"]
    command(
        [
            "gs",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pdfwrite",
            *settings,
            f"-sOutputFile={output}",
            source,
        ],
        output.parent,
    )
    return output if output.stat().st_size < source.stat().st_size else source


def convert(operation, paths, options, work):
    source = paths[0]
    suffix = extension(source.name)
    if operation in {"merge", "split", "extract"}:
        return pdf_pages(operation, paths, options, work)
    if operation == "compress":
        output = work / "result.pdf"
        if compress_pdf(source, output, options) == source:
            shutil.copyfile(source, output)
        return [output]
    if operation == "images_pdf":
        output = work / "result.pdf"
        images_pdf(paths, output)
        return [output]
    if operation in {"markdown", "pdf_word", "pdf_tables"}:
        return structured(operation, source, work)
    target = options["target"]
    output = work / f"result.{target}"
    if suffix == "pdf":
        outputs = pdf_export(source, target, work)
        if target == "txt":
            with outputs[0].open(encoding="utf-8") as text:
                if not any(line.strip() for line in text):
                    raise ConversionError("В PDF нет текстового слоя.")
        return outputs
    if suffix in IMAGES:
        if target == "pdf":
            images_pdf(paths, output)
            return [output]
        return image_export(source, target, output)
    if {suffix, target} == {"csv", "xlsx"}:
        convert_spreadsheet(source, target, output)
        return [output]
    converted = office(source, target, work)
    if target == "csv":
        with (
            converted.open(encoding="utf-8-sig", newline="") as handle,
            output.open(
                "w",
                encoding="utf-8-sig",
                newline="",
            ) as destination,
        ):
            write_csv(destination, read_csv(handle))
        return [output]
    return [converted]


def structured(operation, source, work):
    from .structured import csv_markdown, pdf_markdown, pdf_tables

    suffix = extension(source.name)
    if operation == "pdf_tables":
        workbook = work / "result.xlsx"
        pdf_tables(source, workbook)
        if not workbook.is_file():
            raise ConversionError("В текстовом слое PDF таблицы не найдены.")
        return [workbook]

    folder = work / "markdown"
    folder.mkdir()
    markdown = folder / "document.md"
    if suffix == "txt":
        with source.open(encoding="utf-8-sig") as handle, markdown.open("w", encoding="utf-8") as output:
            shutil.copyfileobj(handle, output)
    elif suffix == "csv":
        csv_markdown(source, markdown)
    elif suffix == "pdf":
        found_text = pdf_markdown(source, markdown)
        if operation == "pdf_word":
            if not found_text:
                raise ConversionError("В PDF нет текстового слоя для экспорта в Word.")
            output = work / "result.docx"
            command(["pandoc", markdown, "--from=gfm", "-o", output], folder)
            return [output]
    elif suffix in IMAGES:
        images = folder / "images"
        images.mkdir()
        references = []
        for index, frame in enumerate(image_frames([source]), 1):
            name = f"image-{index}.png"
            frame.save(images / name, format="PNG")
            references.append(f"![Изображение {index}](images/{name})")
        markdown.write_text("\n\n".join(references) + "\n", encoding="utf-8")
    else:
        if suffix == "doc":
            source = office(source, "docx", work)
        if suffix in {"xls", "xlsx", "ods", "ppt", "pptx", "odp"}:
            source = office(source, "html", work)
            # Package only images generated beside the HTML in the isolated job directory.
            for asset in source.parent.glob(f"{source.stem}_html_*"):
                if (
                    not asset.is_symlink()
                    and asset.is_file()
                    and extension(asset.name) in IMAGES | {"gif", "svg"}
                ):
                    shutil.copyfile(asset, folder / asset.name)
        command(
            ["pandoc", "--sandbox", source, "--to=gfm-raw_html", "--extract-media=images", "-o", markdown],
            folder,
        )

    output = work / "result.zip"
    zip_files(
        output,
        [(path, path.relative_to(folder).as_posix()) for path in sorted(folder.rglob("*")) if path.is_file()],
    )
    return [output]
