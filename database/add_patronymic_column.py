"""Добавляет в таблицу "Profile" колонку patronymic (отчество).

Имя и фамилия пользователя хранятся в стандартной таблице пользователей
Django (first_name, last_name), а отчество необязательно и лежит в профиле.

Запускать один раз после обновления проекта. Повторный запуск безопасен.

    python database/add_patronymic_column.py
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
    ALTER TABLE "Profile"
        ADD COLUMN IF NOT EXISTS patronymic VARCHAR(150) NOT NULL DEFAULT '';
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

    print('Колонка patronymic в таблице "Profile" готова.')


if __name__ == "__main__":
    main()
