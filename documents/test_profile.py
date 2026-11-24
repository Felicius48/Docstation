from contextlib import closing
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .models import UserProfile


class ProfileTests(TestCase):
    def test_admin_can_confirm_user_deletion(self):
        admin = get_user_model().objects.create_superuser("manager")
        employee = get_user_model().objects.create_user("employee")
        url = reverse("account_admin:auth_user_delete", args=[employee.pk])
        self.client.force_login(employee)
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(get_user_model().objects.filter(pk=employee.pk).exists())
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=employee.pk).exists())
        self.assertEqual(self.client.post(reverse("account_admin:auth_user_delete", args=[admin.pk]), {"post": "yes"}).status_code, 403)

    def test_profile_menu_and_private_avatar_upload(self):
        users = get_user_model().objects
        user = users.create_user(
            "employee", email="employee@example.test", first_name="Анна", last_name="Иванова"
        )
        other = users.create_user("other")
        UserProfile.objects.create(
            user=user, patronymic="Сергеевна", position="Инженер", phone="+7 123 456 78 90"
        )
        self.client.force_login(user)
        page = self.client.get(reverse("task_list"))
        self.assertContains(page, 'class="profile-menu"')
        self.assertContains(page, "<span>Иванова</span>")
        self.assertContains(page, "<span>Анна Сергеевна</span>")
        self.assertContains(page, "Инженер")
        self.assertContains(page, "employee@example.test")
        self.assertContains(page, "+7 123 456 78 90")
        self.assertContains(page, 'action="/logout/"')
        avatar_url = reverse("profile_avatar")
        self.assertEqual(self.client.get(avatar_url).status_code, 404)

        with TemporaryDirectory() as directory, self.settings(FILE_UPLOAD_TEMP_DIR=directory):
            self.client.post(
                avatar_url,
                {
                    "file": SimpleUploadedFile("broken.png", b"not an image"),
                },
            )
            self.assertEqual(self.client.get(avatar_url).status_code, 404)
            image = BytesIO()
            Image.new("RGB", (300, 200), "red").save(image, "PNG")
            response = self.client.post(
                avatar_url,
                {
                    "file": SimpleUploadedFile("avatar.png", image.getvalue()),
                    "next": reverse("task_list"),
                    "user": other.pk,
                },
            )
        self.assertRedirects(response, reverse("task_list"))
        self.assertContains(self.client.get(reverse("workspace")), 'src="/profile/avatar/"')
        response = self.client.get(avatar_url)
        self.assertEqual(response["Content-Type"], "image/webp")
        with closing(Image.open(BytesIO(response.content))) as avatar:
            self.assertEqual(avatar.size, (192, 192))
        self.assertEqual(UserProfile.objects.get(user=user).position, "Инженер")

        self.client.force_login(other)
        self.assertEqual(self.client.get(avatar_url).status_code, 404)
