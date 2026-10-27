"""Вход по почте Gmail: проверка и приведение адреса к единому виду."""

import re

from django.core.exceptions import ValidationError

GMAIL_DOMAIN = "gmail.com"
# googlemail.com — старое имя того же почтового сервиса
GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}

MAX_ADDRESS_LENGTH = 254
MIN_NAME_LENGTH = 6
MAX_NAME_LENGTH = 30

_NAME_RE = re.compile(r"[a-z0-9.]+")


def normalize_gmail(value):
    """Возвращает адрес в едином виде или вызывает ValidationError.

    Единый вид: строчные буквы, без точек в имени (Gmail их не различает)
    и домен gmail.com. Так «Ivan.Petrov@Gmail.com» и «ivanpetrov@gmail.com»
    — один и тот же логин.
    """
    address = (value or "").strip().lower()

    if not address:
        raise ValidationError("Введите почту Gmail.", code="required")

    if len(address) > MAX_ADDRESS_LENGTH:
        raise ValidationError("Адрес слишком длинный.", code="too_long")

    if address.count("@") != 1:
        raise ValidationError(
            "Введите адрес вида имя@gmail.com.", code="format"
        )

    name, domain = address.split("@")

    if domain not in GMAIL_DOMAINS:
        raise ValidationError(
            "Вход возможен только с почтой Gmail (имя@gmail.com).",
            code="not_gmail",
        )

    if "+" in name:
        raise ValidationError(
            "В адресе Gmail не должно быть знака «+».", code="plus"
        )

    if not _NAME_RE.fullmatch(name):
        raise ValidationError(
            "Имя до @ может содержать только латинские буквы, цифры "
            "и точки.",
            code="characters",
        )

    if name.startswith(".") or name.endswith(".") or ".." in name:
        raise ValidationError(
            "Точки в адресе стоят неправильно.", code="dots"
        )

    name = name.replace(".", "")

    if not MIN_NAME_LENGTH <= len(name) <= MAX_NAME_LENGTH:
        raise ValidationError(
            f"Имя в адресе Gmail — от {MIN_NAME_LENGTH} до "
            f"{MAX_NAME_LENGTH} символов.",
            code="length",
        )

    return f"{name}@{GMAIL_DOMAIN}"
