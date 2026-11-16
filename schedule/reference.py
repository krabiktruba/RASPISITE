"""«Справочник»: сырые таблицы справочных данных на одной странице.

Группы, преподаватели, предметы (пары), кабинеты и дежурные. Это те же данные,
что видны в расписании, только списком: его можно искать и сортировать.
Личных данных (почт, ИИН, списков студентов) здесь нет: только названия и
числа. Видят справочник все вошедшие; ссылки «Изменить» — только
администратор (их строит вызывающий код через can_edit).
"""

from collections import defaultdict
from urllib.parse import urlencode

from django.db.models import Count
from django.urls import reverse

from .access import ROLE_STUDENT
from .dates import WEEKDAY_NAMES, WEEKDAY_SHORT
from .models import Day, Duty, Group, Pair, PairGroup, Profile, Teacher
from .rooms import room_key

TABS = (
    ("groups", "Группы"),
    ("teachers", "Преподаватели"),
    ("subjects", "Предметы"),
    ("rooms", "Кабинеты"),
    ("duty", "Дежурные"),
)

DEFAULT_TAB = TABS[0][0]
DESCRIPTION_SHOWN = 80


def _cell(text="", url="", sort=None, title=""):
    return {
        "text": text,
        "url": url,
        "sort": text if sort is None else sort,
        "title": title,
    }


def _row(cells, edit_url=""):
    return {
        "cells": cells,
        "edit_url": edit_url,
        # Строка для поиска: только текст ячеек, без кнопки «Изменить»
        "search": " ".join(cell["text"] for cell in cells if cell["text"]),
    }


def _workshop_url(key, pk):
    return f"{reverse('workshop')}?{urlencode({'edit': key, 'item': pk})}"


def _teacher_cell(teacher):
    if teacher is None:
        return _cell("", sort="")

    return _cell(
        teacher.teacher_name,
        url=reverse("teacher_card", kwargs={"teacher_id": teacher.pk}),
    )


def _tag_text(tag):
    return tag.tag_name if tag else ""


def _short(text):
    text = " ".join((text or "").split())

    if len(text) <= DESCRIPTION_SHOWN:
        return text

    return text[:DESCRIPTION_SHOWN].rstrip() + "…"


def _names(names):
    return ", ".join(sorted(names, key=str.casefold))


def groups_table():
    students = dict(
        Profile.objects.filter(role=ROLE_STUDENT, group__isnull=False)
        .values_list("group")
        .annotate(total=Count("pk"))
    )
    lessons = dict(
        Day.objects.values_list("group").annotate(total=Count("pk"))
    )
    rows = []

    groups = Group.objects.select_related(
        "group_tag", "group_curator", "group_course", "shift"
    ).order_by("group_name", "group_id")

    for group in groups:
        rows.append(_row(
            [
                _cell(
                    group.group_name,
                    url=reverse("group_card", kwargs={"group_id": group.pk}),
                ),
                _cell(
                    str(group.group_course_id), sort=group.group_course_id
                ),
                _cell(str(group.shift_id), sort=group.shift_id),
                _teacher_cell(group.group_curator),
                _cell(_tag_text(group.group_tag)),
                _cell(group.room),
                _cell(
                    str(students.get(group.pk, 0)),
                    sort=students.get(group.pk, 0),
                ),
                _cell(
                    str(lessons.get(group.pk, 0)),
                    sort=lessons.get(group.pk, 0),
                ),
            ],
            edit_url=_workshop_url("group", group.pk),
        ))

    return {
        "columns": [
            {"label": "Группа"},
            {"label": "Курс", "number": True},
            {"label": "Смена", "number": True},
            {"label": "Куратор"},
            {"label": "Тег"},
            {"label": "Кабинет"},
            {"label": "Студентов", "number": True},
            {"label": "Пар в неделю", "number": True},
        ],
        "rows": rows,
    }


def teachers_table():
    curated = defaultdict(list)

    for group in Group.objects.filter(group_curator__isnull=False):
        curated[group.group_curator_id].append(group.group_name)

    subjects = dict(
        Pair.objects.values_list("teacher").annotate(total=Count("pk"))
    )
    duty = defaultdict(set)

    for record in Duty.objects.all():
        duty[record.teacher_id].add(record.weekday)

    rows = []

    for teacher in Teacher.objects.select_related("teacher_tag").order_by(
        "teacher_name", "teacher_id"
    ):
        days = ", ".join(
            WEEKDAY_SHORT[number]
            for number in sorted(duty.get(teacher.pk, ()))
        )
        count = subjects.get(teacher.pk, 0)

        rows.append(_row([
            _teacher_cell(teacher),
            _cell(_tag_text(teacher.teacher_tag)),
            _cell(_names(curated.get(teacher.pk, []))),
            _cell(str(count), sort=count),
            _cell(days),
        ]))

    return {
        "columns": [
            {"label": "Преподаватель"},
            {"label": "Тег"},
            {"label": "Куратор групп"},
            {"label": "Предметов", "number": True},
            {"label": "Дежурит"},
        ],
        "rows": rows,
    }


def subjects_table():
    group_names = defaultdict(list)

    for link in PairGroup.objects.select_related("group"):
        group_names[link.pair_id].append(link.group.group_name)

    rows = []

    pairs = Pair.objects.select_related("teacher").order_by(
        "pair_name", "pair_id"
    )

    for pair in pairs:
        names = group_names.get(pair.pk, [])

        rows.append(_row(
            [
                _cell(pair.pair_name),
                _teacher_cell(pair.teacher),
                _cell(_names(names)),
                _cell(
                    _short(pair.description),
                    title=" ".join((pair.description or "").split()),
                ),
            ],
            edit_url=_workshop_url("pair", pair.pk),
        ))

    return {
        "columns": [
            {"label": "Предмет"},
            {"label": "Преподаватель"},
            {"label": "Группы"},
            {"label": "Описание"},
        ],
        "rows": rows,
    }


def rooms_table():
    """Кабинеты: основной у групп и особый у отдельных пар."""
    spelling = {}
    owners = defaultdict(set)
    shifts = defaultdict(set)
    special = defaultdict(lambda: defaultdict(int))

    for group in Group.objects.exclude(room=""):
        key = room_key(group.room)
        spelling.setdefault(key, group.room)
        owners[key].add(group.group_name)
        shifts[key].add(group.shift_id)

    for day in Day.objects.exclude(room="").select_related("group"):
        key = room_key(day.room)
        spelling.setdefault(key, day.room)
        shifts[key].add(day.group.shift_id)
        special[key][day.group.group_name] += 1

    rows = []

    for key in sorted(spelling, key=lambda item: _natural(spelling[item])):
        used = special.get(key, {})
        total = sum(used.values())

        rows.append(_row([
            _cell(spelling[key]),
            _cell(_names(owners.get(key, []))),
            _cell(
                ", ".join(
                    f"{name} ×{count}" if count > 1 else name
                    for name, count in sorted(
                        used.items(), key=lambda item: item[0].casefold()
                    )
                ),
            ),
            _cell(str(total), sort=total),
            _cell(
                ", ".join(str(shift) for shift in sorted(shifts[key]))
            ),
        ]))

    return {
        "columns": [
            {"label": "Кабинет"},
            {"label": "Кабинет группы"},
            {"label": "Особый у пар"},
            {"label": "Особых пар", "number": True},
            {"label": "Смены"},
        ],
        "rows": rows,
    }


def _natural(text):
    """Ключ сортировки: «9» раньше «10», цифры раньше букв."""
    parts = []
    number = ""

    for char in text.casefold():
        if char.isdigit():
            number += char
            continue

        if number:
            parts.append((0, int(number), ""))
            number = ""

        parts.append((1, 0, char))

    if number:
        parts.append((0, int(number), ""))

    return parts


def duty_rows():
    records = Duty.objects.select_related("teacher").order_by(
        "weekday", "shift_id", "teacher__teacher_name", "duty_id"
    )
    rows = []

    for record in records:
        rows.append(_row(
            [
                _cell(
                    WEEKDAY_NAMES[record.weekday], sort=record.weekday
                ),
                _cell(str(record.shift_id), sort=record.shift_id),
                _teacher_cell(record.teacher),
            ],
            edit_url=_workshop_url("duty", record.pk),
        ))

    return {
        "columns": [
            {"label": "День недели", "number": True},
            {"label": "Смена", "number": True},
            {"label": "Преподаватель"},
        ],
        "rows": rows,
    }


BUILDERS = {
    "groups": groups_table,
    "teachers": teachers_table,
    "subjects": subjects_table,
    "rooms": rooms_table,
    "duty": duty_rows,
}


def build(tab):
    """Таблица вкладки; неизвестная вкладка заменяется первой."""
    if tab not in BUILDERS:
        tab = DEFAULT_TAB

    return tab, BUILDERS[tab]()
