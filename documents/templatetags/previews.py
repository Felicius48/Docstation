import json

from django import template

from ..file_responses import preview_type

register = template.Library()
register.filter(preview_type)


@register.filter
def document_icon(name):
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix in {"doc", "docx", "odt", "rtf"}:
        return "word"
    if suffix in {"xls", "xlsx", "ods", "csv"}:
        return "sheet"
    if suffix in {"ppt", "pptx", "odp"}:
        return "slides"
    if suffix in {"jpg", "jpeg", "png", "webp", "tif", "tiff", "heic"}:
        return "image"
    if suffix in {"md", "markdown"}:
        return "markdown"
    return "pdf" if suffix == "pdf" else "file"


@register.filter
def document_suffix(name):
    return name.rpartition(".")[2].upper() if "." in name else ""


@register.filter
def document_icon_image(name):
    suffix = document_suffix(name).lower()
    icon = {"docx": "doc", "xlsx": "xls", "jpeg": "jpg"}.get(suffix, suffix)
    if icon in {"doc", "xls", "pdf", "jpg", "txt"}:
        return f"documents/file-icons/{icon}.png"
    return ""


@register.filter
def marks_json(marks):
    return json.dumps(marks, separators=(",", ":"))
