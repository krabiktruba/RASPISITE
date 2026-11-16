"""Описание предмета: очистка текста и право его менять.

Описание хранится у пары (предмет с преподавателем). Менять его может
администратор и преподаватель этой пары. Текст показывается в подсказке
расписания и целиком в дневнике и журнале.
"""

import re

from django.core.exceptions import ValidationError

from .access import ROLE_ADMIN, own_teacher_id, user_role
from .limits import DESCRIPTION_MAX


def clean_description(value):
    """Текст без лишних пробелов и пустых строк; ошибка, если он длинный."""
    lines = [" ".join(line.split()) for line in (value or "").splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    if len(text) > DESCRIPTION_MAX:
        raise ValidationError(
            f"Описание длиннее {DESCRIPTION_MAX} знаков: "
            f"сейчас {len(text)}."
        )

    return text


def can_edit_description(user, pair_teacher_id):
    """Администратор или преподаватель пары (pair_teacher_id — её ведущий)."""
    if not user.is_authenticated:
        return False

    if user_role(user) == ROLE_ADMIN:
        return True

    teacher_id = own_teacher_id(user)

    return bool(teacher_id) and pair_teacher_id == teacher_id
