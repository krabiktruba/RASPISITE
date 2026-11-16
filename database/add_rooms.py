"""Добавляет кабинеты: у группы (обычный) и у пары в расписании (особый).

Запускать один раз после обновления сайта (раунд 33). Новую базу, созданную
database/create_db.py, запускать не нужно: столбцы там уже есть.
Повторный запуск безопасен. Данные не меняются: у всех кабинет пустой.

    python database/add_rooms.py

Число 20 должно совпадать с ROOM_MAX в schedule/limits.py.
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
    ALTER TABLE "Group"
        ADD COLUMN IF NOT EXISTS room VARCHAR(20) NOT NULL DEFAULT '';
    """,
    """
    ALTER TABLE "Day"
        ADD COLUMN IF NOT EXISTS room VARCHAR(20) NOT NULL DEFAULT '';
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

    print("Кабинеты добавлены в таблицы «Group» и «Day».")


if __name__ == "__main__":
    main()
