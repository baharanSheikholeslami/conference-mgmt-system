from django.core.management.base import BaseCommand

from workflow import statemachine


class Command(BaseCommand):
    help = "خروجی مارک‌داون (جدول گذارها + نمودار Mermaid) از روی ماشین‌حالت کد"

    def handle(self, *args, **options):
        self.stdout.write("## جدول گذارها\n")
        self.stdout.write(statemachine.to_markdown_table())
        self.stdout.write("\n\n## نمودار وضعیت\n\n```mermaid")
        self.stdout.write(statemachine.to_mermaid())
        self.stdout.write("```")
