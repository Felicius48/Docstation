from pathlib import Path

OFFICE = (
    {"doc", "docx", "odt"},
    {"xls", "xlsx", "ods", "csv"},
    {"ppt", "pptx", "odp"},
)
IMAGES = {"jpg", "jpeg", "png", "webp", "tif", "tiff", "heic"}
SUPPORTED = set.union(*OFFICE, IMAGES, {"pdf", "txt", "epub"})
TARGETS = ["pdf", "docx", "odt", "xlsx", "ods", "csv", "pptx", "odp", "jpg", "png", "webp", "tiff", "txt"]
GROUPED = {"merge", "images_pdf"}
PDF_TOOLS = {"merge", "split", "extract", "compress", "pdf_word", "pdf_tables"}

TOOLS = {
    "convert": "Конвертация",
    "markdown": "В Markdown",
    "merge": "Объединить PDF",
    "split": "Разделить PDF",
    "extract": "Извлечь страницы",
    "compress": "Сжать PDF",
    "pdf_word": "PDF в Word",
    "pdf_tables": "Таблицы в Excel",
    "images_pdf": "Фото в PDF",
}

OFFICE_FORMATS = set.union(*OFFICE)
WORKSPACE_SECTIONS = {
    "office": ("Офисные документы", "word", OFFICE_FORMATS, ("convert",)),
    "images": ("Изображения", "image", IMAGES, ("convert", "images_pdf")),
    "pdf": ("PDF", "pages", {"pdf"}, ("convert", "merge", "split", "extract", "compress", "pdf_word", "pdf_tables")),
    "markdown": ("Markdown", "markdown", SUPPORTED, ("markdown",)),
}


def workspace_section(name):
    suffix = extension(name)
    return next(
        (key for key, (_, _, formats, _) in WORKSPACE_SECTIONS.items() if suffix in formats),
        "office",
    )


def extension(name):
    suffix = Path(name).suffix[1:].lower()
    return {"jpeg": "jpg", "tif": "tiff"}.get(suffix, suffix)


def can_convert(source, target):
    if target == source:
        return False
    if source == "pdf":
        return target in {"jpg", "png", "txt"}
    if source in IMAGES:
        return target in {"jpg", "png", "webp", "tiff", "pdf"}
    return any(source in group and (target in group or target == "pdf") for group in OFFICE)


def supports(operation, name, options):
    source = extension(name)
    if operation == "convert":
        return can_convert(source, options["target"])
    if operation in PDF_TOOLS:
        return source == "pdf"
    if operation == "images_pdf":
        return source in IMAGES
    return operation == "markdown" and source in SUPPORTED


def file_rules(max_files, max_bytes):
    suffixes = sorted(SUPPORTED)
    return {
        "targets": {
            suffix: [target for target in TARGETS if can_convert(extension(f"file.{suffix}"), target)]
            for suffix in suffixes
        },
        "inputs": {
            tool: [suffix for suffix in suffixes if supports(tool, f"file.{suffix}", {})]
            for tool in TOOLS if tool != "convert"
        },
        "grouped": sorted(GROUPED),
        "sections": {
            key: {"formats": sorted(formats), "tools": {tool: TOOLS[tool] for tool in tools}}
            for key, (_, _, formats, tools) in WORKSPACE_SECTIONS.items()
        },
        "maxFiles": max_files,
        "maxBytes": max_bytes,
    }


def validate_submission(data, max_files):
    if not isinstance(data, dict):
        raise ValueError("Проверь параметры операции.")
    operation = data.get("operation")
    ids = data.get("versions")
    options = validate_options(operation, data)
    if (not isinstance(ids, list) or not 1 <= len(ids) <= max_files
            or any(type(item) is not int or not 0 < item < 2**63 for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError(f"Выбери от 1 до {max_files} файлов.")
    if operation == "merge" and len(ids) < 2:
        raise ValueError("Для объединения нужны хотя бы два PDF.")
    return operation, options, ids


def validate_options(operation, data):
    if not isinstance(operation, str) or operation not in TOOLS:
        raise ValueError("Выбери инструмент.")
    if operation == "convert":
        target = data.get("target")
        if target not in TARGETS:
            raise ValueError("Выбери выходной формат.")
        return {"target": target}
    if operation == "extract":
        mode = data.get("mode", "combined")
        if mode not in ("combined", "separate"):
            raise ValueError("Выбери формат результата.")
        if "selections" in data:
            selections = data["selections"]
            if not isinstance(selections, dict) or not selections or len(selections) > 50:
                raise ValueError("Выбери страницы для каждого PDF.")
            for pages in selections.values():
                if (not isinstance(pages, list) or not 1 <= len(pages) <= 2000
                        or any(type(page) is not int or not 1 <= page <= 2**31 - 1 for page in pages)
                        or len(set(pages)) != len(pages)):
                    raise ValueError("Выбери до 2000 разных страниц для каждого PDF.")
            return {"selections": selections, "mode": mode}
        pages = str(data.get("pages", "")).strip()
        if not pages or len(pages) > 200:
            raise ValueError("Укажи страницы, например 1, 3-5.")
        parse_pages(pages)
        return {"pages": pages, "mode": mode}
    if operation == "compress":
        if "strength" in data:
            return {"strength": compression_strength(data["strength"])}
        quality = data.get("quality", "balanced")
        if quality not in ("small", "balanced", "high"):
            raise ValueError("Выбери качество сжатия.")
        return {"quality": quality}
    return {}


def compression_strength(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 3:
        value = int(value)
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("Выбери степень сжатия от 0 до 100.")
    return value


def parse_pages(value, total=None):
    result = []
    for part in value.split(","):
        try:
            limits = [int(item) for item in part.split("-")]
        except ValueError:
            limits = []
        if (not 1 <= len(limits) <= 2 or not 1 <= limits[0] <= limits[-1]
                or (total is not None and limits[-1] > total)):
            raise ValueError("Проверь номера страниц. Пример: 1, 3-5.")
        if total is not None:
            if len(result) + limits[-1] - limits[0] + 1 > 2000:
                raise ValueError("За одну операцию можно извлечь до 2000 страниц.")
            result.extend(range(limits[0] - 1, limits[-1]))
    return result
