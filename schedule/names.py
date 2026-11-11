"""Как показывать ФИО в списках и ячейках.

Полное ФИО хранится как «Фамилия Имя Отчество». В списках, таблицах и
ячейках расписания его сокращают до «Отчество И. И.»: отчество целиком,
затем инициал имени и инициал фамилии. Если отчества нет — «Фамилия И.».
Полное ФИО при этом видно во всплывающей подсказке (title).

Крупные заголовки страниц и пункты выпадающих списков показывают ФИО
целиком, их это сокращение не касается.

Имена, которые уже записаны сокращённо («Иванов И. И.») или не разбираются
на 2–3 слова, остаются как есть.
"""

from django.utils.html import format_html


def _is_initial(word):
    return "." in word or len(word) == 1


def split_name(full):
    """Разбирает «Фамилия Имя [Отчество]»: (фамилия, имя, отчество).

    Отчества нет — третий элемент пустой. None, если имя не разбирается
    (одно слово, больше трёх слов или уже сокращено).
    """
    words = (full or "").split()

    if not 2 <= len(words) <= 3:
        return None

    if any(_is_initial(word) for word in words):
        return None

    last, first = words[0], words[1]
    patronymic = words[2] if len(words) == 3 else ""

    return last, first, patronymic


def short_name(full):
    """«Фамилия Имя Отчество» → «Отчество И. И.»; без отчества — «Фамилия И.»."""
    full = " ".join((full or "").split())
    parts = split_name(full)

    if parts is None:
        return full

    last, first, patronymic = parts

    if patronymic:
        return f"{patronymic} {first[0]}. {last[0]}."

    return f"{last} {first[0]}."


def person_html(full):
    """Короткое имя с полным ФИО в подсказке (готовый HTML)."""
    full = " ".join((full or "").split())
    short = short_name(full)

    if short == full:
        return format_html("{}", full)

    return format_html(
        '<span class="person-name" title="{}">{}</span>', full, short
    )
