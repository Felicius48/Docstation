from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Grant administrator rights to an existing active account."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        username = options["username"]
        changed = get_user_model().objects.filter(username=username, is_active=True).update(
            is_staff=True, is_superuser=True,
        )
        if not changed:
            raise CommandError("Активная учётная запись не найдена.")
        self.stdout.write(self.style.SUCCESS(f"Права администратора назначены: {username}"))
