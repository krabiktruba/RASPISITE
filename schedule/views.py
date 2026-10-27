from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import ProtectedError, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .access import (
    full_name,
    own_group_id,
    own_teacher_id,
    real_teachers,
    role_label,
    set_group,
    set_role,
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
    MAX_FILE_BYTES,
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
from .forms import (
    GroupForm,
    PairForm,
    NewUserForm,
    TagForm,
    TagTypeForm,
)
from .models import (
    ROLE_CHOICES,
    ROLE_STUDENT,
    ROLE_TEACHER,
    BreakHours,
    Calendar,
    Course,
    Day,
    Group,
    Pair,
    Profile,
    ScheduleChange,
    Shift,
    StudyHours,
    Teacher,
    Weekday,
)


# Сколько групп показывать на одной странице /groups/
GROUPS_PER_PAGE = 6


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
# Главная страница
# ---------------------------------------------------------------------------

@login_required
def main_page(request):
    return render(request, "mainpage.html")


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


@login_required
def group_schedule(request, group_id):
    group = get_object_or_404(
        Group.objects.select_related(
            "shift",
            "group_course"
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

    # Пагинация: на странице GROUPS_PER_PAGE групп
    paginator = Paginator(
        Group.objects.select_related(
            "shift",
            "group_course",
            "group_curator",
        ).order_by("group_name", "group_id"),
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
        Teacher.objects.order_by("teacher_name", "teacher_id")
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

    context = {
        "teachers": teachers,
        "shifts": shifts,
        "selected_teacher": selected_teacher,
        "selected_teacher_id": selected_teacher_id,
        "selected_shift": selected_shift,
        "selected_shift_id": selected_shift_id,
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

    # Пары группы; уже назначенные пары других групп тоже остаются в списке,
    # чтобы сохранение не стирало их молча
    pairs = list(
        Pair.objects.filter(
            Q(group=group)
            | Q(pk__in={day.pair_id for day in existing.values()})
        ).select_related("teacher").order_by("pair_name", "pair_id")
    )
    pair_by_id = {pair.pk: pair for pair in pairs}

    hours = {
        hour.pair_number: hour
        for hour in StudyHours.objects.filter(shift=group.shift)
    }

    selected = {
        slot: day.pair_id
        for slot, day in existing.items()
    }
    errors = []

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

        # Преподаватель не может вести две пары одновременно
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
                busy[key] = other.group.group_name

        for (weekday_id, number), pair_id in chosen.items():
            if pair_id is None:
                continue

            pair = pair_by_id[pair_id]
            other_group = busy.get((weekday_id, number, pair.teacher_id))

            if other_group:
                errors.append(
                    f"{slot_name(weekday_id, number)}: "
                    f"{pair.teacher.teacher_name} уже ведёт занятие "
                    f"у группы {other_group}."
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
    """Снимает все правки слота (дата, пара, номер пары)."""
    ScheduleChange.objects.filter(
        calendar__date=day,
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
        index_changes(changes), day, record.pair_id, number
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


def _users_url(page=""):
    url = reverse("users")

    return f"{url}?page={page}" if str(page).isdigit() else url


@staff_required
def users_roles(request):
    User = get_user_model()

    groups = list(
        Group.objects.order_by("group_name", "group_id")
    )
    group_by_id = {group.pk: group for group in groups}
    valid_roles = {value for value, _ in ROLE_CHOICES}

    # Преподаватели без учётной записи (остались от прежних версий):
    # их можно привязать к пользователю при смене роли
    linked_ids = set(
        Profile.objects.filter(teacher__isnull=False).values_list(
            "teacher_id", flat=True
        )
    )
    free_teachers = [
        teacher
        for teacher in Teacher.objects.order_by("teacher_name", "teacher_id")
        if teacher.pk not in linked_ids
    ]

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

                    return redirect(_users_url(page))

            # При ошибке форма показывается снова с введёнными данными
            _remember_new_user(request, new_user_form)

        else:
            role = request.POST.get("role", "")
            target = User.objects.filter(
                pk=_int_param(request.POST.get("user_id"))
            ).first()

            teacher_id = _int_param(request.POST.get("teacher"))
            allowed = list(free_teachers)

            if target is not None and own_teacher_id(target):
                allowed.append(Teacher.objects.get(pk=own_teacher_id(target)))

            teacher = next(
                (item for item in allowed if item.pk == teacher_id),
                None,
            )

            # Смена группы студента
            if action == "set_group":
                raw_group = request.POST.get("group", "").strip()
                group = group_by_id.get(_int_param(raw_group))

                if target is None:
                    messages.error(request, "Пользователь не найден.")

                elif raw_group and group is None:
                    messages.error(request, "Выберите группу из списка.")

                elif not set_group(target, group):
                    messages.error(
                        request,
                        "Группа назначается только студентам.",
                    )

                elif group is None:
                    messages.success(
                        request,
                        f"Студент {target.get_username()}: "
                        "группа не назначена.",
                    )

                else:
                    messages.success(
                        request,
                        f"Студент {target.get_username()}: "
                        f"группа {group}.",
                    )

            elif role not in valid_roles:
                messages.error(request, "Выберите роль из списка.")

            elif action == "set_role":
                if target is None:
                    messages.error(request, "Пользователь не найден.")

                elif target.pk == request.user.pk:
                    messages.error(
                        request, "Свою роль изменить нельзя."
                    )

                elif target.is_superuser:
                    messages.error(
                        request,
                        "Роль суперпользователя меняется только "
                        "в админке.",
                    )

                else:
                    set_role(target, role, teacher)
                    messages.success(
                        request,
                        f"Пользователь {target.get_username()}: "
                        f"{role_label(role)}.",
                    )

            else:
                messages.error(request, "Неизвестное действие.")

            return redirect(_users_url(page))

    if new_user_form is None:
        new_user_form = NewUserForm(
            prefix=NEW_USER_FORM_PREFIX,
            initial=_remembered_new_user(request),
        )

    paginator = Paginator(
        User.objects.select_related(
            "profile", "profile__teacher"
        ).order_by("username", "pk"),
        USERS_PER_PAGE,
    )
    page_obj = paginator.get_page(page)

    rows = []

    for item in page_obj.object_list:
        role = user_role(item)
        current_teacher_id = own_teacher_id(item)
        teacher_options = list(free_teachers)

        if current_teacher_id:
            teacher_options.append(item.profile.teacher)

        rows.append({
            "user": item,
            "full_name": full_name(item),
            "role": role,
            "role_label": role_label(role),
            "teacher_id": current_teacher_id,
            "teacher_options": teacher_options,
            "is_student": role == ROLE_STUDENT,
            "group": group_by_id.get(own_group_id(item)),
            "locked": (
                item.is_superuser or item.pk == request.user.pk
            ),
        })

    context = {
        "rows": rows,
        "groups": groups,
        "new_user_form": new_user_form,
        "role_choices": ROLE_CHOICES,
        "page_obj": page_obj,
        "page_numbers": _page_numbers(paginator, page_obj),
    }

    return render(request, "users.html", context)


# ---------------------------------------------------------------------------
# Мастерская (только для персонала)
# ---------------------------------------------------------------------------

WORKSHOP_FORMS = {
    "tag_type": TagTypeForm,
    "tag": TagForm,
    "group": GroupForm,
    "pair": PairForm,
}

FORM_PREFIXES = {
    "tag_type": "tag_type",
    "tag": "tag",
    "group": "group",
    "pair": "pair",
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
    ("tag", "Теги", "add_tag", "тег"),
    ("tag_type", "Типы тегов", "add_tag_type", "тип тега"),
    ("group", "Группы", "add_group", "группу"),
    ("pair", "Пары", "add_pair", "пару"),
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

    # Группу у существующей пары менять нельзя: расписание групп ссылается
    # на пары именно своей группы. Нужна другая группа — создайте новую пару.
    if key == "pair":
        form.fields["group"].disabled = True

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

                if (
                    key == "pair"
                    and new_teacher is not None
                    and new_teacher.pk != old_teacher_id
                ):
                    error = pair_teacher_conflict(item, new_teacher)

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
    upload = request.FILES.get("file")

    if upload is None:
        report = import_csv(kind, b"", dry_run)

        if kind in KINDS:
            report["fatal"] = "выберите файл CSV"

        return report

    # На один байт больше предела: так видно, что файл слишком большой
    return import_csv(kind, upload.read(MAX_FILE_BYTES + 1), dry_run)


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
        "add_tag": "tag",
        "add_tag_type": "tag_type",
        "add_group": "group",
        "add_pair": "pair",
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
        "tag_form": forms["tag"],
        "tag_type_form": forms["tag_type"],
        "group_form": forms["group"],
        "pair_form": forms["pair"],
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