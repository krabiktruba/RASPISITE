"""Страница «Справочник»."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import reference


@login_required
def reference_page(request):
    """Справочные таблицы: все вошедшие смотрят, ссылки «Изменить» — админу."""
    tab, table = reference.build(request.GET.get("tab", ""))
    can_edit = request.user.is_staff

    if not can_edit:
        for row in table["rows"]:
            row["edit_url"] = ""

    return render(request, "reference.html", {
        "tabs": reference.TABS,
        "tab": tab,
        "tab_label": dict(reference.TABS)[tab],
        "columns": table["columns"],
        "rows": table["rows"],
        "can_edit": can_edit and any(row["edit_url"] for row in table["rows"]),
    })
