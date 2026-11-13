from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from django.conf import settings


def stored_file(key):
    return settings.DATA_DIR / "files" / str(key)


def remove_files(keys):
    for key in keys:
        stored_file(key).unlink(missing_ok=True)


def zip_files(destination, entries):
    names = set()
    with ZipFile(destination, "w", compression=ZIP_STORED) as archive:
        for path, name in entries:
            name = Path(name)
            candidate = name
            index = 2
            while candidate.as_posix().casefold() in names:
                candidate = name.with_name(f"{name.stem}-{index}{name.suffix}")
                index += 1
            names.add(candidate.as_posix().casefold())
            archive.write(path, candidate.as_posix())
