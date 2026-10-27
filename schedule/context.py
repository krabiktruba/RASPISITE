"""Данные, которые нужны в шапке каждой страницы."""

from .access import home_group_id


def my_group(request):
    """Группа пользователя для пункта меню «Моё расписание»."""
    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
        return {"my_group_id": None}

    return {"my_group_id": home_group_id(user)}
