"""Режим «Расписание | Дневник»: переключатель на страницах группы и
запоминание выбора студента в cookie.

Cookie хранит одно слово (schedule или diary), личных данных в нём нет;
его можно стереть в настройках сайтов браузера, тогда снова откроется
расписание. Запоминается выбор только у студента: от него зависит, куда
ведёт кнопка меню «Моё расписание».
"""

from django.urls import reverse

from .access import (
    ROLE_STUDENT,
    can_view_group_students,
    own_group_id,
    own_teacher_id,
    user_role,
)

MODE_COOKIE = "raspisite_mode"
MODE_SCHEDULE = "schedule"
MODE_DIARY = "diary"
MODES = (MODE_SCHEDULE, MODE_DIARY)

# Год: потом выбор придётся сделать заново
MODE_MAX_AGE = 365 * 24 * 60 * 60


def read_mode(request):
    """Запомненный режим; если cookie нет или в ней мусор, то расписание."""
    value = request.COOKIES.get(MODE_COOKIE)

    return value if value in MODES else MODE_SCHEDULE


def remember_mode(request, response, mode):
    """Запоминает режим студента. Остальным ничего не записывается."""
    if user_role(request.user) != ROLE_STUDENT or mode not in MODES:
        return response

    if request.COOKIES.get(MODE_COOKIE) != mode:
        response.set_cookie(
            MODE_COOKIE,
            mode,
            max_age=MODE_MAX_AGE,
            path="/",
            samesite="Lax",
            httponly=True,
            secure=request.is_secure(),
        )

    return response


def _with_date(url, date_iso):
    return f"{url}?date={date_iso}" if date_iso else url


def _schedule_url(group_id, date_iso):
    return _with_date(
        reverse("group_schedule", kwargs={"group_id": group_id}), date_iso
    )


def _diary_url(user, group, date_iso):
    """Куда ведёт «Дневник» с расписания группы, или None, если нельзя."""
    if user_role(user) == ROLE_STUDENT:
        # Студент видит только свой дневник и только своей группы
        if own_group_id(user) != group.pk:
            return None

        return _with_date(reverse("diary_home"), date_iso)

    # Дневник группы открывают администратор, куратор и те, кто в ней ведёт
    allowed = can_view_group_students(user, group)

    if not allowed:
        from diary.lessons import teaches_in_group

        allowed = teaches_in_group(own_teacher_id(user), group.pk)

    if not allowed:
        return None

    return _with_date(
        reverse("diary_group", kwargs={"group_id": group.pk}), date_iso
    )


def switch_on_schedule(user, group, date_iso):
    """Переключатель для страницы расписания группы (или None)."""
    diary_url = _diary_url(user, group, date_iso)

    if diary_url is None:
        return None

    return {
        "current": MODE_SCHEDULE,
        "schedule_url": _schedule_url(group.pk, date_iso),
        "diary_url": diary_url,
    }


def switch_on_diary(group_id, date_iso, diary_url):
    """Переключатель для страницы дневника: она уже открыта, права проверены."""
    return {
        "current": MODE_DIARY,
        "schedule_url": _schedule_url(group_id, date_iso),
        "diary_url": diary_url,
    }
