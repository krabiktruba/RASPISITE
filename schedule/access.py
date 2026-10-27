"""Роли пользователей и их привязка к преподавателю или группе."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from .models import (
    Group,
    ROLE_ADMIN,
    ROLE_CHOICES,
    ROLE_STUDENT,
    ROLE_TEACHER,
    Profile,
    Teacher,
)

def role_label(role):
    return dict(ROLE_CHOICES).get(role, "")


def _profile(user):
    try:
        return user.profile
    except ObjectDoesNotExist:
        return None


def user_role(user):
    """Роль пользователя. Права персонала (is_staff) означают администратора."""
    if not user.is_authenticated:
        return ""

    if user.is_staff:
        return ROLE_ADMIN

    profile = _profile(user)

    return profile.role if profile else ROLE_STUDENT


def own_teacher_id(user):
    """ID записи Teacher, привязанной к преподавателю, иначе None."""
    if not user.is_authenticated:
        return None

    profile = _profile(user)

    if profile and profile.role == ROLE_TEACHER:
        return profile.teacher_id

    return None


def own_group_id(user):
    """ID группы, к которой привязан студент, иначе None."""
    if not user.is_authenticated:
        return None

    profile = _profile(user)

    if profile and profile.role == ROLE_STUDENT:
        return profile.group_id

    return None


def home_group_id(user):
    """ID группы для пункта меню «Расписание», иначе None.

    Студент — его группа. Преподаватель — группа, которую он курирует
    (если таких несколько, берётся первая по названию). У администратора
    и у пользователей без привязки группы нет.
    """
    group_id = own_group_id(user)

    if group_id:
        return group_id

    teacher_id = own_teacher_id(user)

    if not teacher_id:
        return None

    return (
        Group.objects
        .filter(group_curator_id=teacher_id)
        .order_by("group_name", "group_id")
        .values_list("group_id", flat=True)
        .first()
    )


def set_role(user, role, teacher=None):
    """Назначает роль пользователю.

    Администратор отличается правом персонала is_staff, поэтому оно
    выставляется и снимается вместе с ролью. Преподаватель всегда связан
    с записью Teacher: если её не указали, берётся прежняя или создаётся
    новая по ФИО пользователя.
    """
    profile, _ = Profile.objects.get_or_create(user=user)

    if role == ROLE_TEACHER and teacher is None:
        teacher = profile.teacher or Teacher.objects.create(
            teacher_name=full_name(user, profile) or user.get_username()
        )

    profile.role = role
    profile.teacher = teacher if role == ROLE_TEACHER else None

    if role != ROLE_STUDENT:
        profile.group = None

    profile.save()

    should_be_staff = role == ROLE_ADMIN

    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        user.save(update_fields=["is_staff"])


def set_group(user, group):
    """Привязывает студента к группе (None — открепить).

    Группа есть только у студентов. Для остальных ролей возвращает False
    и ничего не меняет.
    """
    if user_role(user) != ROLE_STUDENT:
        return False

    profile, _ = Profile.objects.get_or_create(user=user)
    profile.group = group
    profile.save(update_fields=["group"])

    return True


def real_teachers():
    """Преподаватели, у которых есть учётная запись (роль «Преподаватель»).

    Только их можно выбирать куратором группы и преподавателем пары.
    """
    return Teacher.objects.filter(profile__role=ROLE_TEACHER).distinct()


def full_name(user, profile=None):
    """ФИО пользователя: «Фамилия Имя Отчество» (отчество не обязательно)."""
    if profile is None:
        profile = _profile(user)

    parts = [
        user.last_name,
        user.first_name,
        profile.patronymic if profile else "",
    ]

    return " ".join(part for part in parts if part)


def create_account(role, last_name, first_name, patronymic, email,
                   password, group=None):
    """Создаёт студента или преподавателя: пользователя, профиль и связи.

    Логин — почта Gmail (email уже приведён к единому виду).
    Студент привязывается к группе. Преподаватель получает запись Teacher
    с полным ФИО и, если выбрана группа, становится её куратором.
    """
    User = get_user_model()

    with transaction.atomic():
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        if role == ROLE_TEACHER:
            teacher = Teacher.objects.create(
                teacher_name=" ".join(
                    part for part in (last_name, first_name, patronymic)
                    if part
                )
            )
            Profile.objects.create(
                user=user,
                role=ROLE_TEACHER,
                teacher=teacher,
                patronymic=patronymic,
            )

            if group is not None:
                group.group_curator = teacher
                group.save(update_fields=["group_curator"])

        else:
            Profile.objects.create(
                user=user,
                role=ROLE_STUDENT,
                group=group,
                patronymic=patronymic,
            )

    return user
