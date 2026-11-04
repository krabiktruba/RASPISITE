"""Добавляет в таблицу "Tag" колонку tag_color (цвет метки тега).

Цвет хранится как #RRGGBB. У уже существующих тегов он будет серым
(#6b7280), потом его можно поменять в «Кузне».

Запускать один раз после обновления проекта. Повторный запуск безопасен.

    python database/add_tag_color_column.py
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
    ALTER TABLE "Tag"
        ADD COLUMN IF NOT EXISTS tag_color VARCHAR(7) NOT NULL DEFAULT '#6b7280';
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

    print('Колонка tag_color в таблице "Tag" готова.')


if __name__ == "__main__":
    main()
