"""Название группы: две заглавные русские буквы, дефис, три цифры (ПО-107).

Первая цифра — курс, две последние — порядковый номер группы (01–99).
Латиница не принимается: латинская «A» или «O» неотличимы на глаз от
русских, и две «одинаковые» группы оказались бы разными.
"""

import re

GROUP_NAME_RE = re.compile(r"^[А-ЯЁ]{2}-(\d)(\d{2})$")

FORMAT_HINT = (
    "Название группы — две заглавные русские буквы, дефис и три цифры, "
    "например ПО-107."
)


def is_standard_name(name):
    """Соответствует ли название принятому образцу (курс не проверяется)."""
    match = GROUP_NAME_RE.match(name or "")

    return bool(match) and match.group(2) != "00" and match.group(1) != "0"


def check_group_name(name, course_id=None):
    """Проверяет название. Бросает ValueError с текстом для пользователя.

    Курс сверяется только с первой цифрой названия: ПО-107 — первый курс.
    Если курс не выбран (None), проверяется один образец.
    """
    name = (name or "").strip()

    if not GROUP_NAME_RE.match(name):
        raise ValueError(FORMAT_HINT)

    if not is_standard_name(name):
        raise ValueError(
            "Порядковый номер группы — от 01 до 99, первая цифра — курс "
            "(от 1)."
        )

    if course_id is not None and int(name[3]) != course_id:
        raise ValueError(
            f"Первая цифра названия ({name[3]}) не совпадает с выбранным "
            f"курсом ({course_id})."
        )

    return name
