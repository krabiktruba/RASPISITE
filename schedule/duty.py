"""Дежурные преподаватели: кто дежурит в какой день недели и смену.

Дежурство повторяется каждую неделю (без чётных и нечётных недель). Назначают
в «Кузне», карточка «Дежурные». На выходной и праздничный день дежурных нет.
"""

from collections import defaultdict

from .dates import WEEKDAY_NAMES
from .models import Duty, Shift


def _records():
    return list(
        Duty.objects.select_related("teacher").order_by(
            "weekday", "shift_id", "teacher__teacher_name", "duty_id"
        )
    )


def duty_for_weekday(weekday):
    """Дежурные на день недели: [{"shift": смена, "teachers": [Teacher]}]."""
    by_shift = defaultdict(list)

    for record in _records():
        if record.weekday == weekday:
            by_shift[record.shift_id].append(record.teacher)

    return [
        {"shift_id": shift_id, "teachers": by_shift[shift_id]}
        for shift_id in sorted(by_shift)
    ]


def duty_text(entries):
    """Текст для подсказки: «1 смена: Иванов И. И.; 2 смена: …»."""
    return "; ".join(
        f"{entry['shift_id']} смена: "
        + ", ".join(t.teacher_name for t in entry["teachers"])
        for entry in entries
    )


def duty_table():
    """Таблица на страницу календаря: строки — дни недели, столбцы — смены.

    Возвращает (смены, строки); строка — {"name", "weekday", "cells"}, где
    cells идут в порядке смен и содержат списки преподавателей. Дни, где
    никто не дежурит, не показываются.
    """
    records = _records()
    shift_ids = sorted(
        set(Shift.objects.values_list("pk", flat=True))
        | {record.shift_id for record in records}
    )

    grid = defaultdict(lambda: defaultdict(list))

    for record in records:
        grid[record.weekday][record.shift_id].append(record.teacher)

    rows = [
        {
            "weekday": weekday,
            "name": WEEKDAY_NAMES[weekday],
            "cells": [grid[weekday].get(shift_id, []) for shift_id in shift_ids],
        }
        for weekday in sorted(grid)
    ]

    return shift_ids, rows
