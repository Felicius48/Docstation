from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Document, Version, WorkflowRecord


class WorkflowTests(TestCase):
    def test_registry_lifecycle_audit_and_owner_boundary(self):
        users = get_user_model().objects
        owner = users.create_user("author")
        outsider = users.create_user("outsider")
        document = Document.objects.create(owner=owner, title="Договор")
        version = Version.objects.create(document=document, number=1, name="contract.txt", size=10)
        create = reverse("workflow_create", args=[version.pk])
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(create).status_code, 404)
        self.client.force_login(owner)
        data = {"title": "Договор", "kind": "ДОГ", "purpose": "Поставка"}
        self.assertContains(self.client.post(create, {**data, "purpose": ""}), "Назначение")
        self.assertFalse(WorkflowRecord.objects.exists())
        for expected in (1, 2):
            detail = self.client.post(create, data).url
            self.assertEqual(self.client.post(detail, {"state": "registered"}).status_code, 400)
            self.assertEqual(self.client.post(detail, {"state": "withdrawn"}).status_code, 400)
            for next_state in ("review", "signed", "registered", "archived"):
                self.assertRedirects(self.client.post(detail, {"state": next_state}), detail)
            record = WorkflowRecord.objects.get(pk=int(detail.rstrip("/").split("/")[-1]))
            self.assertEqual(record.registration_number, f"ДОГ-{timezone.localdate().year}-{expected:06d}")
            self.assertEqual(record.events.count(), 5)
            self.assertTrue(all(event.actor == owner for event in record.events.all()))
        self.client.post(reverse("delete_version", args=[document.pk, version.pk]))
        self.assertTrue(Version.objects.filter(pk=version.pk).exists())
        rejected = self.client.post(create, data).url
        self.assertRedirects(self.client.post(rejected, {"state": "review"}), rejected)
        self.assertEqual(self.client.post(rejected, {"state": "rejected"}).status_code, 400)
        self.assertRedirects(self.client.post(rejected, {"state": "rejected", "comment": "Нужны правки"}), rejected)
        self.assertContains(self.client.get(rejected), "Нужны правки")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.post(detail, {"state": "review"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workflow_records")), detail)
