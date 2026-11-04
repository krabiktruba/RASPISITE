"""Цвета тегов: проверка, подбор цвета текста и таблица стилей.

Цвет тега хранится как #RRGGBB. Чтобы не писать стили прямо в страницы,
каждому цвету соответствует класс tag-c-RRGGBB, а правила для всех
используемых цветов выдаёт отдельная таблица стилей (адрес «tags.css»).
Цвет проверяется строго, поэтому в таблицу стилей попадает только
безопасный текст.
"""

import re

TAG_COLOR_DEFAULT = "#6b7280"

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def is_color(value):
    return bool(value) and bool(_COLOR_RE.fullmatch(value))


def normalize_color(value):
    """Цвет в виде #rrggbb (строчными) или ValueError, если он неверный."""
    value = (value or "").strip()

    if not is_color(value):
        raise ValueError("Цвет нужно указать в виде #RRGGBB.")

    return value.lower()


def color_or_default(value):
    """Цвет записи; для пустого или повреждённого значения — серый."""
    return value.lower() if is_color(value) else TAG_COLOR_DEFAULT


def color_class(value):
    return "tag-c-" + color_or_default(value)[1:]


def text_color(background):
    """Белый или тёмный текст — какой читается лучше на этом фоне."""
    value = color_or_default(background)
    red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))

    # Воспринимаемая яркость (формула W3C)
    brightness = (red * 299 + green * 587 + blue * 114) / 1000

    return "#111827" if brightness > 140 else "#ffffff"


def build_tag_css(colors):
    """Текст таблицы стилей: по одному правилу на каждый цвет."""
    unique = sorted(
        {color_or_default(color) for color in colors} | {TAG_COLOR_DEFAULT}
    )

    return "".join(
        f".tag-chip.{color_class(color)} "
        f"{{ background: {color}; color: {text_color(color)}; }}\n"
        for color in unique
    )
