"""Разовые правки расписания: отмена, замена и перенос пары на дату.

Постоянное недельное расписание (таблица Day) не меняется. Правка
(ScheduleChange) привязана к дате и описывает исключение именно для неё:

    CANCEL  — пары в этот день не будет;
    REPLACE — пара проходит, но ведёт другой преподаватель и/или у неё
              другое название;
    MOVE    — пара уходит на другую дату и другой номер.

Правка находит нужное занятие по тройке «дата + пара + номер пары в этот
день». Модуль не отрисовывает страницы: он лишь накладывает правки на
обычное расписание и проверяет, что преподаватель не окажется в двух местах.
"""

from django.db.models import Q

from .dates import short_label
from .models import Day, ScheduleChange

CANCEL = "CANCEL"
MOVE = "MOVE"
REPLACE = "REPLACE"

CHANGE_TYPES = (CANCEL, MOVE, REPLACE)

# Состояние строки расписания (используется шаблонами и стилями)
STATUS_CANCEL = "cancel"
STATUS_REPLACE = "replace"
STATUS_REPLACE_IN = "replace-in"
STATUS_REPLACE_OUT = "replace-out"
STATUS_MOVED_FROM = "moved-from"
STATUS_MOVED_TO = "moved-to"

STATUS_LABELS = {
    STATUS_CANCEL: "Отмена",
    STATUS_REPLACE: "Замена",
    STATUS_REPLACE_IN: "Замена",
    STATUS_REPLACE_OUT: "Заменён",
    STATUS_MOVED_FROM: "Перенос",
    STATUS_MOVED_TO: "Перенос",
}

FIRST_PAIR = 1
LAST_PAIR = 6


# ---------------------------------------------------------------------------
# Загрузка и поиск правок
# ---------------------------------------------------------------------------

def load_changes(first, last):
    """Правки, у которых исходная или новая дата попадает в [first, last]."""
    return list(
        ScheduleChange.objects.filter(
            Q(calendar__date__range=(first, last))
            | Q(new_calendar__date__range=(first, last)),
            change_type__in=CHANGE_TYPES,
        ).select_related(
            "calendar",
            "new_calendar",
            "new_teacher",
            "pair",
            "pair__teacher",
            "pair__group",
            "pair__group__shift",
        )
    )


def index_changes(changes, skip_change_id=None):
    """{(исходная дата, pair_id): [правки]} — для быстрого поиска."""
    index = {}

    for change in changes:
        if change.pk == skip_change_id:
            continue

        index.setdefault(
            (change.calendar.date, change.pair_id), []
        ).append(change)

    return index


def find_change(index, day, pair_id, number):
    """Правка, относящаяся к занятию (дата, пара, номер пары), или None.

    Правка с точным номером важнее правки без номера (такие остались бы
    от ручного ввода в /admin/).
    """
    candidates = index.get((day, pair_id), [])

    for change in candidates:
        if change.old_pair_number == number:
            return change

    for change in candidates:
        if change.old_pair_number is None:
            return change

    return None


# ---------------------------------------------------------------------------
# Описание занятия для страницы
# ---------------------------------------------------------------------------

def _lesson(subject, teacher, status="", note="", change=None,
            editable=True, group=None, number=None):
    return {
        "subject": subject,
        "teacher": teacher,
        "status": status,
        "status_label": STATUS_LABELS.get(status, ""),
        "note": note,
        "change": change,
        "editable": editable,
        "group": group,
        "number": number,
    }


def _move_target(change):
    if change.new_calendar_id is None:
        return ""

    text = short_label(change.new_calendar.date)

    if change.new_pair_number:
        text += f", пара {change.new_pair_number}"

    return text


def describe_original(pair, change):
    """Занятие в его исходном слоте с учётом правки (если она есть)."""
    subject = pair.pair_name
    teacher = pair.teacher.teacher_name

    if change is None:
        return _lesson(subject, teacher)

    if change.change_type == CANCEL:
        return _lesson(
            subject, teacher, STATUS_CANCEL,
            "Пара отменена", change,
        )

    if change.change_type == MOVE:
        return _lesson(
            subject, teacher, STATUS_MOVED_FROM,
            f"Перенесена на {_move_target(change)}", change,
        )

    # REPLACE
    notes = []

    if change.new_teacher_id:
        notes.append(f"вместо {teacher}")

    if change.new_pair_name:
        notes.append(f"было: {subject}")

    return _lesson(
        change.new_pair_name or subject,
        change.new_teacher.teacher_name if change.new_teacher_id else teacher,
        STATUS_REPLACE,
        "Замена: " + ", ".join(notes) if notes else "Замена",
        change,
    )


def describe_moved_in(change):
    """Занятие в новом слоте, куда его перенесли."""
    note = f"Перенесена с {short_label(change.calendar.date)}"

    if change.old_pair_number:
        note += f", пара {change.old_pair_number}"

    return _lesson(
        change.pair.pair_name,
        change.pair.teacher.teacher_name,
        STATUS_MOVED_TO,
        note,
        change,
        editable=False,
    )


def _is_move_target(change, day):
    return (
        change.change_type == MOVE
        and change.new_calendar_id is not None
        and change.new_calendar.date == day
        and bool(change.new_pair_number)
    )


# ---------------------------------------------------------------------------
# Расписание группы
# ---------------------------------------------------------------------------

def group_days_by_number(group_id, weekday_id):
    """Постоянное расписание группы на день недели: {номер: Day}."""
    return {
        record.pair_number: record
        for record in Day.objects.filter(
            group_id=group_id,
            weekday_id=weekday_id,
            pair_number__range=(FIRST_PAIR, LAST_PAIR),
        ).select_related("pair", "pair__teacher")
    }


def resolve_group_day(group_id, day, days_by_number, changes,
                      skip_change_id=None):
    """Расписание группы на дату: {номер пары: занятие или None}.

    days_by_number — постоянное расписание группы на этот день недели,
    changes — правки, затрагивающие эту дату (см. load_changes).
    """
    index = index_changes(changes, skip_change_id)
    slots = {number: None for number in range(FIRST_PAIR, LAST_PAIR + 1)}

    for number, record in days_by_number.items():
        if not FIRST_PAIR <= number <= LAST_PAIR:
            continue

        change = find_change(index, day, record.pair_id, number)
        slots[number] = describe_original(record.pair, change)
        slots[number]["number"] = number

    # Перенесённые на эту дату пары своей группы
    for change in changes:
        if change.pk == skip_change_id or not _is_move_target(change, day):
            continue

        if change.pair.group_id != group_id:
            continue

        number = change.new_pair_number

        if FIRST_PAIR <= number <= LAST_PAIR:
            slots[number] = describe_moved_in(change)
            slots[number]["number"] = number

    return slots


def slot_is_free(lesson):
    """В слот можно перенести пару: он пуст, отменён или уже освобождён."""
    return lesson is None or lesson["status"] in (
        STATUS_CANCEL, STATUS_MOVED_FROM,
    )


# ---------------------------------------------------------------------------
# Расписание преподавателя
# ---------------------------------------------------------------------------

def _teacher_lesson(record, change, teacher_id):
    """Занятие преподавателя в его слоте с учётом правки."""
    pair = record.pair

    lesson = describe_original(pair, change)

    if (
        change is not None
        and change.change_type == REPLACE
        and change.new_teacher_id
        and change.new_teacher_id != teacher_id
    ):
        lesson["status"] = STATUS_REPLACE_OUT
        lesson["status_label"] = STATUS_LABELS[STATUS_REPLACE_OUT]
        lesson["note"] = f"Проведёт: {change.new_teacher.teacher_name}"

    lesson["group"] = record.group
    lesson["number"] = record.pair_number

    return lesson


def teacher_lessons(teacher_id, dates, base_records, changes):
    """Занятия преподавателя по датам с учётом правок: {дата: [занятия]}.

    base_records — постоянные занятия этого преподавателя (Day с
    group, group__shift, pair, pair__teacher), dates — показанные даты.
    """
    index = index_changes(changes)
    result = {day: [] for day in dates}

    by_weekday = {}

    for record in base_records:
        by_weekday.setdefault(record.weekday_id, []).append(record)

    for day in dates:
        for record in by_weekday.get(day.isoweekday(), []):
            change = find_change(
                index, day, record.pair_id, record.pair_number
            )
            result[day].append(_teacher_lesson(record, change, teacher_id))

    for change in changes:
        original_day = change.calendar.date

        # Преподавателя назначили вести чужую пару
        if (
            change.change_type == REPLACE
            and change.new_teacher_id == teacher_id
            and change.pair.teacher_id != teacher_id
            and original_day in result
        ):
            slots = Day.objects.filter(
                pair_id=change.pair_id,
                weekday_id=original_day.isoweekday(),
                pair_number__range=(FIRST_PAIR, LAST_PAIR),
            ).select_related("group", "group__shift")

            if change.old_pair_number is not None:
                slots = slots.filter(pair_number=change.old_pair_number)

            for record in slots:
                lesson = describe_original(record.pair, change)
                lesson["status"] = STATUS_REPLACE_IN
                lesson["status_label"] = STATUS_LABELS[STATUS_REPLACE_IN]
                lesson["note"] = (
                    f"Замена вместо {change.pair.teacher.teacher_name}"
                )
                lesson["editable"] = False
                lesson["group"] = record.group
                lesson["number"] = record.pair_number
                result[original_day].append(lesson)

        # Пара преподавателя перенесена на показанную дату
        if (
            change.change_type == MOVE
            and change.new_calendar_id is not None
            and change.new_pair_number
            and change.pair.teacher_id == teacher_id
            and change.new_calendar.date in result
        ):
            lesson = describe_moved_in(change)
            lesson["group"] = change.pair.group
            lesson["number"] = change.new_pair_number
            result[change.new_calendar.date].append(lesson)

    return result


# ---------------------------------------------------------------------------
# Проверка: преподаватель не может быть в двух местах сразу
# ---------------------------------------------------------------------------

def teacher_conflict(teacher_id, day, number, changes, skip_change_id=None):
    """Название группы, где преподаватель уже занят в этот день и на этой
    паре, или None, если он свободен. Правки учитываются."""
    others = [
        change for change in changes
        if change.pk != skip_change_id
    ]
    index = index_changes(others)

    records = Day.objects.filter(
        weekday_id=day.isoweekday(),
        pair_number=number,
        pair__teacher_id=teacher_id,
    ).select_related("group", "pair")

    for record in records:
        change = find_change(index, day, record.pair_id, number)

        if change is not None:
            if change.change_type in (CANCEL, MOVE):
                continue

            if (
                change.change_type == REPLACE
                and change.new_teacher_id
                and change.new_teacher_id != teacher_id
            ):
                continue

        return record.group.group_name

    for change in others:
        # Его уже поставили на замену в этот слот
        if (
            change.change_type == REPLACE
            and change.new_teacher_id == teacher_id
            and change.calendar.date == day
        ):
            same_slot = (
                change.old_pair_number == number
                or (
                    change.old_pair_number is None
                    and Day.objects.filter(
                        pair_id=change.pair_id,
                        weekday_id=day.isoweekday(),
                        pair_number=number,
                    ).exists()
                )
            )

            if same_slot:
                return change.pair.group.group_name

        # Сюда уже перенесена его пара
        if (
            _is_move_target(change, day)
            and change.new_pair_number == number
            and change.pair.teacher_id == teacher_id
        ):
            return change.pair.group.group_name

    return None
