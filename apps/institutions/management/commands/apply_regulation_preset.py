from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import diff, log_action, snapshot
from apps.institutions.models import AcademicRegulation
from apps.institutions.regulation_presets import PRESETS
from apps.institutions.serializers import AcademicRegulationSerializer


class Command(BaseCommand):
    help = "Apply a ready-made academic regulations preset (see apps/institutions/regulation_presets.py)."

    def add_arguments(self, parser):
        parser.add_argument("preset", nargs="?", help=f"One of: {', '.join(PRESETS)}")
        parser.add_argument("--list", action="store_true", help="List the available presets.")

    def handle(self, *args, **options):
        if options["list"] or not options["preset"]:
            for key, preset in PRESETS.items():
                self.stdout.write(f"{key}\n    {preset['name']}\n    {preset['summary']}\n")
            return
        preset = PRESETS.get(options["preset"])
        if preset is None:
            raise CommandError(f"Unknown preset. Choose one of: {', '.join(PRESETS)}")
        regulation = AcademicRegulation.load()
        before = snapshot(regulation)
        serializer = AcademicRegulationSerializer(regulation, data=preset["values"], partial=True)
        if not serializer.is_valid():
            raise CommandError(f"The preset is not valid: {serializer.errors}")
        serializer.save()
        changed_before, changed_after = diff(before, snapshot(regulation))
        log_action("regulations.updated", instance=regulation, summary=f"Applied preset {options['preset']}",
                   before=changed_before, after=changed_after)
        self.stdout.write(self.style.SUCCESS(f"Applied '{preset['name']}'."))
