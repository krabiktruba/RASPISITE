"""Перевод существующих учётных записей на вход по почте Gmail.

    python manage.py set_login --list
    python manage.py set_login СТАРЫЙ_ЛОГИН имя@gmail.com
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from schedule.gmail import normalize_gmail


def _can_log_in(username):
    try:
        return normalize_gmail(username) == username
    except ValidationError:
        return False


class Command(BaseCommand):
    help = (
        "Меняет логин пользователя на почту Gmail "
        "(или показывает тех, кто войти не сможет: --list)."
    )

    def add_arguments(self, parser):
        parser.add_argument("old", nargs="?", help="Текущий логин")
        parser.add_argument("new", nargs="?", help="Почта Gmail")
        parser.add_argument(
            "--list",
            action="store_true",
            help="Показать пользователей, чей логин не является Gmail",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        if options["list"]:
            names = [
                user.username
                for user in User.objects.order_by("username")
                if not _can_log_in(user.username)
            ]

            if not names:
                self.stdout.write("Все логины уже в виде Gmail.")

            for name in names:
                self.stdout.write(name)

            return

        if not options["old"] or not options["new"]:
            raise CommandError(
                "Укажите старый логин и новую почту: "
                "set_login СТАРЫЙ_ЛОГИН имя@gmail.com"
            )

        try:
            new = normalize_gmail(options["new"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        try:
            user = User.objects.get(username=options["old"])
        except User.DoesNotExist:
            raise CommandError(
                f"Пользователь «{options['old']}» не найден."
            )

        if User.objects.filter(username__iexact=new).exclude(
            pk=user.pk
        ).exists():
            raise CommandError(f"Логин {new} уже занят.")

        user.username = new
        user.email = new
        user.save(update_fields=["username", "email"])

        self.stdout.write(
            f"Готово: «{options['old']}» теперь входит как {new}."
        )
