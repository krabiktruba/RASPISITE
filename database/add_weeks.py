"""Верхняя и нижняя недели (раунд 35).

Что делает:
  * «Day»: столбец week (0 — каждую неделю, 1 — верхняя, 2 — нижняя),
    проверка 0..2; уникальность теперь по (группа, день, номер пары, неделя);
  * «Semester»: столбец swap_weeks (галочка «первая неделя — нижняя»).

Все существующие пары получают week = 0, то есть идут каждую неделю:
расписание не меняется.

Запускать один раз после обновления сайта. Новую базу, созданную
database/create_db.py, запускать не нужно. Повторный запуск безопасен.

    python database/add_weeks.py
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
    ALTER TABLE "Day"
        ADD COLUMN IF NOT EXISTS week SMALLINT NOT NULL DEFAULT 0;
    """,
    """
    ALTER TABLE "Semester"
        ADD COLUMN IF NOT EXISTS swap_weeks BOOLEAN NOT NULL DEFAULT FALSE;
    """,
]

CHECK_SQL = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'day_week_range'
        ) THEN
            ALTER TABLE "Day"
                ADD CONSTRAINT day_week_range CHECK (week BETWEEN 0 AND 2);
        END IF;
    END $$;
"""

# Старое ограничение уникальности (группа, день, номер) называется по-разному
# в разных базах, поэтому находим его по составу столбцов
FIND_OLD_UNIQUE = """
    SELECT c.conname
    FROM pg_constraint c
    WHERE c.conrelid = '"Day"'::regclass
      AND c.contype = 'u'
      AND (
          SELECT array_agg(a.attname::text ORDER BY a.attname::text)
          FROM unnest(c.conkey) AS k(attnum)
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = k.attnum
      ) = ARRAY['group', 'pair_number', 'weekday']::text[];
"""

NEW_UNIQUE = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'unique_group_weekday_pair_week'
        ) THEN
            ALTER TABLE "Day"
                ADD CONSTRAINT unique_group_weekday_pair_week
                UNIQUE ("group", weekday, pair_number, week);
        END IF;
    END $$;
"""


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in SQL:
                    cursor.execute(query)

                cursor.execute(CHECK_SQL)

                cursor.execute(FIND_OLD_UNIQUE)
                for (name,) in cursor.fetchall():
                    cursor.execute(f'ALTER TABLE "Day" DROP CONSTRAINT "{name}"')

                cursor.execute(NEW_UNIQUE)
    finally:
        connection.close()

    print("Недели добавлены: «Day».week и «Semester».swap_weeks.")


if __name__ == "__main__":
    main()
