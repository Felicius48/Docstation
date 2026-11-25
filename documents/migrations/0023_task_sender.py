import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def populate_sender(apps, schema_editor):
    tasks = apps.get_model("documents", "WorkflowRecord").objects.using(schema_editor.connection.alias)
    versions = apps.get_model("documents", "Version").objects.using(schema_editor.connection.alias)
    tasks.update(sender_id=Subquery(versions.filter(pk=OuterRef("version_id")).values("document__owner_id")))


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0022_workflowrecord_due_date_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="workflowrecord", name="sender",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="sent_tasks", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(populate_sender, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="workflowrecord", name="sender",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name="sent_tasks", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="workflowrecord", name="version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    to="documents.version"),
        ),
    ]
