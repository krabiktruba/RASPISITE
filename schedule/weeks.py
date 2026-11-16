"""Верхняя и нижняя недели.

Day.week: 0 — пара идёт каждую неделю, 1 — только на верхней, 2 — только на
нижней. Первая неделя семестра — верхняя; в Кузне у семестра есть галочка,
которая меняет верхнюю и нижнюю местами.

Если дата не попала ни в один семестр (каникулы, семестры не заданы),
берётся ближайший по датам семестр; если семестров нет вовсе — верхняя.
"""

from datetime import timedelta

from .models import Semester

WEEK_EVERY = 0
WEEK_UPPER = 1
WEEK_LOWER = 2

WEEK_LABELS = {
    WEEK_EVERY: "каждая",
    WEEK_UPPER: "верхняя",
    WEEK_LOWER: "нижняя",
}

WEEK_SHORT = {WEEK_UPPER: "верх.", WEEK_LOWER: "ниж."}

# Названия для CSV: допускаем и короткие формы
WEEK_BY_NAME = {
    "": WEEK_EVERY,
    "каждая": WEEK_EVERY,
    "каждую": WEEK_EVERY,
    "обе": WEEK_EVERY,
    "верхняя": WEEK_UPPER,
    "верх": WEEK_UPPER,
    "нижняя": WEEK_LOWER,
    "низ": WEEK_LOWER,
}


def _monday(day):
    return day - timedelta(days=day.weekday())


def load_semesters():
    """Семестры одним запросом: для цикла по датам передавать в week_kind."""
    return list(Semester.objects.all())


def _nearest_semester(day, semesters):
    if not semesters:
        return None

    for item in semesters:
        if item.start_date <= day <= item.end_date:
            return item

    def distance(item):
        if day < item.start_date:
            return (item.start_date - day).days
        return (day - item.end_date).days

    return min(semesters, key=distance)


def week_kind(day, semesters=None):
    """WEEK_UPPER или WEEK_LOWER для даты."""
    if semesters is None:
        semesters = load_semesters()

    semester = _nearest_semester(day, semesters)

    if semester is None:
        return WEEK_UPPER

    shift = (_monday(day) - _monday(semester.start_date)).days // 7
    upper = shift % 2 == 0

    if semester.swap_weeks:
        upper = not upper

    return WEEK_UPPER if upper else WEEK_LOWER


def week_filter(kind):
    """Значения Day.week, которые идут на неделе вида kind."""
    return (WEEK_EVERY, kind)


def compatible(first, second):
    """Могут ли две пары (с такими значениями week) идти в одну неделю."""
    if first == WEEK_EVERY or second == WEEK_EVERY:
        return True

    return first == second


def week_label(week):
    return WEEK_LABELS.get(week, "")
