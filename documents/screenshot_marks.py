import json

from .file_responses import preview_type


def attachment_marks(request, field, uploads):
    """Match each set of marks to its file's position in the multipart submission."""
    raw = request.POST.get(f"{field}_marks")
    if not raw:
        return [[] for _ in uploads]
    error = "Не удалось прочитать отметки на скриншоте."
    try:
        groups = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError(error) from exc
    if not isinstance(groups, list) or len(groups) != len(uploads):
        raise ValueError(error)
    for marks, upload in zip(groups, uploads):
        if not isinstance(marks, list) or len(marks) > 50:
            raise ValueError(error)
        if marks and not preview_type(upload.name).startswith("image/"):
            raise ValueError(error)
        for mark in marks:
            if (not isinstance(mark, list) or len(mark) != 5 or mark[0] not in ("arrow", "rect")
                    or any(type(n) is not int or not 0 <= n <= 1000 for n in mark[1:])):
                raise ValueError(error)
    return groups
