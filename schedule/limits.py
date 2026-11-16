"""Ограничения расписания, которые должны совпадать во всём проекте.

Число пар в день задаётся здесь один раз. Если колледж изменит его, меняется
LAST_PAIR (и ограничения в базе: database/limit_pairs.py).
"""

FIRST_PAIR = 1
LAST_PAIR = 4

PAIR_NUMBERS = range(FIRST_PAIR, LAST_PAIR + 1)

# Перемена стоит между двумя соседними парами
BREAK_NUMBERS = range(FIRST_PAIR, LAST_PAIR)
BREAK_COUNT = len(BREAK_NUMBERS)


def pair_range_text():
    """«от 1 до 4» для сообщений об ошибках."""
    return f"от {FIRST_PAIR} до {LAST_PAIR}"


# Описание предмета: подсказка в расписании и текст в журнале
DESCRIPTION_MAX = 500


# Кабинет: номер или название («204», «Спортзал»)
ROOM_MAX = 20
