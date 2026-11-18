from django.http import FileResponse, Http404

from .operations import extension
from .storage import stored_file


def preview_type(name):
    return {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "txt": "text/plain; charset=utf-8",
    }.get(extension(name), "")


def private_file_response(handle, name, *, content_type=None):
    response = FileResponse(
        handle,
        as_attachment=content_type is None,
        filename=name,
        content_type=content_type,
    )
    response["Cache-Control"] = "private, no-store"
    if content_type:
        response["X-Content-Type-Options"] = "nosniff"
    return response


def download(key, name, *, preview=False):
    content_type = preview_type(name) if preview else None
    if preview and not content_type:
        raise Http404
    try:
        handle = stored_file(key).open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    return private_file_response(handle, name, content_type=content_type)
