"""Кабинеты: очистка названия и проверка совпадений.

Кабинет привязан к группе (Group.room), а не к паре: когда группа переезжает,
меняется одно поле. Исключения задаются на конкретной паре в расписании
(Day.room, «особый кабинет»): физкультура в зале, лекция в актовом.

Две группы ОДНОЙ СМЕНЫ с одним и тем же кабинетом по умолчанию — ошибка:
они бы занимали его одновременно. Исключения (особый кабинет у пары) этой
проверкой не охватываются, их ставят осознанно.
"""

import re

from django.core.exceptions import ValidationError

from .limits import ROOM_MAX

_ROOM_RE = re.compile(r"[\w][\w .,/№-]*", re.UNICODE)


def clean_room(value):
    """Название кабинета без лишних пробелов; пустая строка — кабинета нет."""
    room = " ".join((value or "").split())

    if not room:
        return ""

    if len(room) > ROOM_MAX:
        raise ValidationError(
            f"Кабинет: не больше {ROOM_MAX} знаков (сейчас {len(room)})."
        )

    if not _ROOM_RE.fullmatch(room):
        raise ValidationError(
            "Кабинет: буквы, цифры, пробел и знаки . , / - №."
        )

    return room


def room_key(room):
    """Ключ для сравнения: «204», « 204 » и «204» одинаковы, регистр не важен."""
    return " ".join((room or "").split()).casefold()


def group_room_conflict(room, shift_id, exclude_group_id=None):
    """Группа той же смены с таким же кабинетом или None."""
    from .models import Group

    if not room_key(room):
        return None

    candidates = Group.objects.filter(shift_id=shift_id).exclude(room="")

    if exclude_group_id:
        candidates = candidates.exclude(pk=exclude_group_id)

    wanted = room_key(room)

    for group in candidates.order_by("group_name", "group_id"):
        if room_key(group.room) == wanted:
            return group

    return None


def conflict_text(room, group):
    return (
        f"Кабинет {room} уже занят группой {group.group_name} этой же "
        "смены. Выберите другой или для нужных пар задайте особый кабинет."
    )
