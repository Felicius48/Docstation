from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from PIL import Image, ImageOps

from .models import UserProfile
from .uploads import request_file


def avatar_bytes(upload):
    if upload.size > 5 * 1024 * 1024:
        raise ValueError("Фото должно быть не больше 5 МБ.")
    try:
        with Image.open(upload, formats=["JPEG", "PNG", "WEBP"]) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError("Фото должно содержать не больше 16 мегапикселей.")
            thumbnail = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), (192, 192))
            with BytesIO() as output:
                thumbnail.save(output, format="WEBP", quality=82)
                return output.getvalue()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("Выбери корректное фото в формате JPEG, PNG или WebP.") from error


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def profile_avatar(request):
    if request.method != "POST":
        avatar = UserProfile.objects.filter(user=request.user).values_list("avatar", flat=True).first()
        if not avatar:
            raise Http404
        return HttpResponse(
            avatar,
            content_type="image/webp",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    try:
        avatar = avatar_bytes(request_file(request, "Выбери фото профиля."))
    except ValueError as error:
        messages.error(request, str(error))
    else:
        UserProfile.objects.update_or_create(user=request.user, defaults={"avatar": avatar})
        messages.success(request, "Фото профиля обновлено.")
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect("workspace")
