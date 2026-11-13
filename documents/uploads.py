from django.conf import settings
from django.core.files.uploadhandler import StopUpload, TemporaryFileUploadHandler


TOO_LARGE = "Файл больше 50 МБ."


def request_file(request, missing_message):
    upload = request.FILES.get("file")
    if getattr(request, "file_too_large", False):
        raise ValueError(TOO_LARGE)
    if upload is None:
        raise ValueError(missing_message)
    return upload


class LimitedUploadHandler(TemporaryFileUploadHandler):
    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > settings.MAX_FILE_SIZE:
            self.request.file_too_large = True
            raise StopUpload()
        return super().receive_data_chunk(raw_data, start)
