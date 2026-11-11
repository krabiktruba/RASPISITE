"""Страницы «Журнал» и «Общий журнал»: оценки и статистика за период."""

from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from schedule.dates import week_start

from .journal import (
    COURSES,
    SEMESTERS,
    Filters,
    build_journal,
    journal_role,
)
from .views import MARK_COLUMN_WIDTH, STUDENT_COLUMN_WIDTH

# Узкие столбцы журнала, px (те же числа в style.css)
GROUP_COLUMN_WIDTH = 72
STAT_COLUMN_WIDTH = 64


def _url(name, filters, **override):
    return reverse(name) + "?" + urlencode(filters.params(**override))


def _page(request, general):
    """Общая часть двух страниц: доступ, фильтры, данные, адреса."""
    role = journal_role(request.user)

    if role is None:
        raise PermissionDenied

    name = "diary_journal_general" if general else "diary_journal"
    today = timezone.localdate()
    filters = Filters(request.GET, today)
    data = build_journal(request.user, role, filters, today, general=general)

    data.update({
        "filters": filters,
        "courses": COURSES,
        "semesters": SEMESTERS,
        "general": general,
        "form_action": reverse(name),
        "week_date": filters.monday.isoformat(),
        "prev_url": _url(name, filters, week=True,
                         date=filters.monday - timedelta(days=7)),
        "next_url": _url(name, filters, week=True,
                         date=filters.monday + timedelta(days=7)),
        "today_url": _url(name, filters, week=True, date=week_start(today)),
        "is_current_week": filters.monday == week_start(today),
        "reset_url": reverse(name),
        "has_filters": filters.submitted,
    })

    return data


@login_required
def diary_journal(request):
    """Журнал выбранных групп: студенты и оценки по занятиям.

    Администратор и преподаватель; студенту — только его дневник.
    """
    data = _page(request, general=False)
    data["table_width"] = (
        STUDENT_COLUMN_WIDTH + GROUP_COLUMN_WIDTH + 2 * STAT_COLUMN_WIDTH
        + MARK_COLUMN_WIDTH * len(data["columns"])
    )

    return render(request, "diary/journal.html", data)


@login_required
def diary_journal_general(request):
    """Общий журнал: по строке на группу — студентов, занятий, оценок, средний.

    Те же фильтры, что и в журнале; без выбранных групп — все доступные.
    Строка группы ведёт в журнал этой группы за тот же период.
    """
    data = _page(request, general=True)
    filters = data["filters"]

    for item in data["group_stats"]:
        item["url"] = _url(
            "diary_journal", filters, groups=[item["group"].pk]
        )

    scores = sum(item["scores"] for item in data["group_stats"])
    data["totals"] = {
        "students": sum(item["students"] for item in data["group_stats"]),
        "scores": scores,
        "average": data["average"],
    }

    return render(request, "diary/journal_general.html", data)
