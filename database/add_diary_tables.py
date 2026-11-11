"""Создаёт таблицу DiaryMark (дневник: оценки и отметки Н / Б).

Запускать после database/create_db.py и add_access_tables.py: таблица
ссылается на auth_user, "Pairs" и "Teacher". Повторный запуск безопасен.

    python database/add_diary_tables.py

Приложение diary без миграций (как и schedule), поэтому таблица создаётся
этим скриптом, а команду `python manage.py makemigrations` для неё запускать
не нужно.
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
    CREATE TABLE IF NOT EXISTS "DiaryMark" (
        mark_id SERIAL PRIMARY KEY,
        student_id INT NOT NULL,
        pair INT NOT NULL,
        lesson_date DATE NOT NULL,
        pair_number SMALLINT NOT NULL,
        kind VARCHAR(6) NOT NULL DEFAULT 'score',
        score SMALLINT,
        teacher INT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        FOREIGN KEY (student_id)
            REFERENCES auth_user(id)
            ON DELETE CASCADE,

        FOREIGN KEY (pair)
            REFERENCES "Pairs"(pair_id)
            ON DELETE CASCADE,

        FOREIGN KEY (teacher)
            REFERENCES "Teacher"(teacher_id)
            ON DELETE SET NULL,

        CONSTRAINT unique_diary_mark_slot
            UNIQUE (student_id, pair, lesson_date, pair_number),

        CONSTRAINT diary_mark_kind_score CHECK (
            (kind = 'score' AND score IS NOT NULL
                AND score BETWEEN 0 AND 100)
            OR (kind <> 'score' AND score IS NULL)
        ),

        CONSTRAINT diary_mark_pair_number
            CHECK (pair_number BETWEEN 1 AND 6)
    );
    """,

    # Для таблицы, созданной прежней версией скрипта: там оценка без баллов
    # (NULL) проходила проверку, потому что в CHECK неизвестное значение
    # считается «не нарушено». Правило пересоздаётся; на новой таблице оно
    # просто заменяется таким же.
    """
    ALTER TABLE "DiaryMark" DROP CONSTRAINT IF EXISTS diary_mark_kind_score;
    """,
    """
    ALTER TABLE "DiaryMark" ADD CONSTRAINT diary_mark_kind_score CHECK (
        (kind = 'score' AND score IS NOT NULL
            AND score BETWEEN 0 AND 100)
        OR (kind <> 'score' AND score IS NULL)
    );
    """,

    # Дневник студента: его записи по датам
    """
    CREATE INDEX IF NOT EXISTS diary_mark_student_date
        ON "DiaryMark" (student_id, lesson_date);
    """,

    # Журнал занятия: все оценки одного занятия
    """
    CREATE INDEX IF NOT EXISTS diary_mark_slot
        ON "DiaryMark" (pair, lesson_date, pair_number);
    """,
]


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in TABLES_SQL:
                    cursor.execute(query)

        print("Таблица DiaryMark (дневник) готова.")

    finally:
        connection.close()


if __name__ == "__main__":
    main()
