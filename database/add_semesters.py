"""Создаёт таблицу "Semester" (семестры: номер 1–8 и даты).

Запускать после database/create_db.py. Повторный запуск безопасен, данные
не затрагиваются. Даты семестров администратор задаёт в Кузне, карточка
«Семестры»; один интервал на номер, пересечения запрещены (проверяет форма).

    python database/add_semesters.py
"""

import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "admin",
}

DB_NAME = "schedule_db"

TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS "Semester" (
        semester_id SERIAL PRIMARY KEY,
        semester_number SMALLINT NOT NULL UNIQUE,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,

        CONSTRAINT semester_number_range
            CHECK (semester_number BETWEEN 1 AND 8),

        CONSTRAINT semester_dates_order CHECK (end_date >= start_date)
    );
    """,
]


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in TABLES_SQL:
                    cursor.execute(query)

        print('Таблица "Semester" (семестры) готова.')

    finally:
        connection.close()


if __name__ == "__main__":
    main()
