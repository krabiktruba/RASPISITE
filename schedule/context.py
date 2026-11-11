"""Данные, которые нужны в шапке каждой страницы."""

from django.urls import reverse

from .access import ROLE_STUDENT, home_group_id, user_role

# Страницы, где кнопки «Назад» нет: это начало пути
NO_BACK_PAGES = {"home", "login", "logout"}


def _group_schedule(kwargs):
    return reverse("group_schedule", kwargs={"group_id": kwargs["group_id"]})


# Куда ведёт «Назад», если вернуться по истории браузера нельзя
# (страницу открыли по прямой ссылке): логический «родитель» страницы
BACK_PARENTS = {
    "group_schedule": lambda kwargs: reverse("groups"),
    "group_change": _group_schedule,
    "group_edit": _group_schedule,
    "group_card": lambda kwargs: reverse("groups"),
    "teacher_card": lambda kwargs: reverse("teachers"),
    "student_card": lambda kwargs: reverse("users"),
    "diary_group": lambda kwargs: reverse("diary_home"),
    "calendar_year": lambda kwargs: reverse("calendar"),
}


def fallback_back_url(request):
    """Адрес для «Назад» без истории или None, если кнопка не нужна."""
    match = getattr(request, "resolver_match", None)
    name = match.url_name if match else None

    if name in NO_BACK_PAGES:
        return None

    parent = BACK_PARENTS.get(name)

    if parent is None:
        return reverse("home")

    try:
        return parent(match.kwargs)
    except (KeyError, LookupError):
        return reverse("home")


def my_group(request):
    """Группа и профиль пользователя для пунктов меню.

    my_group_id — «Моё расписание»; my_card_id — «Мой профиль»
    (только у студента, это его собственный id); nav_back_url — запасной адрес
    кнопки «Назад» (её нет у гостя и на начальных страницах).
    """
    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
        return {"my_group_id": None, "my_card_id": None, "nav_back_url": None}

    is_student = user_role(user) == ROLE_STUDENT

    return {
        "my_group_id": home_group_id(user),
        "my_card_id": user.pk if is_student else None,
        "nav_back_url": fallback_back_url(request),
    }
