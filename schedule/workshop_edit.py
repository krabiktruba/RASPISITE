"""Изменение и удаление записей в Кузне.

Модуль не отрисовывает страницы и не знает про формы: он перечисляет
записи для выбора, находит нужную, проверяет, можно ли её удалить, и
готовит предупреждение о том, что удалится вместе с ней.

Пять разделов Кузни: теги групп, теги преподавателей, группы, пары и семестры
(преподавателей добавляют на странице «Пользователи»).
"""

from .dates import WEEKDAY_NAMES
from .models import (
    Day,
    Group,
    GroupTag,
    Pair,
    PairGroup,
    Profile,
    ScheduleChange,
    Semester,
    Teacher,
    TeacherTag,
)

EDIT_KEYS = ("group_tag", "teacher_tag", "group", "pair", "semester")

# Разделы-теги: у каждого своя таблица
TAG_MODELS = {"group_tag": GroupTag, "teacher_tag": TeacherTag}

# ---------------------------------------------------------------------------
# Список записей и поиск
# ---------------------------------------------------------------------------

def _queryset(key):
    if key in TAG_MODELS:
        return TAG_MODELS[key].objects.order_by("tag_name", "tag_id")

    if key == "group":
        return Group.objects.order_by("group_name", "group_id")

    if key == "semester":
        return Semester.objects.order_by("semester_number")

    return Pair.objects.select_related("teacher").order_by(
        "pair_name", "teacher__teacher_name", "pair_id"
    )


def edit_items(key):
    """Все записи раздела в порядке показа."""
    return list(_queryset(key))


def item_label(key, obj):
    """Подпись записи в списке выбора."""
    if key in TAG_MODELS:
        return obj.tag_name

    if key == "group":
        return obj.group_name

    if key == "semester":
        return (
            f"{obj.semester_number} семестр · "
            f"{obj.start_date:%d.%m.%Y} — {obj.end_date:%d.%m.%Y}"
        )

    return f"{obj.pair_name} · {obj.teacher.teacher_name}"


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
    # Сейчас ничто не мешает удалению: остальное удаляется с предупреждением
    return ""


def _marks_of(pair):
    """Сколько записей дневника связано с парой (приложение diary)."""
    from django.apps import apps

    return apps.get_model("diary", "Mark").objects.filter(pair=pair).count()


def _parts(pairs):
    """«подпись — число» только для ненулевых значений."""
    return [f"{label} — {count}" for label, count in pairs if count]


def delete_warning(key, obj):
    """Что произойдёт при удалении (для подтверждения). Может быть пустым."""
    if key == "group_tag":
        count = Group.objects.filter(group_tag=obj).count()

        if count:
            return f"Тег будет снят у групп: {count}."

    if key == "teacher_tag":
        count = Teacher.objects.filter(teacher_tag=obj).count()

        if count:
            return f"Тег будет снят у преподавателей: {count}."

    if key == "semester":
        return "Оценки и расписание не удаляются: пропадёт только период."

    if key == "group":
        removed = _parts([
            ("занятия в расписании", Day.objects.filter(group=obj).count()),
            ("разовые правки", ScheduleChange.objects.filter(
                group=obj).count()),
            ("привязки пар", PairGroup.objects.filter(group=obj).count()),
        ])
        students = Profile.objects.filter(group=obj).count()
        text = []

        if removed:
            text.append("Вместе с группой удалятся: " + ", ".join(removed) + ".")
            text.append("Сами пары останутся.")

        if students:
            text.append(f"Студенты ({students}) останутся без группы.")

        return " ".join(text)

    if key == "pair":
        parts = _parts([
            ("занятия в расписании", Day.objects.filter(pair=obj).count()),
            ("разовые правки", ScheduleChange.objects.filter(
                pair=obj).count()),
            ("записи дневника", _marks_of(obj)),
        ])

        if parts:
            return "Вместе с парой удалятся: " + ", ".join(parts) + "."

    return ""


# ---------------------------------------------------------------------------
# Смена преподавателя у пары
# ---------------------------------------------------------------------------

def pair_teacher_conflict(pair, teacher):
    """Текст предупреждения, если новый преподаватель окажется в двух местах.

    Проверяется постоянное расписание: каждое занятие этой пары не должно
    совпасть по дню и номеру с другой парой того же преподавателя. Это только
    предупреждение: изменение всё равно сохраняется.
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
                f"{teacher.teacher_name} в это же время ведёт занятие у группы "
                f"{other.group.group_name} ({weekday}, пара "
                f"{slot.pair_number}). Проверьте расписание."
            )

    return ""
