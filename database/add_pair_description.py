"""Добавляет описание предмета (до 500 знаков) в таблицу пар.

Запускать один раз после обновления сайта (раунд 29). Новую базу, созданную
database/create_db.py, запускать не нужно: столбец там уже есть.
Повторный запуск безопасен. Данные не меняются: у всех пар описание пустое.

    python database/add_pair_description.py

Число 500 должно совпадать с DESCRIPTION_MAX в schedule/limits.py.
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
    ALTER TABLE "Pairs"
        ADD COLUMN IF NOT EXISTS description VARCHAR(500) NOT NULL DEFAULT '';
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

    print("Описание предмета добавлено в таблицу «Pairs».")


if __name__ == "__main__":
    main()
