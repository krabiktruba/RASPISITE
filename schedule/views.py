from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Prefetch, ProtectedError, Q
from django.http import Http404, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .access import (
    can_view_group_students,
    can_view_student,
    full_name,
    own_group_id,
    own_teacher_id,
    real_teachers,
    role_label,
    set_group,
    set_role,
    set_teacher_tag,
    user_role,
)
from .changes import (
    CANCEL,
    MOVE,
    REPLACE,
    find_change,
    group_days_by_number,
    index_changes,
    load_changes,
    resolve_group_day,
    slot_is_free,
    teacher_conflict,
    teacher_lessons,
)
from .csv_io import (
    KINDS,
    build_zip,
    max_bytes,
    build_csv,
    import_csv,
    kind_choices,
)
from .workshop_edit import (
    EDIT_KEYS,
    delete_blocker,
    delete_warning,
    edit_items,
    find_item,
    item_label,
    pair_teacher_conflict,
)
from .dates import (
    MAX_YEAR,
    MIN_YEAR,
    MONTH_NAMES,
    WEEKDAY_NAMES,
    WEEKDAY_SHORT,
    full_label,
    month_weeks,
    parse_date,
    shift_month,
    short_label,
    week_days,
)

from .tags import build_tag_css
from .forms import (
    GroupForm,
    GroupTagForm,
    PairForm,
    NewUserForm,
    SemesterForm,
    TeacherTagForm,
)
from .models import (
    ROLE_ADMIN,
    ROLE_CHOICES,
    ROLE_STUDENT,
    ROLE_TEACHER,
    BreakHours,
    Calendar,
    Course,
    Day,
    Group,
    GroupTag,
    Pair,
    PairGroup,
    Profile,
    ScheduleChange,
    Shift,
    StudyHours,
    Teacher,
    TeacherTag,
    Weekday,
)


# Сколько групп показывать на одной странице /groups/
GROUPS_PER_PAGE = 6
MAX_GROUP_SEARCH = 100


# ---------------------------------------------------------------------------
# Доступ (регистрации нет: учётные записи выдаёт администратор)
# ---------------------------------------------------------------------------

def staff_required(view):
    """Страница только для вошедших пользователей с правами персонала."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied

        return view(request, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Цвета тегов (таблица стилей)
# ---------------------------------------------------------------------------

def tag_styles(request):
    """Правила цветов для меток тегов: по классу на каждый цвет в базе."""
    colors = list(
        GroupTag.objects.values_list("tag_color", flat=True).distinct()
    ) + list(
        TeacherTag.objects.values_list("tag_color", flat=True).distinct()
    )

    response = HttpResponse(
        build_tag_css(colors),
        content_type="text/css; charset=utf-8",
    )
    # Цвет можно сменить в Кузне, поэтому браузер каждый раз сверяется
    response["Cache-Control"] = "no-cache"

    return response


# ---------------------------------------------------------------------------
# Главная страница
# ---------------------------------------------------------------------------

@login_required
def main_page(request):
    return render(request, "mainpage.html")


def csrf_failed(request, reason=""):
    """Страница вместо ошибки 403, если форма устарела.

    Токен формы перестаёт подходить после кнопки «Назад», после входа в
    другой вкладке или если страница долго стояла открытой. Данные при этом
    не меняются: человеку достаточно вернуться на страницу и повторить.
    """
    back_url = request.META.get("HTTP_REFERER", "")

    # Возвращаем только на страницы этого же сайта.
    if not back_url or not url_has_allowed_host_and_scheme(
        back_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        back_url = reverse("home")

    return render(
        request,
        "csrf_failed.html",
        {"back_url": back_url},
        status=403,
    )


# ---------------------------------------------------------------------------
# Общие помощники для дат и календаря
# ---------------------------------------------------------------------------

def _int_param(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _page_numbers(paginator, page_obj):
    """Номера страниц для панели пагинации; None обозначает многоточие."""
    return [
        number if isinstance(number, int) else None
        for number in paginator.get_elided_page_range(
            page_obj.number,
            on_each_side=1,
            on_ends=1,
        )
    ]


def _weekday_names():
    """Названия дней из таблицы Weekday (если в ней есть записи)."""
    return {
        weekday.weekday_id: weekday.weekday_name
        for weekday in Weekday.objects.filter(weekday_id__range=(1, 6))
    }


def _holidays(start, end):
    """Праздничные дни из таблицы Calendar: {дата: название}."""
    return {
        record.date: record.holiday_name or ""
        for record in Calendar.objects.filter(
            date__range=(start, end),
            is_holiday=True,
        )
    }


def _off_reason(day, holidays):
    """Причина, по которой в этот день нет занятий, или пустая строка."""
    if day.isoweekday() == 7:
        return "Воскресенье — выходной день."

    if day in holidays:
        name = holidays[day]
        return f"Праздничный день: {name}." if name else "Праздничный день."

    return ""


def _sync_calendar(first, last):
    """Приводит таблицу Calendar в соответствие с реальным календарём.

    Недостающие даты создаются, а неверный день недели исправляется.
    """
    existing = {
        record.date: record
        for record in Calendar.objects.filter(date__range=(first, last))
    }

    missing = []

    for offset in range((last - first).days + 1):
        current = first + timedelta(days=offset)

        if current not in existing:
            missing.append(
                Calendar(date=current, weekday=current.isoweekday())
            )

    if missing:
        Calendar.objects.bulk_create(missing, ignore_conflicts=True)

    wrong = [
        record
        for record in existing.values()
        if record.weekday != record.date.isoweekday()
    ]

    for record in wrong:
        record.weekday = record.date.isoweekday()

    if wrong:
        Calendar.objects.bulk_update(wrong, ["weekday"])


# ---------------------------------------------------------------------------
# Календарь
# ---------------------------------------------------------------------------

def _calendar_target(user):
    """Куда ведёт число в календаре: адрес страницы и постоянные параметры.

    Преподаватель видит своё расписание, студент — свою группу,
    администратор (и все без привязки) — расписание всех групп.
    """
    role = user_role(user)

    if role == ROLE_TEACHER:
        teacher_id = own_teacher_id(user)

        return (
            reverse("teachers"),
            {"teacher": teacher_id} if teacher_id else {},
        )

    if role == ROLE_STUDENT:
        group_id = own_group_id(user)

        if group_id:
            return reverse("group_schedule", args=[group_id]), {}

    return reverse("groups"), {}


@login_required
def student_card(request, user_id):
    """Профиль студента: ФИО, логин, группа, куратор и тег группы.

    Видят администратор, сам студент и куратор его группы; остальным 403.
    Если такого пользователя нет или он не студент — 404.
    """
    student = get_object_or_404(
        get_user_model().objects.select_related("profile"), pk=user_id
    )

    if user_role(student) != ROLE_STUDENT:
        raise Http404("Это не студент.")

    if not can_view_student(request.user, student):
        raise PermissionDenied

    group = None
    group_id = own_group_id(student)

    if group_id:
        group = Group.objects.select_related(
            "group_tag", "group_curator"
        ).filter(pk=group_id).first()

    context = {
        "student": student,
        "student_name": full_name(student) or student.get_username(),
        "group": group,
        "curator": group.group_curator if group else None,
        "from_users": request.user.is_staff,
        "edit_url": _user_row_url(student) if request.user.is_staff else None,
    }

    return render(request, "student_card.html", context)


def _user_row_url(account):
    """Список пользователей, отфильтрованный по логину, с выделенной строкой."""
    return reverse("users") + "?" + urlencode(
        {"q": account.get_username(), "new": account.pk}
    )


def _name_sort_key(profile):
    user = profile.user

    return (
        user.last_name.lower(), user.first_name.lower(),
        profile.patronymic.lower(), user.username.lower(),
    )


@login_required
def teacher_card(request, teacher_id):
    """Профиль преподавателя: тег, учётная запись, группы и пары.

    Открыть может любой вошедший (расписание преподавателя и так общее),
    но логин (почту) видят только администратор и сам преподаватель.
    Старые записи без учётной записи тоже открываются: это история.
    """
    teacher = get_object_or_404(
        Teacher.objects.select_related("teacher_tag"),
        pk=teacher_id,
    )
    profile = Profile.objects.filter(
        teacher=teacher, role=ROLE_TEACHER
    ).select_related("user").first()
    account = profile.user if profile else None
    is_self = own_teacher_id(request.user) == teacher.pk
    show_login = account is not None and (request.user.is_staff or is_self)

    curated = list(
        Group.objects.filter(group_curator=teacher)
        .select_related("group_tag")
        .order_by("group_name", "group_id")
    )
    pairs = list(
        Pair.objects.filter(teacher=teacher)
        .prefetch_related(Prefetch(
            "groups", queryset=Group.objects.order_by("group_name", "group_id")
        ))
        .order_by("pair_name", "pair_id")
    )

    context = {
        "teacher": teacher,
        "account": account,
        "show_login": show_login,
        "has_account": account is not None,
        "curated_groups": curated,
        "pairs": pairs,
        "from_users": request.user.is_staff,
        # «Редактировать»: строка преподавателя в списке пользователей
        "edit_url": (
            _user_row_url(account)
            if request.user.is_staff and account is not None
            else None
        ),
    }

    return render(request, "teacher_card.html", context)


@login_required
def group_card(request, group_id):
    """Профиль группы: куратор, тег, студенты, пары и преподаватели.

    Открыть может любой вошедший. Список студентов видят администратор и
    куратор группы, остальным показывается только их число.
    """
    group = get_object_or_404(
        Group.objects.select_related(
            "group_tag", "group_curator",
            "group_curator__teacher_tag",
            "group_course", "shift",
        ),
        pk=group_id,
    )

    students_qs = Profile.objects.filter(
        group=group, role=ROLE_STUDENT, user__is_staff=False
    ).select_related("user")
    show_students = can_view_group_students(request.user, group)
    students = []

    if show_students:
        for profile in sorted(students_qs, key=_name_sort_key):
            students.append({
                "user": profile.user,
                "name": full_name(profile.user, profile)
                or profile.user.get_username(),
            })

        student_count = len(students)
    else:
        student_count = students_qs.count()

    pairs = list(
        Pair.objects.filter(groups=group)
        .select_related("teacher", "teacher__teacher_tag")
        .order_by("pair_name", "pair_id")
    )

    context = {
        "group": group,
        "curator": group.group_curator,
        "show_students": show_students,
        "students": students,
        "student_count": student_count,
        "pairs": pairs,
        "from_users": request.user.is_staff,
    }

    return render(request, "group_card.html", context)


@login_required
def calendar_view(request):
    today = timezone.localdate()

    year = _int_param(request.GET.get("year"))
    month = _int_param(request.GET.get("month"))

    if (
        year is None
        or month is None
        or not 1 <= month <= 12
        or not MIN_YEAR <= year <= MAX_YEAR
    ):
        year, month = today.year, today.month

    weeks = month_weeks(year, month)
    first, last = weeks[0][0], weeks[-1][-1]

    _sync_calendar(first, last)
    holidays = _holidays(first, last)

    target_url, target_params = _calendar_target(request.user)

    calendar_weeks = [
        [
            {
                "number": current.day,
                "iso": current.isoformat(),
                "url": (
                    f"{target_url}?"
                    + urlencode({
                        **target_params,
                        "date": current.isoformat(),
                    })
                ),
                "in_month": current.month == month,
                "is_today": current == today,
                "is_sunday": current.isoweekday() == 7,
                "is_holiday": current in holidays,
                "holiday_name": holidays.get(current, ""),
            }
            for current in week
        ]
        for week in weeks
    ]

    month_holidays = [
        {
            "label": short_label(current),
            "name": holidays[current],
        }
        for current in sorted(holidays)
        if current.year == year and current.month == month
    ]

    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)

    context = {
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month],
        "weekday_heads": [
            {
                "short": WEEKDAY_SHORT[number],
                "name": WEEKDAY_NAMES[number],
            }
            for number in range(1, 8)
        ],
        "calendar_weeks": calendar_weeks,
        "month_holidays": month_holidays,
        "prev": (
            {"year": prev_year, "month": prev_month}
            if prev_year >= MIN_YEAR else None
        ),
        "next": (
            {"year": next_year, "month": next_month}
            if next_year <= MAX_YEAR else None
        ),
        "is_current_month": (year, month) == (today.year, today.month),
    }

    return render(request, "calendar.html", context)


@login_required
def calendar_year(request):
    """Годовой календарь: 12 маленьких месяцев; число ведёт в расписание дня.

    Страница только читает данные: праздники берутся из таблицы Calendar,
    но сама таблица здесь не пополняется (это делает месячный календарь).
    """
    today = timezone.localdate()
    year = _int_param(request.GET.get("year"))

    if year is None or not MIN_YEAR <= year <= MAX_YEAR:
        year = today.year

    holidays = _holidays(date(year, 1, 1), date(year, 12, 31))
    target_url, target_params = _calendar_target(request.user)
    months = []

    for month in range(1, 13):
        weeks = []

        for week in month_weeks(year, month):
            cells = []

            for current in week:
                if current.month != month:
                    cells.append(None)
                    continue

                cells.append({
                    "number": current.day,
                    "url": (
                        f"{target_url}?"
                        + urlencode({
                            **target_params,
                            "date": current.isoformat(),
                        })
                    ),
                    "is_today": current == today,
                    "is_sunday": current.isoweekday() == 7,
                    "is_holiday": current in holidays,
                    "holiday_name": holidays.get(current, ""),
                })

            weeks.append(cells)

        months.append({
            "number": month,
            "name": MONTH_NAMES[month],
            "weeks": weeks,
            "is_current": (year, month) == (today.year, today.month),
        })

    context = {
        "year": year,
        "months": months,
        "weekday_heads": [
            {
                "short": WEEKDAY_SHORT[number],
                "name": WEEKDAY_NAMES[number],
            }
            for number in range(1, 8)
        ],
        "prev_year": year - 1 if year - 1 >= MIN_YEAR else None,
        "next_year": year + 1 if year + 1 <= MAX_YEAR else None,
        "is_current_year": year == today.year,
        "month_view_month": today.month if year == today.year else 1,
    }

    return render(request, "calendar_year.html", context)


# ---------------------------------------------------------------------------
# Расписание группы (неделя)
# ---------------------------------------------------------------------------

def _open_change_form(params, schedule_days):
    """Форма переноса или замены, раскрытая под парой (режим правок).

    Открывается ссылкой кнопки пары: ?act=move|replace&slot=ДАТА&number=N.
    Возвращает описание формы или None, если ссылка не указывает на пару,
    которую можно изменить. В один момент открыта только одна форма.
    """
    act = params.get("act")

    if act not in ("move", "replace"):
        return None

    slot_day = parse_date(params.get("slot"), None)
    number = _int_param(params.get("number"))

    if slot_day is None or number not in range(1, 7):
        return None

    item = next(
        (day for day in schedule_days if day["date"] == slot_day), None
    )

    if item is None or not item["pairs"]:
        return None

    lesson = item["pairs"][number - 1]["lesson"]

    if not lesson or not lesson["editable"]:
        return None

    change = lesson["change"]
    replace = change is not None and change.change_type == REPLACE
    move = change is not None and change.change_type == MOVE

    # Значения: из адреса (после ошибки) или из действующей правки
    values = {
        "new_teacher": params.get("new_teacher") or (
            change.new_teacher_id if replace and change.new_teacher_id
            else ""
        ),
        "new_pair_name": params.get("new_pair_name") or (
            change.new_pair_name if replace and change.new_pair_name else ""
        ),
        "new_date": params.get("new_date") or (
            change.new_calendar.date.isoformat()
            if move and change.new_calendar_id else slot_day.isoformat()
        ),
        "new_number": params.get("new_number") or (
            change.new_pair_number
            if move and change.new_pair_number else number
        ),
    }

    return {
        "act": act,
        "slot": slot_day.isoformat(),
        "number": number,
        "subject": lesson["subject"],
        "teacher": lesson["teacher"],
        "values": values,
        "teachers": real_teachers().order_by("teacher_name", "teacher_id"),
        "pair_numbers": range(1, 7),
    }


def _group_schedule_stats(group):
    """Небольшая сводка для администратора под расписанием группы."""
    slots = list(Day.objects.filter(group=group).select_related("pair"))
    attached_ids = set(
        PairGroup.objects.filter(group=group).values_list("pair_id", flat=True)
    )
    used_ids = {day.pair_id for day in slots}

    return {
        "attached": len(attached_ids),
        "lessons": len(slots),
        "teachers": len({day.pair.teacher_id for day in slots}),
        "unused": len(attached_ids - used_ids),
    }


@login_required
def group_schedule(request, group_id):
    group = get_object_or_404(
        Group.objects.select_related(
            "shift",
            "group_course",
            "group_tag",
        ),
        pk=group_id
    )

    today = timezone.localdate()
    selected_date = parse_date(request.GET.get("date"), today)

    week = week_days(selected_date, _weekday_names())
    holidays = _holidays(week[0]["date"], week[-1]["date"])

    hours_by_pair = {
        hour.pair_number: hour
        for hour in StudyHours.objects.filter(shift=group.shift)
    }

    days_by_slot = {
        (day.weekday_id, day.pair_number): day
        for day in Day.objects.filter(
            group=group
        ).select_related(
            "pair",
            "pair__teacher",
        )
    }

    changes = load_changes(week[0]["date"], week[-1]["date"])

    schedule_days = []

    for item in week:
        off_reason = _off_reason(item["date"], holidays)
        pairs = []

        if not off_reason:
            slots = resolve_group_day(
                group.pk,
                item["date"],
                {
                    number: day
                    for (weekday_id, number), day in days_by_slot.items()
                    if weekday_id == item["id"]
                },
                changes,
            )

            for pair_number in range(1, 7):
                pairs.append({
                    "number": pair_number,
                    "time": hours_by_pair.get(pair_number),
                    "lesson": slots[pair_number],
                })

        schedule_days.append({
            **item,
            "is_today": item["date"] == today,
            "off_reason": off_reason,
            "pairs": pairs,
        })

    # Режим правок (отмена, замена, перенос) — только для администратора
    edit_mode = request.GET.get("edit") == "1" and request.user.is_staff

    context = {
        "group": group,
        "group_stats": (
            _group_schedule_stats(group) if request.user.is_staff else None
        ),
        "schedule_days": schedule_days,
        "edit_mode": edit_mode,
        "edit_form": (
            _open_change_form(request.GET, schedule_days)
            if edit_mode else None
        ),
        "selected_date_iso": selected_date.isoformat(),
        "edit_suffix": "&edit=1" if edit_mode else "",
        "edit_first": "?edit=1" if edit_mode else "",
        "week_label": f"{week[0]['label']} – {week[-1]['label']}",
        "prev_week_iso": (
            selected_date - timedelta(days=7)
        ).isoformat(),
        "next_week_iso": (
            selected_date + timedelta(days=7)
        ).isoformat(),
    }

    return render(
        request,
        "days.html",
        context
    )


# ---------------------------------------------------------------------------
# Группы: расписание всех групп за выбранный день
# ---------------------------------------------------------------------------

@login_required
def daily_schedule(request):
    today = timezone.localdate()
    selected_date = parse_date(request.GET.get("date"), today)

    week = week_days(selected_date, _weekday_names())
    holidays = _holidays(week[0]["date"], week[-1]["date"])
    day_off = _off_reason(selected_date, holidays)

    for item in week:
        item["is_selected"] = item["date"] == selected_date
        item["is_today"] = item["date"] == today
        item["is_off"] = bool(_off_reason(item["date"], holidays))

    # Поиск ?q=: по названию группы или по ФИО куратора (на сервере, потому
    # что группы разбиты на страницы и на странице видна лишь часть из них)
    query = " ".join(request.GET.get("q", "").split())[:MAX_GROUP_SEARCH]
    found = Group.objects.select_related(
        "shift",
        "group_course",
        "group_curator",
        "group_tag",
    )

    if query:
        found = found.filter(
            Q(group_name__icontains=query)
            | Q(group_curator__teacher_name__icontains=query)
        )

    # Пагинация: на странице GROUPS_PER_PAGE групп
    paginator = Paginator(
        found.order_by("group_name", "group_id"),
        GROUPS_PER_PAGE,
    )
    page_obj = paginator.get_page(request.GET.get("page"))

    page_numbers = _page_numbers(paginator, page_obj)

    schedule_groups = []

    if not day_off:
        groups = list(page_obj.object_list)

        hours_by_slot = {
            (hour.shift_id, hour.pair_number): hour
            for hour in StudyHours.objects.filter(
                shift_id__in={group.shift_id for group in groups}
            )
        }

        days_by_slot = {
            (day.group_id, day.pair_number): day
            for day in Day.objects.filter(
                weekday_id=selected_date.isoweekday(),
                group__in=groups,
            ).select_related(
                "pair",
                "pair__teacher",
            )
        }

        changes = load_changes(selected_date, selected_date)

        for group in groups:
            pairs = []

            slots = resolve_group_day(
                group.pk,
                selected_date,
                {
                    number: day
                    for (group_id, number), day in days_by_slot.items()
                    if group_id == group.pk
                },
                changes,
            )

            for pair_number in range(1, 7):
                pairs.append({
                    "number": pair_number,
                    "time": hours_by_slot.get(
                        (group.shift_id, pair_number)
                    ),
                    "lesson": slots[pair_number],
                })

            schedule_groups.append({
                "group": group,
                "pairs": pairs,
            })

    context = {
        "week_tabs": week,
        "selected_date_iso": selected_date.isoformat(),
        "selected_label": full_label(selected_date),
        "is_today_selected": selected_date == today,
        "prev_week_iso": (
            selected_date - timedelta(days=7)
        ).isoformat(),
        "next_week_iso": (
            selected_date + timedelta(days=7)
        ).isoformat(),
        "day_off": day_off,
        "schedule_groups": schedule_groups,
        "page_obj": page_obj,
        "page_numbers": page_numbers,
        "q": query,
        "found_count": paginator.count,
    }

    return render(
        request,
        "groups.html",
        context,
    )


# ---------------------------------------------------------------------------
# Преподаватели: по умолчанию расписание на сегодня
# ---------------------------------------------------------------------------

def _teachers_url(**params):
    query = {
        key: value
        for key, value in params.items()
        if value not in (None, "")
    }
    base = reverse("teachers")

    return f"{base}?{urlencode(query)}" if query else base


@login_required
def teachers_schedule(request):
    today = timezone.localdate()

    # Выбранная дата (по умолчанию — сегодня); показывается её неделя
    requested_date = parse_date(request.GET.get("date"), None)
    selected_date = requested_date or today

    # Преподаватели и смены для фильтров
    teachers = list(
        Teacher.objects.select_related(
            "teacher_tag",
        ).order_by("teacher_name", "teacher_id")
    )
    shifts = list(
        Shift.objects.order_by("shift_id")
    )

    # Выбранный преподаватель
    selected_teacher_id = _int_param(request.GET.get("teacher"))

    teacher_ids = {teacher.pk for teacher in teachers}

    if selected_teacher_id not in teacher_ids:
        own_id = own_teacher_id(request.user)

        if own_id in teacher_ids:
            selected_teacher_id = own_id
        else:
            selected_teacher_id = (
                teachers[0].pk if teachers else None
            )

    selected_teacher = next(
        (
            teacher
            for teacher in teachers
            if teacher.pk == selected_teacher_id
        ),
        None,
    )

    # Выбранная смена
    selected_shift_id = _int_param(request.GET.get("shift"))

    shift_ids = {shift.pk for shift in shifts}

    if selected_shift_id not in shift_ids:
        selected_shift_id = None

    selected_shift = next(
        (
            shift
            for shift in shifts
            if shift.pk == selected_shift_id
        ),
        None,
    )

    # Дни, которые нужно показать
    week = week_days(selected_date, _weekday_names())
    holidays = _holidays(week[0]["date"], week[-1]["date"])

    schedule_days = [
        {
            **item,
            "is_today": item["date"] == today,
            "off_reason": _off_reason(item["date"], holidays),
            "shift_label": "",
            "pairs": [
                {
                    "number": pair_number,
                    "lessons": [],
                }
                for pair_number in range(1, 7)
            ],
        }
        for item in week
    ]

    # Время занятий: номер пары + смена
    hours_by_slot = {
        (hour.shift_id, hour.pair_number): hour
        for hour in StudyHours.objects.all()
    }

    working_ids = {
        day["id"] for day in schedule_days
        if not day["off_reason"]
    }

    # Получаем занятия выбранного преподавателя с учётом разовых правок
    if selected_teacher is not None and working_ids:
        days_query = (
            Day.objects
            .filter(
                weekday_id__in=working_ids,
                pair__teacher_id=selected_teacher.pk,
            )
            .select_related(
                "group",
                "group__shift",
                "pair",
                "pair__teacher",
            )
        )

        # При необходимости ограничиваем одной сменой
        if selected_shift_id is not None:
            days_query = days_query.filter(
                group__shift_id=selected_shift_id
            )

        working_dates = [
            day["date"] for day in schedule_days
            if not day["off_reason"]
        ]

        lessons_by_date = teacher_lessons(
            selected_teacher.pk,
            working_dates,
            list(days_query),
            load_changes(week[0]["date"], week[-1]["date"]),
        )

        for day_data in schedule_days:
            lessons = lessons_by_date.get(day_data["date"], [])

            lessons.sort(key=lambda lesson: (
                lesson["number"],
                lesson["group"].shift_id,
                lesson["group"].group_name,
            ))

            for lesson in lessons:
                number = lesson["number"]
                group = lesson["group"]

                # Отображаем номера пар с 1 по 6
                if not 1 <= number <= 6:
                    continue

                if (
                    selected_shift_id is not None
                    and group.shift_id != selected_shift_id
                ):
                    continue

                day_data["pairs"][number - 1]["lessons"].append({
                    **lesson,
                    "time": hours_by_slot.get(
                        (group.shift_id, number)
                    ),
                    "shift": group.shift,
                })

    # Подпись смены в заголовке каждого дня
    for day in schedule_days:
        used = sorted({
            lesson["shift"].shift_id
            for pair in day["pairs"]
            for lesson in pair["lessons"]
        })

        if selected_shift is not None:
            day["shift_label"] = f"{selected_shift.shift_id} смена"
        elif used:
            day["shift_label"] = (
                ", ".join(str(number) for number in used) + " смена"
            )

    # Ссылки навигации сохраняют выбранных преподавателя и смену
    common = {
        "teacher": selected_teacher_id,
        "shift": selected_shift_id,
    }

    # Кнопки смен: «Все смены» и по одной на каждую смену. Меняется только
    # показанная неделя, список преподавателей остаётся тем же.
    date_value = requested_date.isoformat() if requested_date else ""

    shift_links = [
        {
            "label": "Все смены",
            "url": _teachers_url(
                teacher=selected_teacher_id,
                date=date_value,
            ),
            "selected": selected_shift_id is None,
        }
    ] + [
        {
            "label": f"{shift.shift_id} смена",
            "url": _teachers_url(
                teacher=selected_teacher_id,
                date=date_value,
                shift=shift.shift_id,
            ),
            "selected": shift.shift_id == selected_shift_id,
        }
        for shift in shifts
    ]

    context = {
        "teachers": teachers,
        "shifts": shifts,
        "selected_teacher": selected_teacher,
        "selected_teacher_id": selected_teacher_id,
        "selected_shift": selected_shift,
        "selected_shift_id": selected_shift_id,
        "shift_links": shift_links,
        "schedule_days": schedule_days,
        "date_param": (
            requested_date.isoformat() if requested_date else ""
        ),
        "week_label": f"{week[0]['label']} – {week[-1]['label']}",
        "prev_url": _teachers_url(
            **common,
            date=(selected_date - timedelta(days=7)).isoformat(),
        ),
        "next_url": _teachers_url(
            **common,
            date=(selected_date + timedelta(days=7)).isoformat(),
        ),
        "today_url": _teachers_url(**common),
    }

    return render(
        request,
        "teachers.html",
        context,
    )


# ---------------------------------------------------------------------------
# Редактор расписания группы (только администратор)
# ---------------------------------------------------------------------------

@staff_required
def group_edit(request, group_id):
    group = get_object_or_404(
        Group.objects.select_related("shift", "group_course"),
        pk=group_id,
    )

    # Таблица Weekday должна содержать шесть учебных дней
    for number in range(1, 7):
        Weekday.objects.get_or_create(
            weekday_id=number,
            defaults={"weekday_name": WEEKDAY_NAMES[number]},
        )

    names = _weekday_names()

    existing = {
        (day.weekday_id, day.pair_number): day
        for day in Day.objects.filter(
            group=group,
            weekday_id__in=range(1, 7),
            pair_number__range=(1, 6),
        )
    }

    # В расписание ставятся только пары, привязанные к группе. Уже стоящая
    # в расписании пара остаётся в списке, даже если её отвязали, чтобы
    # сохранение не стирало её молча
    pairs = list(
        Pair.objects.filter(
            Q(groups=group)
            | Q(pk__in={day.pair_id for day in existing.values()})
        ).select_related("teacher").order_by(
            "pair_name", "pair_id"
        ).distinct()
    )
    pair_by_id = {pair.pk: pair for pair in pairs}
    attached_ids = set(
        PairGroup.objects.filter(group=group).values_list("pair_id", flat=True)
    )

    for pair in pairs:
        pair.attached = pair.pk in attached_ids

    hours = {
        hour.pair_number: hour
        for hour in StudyHours.objects.filter(shift=group.shift)
    }

    selected = {
        slot: day.pair_id
        for slot, day in existing.items()
    }
    errors = []
    warnings = []

    def slot_name(weekday_id, number):
        weekday_name = names.get(weekday_id, WEEKDAY_NAMES[weekday_id])
        return f"{weekday_name}, пара {number}"

    if request.method == "POST":
        chosen = {}

        for weekday_id in range(1, 7):
            for number in range(1, 7):
                slot = (weekday_id, number)
                raw = request.POST.get(
                    f"cell_{weekday_id}_{number}", ""
                ).strip()

                if not raw:
                    chosen[slot] = None
                    continue

                pair_id = _int_param(raw)

                if pair_id not in pair_by_id:
                    errors.append(
                        f"{slot_name(*slot)}: выбрана недопустимая пара."
                    )
                    chosen[slot] = None
                else:
                    chosen[slot] = pair_id

        # Преподаватель у двух групп в одно время — предупреждение: пара может
        # быть общей (лекция для нескольких групп), поэтому сохранять можно
        teacher_ids = {
            pair_by_id[pair_id].teacher_id
            for pair_id in chosen.values()
            if pair_id is not None
        }

        busy = {}

        if teacher_ids:
            others = Day.objects.exclude(group=group).filter(
                pair__teacher_id__in=teacher_ids,
                weekday_id__in=range(1, 7),
            ).select_related("group", "pair")

            for other in others:
                key = (
                    other.weekday_id,
                    other.pair_number,
                    other.pair.teacher_id,
                )
                busy.setdefault(key, []).append(other.group.group_name)

        for (weekday_id, number), pair_id in chosen.items():
            if pair_id is None:
                continue

            pair = pair_by_id[pair_id]
            other_groups = busy.get((weekday_id, number, pair.teacher_id))

            if other_groups:
                warnings.append(
                    f"{slot_name(weekday_id, number)}: "
                    f"{pair.teacher.teacher_name} в это же время ведёт "
                    f"занятие у группы {', '.join(sorted(other_groups))}."
                )

        selected = chosen

        if not errors:
            try:
                with transaction.atomic():
                    for slot, pair_id in chosen.items():
                        day = existing.get(slot)

                        if pair_id is None:
                            if day is not None:
                                day.delete()

                        elif day is None:
                            Day.objects.create(
                                group=group,
                                weekday_id=slot[0],
                                pair_number=slot[1],
                                pair_id=pair_id,
                            )

                        elif day.pair_id != pair_id:
                            day.pair_id = pair_id
                            day.save(update_fields=["pair"])

            except DatabaseError as exc:
                errors.append(f"Не удалось сохранить расписание: {exc}")

            else:
                messages.success(request, "Расписание группы сохранено.")

                for warning in warnings:
                    messages.warning(
                        request, f"Совпадение у преподавателя. {warning}"
                    )

                return redirect("group_edit", group_id=group.pk)

    edit_days = [
        {
            "id": weekday_id,
            "name": names.get(weekday_id, WEEKDAY_NAMES[weekday_id]),
            "rows": [
                {
                    "number": number,
                    "field": f"cell_{weekday_id}_{number}",
                    "time": hours.get(number),
                    "selected": selected.get((weekday_id, number)),
                }
                for number in range(1, 7)
            ],
        }
        for weekday_id in range(1, 7)
    ]

    context = {
        "group": group,
        "edit_days": edit_days,
        "pair_options": pairs,
        "attached_count": len(attached_ids),
        "errors": errors,
    }

    return render(request, "group_edit.html", context)


# ---------------------------------------------------------------------------
# Разовые правки расписания группы: отмена, замена, перенос
# ---------------------------------------------------------------------------

CHANGE_KINDS = ("cancel", "replace", "move", "restore")


def _changes_page(group_id, day, **extra):
    """Расписание группы на неделю даты, в режиме правок.

    extra — параметры раскрытой формы (act, slot, number, new_...).
    """
    url = reverse("group_schedule", args=[group_id])
    query = {"date": day.isoformat(), "edit": 1}
    query.update({k: v for k, v in extra.items() if v not in (None, "")})

    return redirect(f"{url}?{urlencode(query)}")


def _calendar_row(day):
    """Запись таблицы Calendar для даты (создаётся при необходимости)."""
    _sync_calendar(day, day)

    return Calendar.objects.get(date=day)


def _clear_slot_changes(day, record, number, existing):
    """Снимает все правки слота (дата, группа, пара, номер пары)."""
    ScheduleChange.objects.filter(
        calendar__date=day,
        group_id=record.group_id,
        pair_id=record.pair_id,
        old_pair_number=number,
    ).delete()

    if existing is not None:
        ScheduleChange.objects.filter(pk=existing.pk).delete()


def _save_cancel(day, record, number, existing):
    with transaction.atomic():
        _clear_slot_changes(day, record, number, existing)

        ScheduleChange.objects.create(
            calendar=_calendar_row(day),
            group_id=record.group_id,
            pair=record.pair,
            old_pair_number=number,
            change_type=CANCEL,
        )

    return ""


def _save_replace(data, day, record, number, existing, changes):
    """Замена преподавателя и/или названия. Возвращает текст ошибки."""
    teacher = None
    raw_teacher = (data.get("new_teacher") or "").strip()

    if raw_teacher:
        teacher = real_teachers().filter(
            pk=_int_param(raw_teacher)
        ).first()

        if teacher is None:
            return "Выберите преподавателя из списка."

    name = " ".join((data.get("new_pair_name") or "").split())

    if len(name) > 150:
        return "Название слишком длинное (не больше 150 символов)."

    # Те же значения, что и в расписании, заменой не считаются
    if teacher is not None and teacher.pk == record.pair.teacher_id:
        teacher = None

    if name == record.pair.pair_name:
        name = ""

    if teacher is None and not name:
        return "Выберите другого преподавателя или введите новое название."

    if teacher is not None:
        busy = teacher_conflict(
            teacher.pk,
            day,
            number,
            changes,
            skip_change_id=existing.pk if existing else None,
        )

        if busy:
            return (
                f"{teacher.teacher_name} в это время ведёт группу {busy}."
            )

    with transaction.atomic():
        _clear_slot_changes(day, record, number, existing)

        ScheduleChange.objects.create(
            calendar=_calendar_row(day),
            group_id=record.group_id,
            pair=record.pair,
            old_pair_number=number,
            new_teacher=teacher,
            new_pair_name=name or None,
            change_type=REPLACE,
        )

    return ""


def _save_move(data, group, day, record, number, existing, changes):
    """Перенос пары на другую дату и номер. Возвращает текст ошибки."""
    new_day = parse_date(data.get("new_date"), None)
    new_number = _int_param(data.get("new_number"))

    if new_day is None:
        return "Укажите дату переноса."

    if new_number not in range(1, 7):
        return "Номер пары должен быть от 1 до 6."

    if (new_day, new_number) == (day, number):
        return "Выберите другой день или другую пару."

    reason = _off_reason(new_day, _holidays(new_day, new_day))

    if reason:
        return f"На эту дату перенести нельзя. {reason}"

    skip_id = existing.pk if existing else None
    new_changes = changes if new_day == day else load_changes(new_day, new_day)

    slots = resolve_group_day(
        group.pk,
        new_day,
        group_days_by_number(group.pk, new_day.isoweekday()),
        new_changes,
        skip_change_id=skip_id,
    )

    if not slot_is_free(slots[new_number]):
        return (
            f"Пара {new_number} на {short_label(new_day)} "
            f"у группы уже занята."
        )

    busy = teacher_conflict(
        record.pair.teacher_id,
        new_day,
        new_number,
        new_changes,
        skip_change_id=skip_id,
    )

    if busy:
        return (
            f"{record.pair.teacher.teacher_name} в это время "
            f"ведёт группу {busy}."
        )

    with transaction.atomic():
        _clear_slot_changes(day, record, number, existing)

        ScheduleChange.objects.create(
            calendar=_calendar_row(day),
            group_id=record.group_id,
            pair=record.pair,
            old_pair_number=number,
            new_calendar=_calendar_row(new_day),
            new_pair_number=new_number,
            change_type=MOVE,
        )

    return ""


@staff_required
def group_change(request, group_id):
    """Отмена, замена, перенос и сброс одной пары на одну дату (только админ).

    Принимает только отправку формы (POST) из режима правок на странице
    расписания. Форма переноса и замены раскрывается под парой на самой
    странице расписания, отдельной страницы нет. Ошибка показывается
    сообщением, а форма остаётся раскрытой с введёнными значениями.
    """
    group = get_object_or_404(
        Group.objects.select_related("shift", "group_course"),
        pk=group_id,
    )

    data = request.POST if request.method == "POST" else request.GET

    day = parse_date(data.get("date"), None)
    number = _int_param(data.get("number"))
    kind = data.get("kind")

    if day is None or number not in range(1, 7) or kind not in CHANGE_KINDS:
        raise Http404

    # Прежние адреса со страницей изменения ведут в режим правок
    if request.method != "POST":
        if kind in ("move", "replace"):
            return _changes_page(
                group.pk, day, act=kind, slot=day.isoformat(), number=number
            )

        return _changes_page(group.pk, day)

    reason = _off_reason(day, _holidays(day, day))

    if reason:
        messages.error(request, f"В этот день пар нет. {reason}")
        return _changes_page(group.pk, day)

    record = group_days_by_number(group.pk, day.isoweekday()).get(number)

    if record is None:
        messages.error(
            request,
            "В этом слоте нет занятия, которое можно изменить. "
            "Перенесённую пару меняют в её исходный день.",
        )
        return _changes_page(group.pk, day)

    changes = load_changes(day, day)
    existing = find_change(
        index_changes(changes), day, group.pk, record.pair_id, number
    )

    if kind == "restore":
        _clear_slot_changes(day, record, number, existing)
        messages.success(
            request,
            f"Правка снята: пара {number} на {short_label(day)} "
            f"снова идёт по основному расписанию.",
        )
        return _changes_page(group.pk, day)

    if kind == "cancel":
        _save_cancel(day, record, number, existing)
        messages.success(
            request,
            f"Пара {number} ({record.pair.pair_name}) "
            f"отменена на {short_label(day)}.",
        )
        return _changes_page(group.pk, day)

    if kind == "replace":
        error = _save_replace(
            request.POST, day, record, number, existing, changes
        )
        field_names = ("new_teacher", "new_pair_name")
    else:
        error = _save_move(
            request.POST, group, day, record, number, existing, changes
        )
        field_names = ("new_date", "new_number")

    if error:
        messages.error(request, error)

        return _changes_page(
            group.pk,
            day,
            act=kind,
            slot=day.isoformat(),
            number=number,
            **{name: request.POST.get(name, "") for name in field_names},
        )

    messages.success(
        request,
        "Замена сохранена." if kind == "replace" else "Перенос сохранён.",
    )

    return _changes_page(group.pk, day)


# ---------------------------------------------------------------------------
# Пользователи и роли
# ---------------------------------------------------------------------------

USERS_PER_PAGE = 15

# Префикс полей формы «Новый пользователь»
NEW_USER_FORM_PREFIX = "new_user"

# Роль и группа, выбранные при добавлении, хранятся в сессии. Благодаря
# этому они не сбрасываются ни после добавления, ни при обновлении страницы
# (сбрасываются только ФИО, почта и пароль).
NEW_USER_GROUP_SESSION_KEY = "new_user_group_id"
NEW_USER_ROLE_SESSION_KEY = "new_user_role"

NEW_USER_ROLES = (ROLE_STUDENT, ROLE_TEACHER)


def _remembered_new_user(request):
    """Начальные значения формы: роль и группа прошлого добавления."""
    role = request.session.get(NEW_USER_ROLE_SESSION_KEY)

    if role not in NEW_USER_ROLES:
        role = ROLE_STUDENT

    initial = {"role": role}

    # Группа запоминается для студентов: преподавателю она не нужна
    # (выбранная группа после его добавления уже занята куратором)
    if role == ROLE_STUDENT:
        initial["group"] = _int_param(
            request.session.get(NEW_USER_GROUP_SESSION_KEY)
        )

    return initial


def _remember_new_user(request, form):
    """Запоминает роль и (для студента) группу из формы."""
    cleaned = getattr(form, "cleaned_data", {})
    role = cleaned.get("role")
    group = cleaned.get("group")

    if role in NEW_USER_ROLES:
        request.session[NEW_USER_ROLE_SESSION_KEY] = role

        if role == ROLE_STUDENT and group is not None:
            request.session[NEW_USER_GROUP_SESSION_KEY] = group.pk


MAX_USER_SEARCH = 100
USER_ROLE_FILTERS = (ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN)


def _users_url(page="", query="", new=None):
    """Адрес списка: страница, фильтры (готовая строка) и новый пользователь."""
    params = []

    if query:
        params.append(query)

    if str(page).isdigit():
        params.append(f"page={page}")

    if new:
        params.append(f"new={new}")

    url = reverse("users")

    return f"{url}?{'&'.join(params)}" if params else url


def _parse_tag_filter(raw, group_tag_ids, teacher_tag_ids):
    """Тег из адреса: «g5» — тег группы, «t3» — тег преподавателя."""
    raw = (raw or "").strip()
    number = _int_param(raw[1:])

    if raw[:1] == "g" and number in group_tag_ids:
        return f"g{number}"

    if raw[:1] == "t" and number in teacher_tag_ids:
        return f"t{number}"

    return ""


def _parse_user_filters(params, group_ids, group_tag_ids, teacher_tag_ids):
    """Фильтры списка из адреса; всё неизвестное тихо отбрасывается."""
    role = params.get("role", "")
    group = _int_param(params.get("group"))

    return {
        "q": " ".join(params.get("q", "").split())[:MAX_USER_SEARCH],
        "role": role if role in USER_ROLE_FILTERS else "",
        "group": group if group in group_ids else None,
        "tag": _parse_tag_filter(
            params.get("tag"), group_tag_ids, teacher_tag_ids
        ),
        "nogroup": params.get("nogroup") == "1",
        "curators": params.get("curators") == "1",
        "sort": "name" if params.get("sort") == "name" else "new",
    }


def _filters_query(filters):
    """Строка адреса из фильтров (только заданные), без страницы."""
    params = []

    for key in ("q", "role", "group", "tag"):
        if filters[key] not in ("", None):
            params.append((key, filters[key]))

    for key in ("nogroup", "curators"):
        if filters[key]:
            params.append((key, "1"))

    if filters["sort"] != "new":
        params.append(("sort", filters["sort"]))

    return urlencode(params)


def _filter_users(queryset, filters):
    """Применяет фильтры к списку пользователей одним запросом к базе."""
    for word in filters["q"].split()[:5]:
        queryset = queryset.filter(
            Q(last_name__icontains=word)
            | Q(first_name__icontains=word)
            | Q(username__icontains=word)
            | Q(email__icontains=word)
            | Q(profile__patronymic__icontains=word)
        )

    role = filters["role"]
    student = Q(is_staff=False) & (
        Q(profile__isnull=True) | Q(profile__role=ROLE_STUDENT)
    )

    if role == ROLE_ADMIN:
        queryset = queryset.filter(is_staff=True)
    elif role == ROLE_TEACHER:
        queryset = queryset.filter(
            is_staff=False, profile__role=ROLE_TEACHER
        )
    elif role == ROLE_STUDENT:
        queryset = queryset.filter(student)

    if filters["group"]:
        queryset = queryset.filter(
            is_staff=False,
            profile__role=ROLE_STUDENT,
            profile__group_id=filters["group"],
        )

    if filters["nogroup"]:
        queryset = queryset.filter(student).filter(
            Q(profile__isnull=True) | Q(profile__group__isnull=True)
        )

    if filters["curators"]:
        queryset = queryset.filter(
            is_staff=False,
            profile__role=ROLE_TEACHER,
            profile__teacher_id__in=Group.objects.filter(
                group_curator__isnull=False
            ).values("group_curator_id"),
        )

    if filters["tag"]:
        kind, tag = filters["tag"][0], int(filters["tag"][1:])

        if kind == "t":
            # Тег преподавателя: сами преподаватели
            queryset = queryset.filter(
                is_staff=False,
                profile__role=ROLE_TEACHER,
                profile__teacher__teacher_tag_id=tag,
            )
        else:
            # Тег группы: студенты этой группы
            queryset = queryset.filter(
                is_staff=False,
                profile__role=ROLE_STUDENT,
                profile__group__group_tag_id=tag,
            )

    return queryset


def _save_user(request, User, group_by_id, teacher_tag_by_id, valid_roles):
    """Одна кнопка «Сохранить» в строке пользователя.

    Из формы приходит только то, что есть в строке: роль (если её можно
    менять), группа (у студента) или тег (у преподавателя). Если роль
    изменилась, группа и тег в этом же запросе не трогаются: они относятся
    к прежней роли.
    """
    post = request.POST
    target = User.objects.filter(
        pk=_int_param(post.get("user_id"))
    ).first()

    if target is None:
        messages.error(request, "Пользователь не найден.")

        return

    name = target.get_username()
    done = False

    # Роль
    new_role = post.get("role") if "role" in post else None

    if new_role is not None and new_role not in valid_roles:
        messages.error(request, "Выберите роль из списка.")

        return

    if new_role is not None and new_role != user_role(target):
        if target.pk == request.user.pk:
            messages.error(request, "Свою роль изменить нельзя.")

        elif target.is_superuser:
            messages.error(
                request,
                "Роль суперпользователя меняется только в админке.",
            )

        else:
            set_role(target, new_role)
            messages.success(
                request, f"Пользователь {name}: {role_label(new_role)}."
            )

        return

    # Группа студента
    if "group" in post:
        raw_group = post.get("group", "").strip()
        group = group_by_id.get(_int_param(raw_group))

        if raw_group and group is None:
            messages.error(request, "Выберите группу из списка.")

            return

        if not set_group(target, group):
            messages.error(request, "Группа назначается только студентам.")

            return

        done = True
        messages.success(
            request,
            f"Студент {name}: "
            + (f"группа {group}." if group else "группа не назначена."),
        )

    # Тег преподавателя
    if "tag" in post:
        raw_tag = post.get("tag", "").strip()
        tag = teacher_tag_by_id.get(_int_param(raw_tag))

        if raw_tag and tag is None:
            messages.error(request, "Выберите тег из списка.")

            return

        if not set_teacher_tag(target, tag):
            messages.error(request, "Тег назначается только преподавателям.")

            return

        done = True
        messages.success(
            request,
            f"Преподаватель {name}: "
            + (f"тег {tag.full_name}." if tag else "тег снят."),
        )

    if not done:
        messages.info(request, f"Пользователь {name}: изменений нет.")


@staff_required
def users_roles(request):
    User = get_user_model()

    groups = list(
        Group.objects.select_related("group_curator").order_by(
            "group_name", "group_id"
        )
    )
    group_by_id = {group.pk: group for group in groups}
    group_tags = list(
        GroupTag.objects.order_by("tag_name", "tag_id")
    )
    teacher_tags = list(
        TeacherTag.objects.order_by("tag_name", "tag_id")
    )
    teacher_tag_by_id = {tag.pk: tag for tag in teacher_tags}
    group_tag_ids = {tag.pk for tag in group_tags}
    valid_roles = {value for value, _ in ROLE_CHOICES}

    page = request.POST.get("page") or request.GET.get("page") or ""
    new_user_form = None

    if request.method == "POST":
        action = request.POST.get("action", "")

        # Новый пользователь: роль, ФИО, почта Gmail, пароль и группа
        if action == "create_user":
            new_user_form = NewUserForm(
                request.POST,
                prefix=NEW_USER_FORM_PREFIX,
            )

            if new_user_form.is_valid():
                try:
                    created = new_user_form.save()

                except DatabaseError:
                    new_user_form.add_error(
                        None,
                        "Не удалось создать пользователя. "
                        "Попробуйте ещё раз.",
                    )

                else:
                    _remember_new_user(request, new_user_form)
                    cleaned = new_user_form.cleaned_data
                    who = f"{full_name(created)} ({created.get_username()})"
                    group = cleaned["group"]

                    if cleaned["role"] == ROLE_TEACHER:
                        text = f"Преподаватель {who} добавлен"

                        if group is not None:
                            text += f", куратор группы {group}"
                    else:
                        text = f"Студент {who} добавлен в группу {group}"

                    messages.success(request, text + ".")

                    # Новый человек в начале списка и подсвечен; страница и
                    # фильтры сбрасываются, чтобы он точно был виден
                    return redirect(_users_url(new=created.pk))

            # При ошибке форма показывается снова с введёнными данными
            _remember_new_user(request, new_user_form)

        elif action == "save_user":
            _save_user(
                request, User, group_by_id, teacher_tag_by_id, valid_roles
            )
            kept = _parse_user_filters(
                QueryDict(request.POST.get("qs", "")), set(group_by_id),
                group_tag_ids, set(teacher_tag_by_id),
            )

            return redirect(_users_url(page, _filters_query(kept)))

        else:
            messages.error(request, "Неизвестное действие.")

            return redirect(_users_url(page))

    if new_user_form is None:
        new_user_form = NewUserForm(
            prefix=NEW_USER_FORM_PREFIX,
            initial=_remembered_new_user(request),
        )

    filters = _parse_user_filters(
        request.GET, set(group_by_id), group_tag_ids, set(teacher_tag_by_id)
    )
    ordering = (
        ("last_name", "first_name", "username", "pk")
        if filters["sort"] == "name" else ("-pk",)
    )
    paginator = Paginator(
        _filter_users(
            User.objects.select_related("profile", "profile__teacher"),
            filters,
        ).order_by(*ordering),
        USERS_PER_PAGE,
    )
    page_obj = paginator.get_page(page)
    new_id = _int_param(request.GET.get("new"))

    # Кто какие группы курирует — из уже загруженного списка групп
    curated = {}

    for group in groups:
        if group.group_curator_id:
            curated.setdefault(group.group_curator_id, []).append(group)

    rows = []

    for item in page_obj.object_list:
        role = user_role(item)
        current_teacher_id = own_teacher_id(item)
        current_group = group_by_id.get(own_group_id(item))

        rows.append({
            "user": item,
            "full_name": full_name(item),
            "role": role,
            "role_label": role_label(role),
            "is_student": role == ROLE_STUDENT,
            "is_teacher": role == ROLE_TEACHER,
            "is_new": item.pk == new_id,
            "teacher_id": current_teacher_id,
            "teacher_tag_id": (
                item.profile.teacher.teacher_tag_id
                if current_teacher_id else None
            ),
            "group": current_group,
            "curator": (
                current_group.group_curator
                if current_group and role == ROLE_STUDENT else None
            ),
            "curated": curated.get(current_teacher_id, []),
            "last_login": item.last_login,
            "locked": (
                item.is_superuser or item.pk == request.user.pk
            ),
        })

    context = {
        "rows": rows,
        "groups": groups,
        "teacher_tags": teacher_tags,
        "tag_filter_groups": [
            ("Теги групп", [(f"g{tag.pk}", tag.tag_name) for tag in group_tags]),
            (
                "Теги преподавателей",
                [(f"t{tag.pk}", tag.tag_name) for tag in teacher_tags],
            ),
        ],
        "new_user_form": new_user_form,
        "role_choices": ROLE_CHOICES,
        "page_obj": page_obj,
        "page_numbers": _page_numbers(paginator, page_obj),
        "filters": filters,
        "filter_query": _filters_query(filters),
        "has_filters": bool(_filters_query({**filters, "sort": "new"})),
        "found_count": paginator.count,
        "user_role_filters": [
            (value, role_label(value)) for value in USER_ROLE_FILTERS
        ],
    }

    return render(request, "users.html", context)


# ---------------------------------------------------------------------------
# Мастерская (только для персонала)
# ---------------------------------------------------------------------------

WORKSHOP_FORMS = {
    "group_tag": GroupTagForm,
    "teacher_tag": TeacherTagForm,
    "group": GroupForm,
    "pair": PairForm,
    "semester": SemesterForm,
}

FORM_PREFIXES = {
    "group_tag": "group_tag",
    "teacher_tag": "teacher_tag",
    "group": "group",
    "pair": "pair",
    "semester": "semester",
}


def _build_workshop_forms(data=None, active_key=None):
    return {
        key: form_class(
            data=data if key == active_key else None,
            prefix=FORM_PREFIXES[key],
        )
        for key, form_class in WORKSHOP_FORMS.items()
    }


# Разделы Кузни: ключ, заголовок, действие «Добавить», что выбирать
WORKSHOP_CARDS = (
    ("group_tag", "Теги групп", "add_group_tag", "тег"),
    ("teacher_tag", "Теги преподавателей", "add_teacher_tag", "тег"),
    ("group", "Группы", "add_group", "группу"),
    ("pair", "Пары", "add_pair", "пару"),
    ("semester", "Семестры", "add_semester", "семестр"),
)

EDIT_ACTIONS = ("update_item", "delete_item", "delete_confirmed")


def _workshop_url(shift_id, mode, **extra):
    """Адрес Кузни: смена и режим звонков сохраняются, лишнее отбрасывается."""
    query = {"time_shift": shift_id, "time_mode": mode}
    query.update({k: v for k, v in extra.items() if v not in (None, "")})

    return f"{reverse('workshop')}?{urlencode(query)}"


def _make_edit_form(key, item, data=None):
    """Форма раздела, заполненная данными выбранной записи."""
    form = WORKSHOP_FORMS[key](
        data=data,
        instance=item,
        prefix=FORM_PREFIXES[key],
    )

    return form


def _edit_state(request, key, raw_item, action):
    """Режим редактирования одного раздела.

    Возвращает (состояние, ошибка, успех). Состояние описывает выбранную
    запись, форму с её настройками и стадию подтверждения удаления.
    """
    error = ""
    success = ""

    item = find_item(key, raw_item) if raw_item else None
    form = None
    confirm = False
    confirm_text = ""

    if raw_item and item is None:
        error = "Запись не найдена: возможно, её уже удалили."

    elif item is not None and request.method == "POST":
        label = item_label(key, item)

        if action == "update_item":
            old_teacher_id = getattr(item, "teacher_id", None)
            form = _make_edit_form(key, item, request.POST)

            if not form.is_valid():
                error = "Проверьте заполненные поля."

            else:
                new_teacher = form.cleaned_data.get("teacher")

                warning = ""

                if (
                    key == "pair"
                    and new_teacher is not None
                    and new_teacher.pk != old_teacher_id
                ):
                    warning = pair_teacher_conflict(item, new_teacher)

                if not error:
                    try:
                        with transaction.atomic():
                            form.save()

                    except (DatabaseError, ValueError) as exc:
                        error = f"Не удалось сохранить изменения: {exc}"

                    else:
                        item = find_item(key, item.pk)
                        label = item_label(key, item)
                        success = f"Изменения сохранены: {label}."
                        form = None

                        if warning:
                            messages.warning(
                                request, f"Совпадение у преподавателя. {warning}"
                            )

        elif action == "delete_item":
            error = delete_blocker(key, item)

            if not error:
                confirm = True
                confirm_text = f"Удалить «{label}»? {delete_warning(key, item)}"

        elif action == "delete_confirmed":
            error = delete_blocker(key, item)

            if not error:
                try:
                    with transaction.atomic():
                        item.delete()

                except ProtectedError:
                    error = (
                        "Нельзя удалить: запись используется "
                        "в других данных."
                    )

                except DatabaseError as exc:
                    error = f"Не удалось удалить запись: {exc}"

                else:
                    success = f"Удалено: {label}."
                    item = None

    # Форма со свойствами выбранной записи (если нет формы с ошибками)
    if item is not None and form is None and not confirm:
        form = _make_edit_form(key, item)

    state = {
        "key": key,
        "item": item,
        "form": form,
        "confirm": confirm,
        "confirm_text": confirm_text,
    }

    return state, error, success


def _build_cards(forms, edit, shift_id, mode):
    """Данные карточек Кузни для шаблона: добавление или редактирование."""
    cards = {}

    for key, title, add_action, noun in WORKSHOP_CARDS:
        card = {
            "key": key,
            "title": title,
            "noun": noun,
            "add_action": add_action,
            "editing": False,
            "form": forms[key],
            "edit_url": _workshop_url(shift_id, mode, edit=key),
            "cancel_url": _workshop_url(shift_id, mode),
        }

        if edit is not None and edit["key"] == key:
            item = edit["item"]

            card.update({
                "editing": True,
                "form": edit["form"],
                "item": item,
                "confirm": edit["confirm"],
                "confirm_text": edit["confirm_text"],
                "back_url": (
                    _workshop_url(shift_id, mode, edit=key, item=item.pk)
                    if item is not None else ""
                ),
                "items": [
                    {
                        "label": item_label(key, obj),
                        "url": _workshop_url(
                            shift_id, mode, edit=key, item=obj.pk
                        ),
                        "selected": item is not None and obj.pk == item.pk,
                    }
                    for obj in edit_items(key)
                ],
            })

        cards[key] = card

    return cards


def _form_has_input(form):
    if not form.is_bound:
        return False

    for field_name in form.fields:
        value = form.data.get(form.add_prefix(field_name), "")

        if value is not None and str(value).strip():
            return True

    return False


def _format_time(value):
    return value.strftime("%H:%M") if value else ""


def _selected_time_settings(request, shifts):
    valid_shift_ids = {shift.shift_id for shift in shifts}
    default_shift_id = shifts[0].shift_id if shifts else 1

    # Из формы (POST) или из ссылки режима редактирования (GET)
    source = request.POST if request.method == "POST" else request.GET

    try:
        shift_id = int(
            source.get("time_shift", default_shift_id)
        )
    except (TypeError, ValueError):
        shift_id = default_shift_id

    if shift_id not in valid_shift_ids:
        shift_id = default_shift_id

    mode = source.get("time_mode", "study")

    if mode not in {"study", "break"}:
        mode = "study"

    return shift_id, mode


def _time_table_context(
    shift_id,
    mode,
    post_data=None,
    preserve_post=False,
):
    if mode == "break":
        records = {
            item.break_number: item
            for item in BreakHours.objects.filter(
                shift_id=shift_id
            )
        }
        row_count = 5
        number_label = "Номер перемены"
    else:
        records = {
            item.pair_number: item
            for item in StudyHours.objects.filter(
                shift_id=shift_id
            )
        }
        row_count = 6
        number_label = "Номер пары"

    rows = []

    for number in range(1, row_count + 1):
        record = records.get(number)

        if mode == "break":
            start = (
                _format_time(record.break_start_time)
                if record else ""
            )
            end = (
                _format_time(record.break_end_time)
                if record else ""
            )
        else:
            start = (
                _format_time(record.pair_start_time)
                if record else ""
            )
            end = (
                _format_time(record.pair_end_time)
                if record else ""
            )

        start_key = f"time_{number}_start"
        end_key = f"time_{number}_end"

        if preserve_post and post_data is not None:
            start = post_data.get(start_key, start)
            end = post_data.get(end_key, end)

        rows.append({
            "number": number,
            "start_key": start_key,
            "end_key": end_key,
            "start": start,
            "end": end,
        })

    return {
        "rows": rows,
        "number_label": number_label,
        "mode": mode,
    }


def _parse_time_updates(request, shift_id, mode):
    updates = []
    errors = []
    row_count = 5 if mode == "break" else 6

    for number in range(1, row_count + 1):
        start_raw = request.POST.get(
            f"time_{number}_start", ""
        ).strip()

        end_raw = request.POST.get(
            f"time_{number}_end", ""
        ).strip()

        if not start_raw and not end_raw:
            continue

        if not start_raw or not end_raw:
            errors.append(
                f"Строка {number}: укажите начало и окончание."
            )
            continue

        try:
            start = datetime.strptime(
                start_raw, "%H:%M"
            ).time()

            end = datetime.strptime(
                end_raw, "%H:%M"
            ).time()

        except ValueError:
            errors.append(
                f"Строка {number}: некорректное время."
            )
            continue

        if start >= end:
            errors.append(
                f"Строка {number}: начало должно быть раньше окончания."
            )
            continue

        updates.append({
            "shift_id": shift_id,
            "number": number,
            "start": start,
            "end": end,
            "mode": mode,
        })

    return updates, errors


def _save_time_updates(updates):
    for item in updates:
        if item["mode"] == "break":
            BreakHours.objects.update_or_create(
                shift_id=item["shift_id"],
                break_number=item["number"],
                defaults={
                    "break_start_time": item["start"],
                    "break_end_time": item["end"],
                },
            )
        else:
            StudyHours.objects.update_or_create(
                shift_id=item["shift_id"],
                pair_number=item["number"],
                defaults={
                    "pair_start_time": item["start"],
                    "pair_end_time": item["end"],
                },
            )


def _run_import(request):
    """Загрузка CSV из формы Кузни. Возвращает отчёт для карточки."""
    kind = request.POST.get("kind", "")
    dry_run = request.POST.get("dry_run") == "1"
    replace = request.POST.get("mode") == "replace"
    confirmed = request.POST.get("confirm_replace") == "1"
    upload = request.FILES.get("file")

    if upload is None:
        report = import_csv(kind, b"", dry_run)

        if kind in KINDS:
            report["fatal"] = "выберите файл CSV"

        return report

    # На один байт больше предела: так видно, что файл слишком большой
    return import_csv(
        kind, upload.read(max_bytes(kind) + 1), dry_run,
        replace=replace, confirmed=confirmed,
    )


@staff_required
def workshop_export(request):
    """Скачивание данных Кузни в CSV (то же читает импорт)."""
    kind = request.GET.get("kind", "")

    if kind not in KINDS:
        raise Http404

    template = request.GET.get("template") == "1"

    response = HttpResponse(
        build_csv(kind, template=template),
        content_type="text/csv; charset=utf-8",
    )
    name = f"raspisite_{kind}{'_template' if template else ''}.csv"
    response["Content-Disposition"] = f'attachment; filename="{name}"'

    return response


@staff_required
def workshop_export_all(request):
    """Все наборы Кузни одним ZIP: те же CSV, что и при выгрузке по одному."""
    response = HttpResponse(build_zip(), content_type="application/zip")
    name = f"raspisite_{timezone.localdate().isoformat()}.zip"
    response["Content-Disposition"] = f'attachment; filename="{name}"'

    return response


@staff_required
def schedule_overview(request):
    """Общее расписание: строки — группы, столбцы — дни недели (Пн–Сб).

    Показывает постоянное недельное расписание (таблица Day), разовые правки
    сюда не входят. Клетки раскрашены в шахматном порядке, чтобы шесть дней
    одной группы не сливались. Клетка ведёт в редактор недели этой группы.
    """
    names = _weekday_names()
    columns = [
        {"number": number, "name": names.get(number, WEEKDAY_NAMES[number])}
        for number in range(1, 7)
    ]

    shifts = list(Shift.objects.order_by("shift_id"))
    selected_shift_id = _int_param(request.GET.get("shift"))

    if selected_shift_id not in {shift.shift_id for shift in shifts}:
        selected_shift_id = None

    groups = Group.objects.select_related(
        "shift",
        "group_course",
        "group_tag",
    ).order_by("shift_id", "group_name", "group_id")

    if selected_shift_id:
        groups = groups.filter(shift_id=selected_shift_id)

    groups = list(groups)

    lessons = {}

    days = (
        Day.objects
        .filter(
            group_id__in=[group.group_id for group in groups],
            weekday_id__in=range(1, 7),
            pair_number__range=(1, 6),
        )
        .select_related("pair", "pair__teacher")
        .order_by("pair_number")
    )

    for day in days:
        lessons.setdefault((day.group_id, day.weekday_id), []).append({
            "number": day.pair_number,
            "name": day.pair.pair_name,
            "teacher": day.pair.teacher.teacher_name,
        })

    rows = []

    for row_index, group in enumerate(groups):
        edit_url = reverse("group_edit", args=[group.group_id])

        cells = [
            {
                # Шахматный порядок: тон зависит от суммы номера строки и столбца
                "tone": (row_index + column_index) % 2,
                "lessons": lessons.get((group.group_id, column["number"]), []),
                "url": edit_url,
                "label": (
                    f"{group.group_name}, {column['name']}: "
                    "открыть редактор расписания группы"
                ),
            }
            for column_index, column in enumerate(columns)
        ]

        rows.append({"group": group, "cells": cells})

    return render(request, "overview.html", {
        "columns": columns,
        "rows": rows,
        "shifts": shifts,
        "selected_shift_id": selected_shift_id,
    })


@staff_required
def workshop(request):
    for course_id in range(1, 5):
        Course.objects.get_or_create(course_id=course_id)

    for shift_id in range(1, 3):
        Shift.objects.get_or_create(shift_id=shift_id)

    shifts = list(Shift.objects.order_by("shift_id"))

    action = (
        request.POST.get("action", "")
        if request.method == "POST"
        else ""
    )

    form_actions = {
        "add_group_tag": "group_tag",
        "add_teacher_tag": "teacher_tag",
        "add_group": "group",
        "add_pair": "pair",
        "add_semester": "semester",
    }

    active_key = form_actions.get(action)

    forms = _build_workshop_forms(
        data=request.POST if active_key else None,
        active_key=active_key,
    )

    selected_shift_id, selected_time_mode = (
        _selected_time_settings(request, shifts)
    )

    # Режим редактирования: из адреса (открытие) или скрытых полей (кнопки)
    source = request.POST if request.method == "POST" else request.GET
    edit_key = source.get("edit", "")

    if edit_key not in EDIT_KEYS or (
        request.method == "POST" and action not in EDIT_ACTIONS
    ):
        edit_key = ""

    edit = None
    error = ""
    success = ""
    preserve_post = False
    import_report = None

    if edit_key:
        edit, error, success = _edit_state(
            request, edit_key, source.get("item", ""), action
        )

    if request.method == "POST" and not edit_key:

        # Добавление записи из выбранной формы
        if active_key:
            form = forms[active_key]

            if not _form_has_input(form):
                error = "Заполните форму перед добавлением."

            elif form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()

                    success = "Запись добавлена."

                    forms[active_key] = WORKSHOP_FORMS[active_key](
                        prefix=FORM_PREFIXES[active_key]
                    )

                except (DatabaseError, ValueError) as exc:
                    error = f"Не удалось добавить запись: {exc}"

            else:
                error = "Проверьте заполненные поля."

        # Сохранение расписания звонков
        elif action == "save_times":
            updates, time_errors = _parse_time_updates(
                request,
                selected_shift_id,
                selected_time_mode,
            )

            if time_errors:
                error = " ".join(time_errors)

            elif not updates:
                error = "Укажите хотя бы одно время."

            else:
                try:
                    with transaction.atomic():
                        _save_time_updates(updates)

                    success = "Расписание звонков сохранено."

                except (DatabaseError, ValueError) as exc:
                    error = f"Не удалось сохранить расписание: {exc}"

            preserve_post = bool(error)

        # Загрузка CSV: отчёт показывается в карточке «Импорт и экспорт»
        elif action == "import_csv":
            import_report = _run_import(request)

        # show_time переключает смену или режим,
        # не сохраняя введённые значения.

    # После успешного действия — переход на обычную страницу (GET), а не
    # показ ответа на POST: обновление страницы (F5) не отправляет форму
    # повторно, а поля добавления не остаются заполненными.
    if request.method == "POST" and success and not error:
        messages.success(request, success)
        extra = {}

        if edit_key:
            extra["edit"] = edit_key

            if edit is not None and edit["item"] is not None:
                extra["item"] = edit["item"].pk

        return redirect(
            _workshop_url(selected_shift_id, selected_time_mode, **extra)
        )

    context = _build_workshop_context(
        forms=forms,
        edit=edit,
        import_report=import_report,
        import_kind=request.POST.get("kind", "") if import_report else "",
        shifts=shifts,
        selected_shift_id=selected_shift_id,
        selected_time_mode=selected_time_mode,
        error=error,
        success=success,
        post_data=request.POST if action == "save_times" else None,
        preserve_post=preserve_post,
    )

    return render(request, "workshop.html", context)


def _build_workshop_context(
    forms,
    edit,
    import_report,
    import_kind,
    shifts,
    selected_shift_id,
    selected_time_mode,
    error="",
    success="",
    post_data=None,
    preserve_post=False,
):
    return {
        "group_tag_form": forms["group_tag"],
        "teacher_tag_form": forms["teacher_tag"],
        "group_form": forms["group"],
        "pair_form": forms["pair"],
        "semester_form": forms["semester"],
        "cards": _build_cards(
            forms, edit, selected_shift_id, selected_time_mode
        ),
        "data_kinds": kind_choices(),
        "import_report": import_report,
        "import_kind": import_kind,
        "shifts": shifts,
        "selected_shift_id": selected_shift_id,
        "selected_time_mode": selected_time_mode,
        "time_table": _time_table_context(
            selected_shift_id,
            selected_time_mode,
            post_data=post_data,
            preserve_post=preserve_post,
        ),
        "error": error,
        "success": success,
    }