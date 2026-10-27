"""Изменение и удаление записей в Кузне.

Модуль не отрисовывает страницы и не знает про формы: он перечисляет
записи для выбора, находит нужную, проверяет, можно ли её удалить, и
готовит предупреждение о том, что удалится вместе с ней.

Четыре раздела Кузни: теги, типы тегов, группы, пары (преподавателей
добавляют на странице «Пользователи»).
"""

from .dates import WEEKDAY_NAMES
from .models import (
    Day,
    Group,
    Pair,
    Profile,
    ScheduleChange,
    Tag,
    TagType,
    Teacher,
)

EDIT_KEYS = ("tag_type", "tag", "group", "pair")

# ---------------------------------------------------------------------------
# Список записей и поиск
# ---------------------------------------------------------------------------

def _queryset(key):
    if key == "tag_type":
        return TagType.objects.order_by("tag_type_name", "tag_type_id")

    if key == "tag":
        return Tag.objects.select_related("tag_type").order_by(
            "tag_type__tag_type_name", "tag_name", "tag_id"
        )

    if key == "group":
        return Group.objects.order_by("group_name", "group_id")

    return Pair.objects.select_related("group", "teacher").order_by(
        "group__group_name", "pair_name", "pair_id"
    )


def edit_items(key):
    """Все записи раздела в порядке показа."""
    return list(_queryset(key))


def item_label(key, obj):
    """Подпись записи в списке выбора."""
    if key == "tag_type":
        return obj.tag_type_name

    if key == "tag":
        return f"{obj.tag_type.tag_type_name}: {obj.tag_name}"

    if key == "group":
        return obj.group_name

    return (
        f"{obj.group.group_name} · {obj.pair_name} · "
        f"{obj.teacher.teacher_name}"
    )


def find_item(key, raw_id):
    """Запись по номеру из адреса или формы; None, если её нет."""
    try:
        pk = int(raw_id)
    except (TypeError, ValueError):
        return None

    if pk <= 0 or pk > 2147483647:
        return None

    return _queryset(key).filter(pk=pk).first()


# ---------------------------------------------------------------------------
# Удаление: что мешает и что удалится вместе с записью
# ---------------------------------------------------------------------------

def delete_blocker(key, obj):
    """Текст причины, по которой запись нельзя удалить, или пустая строка."""
    if key == "tag_type":
        count = Tag.objects.filter(tag_type=obj).count()

        if count:
            return (
                f"Нельзя удалить тип «{obj.tag_type_name}»: у него есть "
                f"теги ({count}). Сначала удалите их или переведите "
                f"на другой тип."
            )

    return ""


def _parts(pairs):
    """«подпись — число» только для ненулевых значений."""
    return [f"{label} — {count}" for label, count in pairs if count]


def delete_warning(key, obj):
    """Что произойдёт при удалении (для подтверждения). Может быть пустым."""
    if key == "tag":
        parts = _parts([
            ("преподаватели", Teacher.objects.filter(teacher_tag=obj).count()),
            ("группы", Group.objects.filter(group_tag=obj).count()),
        ])

        if parts:
            return "Тег будет снят у: " + ", ".join(parts) + "."

    if key == "group":
        removed = _parts([
            ("пары", Pair.objects.filter(group=obj).count()),
            ("занятия в расписании", Day.objects.filter(group=obj).count()),
        ])
        students = Profile.objects.filter(group=obj).count()
        text = []

        if removed:
            text.append("Вместе с группой удалятся: " + ", ".join(removed) + ".")

        if students:
            text.append(f"Студенты ({students}) останутся без группы.")

        return " ".join(text)

    if key == "pair":
        parts = _parts([
            ("занятия в расписании", Day.objects.filter(pair=obj).count()),
            ("разовые правки", ScheduleChange.objects.filter(
                pair=obj).count()),
        ])

        if parts:
            return "Вместе с парой удалятся: " + ", ".join(parts) + "."

    return ""


# ---------------------------------------------------------------------------
# Смена преподавателя у пары
# ---------------------------------------------------------------------------

def pair_teacher_conflict(pair, teacher):
    """Текст ошибки, если новый преподаватель окажется в двух местах сразу.

    Проверяется постоянное расписание: каждое занятие этой пары не должно
    совпасть по дню и номеру с другой парой того же преподавателя.
    """
    for slot in Day.objects.filter(pair=pair):
        other = Day.objects.filter(
            weekday_id=slot.weekday_id,
            pair_number=slot.pair_number,
            pair__teacher=teacher,
        ).exclude(pair=pair).select_related("group").first()

        if other is not None:
            weekday = WEEKDAY_NAMES.get(slot.weekday_id, slot.weekday_id)

            return (
                f"{teacher.teacher_name} уже ведёт занятие у группы "
                f"{other.group.group_name} ({weekday}, пара "
                f"{slot.pair_number}). Сначала измените расписание."
            )

    return ""
