"""Семестры: какой семестр у даты и какие семестры у курса.

Нужны журналу: фильтры «семестр» и «курс» взаимно исключают друг друга
(курс N = семестры 2N-1 и 2N), а граница периода берётся из дат семестров,
которые администратор задаёт в Кузне.
"""

from .models import SEMESTER_NUMBERS, Semester


def semesters_of_course(course):
    """Номера двух семестров курса: 1 → (1, 2), 4 → (7, 8)."""
    return (2 * course - 1, 2 * course)


def course_of_semester(number):
    return (number + 1) // 2


def semester_at(day):
    """Семестр, в который попадает дата, или None (каникулы, не задан)."""
    return Semester.objects.filter(
        start_date__lte=day, end_date__gte=day
    ).first()


def semester_ranges(numbers=None):
    """Интервалы выбранных семестров: [(начало, конец)] по порядку номеров.

    numbers=None — все заданные. Незаданные номера пропускаются.
    """
    queryset = Semester.objects.order_by("semester_number")

    if numbers is not None:
        queryset = queryset.filter(
            semester_number__in=[n for n in numbers if n in SEMESTER_NUMBERS]
        )

    return [(item.start_date, item.end_date) for item in queryset]
