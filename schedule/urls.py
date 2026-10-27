from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import GmailLoginForm


urlpatterns = [
    # Главная страница (логотип в шапке)
    path(
        "",
        views.main_page,
        name="home",
    ),

    # Авторизация
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="login.html",
            authentication_form=GmailLoginForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),

    # Роли пользователей
    path(
        "users/",
        views.users_roles,
        name="users",
    ),

    # Расписание
    path(
        "calendar/",
        views.calendar_view,
        name="calendar",
    ),
    path(
        "groups/<int:group_id>/schedule/",
        views.group_schedule,
        name="group_schedule",
    ),
    path(
        "groups/<int:group_id>/change/",
        views.group_change,
        name="group_change",
    ),
    path(
        "groups/<int:group_id>/edit/",
        views.group_edit,
        name="group_edit",
    ),
    path(
        "groups/",
        views.daily_schedule,
        name="groups",
    ),
    path(
        "teachers/",
        views.teachers_schedule,
        name="teachers",
    ),
    path(
        "workshop/export/",
        views.workshop_export,
        name="workshop_export",
    ),
    path(
        "workshop/",
        views.workshop,
        name="workshop",
    ),
]
