"""Журнал: оценки студентов нескольких групп за период (только просмотр).

Оценки ставятся в дневнике группы; журнал их только показывает: строки —
студенты выбранных групп, столбцы — проведённые занятия за период.

Период задаёт один из режимов:
  • «одна неделя» (по умолчанию, текущая; стрелки ← →);
  • курсы (курс N = семестры 2N-1 и 2N);
  • семестры (даты задаёт администратор в Кузне).
Курс и семестр взаимно исключают друг друга: если выбраны оба, берётся курс.
Без выбора берётся текущий семестр, а если семестры не заданы — текущая неделя.

Какие занятия видит пользователь: администратор и куратор группы — все,
остальные преподаватели — только свои.
"""

from collections import defaultdict
from datetime import timedelta

from schedule.access import full_name, own_teacher_id, user_role
from schedule.changes import FIRST_PAIR, LAST_PAIR, load_changes
from schedule.dates import WEEKDAY_SHORT, short_label, week_start, parse_date
from schedule.models import (
    ROLE_ADMIN,
    ROLE_STUDENT,
    ROLE_TEACHER,
    Day,
    Group,
    PairGroup,
    Profile,
    Semester,
)
from schedule.semesters import semester_at, semesters_of_course

from .lessons import _resolve, holiday_dates, is_school_day, teacher_group_ids
from .models import MARK_SCORE, Mark

COURSES = (1, 2, 3, 4)
SEMESTERS = tuple(range(1, 9))

# Больше столько дней за раз не показываем: два семестра с запасом
MAX_DAYS = 400


class Filters:
    """Разобранные параметры адреса."""

    def __init__(self, query, today):
        self.submitted = "f" in query
        # Без отправленной формы по умолчанию включена «одна неделя»
        self.week = query.get("week") == "1" if self.submitted else True
        self.date = parse_date(query.get("date"), today)
        self.monday = week_start(self.date)
        self.groups = _ints(query.getlist("group"), None)
        self.subjects = [
            " ".join(value.split())
            for value in query.getlist("subject")
            if value.strip()
        ][:50]
        self.courses = _ints(query.getlist("course"), COURSES)
        self.semesters = _ints(query.getlist("semester"), SEMESTERS)
        self.notes = []

        if self.courses and self.semesters:
            self.semesters = []
            self.notes.append(
                "Курс и семестр одновременно выбрать нельзя: использован курс."
            )

    def params(self, **override):
        """Пары (имя, значение) для адреса; override заменяет week, date, groups."""
        pairs = [("f", "1")]
        week = override.get("week", self.week)

        if week:
            pairs.append(("week", "1"))
            pairs.append(("date", override.get("date", self.monday).isoformat()))

        pairs += [("course", n) for n in self.courses]
        pairs += [("semester", n) for n in self.semesters]
        pairs += [("group", n) for n in override.get("groups", self.groups)]
        pairs += [("subject", name) for name in self.subjects]

        return pairs


def _ints(values, allowed):
    result = []

    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue

        if allowed is not None and number not in allowed:
            continue

        if number > 0 and number not in result:
            result.append(number)

    return result


# ---------------------------------------------------------------------------
# Доступ и группы
# ---------------------------------------------------------------------------

def journal_role(user):
    """Роль, которой разрешён журнал, иначе None (студенту — только дневник)."""
    role = user_role(user)

    return role if role in (ROLE_ADMIN, ROLE_TEACHER) else None


def allowed_groups(user, role):
    """Группы, которые пользователь может выбрать, по названию."""
    groups = Group.objects.select_related("group_tag", "group_curator")

    if role == ROLE_TEACHER:
        groups = groups.filter(pk__in=teacher_group_ids(own_teacher_id(user)))

    return list(groups.order_by("group_name", "group_id"))


# ---------------------------------------------------------------------------
# Период
# ---------------------------------------------------------------------------

def resolve_period(filters, today):
    """Период журнала: (дни, подпись, режим недели, пояснения).

    Дни — учебные даты по порядку; даты из будущего не показываются
    (кроме режима недели: там видна вся неделя).
    """
    notes = list(filters.notes)
    week = filters.week
    numbers = []

    if not week:
        wanted = []

        for course in filters.courses:
            wanted += list(semesters_of_course(course))

        wanted += filters.semesters

        if not wanted:
            current = semester_at(today)

            if current is not None:
                wanted = [current.semester_number]

        semesters = list(
            Semester.objects.filter(semester_number__in=wanted)
            .order_by("start_date")
        )
        numbers = [item.semester_number for item in semesters]
        missing = sorted(set(wanted) - set(numbers))

        if missing:
            notes.append(
                "Не заданы даты семестров: "
                + ", ".join(str(n) for n in missing)
                + ". Задайте их в Кузне."
            )

        if not semesters:
            if not wanted:
                notes.append(
                    "Семестры не заданы, показана текущая неделя."
                )

            week = True

    if week:
        monday = filters.monday
        days = [monday + timedelta(days=offset) for offset in range(6)]
        label = f"{short_label(monday)} — {short_label(days[-1])}"

        return days, label, True, notes

    days = []

    for item in semesters:
        day = item.start_date
        last = min(item.end_date, today)

        while day <= last and len(days) < MAX_DAYS:
            days.append(day)
            day += timedelta(days=1)

    label = ", ".join(f"{n} сем." for n in numbers)

    return days, label, False, notes


# ---------------------------------------------------------------------------
# Занятия и таблица
# ---------------------------------------------------------------------------

def _lessons_of(groups, days):
    """Проведённые занятия групп за дни: [занятие], без отбора по преподавателю."""
    if not days or not groups:
        return []

    group_ids = [group.pk for group in groups]
    holidays = holiday_dates(days[0], days[-1])
    changes_by_date = defaultdict(list)

    for change in load_changes(days[0], days[-1]):
        changes_by_date[change.calendar.date].append(change)

        if change.new_calendar_id and change.new_calendar.date != change.calendar.date:
            changes_by_date[change.new_calendar.date].append(change)

    schedule = defaultdict(lambda: defaultdict(dict))

    for record in Day.objects.filter(
        group_id__in=group_ids,
        pair_number__range=(FIRST_PAIR, LAST_PAIR),
    ).select_related("pair", "pair__teacher", "group"):
        schedule[record.group_id][record.weekday_id][record.pair_number] = record

    lessons = []

    for day in days:
        if not is_school_day(day, holidays):
            continue

        for group_id in group_ids:
            lessons += _resolve(
                group_id,
                day,
                schedule[group_id].get(day.isoweekday(), {}),
                changes_by_date.get(day, []),
            )

    return lessons


def _subject_options(groups, sees_all, teacher_id, lessons):
    """Названия предметов для фильтра: пары выбранных групп и их занятия."""
    names = set()

    for link in PairGroup.objects.filter(
        group_id__in=[group.pk for group in groups]
    ).select_related("pair"):
        if sees_all[link.group_id] or link.pair.teacher_id == teacher_id:
            names.add(link.pair.pair_name)

    names.update(lesson["subject"] for lesson in lessons)

    return sorted(names, key=str.lower)


def _students(groups):
    """Студенты выбранных групп: группы по названию, внутри по алфавиту."""
    order = {group.pk: index for index, group in enumerate(groups)}
    profiles = Profile.objects.filter(
        group_id__in=list(order), role=ROLE_STUDENT, user__is_staff=False
    ).select_related("user")

    return sorted(
        profiles,
        key=lambda profile: (
            order[profile.group_id],
            profile.user.last_name.lower(),
            profile.user.first_name.lower(),
            profile.patronymic.lower(),
            profile.user.username.lower(),
        ),
    )


def build_journal(user, role, filters, today, general=False):
    """Всё, что нужно странице журнала, одним словарём.

    general=True — общий журнал: если группы не выбраны, берутся все
    доступные (предметы тогда не предлагаются: только при выбранной группе).
    """
    groups = allowed_groups(user, role)
    teacher_id = own_teacher_id(user)
    explicit = [group for group in groups if group.pk in filters.groups]
    chosen = explicit or (groups if general else [])
    sees_all = {
        group.pk: (
            role == ROLE_ADMIN
            or (teacher_id is not None and group.group_curator_id == teacher_id)
        )
        for group in chosen
    }

    days, period_label, week_mode, notes = resolve_period(filters, today)

    lessons = [
        lesson
        for lesson in _lessons_of(chosen, days)
        if sees_all[lesson["group_id"]] or lesson["teacher_id"] == teacher_id
    ]

    options = (
        _subject_options(explicit, sees_all, teacher_id, lessons)
        if explicit else []
    )
    subjects = [name for name in filters.subjects if name in options]

    if subjects:
        lessons = [lesson for lesson in lessons if lesson["subject"] in subjects]

    # Столбец — занятие (дата, номер, пара); у пары, общей для групп,
    # столбец один на все её группы
    columns = {}

    for lesson in lessons:
        key = (lesson["day"], lesson["number"], lesson["pair_id"])
        column = columns.get(key)

        if column is None:
            column = columns[key] = {
                "day": lesson["day"],
                "number": lesson["number"],
                "pair_id": lesson["pair_id"],
                "subject": lesson["subject"],
                "teacher": lesson["teacher"],
                "note": lesson["note"],
                "groups": set(),
            }

        column["groups"].add(lesson["group_id"])

    ordered = sorted(
        columns.values(),
        key=lambda column: (column["day"], column["number"], column["subject"]),
    )
    order = {group.pk: index for index, group in enumerate(chosen)}
    previous = None
    day_heads = []

    for column in ordered:
        column["first_of_day"] = column["day"] != previous
        column["day_iso"] = column["day"].isoformat()
        column["link_group"] = min(column["groups"], key=order.get)

        if column["first_of_day"]:
            day_heads.append({
                "name": WEEKDAY_SHORT[column["day"].isoweekday()],
                "label": short_label(column["day"]),
                "is_today": column["day"] == today,
                "count": 0,
            })

        day_heads[-1]["count"] += 1
        previous = column["day"]

    table = _rows(chosen, ordered, days)
    scores = table["scores"]

    return {
        "groups": groups,
        "chosen": chosen,
        "subject_options": options,
        "subjects": subjects,
        "columns": ordered,
        "day_heads": day_heads,
        "rows": table["rows"],
        "scores_count": len(scores),
        "average": format_average(average(scores)),
        "students_count": len(table["rows"]),
        "group_stats": _group_stats(chosen, ordered, table["rows"]),
        "period_label": period_label,
        "week_mode": week_mode,
        "notes": notes,
        "has_columns": bool(ordered),
    }


def _rows(chosen, columns, days):
    """Строки таблицы: студент и его ячейки по столбцам."""
    students = _students(chosen) if chosen else []

    if not students:
        return {"rows": [], "scores": []}

    marks = {}

    if columns:
        for mark in Mark.objects.filter(
            student_id__in=[profile.user_id for profile in students],
            lesson_date__range=(days[0], days[-1]),
            pair_id__in={column["pair_id"] for column in columns},
        ):
            marks[(mark.student_id, mark.pair_id, mark.lesson_date,
                   mark.pair_number)] = mark

    stripe = {group.pk: index % 2 for index, group in enumerate(chosen)}
    names = {group.pk: group.group_name for group in chosen}
    rows = []
    all_scores = []

    for profile in students:
        cells = []
        scores = []

        for column in columns:
            if profile.group_id not in column["groups"]:
                cells.append({"none": True, "first_of_day": column["first_of_day"]})
                continue

            mark = marks.get((profile.user_id, column["pair_id"],
                              column["day"], column["number"]))

            if mark is not None and mark.kind == MARK_SCORE and mark.score is not None:
                scores.append(mark.score)

            cells.append({
                "none": False,
                "text": mark.label if mark else "",
                "kind": mark.kind if mark else "",
                "first_of_day": column["first_of_day"],
            })

        all_scores += scores
        rows.append({
            "group_id": profile.group_id,
            "scores": len(scores),
            "score_values": scores,
            "average": format_average(average(scores)),
            "user": profile.user,
            "name": full_name(profile.user, profile) or profile.user.get_username(),
            "group": names[profile.group_id],
            "stripe": "ab"[stripe[profile.group_id]],
            "cells": cells,
        })

    return {"rows": rows, "scores": all_scores}


def _group_stats(chosen, columns, rows):
    """Итоги по группам: студентов, занятий, оценок, средний балл."""
    stats = []

    for group in chosen:
        scores = []
        students = 0

        for row in rows:
            if row["group_id"] == group.pk:
                students += 1
                scores += row["score_values"]

        stats.append({
            "group": group,
            "students": students,
            "lessons": sum(1 for c in columns if group.pk in c["groups"]),
            "scores": len(scores),
            "average": format_average(average(scores)),
        })

    return stats


def average(values):
    """Среднее арифметическое или None, если оценок нет."""
    return sum(values) / len(values) if values else None


def format_average(value):
    """«78,5»; без оценок — тире."""
    return "—" if value is None else f"{value:.1f}".replace(".", ",")
