"""Занятия для дневника: что проходит в группе в дату и кто его ведёт.

Занятие определяется так же, как у разовых правок расписания: дата + пара
+ номер пары в этот день. Постоянное расписание берётся из таблицы Day,
поверх него накладываются правки (отмена, замена, перенос) — для этого
используются функции из schedule.changes, чтобы дневник и расписание не
расходились.

Ставить оценки можно только за состоявшееся занятие: отменённое, ушедшее
по переносу, воскресенье и праздник в дневнике колонок не имеют.
"""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count

from schedule.access import own_teacher_id
from schedule.changes import (
    FIRST_PAIR,
    LAST_PAIR,
    STATUS_CANCEL,
    STATUS_MOVED_FROM,
    STATUS_MOVED_TO,
    STATUS_REPLACE,
    _is_move_target,
    load_changes,
    resolve_group_day,
)
from schedule.models import (
    ROLE_STUDENT,
    Calendar,
    Day,
    Group,
    Pair,
    PairGroup,
    Profile,
    ScheduleChange,
)

from .models import Mark

# Состояния, при которых занятия в слоте нет
NOT_HELD = (STATUS_CANCEL, STATUS_MOVED_FROM)


def holiday_dates(first, last):
    """Праздничные даты из таблицы Calendar в промежутке [first, last]."""
    return set(
        Calendar.objects.filter(
            date__range=(first, last), is_holiday=True
        ).values_list("date", flat=True)
    )


def is_school_day(day, holidays):
    """Учебный день: не воскресенье и не праздник."""
    return day.isoweekday() != 7 and day not in holidays


def _actual_teacher_id(lesson, pair):
    """Кто ведёт занятие на самом деле (с учётом замены преподавателя)."""
    change = lesson["change"]

    if (
        lesson["status"] == STATUS_REPLACE
        and change is not None
        and change.new_teacher_id
    ):
        return change.new_teacher_id

    return pair.teacher_id


def _pair_of(lesson, number, days_by_number):
    if lesson["status"] == STATUS_MOVED_TO:
        return lesson["change"].pair

    return days_by_number[number].pair


def _group_of(lesson, number, days_by_number):
    """Группа занятия: у перенесённой пары — из правки, иначе из расписания."""
    if lesson["status"] == STATUS_MOVED_TO:
        return lesson["change"].group

    return days_by_number[number].group


def _describe(lesson, number, days_by_number, day):
    """Обычный словарь занятия для дневника (с id пары и преподавателя)."""
    pair = _pair_of(lesson, number, days_by_number)
    group = _group_of(lesson, number, days_by_number)

    return {
        "day": day,
        "number": number,
        "pair": pair,
        "pair_id": pair.pk,
        "group": group,
        "group_id": group.pk,
        "subject": lesson["subject"],
        "teacher": lesson["teacher"],
        "teacher_id": _actual_teacher_id(lesson, pair),
        "status": lesson["status"],
        "status_label": lesson["status_label"],
        "note": lesson["note"],
        "held": lesson["status"] not in NOT_HELD,
    }


def _resolve(group_id, day, days_by_number, changes):
    """Проведённые занятия группы в день: список по номерам пар."""
    slots = resolve_group_day(group_id, day, days_by_number, changes)
    result = []

    for number in range(FIRST_PAIR, LAST_PAIR + 1):
        lesson = slots.get(number)

        if lesson is None or lesson["status"] in NOT_HELD:
            continue

        result.append(_describe(lesson, number, days_by_number, day))

    return result


def _days_by_group(group_ids, weekday_id):
    """Постоянное расписание групп на день недели: {группа: {номер: Day}}."""
    result = defaultdict(dict)

    records = Day.objects.filter(
        group_id__in=group_ids,
        weekday_id=weekday_id,
        pair_number__range=(FIRST_PAIR, LAST_PAIR),
    ).select_related("pair", "pair__teacher", "group")

    for record in records:
        result[record.group_id][record.pair_number] = record

    return result


def teacher_day_lessons(teacher_id, day):
    """Занятия, которые преподаватель ведёт в этот день, по порядку.

    В каждом занятии есть число студентов и число уже выставленных записей.
    Замены учитываются: замещающий преподаватель видит занятие у себя,
    а заменённый — нет.
    """
    if not is_school_day(day, holiday_dates(day, day)):
        return []

    weekday_id = day.isoweekday()
    changes = load_changes(day, day)

    group_ids = set(
        Day.objects.filter(
            weekday_id=weekday_id, pair__teacher_id=teacher_id
        ).values_list("group_id", flat=True)
    )

    for change in changes:
        if change.new_teacher_id == teacher_id:
            group_ids.add(change.group_id)

        if _is_move_target(change, day) and change.pair.teacher_id == teacher_id:
            group_ids.add(change.group_id)

    if not group_ids:
        return []

    days = _days_by_group(group_ids, weekday_id)
    lessons = []

    for group_id in group_ids:
        for lesson in _resolve(group_id, day, days.get(group_id, {}), changes):
            if lesson["teacher_id"] == teacher_id:
                lessons.append(lesson)

    lessons.sort(key=lambda lesson: (lesson["number"], lesson["group"].group_name))
    attach_progress(lessons, day)

    return lessons


def attach_progress(lessons, day):
    """Дописывает занятиям students и marked — сколько студентов и записей."""
    if not lessons:
        return

    group_ids = {lesson["group_id"] for lesson in lessons}
    pair_ids = {lesson["pair_id"] for lesson in lessons}

    students = {
        row["group"]: row["total"]
        for row in Profile.objects.filter(
            group_id__in=group_ids, role=ROLE_STUDENT, user__is_staff=False
        ).values("group").annotate(total=Count("pk"))
    }

    # Одна пара бывает у нескольких групп в один слот: считаем по группам
    marked = {
        (row["student__profile__group_id"], row["pair_id"],
         row["pair_number"]): row["total"]
        for row in Mark.objects.filter(
            lesson_date=day, pair_id__in=pair_ids
        ).values(
            "student__profile__group_id", "pair_id", "pair_number"
        ).annotate(total=Count("pk"))
    }

    for lesson in lessons:
        lesson["students"] = students.get(lesson["group_id"], 0)
        lesson["marked"] = marked.get(
            (lesson["group_id"], lesson["pair_id"], lesson["number"]), 0
        )


def week_lessons(group_id, monday):
    """Проведённые занятия группы за неделю: [(день, [занятия])] пн–сб."""
    saturday = monday + timedelta(days=5)
    holidays = holiday_dates(monday, saturday)
    changes = load_changes(monday, saturday)

    by_weekday = defaultdict(dict)

    for record in Day.objects.filter(
        group_id=group_id,
        pair_number__range=(FIRST_PAIR, LAST_PAIR),
    ).select_related("pair", "pair__teacher", "group"):
        by_weekday[record.weekday_id][record.pair_number] = record

    week = []

    for offset in range(6):
        day = monday + timedelta(days=offset)
        lessons = []

        if is_school_day(day, holidays):
            lessons = _resolve(
                group_id, day, by_weekday.get(day.isoweekday(), {}), changes
            )

        week.append((day, lessons))

    return week


def can_grade(user, lesson):
    """Может ли пользователь ставить оценки за занятие.

    Администратор — любые; преподаватель — только те, которые ведёт сам
    (в том числе по замене), то есть оценки за чужое занятие ставить нельзя.
    """
    if not user.is_authenticated:
        return False

    if user.is_staff:
        return True

    teacher_id = own_teacher_id(user)

    return bool(teacher_id) and teacher_id == lesson["teacher_id"]


def teaches_in_group(teacher_id, group_id):
    """Ведёт ли преподаватель что-то в группе: свою пару или как замена."""
    if not teacher_id:
        return False

    return (
        Pair.objects.filter(groups=group_id, teacher_id=teacher_id).exists()
        or ScheduleChange.objects.filter(
            new_teacher_id=teacher_id, group_id=group_id
        ).exists()
    )


def teacher_group_ids(teacher_id):
    """Группы преподавателя: куратор, ведёт пару или заменял."""
    if not teacher_id:
        return set()

    ids = set(
        Group.objects.filter(group_curator_id=teacher_id)
        .values_list("pk", flat=True)
    )
    ids.update(
        PairGroup.objects.filter(pair__teacher_id=teacher_id)
        .values_list("group_id", flat=True)
    )
    ids.update(
        ScheduleChange.objects.filter(new_teacher_id=teacher_id)
        .exclude(group_id=None)
        .values_list("group_id", flat=True)
    )

    return ids
