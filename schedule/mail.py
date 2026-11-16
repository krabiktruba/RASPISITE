"""Письма сайта.

Пока одно письмо: администратор сменил пользователю почту (логин). Оно уходит
и на старый, и на новый адрес, чтобы человек не остался без доступа
незаметно. Ошибка отправки не отменяет сохранённых изменений: сайт
сообщает о ней администратору.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _site():
    return getattr(settings, "SITE_NAME", "RASPISITE")


def send_email_change_notice(name, old_address, new_address):
    """Уведомляет оба адреса. Возвращает список адресов, куда не удалось."""
    site = _site()
    subject = f"{site}: изменена почта для входа"
    failed = []

    texts = (
        (
            old_address,
            f"Здравствуйте, {name}!\n\n"
            f"Администратор сайта {site} изменил вашу почту для входа:\n"
            f"было: {old_address}\n"
            f"стало: {new_address}\n\n"
            "С этого момента входите с новым адресом, пароль прежний. "
            "Если вы не просили об этом, обратитесь к администратору.",
        ),
        (
            new_address,
            f"Здравствуйте, {name}!\n\n"
            f"Этот адрес теперь ваш логин на сайте {site} "
            f"(раньше было {old_address}).\n"
            "Пароль прежний. Если вы не ожидали этого письма, "
            "обратитесь к администратору.",
        ),
    )

    for address, body in texts:
        try:
            send_mail(subject, body, None, [address])
        except Exception:  # сеть, ящик, настройки: сайт не должен падать
            logger.exception("Не удалось отправить письмо на %s", address)
            failed.append(address)

    return failed


def send_registration_code(address, code, minutes):
    """Письмо с кодом подтверждения почты. False, если отправить не удалось."""
    site = _site()

    try:
        send_mail(
            f"{site}: код подтверждения {code}",
            f"Ваш код подтверждения: {code}\n\n"
            f"Введите его на сайте {site}, чтобы завершить регистрацию. "
            f"Код действует {minutes} минут.\n\n"
            "Если вы не регистрировались, просто проигнорируйте письмо.",
            None,
            [address],
        )
    except Exception:
        logger.exception("Не удалось отправить код на %s", address)

        return False

    return True


def send_password_changed_notice(user):
    """Письмо «пароль изменён» после восстановления (ошибка не мешает)."""
    address = user.email or ""

    if "@" not in address:
        return False

    site = _site()

    try:
        send_mail(
            f"{site}: пароль изменён",
            f"Здравствуйте, {user.first_name or 'пользователь'}!\n\n"
            f"Пароль для входа на сайт {site} только что изменён.\n"
            "Если это сделали не вы, срочно восстановите пароль заново "
            "и сообщите администратору.",
            None,
            [address],
        )
    except Exception:
        logger.exception("Не удалось отправить письмо на %s", address)

        return False

    return True
