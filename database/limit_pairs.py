"""Сокращает число пар в день до 4 в уже существующей базе.

Запускать один раз после обновления сайта (раунд 29). Новую базу, созданную
database/create_db.py, запускать не нужно: там ограничения уже стоят.

Что делает скрипт:
  1. Считает записи с парой номер 5 и 6 (расписание групп, разовые правки,
     оценки дневника, время пар) и перемены с номером 4 и выше (перемена
     стоит между парами, при 4 парах их 3).
  2. Если такие записи есть, показывает счётчики и просит ввести ДА.
     Без подтверждения ничего не удаляется. Записи удаляются безвозвратно:
     перед запуском сделайте копию базы
         pg_dump -U postgres schedule_db > backup_before_limit_pairs.sql
  3. Удаляет эти записи и ставит в базе ограничения: пара 1–4, перемена 1–3.
     Всё одной транзакцией: при ошибке база остаётся как была.

Повторный запуск безопасен: если пар 5–6 уже нет, скрипт только проверяет
ограничения.

    python database/limit_pairs.py

Число 4 должно совпадать с LAST_PAIR в schedule/limits.py.
"""

import sys

import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "admin",
}

DB_NAME = "schedule_db"

LAST_PAIR = 4
LAST_BREAK = LAST_PAIR - 1


def table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s)", (f'public."{name}"',))

    return cursor.fetchone()[0] is not None


def count(cursor, query, *params):
    cursor.execute(query, params)

    return cursor.fetchone()[0]


def collect(cursor):
    """Сколько записей выходит за новые рамки: список (название, число)."""
    result = [
        (
            "Расписание групп (Day)",
            count(
                cursor,
                'SELECT COUNT(*) FROM "Day" WHERE pair_number > %s',
                LAST_PAIR,
            ),
        ),
        (
            "Разовые правки (ScheduleChange)",
            count(
                cursor,
                'SELECT COUNT(*) FROM "ScheduleChange" '
                "WHERE old_pair_number > %s OR new_pair_number > %s "
                "OR study_hours_id IN ("
                '  SELECT study_hours_id FROM "StudyHours" '
                "  WHERE pair_number > %s)",
                LAST_PAIR, LAST_PAIR, LAST_PAIR,
            ),
        ),
        (
            "Время пар (StudyHours)",
            count(
                cursor,
                'SELECT COUNT(*) FROM "StudyHours" WHERE pair_number > %s',
                LAST_PAIR,
            ),
        ),
        (
            "Перемены (BreakHours)",
            count(
                cursor,
                'SELECT COUNT(*) FROM "BreakHours" WHERE break_number > %s',
                LAST_BREAK,
            ),
        ),
    ]

    if table_exists(cursor, "DiaryMark"):
        result.append((
            "Оценки дневника (DiaryMark)",
            count(
                cursor,
                'SELECT COUNT(*) FROM "DiaryMark" WHERE pair_number > %s',
                LAST_PAIR,
            ),
        ))

    return result


def delete_extra(cursor):
    # Сначала то, что ссылается на время пар
    cursor.execute(
        'DELETE FROM "ScheduleChange" '
        "WHERE old_pair_number > %s OR new_pair_number > %s "
        "OR study_hours_id IN ("
        '  SELECT study_hours_id FROM "StudyHours" WHERE pair_number > %s)',
        (LAST_PAIR, LAST_PAIR, LAST_PAIR),
    )

    if table_exists(cursor, "DiaryMark"):
        cursor.execute(
            'DELETE FROM "DiaryMark" WHERE pair_number > %s', (LAST_PAIR,)
        )

    cursor.execute('DELETE FROM "Day" WHERE pair_number > %s', (LAST_PAIR,))
    cursor.execute(
        'DELETE FROM "StudyHours" WHERE pair_number > %s', (LAST_PAIR,)
    )
    cursor.execute(
        'DELETE FROM "BreakHours" WHERE break_number > %s', (LAST_BREAK,)
    )


def set_constraints(cursor):
    rules = [
        ("Day", "day_pair_number_range", f"pair_number BETWEEN 1 AND {LAST_PAIR}"),
        (
            "StudyHours",
            "study_hours_pair_number_range",
            f"pair_number BETWEEN 1 AND {LAST_PAIR}",
        ),
        (
            "BreakHours",
            "break_hours_number_range",
            f"break_number BETWEEN 1 AND {LAST_BREAK}",
        ),
    ]

    if table_exists(cursor, "DiaryMark"):
        # Это правило создавал add_diary_tables.py (раньше 1–6)
        rules.append((
            "DiaryMark",
            "diary_mark_pair_number",
            f"pair_number BETWEEN 1 AND {LAST_PAIR}",
        ))

    for table, name, condition in rules:
        cursor.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS {name}')
        cursor.execute(
            f'ALTER TABLE "{table}" ADD CONSTRAINT {name} CHECK ({condition})'
        )


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                extra = collect(cursor)
                total = sum(number for _, number in extra)

                if total:
                    print(
                        f"Записи вне рамок «{LAST_PAIR} пары в день» "
                        "(будут удалены безвозвратно):"
                    )

                    for title, number in extra:
                        print(f"  {title}: {number}")

                    print(
                        "\nСначала сделайте копию базы:\n"
                        "  pg_dump -U postgres schedule_db "
                        "> backup_before_limit_pairs.sql\n"
                    )

                    answer = input("Удалить эти записи? Введите ДА: ").strip()

                    if answer != "ДА":
                        print("Отменено, база не тронута.")
                        sys.exit(1)

                    delete_extra(cursor)
                else:
                    print("Записей с парой 5 и выше нет, удалять нечего.")

                set_constraints(cursor)

        print(
            f"Готово: в базе пары 1–{LAST_PAIR}, перемены 1–{LAST_BREAK}."
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
