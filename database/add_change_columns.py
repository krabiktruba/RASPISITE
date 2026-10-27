"""Добавляет в таблицу "ScheduleChange" колонку old_pair_number.

В ней хранится номер пары (1–6) в исходный день: одна и та же пара может
стоять в расписании дня несколько раз, и правка должна знать, какую именно
из них она отменяет, заменяет или переносит.

Запускать один раз после обновления проекта. Повторный запуск безопасен.

    python database/add_change_columns.py
"""

import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "admin",
}

DB_NAME = "schedule_db"

SQL = [
    """
    ALTER TABLE "ScheduleChange"
        ADD COLUMN IF NOT EXISTS old_pair_number INT;
    """,
]


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in SQL:
                    cursor.execute(query)
    finally:
        connection.close()

    print('Колонка old_pair_number в таблице "ScheduleChange" готова.')


if __name__ == "__main__":
    main()
