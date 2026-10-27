import getpass
import os

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role, User
from apps.audit.services import log_action


class Command(BaseCommand):
    help = (
        "Create the first institution administrator (an ordinary ADMIN account, not a Django superuser). "
        "The password is read interactively, or from INITIAL_ADMIN_PASSWORD for automated deployments."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", required=True)
        parser.add_argument("--last-name", required=True)

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise CommandError(f"A user with email {email} already exists.")
        password = os.environ.get("INITIAL_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        candidate = User(email=email, first_name=options["first_name"], last_name=options["last_name"])
        try:
            password_validation.validate_password(password, candidate)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        user = User.objects.create_user(
            email=email, password=password, role=Role.ADMIN, first_name=options["first_name"],
            last_name=options["last_name"],
        )
        log_action("user.created", instance=user, summary=f"Initial administrator {email} created from the command line")
        self.stdout.write(self.style.SUCCESS(f"Administrator {email} created."))
