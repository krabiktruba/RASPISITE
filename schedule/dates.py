"""Даты и русские названия дней недели и месяцев.

Модуль не зависит от Django и базы данных: вся арифметика дат берётся из
стандартных datetime и calendar, поэтому совпадает с реальным календарём.
"""

import calendar
from datetime import date, timedelta

# Допустимый диапазон лет для параметров из адресной строки.
MIN_YEAR = 2000
MAX_YEAR = 2100

WEEKDAY_NAMES = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}

WEEKDAY_SHORT = {
    1: "Пн",
    2: "Вт",
    3: "Ср",
    4: "Чт",
    5: "Пт",
    6: "Сб",
    7: "Вс",
}

MONTH_NAMES = [
    "",
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

MONTH_GENITIVE = [
    "",
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

MONTH_SHORT = [
    "",
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


def parse_date(value, default):
    """Разбирает дату формата ГГГГ-ММ-ДД; при ошибке возвращает default."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return default

    if MIN_YEAR <= parsed.year <= MAX_YEAR:
        return parsed

    return default


def short_label(day):
    """Например: «19 окт»."""
    return f"{day.day} {MONTH_SHORT[day.month]}"


def full_label(day):
    """Например: «Воскресенье, 18 октября 2026»."""
    return (
        f"{WEEKDAY_NAMES[day.isoweekday()]}, "
        f"{day.day} {MONTH_GENITIVE[day.month]} {day.year}"
    )


def week_start(day):
    """Понедельник недели, в которую входит дата."""
    return day - timedelta(days=day.weekday())


def week_days(day, names=None):
    """Шесть учебных дней (понедельник–суббота) недели с датой day.

    names — необязательный словарь {номер дня: название} из таблицы Weekday.
    Для отсутствующих номеров берутся стандартные названия.
    """
    names = names or {}
    monday = week_start(day)
    result = []

    for weekday_id in range(1, 7):
        current = monday + timedelta(days=weekday_id - 1)

        result.append({
            "id": weekday_id,
            "name": names.get(weekday_id, WEEKDAY_NAMES[weekday_id]),
            "short": WEEKDAY_SHORT[weekday_id],
            "date": current,
            "iso": current.isoformat(),
            "number": current.day,
            "label": short_label(current),
        })

    return result


def shift_month(year, month, delta):
    """Сдвигает пару (год, месяц) на delta месяцев."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_weeks(year, month):
    """Недели месяца (с понедельника), каждая — список из 7 дат."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
