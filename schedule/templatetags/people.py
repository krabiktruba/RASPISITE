from django import template

from ..names import person_html, short_name

register = template.Library()


@register.filter
def person(value):
    """ФИО в списке: «Отчество И. И.», полное имя — в подсказке."""
    return person_html(value)


@register.filter
def short(value):
    """Только текст «Отчество И. И.» (для мест, где HTML нельзя)."""
    return short_name(value)


@register.filter
def room_label(value):
    """«204» → «каб. 204»; названия вроде «Спортзал» остаются как есть."""
    room = (value or "").strip()

    if room[:1].isdigit():
        return f"каб. {room}"

    return room
