"""Создаёт таблицу «Duty»: дежурные преподаватели (день недели и смена).

Запускать один раз после обновления сайта (раунд 34). Новую базу, созданную
database/create_db.py, запускать не нужно: таблица там уже есть.
Повторный запуск безопасен, существующие данные не трогаются.

    python database/add_duty.py
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
    CREATE TABLE IF NOT EXISTS "Duty" (
        duty_id SERIAL PRIMARY KEY,
        shift INT NOT NULL,
        weekday SMALLINT NOT NULL,
        teacher_id INT NOT NULL,

        FOREIGN KEY (shift)
            REFERENCES "Shift"(shift_id) ON DELETE CASCADE,

        FOREIGN KEY (teacher_id)
            REFERENCES "Teacher"(teacher_id) ON DELETE CASCADE,

        CONSTRAINT unique_duty_shift_weekday_teacher
            UNIQUE (shift, weekday, teacher_id),

        CONSTRAINT duty_weekday_range
            CHECK (weekday BETWEEN 1 AND 6)
    );
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

    print("Таблица «Duty» готова.")


if __name__ == "__main__":
    main()
