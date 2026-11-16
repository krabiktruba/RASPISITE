"""Данные, которые нужны в шапке каждой страницы."""

from django.urls import reverse

from .access import (
    ROLE_ADMIN,
    ROLE_STUDENT,
    ROLE_TEACHER,
    home_group_id,
    own_group_id,
    own_teacher_id,
    user_role,
)
from .modes import MODE_DIARY, MODE_SCHEDULE, read_mode

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
    "diary_journal": lambda kwargs: reverse("diary_home"),
    "diary_journal_general": lambda kwargs: reverse("diary_home"),
    "calendar_year": lambda kwargs: reverse("calendar"),
}


# Зоны сайта: ключ, подпись в шапке и страницы зоны (по именам адресов).
# Дневник и расписание не пересекаются; «Общее» принадлежит обеим сразу,
# поэтому у него свой цвет и своё название.
ZONES = (
    (
        "schedule",
        "Расписание",
        {"groups", "group_schedule", "group_change", "group_edit", "overview"},
    ),
    (
        "diary",
        "Дневник",
        {"diary_home", "diary_group", "diary_journal", "diary_journal_general"},
    ),
    (
        "common",
        "Общее",
        {
            "teachers", "teacher_card", "student_card", "group_card",
            "calendar", "calendar_year", "reference",
        },
    ),
    (
        "admin",
        "Управление",
        {"users", "workshop", "workshop_export", "workshop_export_all"},
    ),
)


def zone_of(url_name):
    """(ключ, подпись) зоны страницы или (None, None) для остальных."""
    for key, label, names in ZONES:
        if url_name in names:
            return key, label

    return None, None


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


def home_url(user, mode=MODE_SCHEDULE):
    """Куда ведёт самая заметная кнопка меню «Моё расписание».

    Студент и куратор — в расписание своей группы; студент, который в прошлый
    раз выбрал «Дневник», попадает в свой дневник. Преподаватель без группы —
    в собственное расписание преподавателя. Администратор и все остальные —
    на «Группы на сегодня».
    """
    if mode == MODE_DIARY and own_group_id(user):
        return reverse("diary_home")

    group_id = home_group_id(user)

    if group_id:
        return reverse("group_schedule", kwargs={"group_id": group_id})

    teacher_id = own_teacher_id(user)

    if teacher_id:
        return f"{reverse('teachers')}?teacher={teacher_id}"

    return reverse("groups")


def my_group(request):
    """Группа, профиль и разделы меню пользователя.

    my_group_id — группа для «Моё расписание»; my_home_url и my_home_label —
    куда ведёт эта кнопка и как подписана (у студента с запомненным режимом
    «Дневник» это «Мой дневник»); my_schedule_url — всегда расписание (для
    выбора на главной); my_card_id — «Мой профиль» (только у студента, это его
    собственный id); nav_zone и nav_zone_label — раздел, в котором находится
    страница (подпись в шапке); nav_current — имя адреса для подсветки
    пункта меню; nav_can_journal — показывать журналы дневника;
    nav_back_url — запасной адрес кнопки «Назад» (её нет у гостя и на
    начальных страницах).
    """
    user = getattr(request, "user", None)

    if user is None or not user.is_authenticated:
        return {
            "my_group_id": None,
            "my_card_id": None,
            "my_home_url": None,
            "my_home_label": None,
            "my_schedule_url": None,
            "nav_back_url": None,
            "nav_zone": None,
            "nav_zone_label": None,
            "nav_current": None,
            "nav_can_journal": False,
        }

    role = user_role(user)
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else None
    zone, zone_label = zone_of(current)

    # Выбор «Дневник» запоминается только у студента с группой
    mode = read_mode(request) if role == ROLE_STUDENT else MODE_SCHEDULE
    home = home_url(user, mode)

    return {
        "my_group_id": home_group_id(user),
        "my_card_id": user.pk if role == ROLE_STUDENT else None,
        "my_home_url": home,
        "my_home_label": (
            "Мой дневник" if home == reverse("diary_home") and
            role == ROLE_STUDENT else "Моё расписание"
        ),
        "my_schedule_url": home_url(user),
        "nav_back_url": fallback_back_url(request),
        "nav_zone": zone,
        "nav_zone_label": zone_label,
        "nav_current": current,
        "nav_can_journal": role in (ROLE_TEACHER, ROLE_ADMIN),
    }
