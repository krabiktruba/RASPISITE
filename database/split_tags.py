"""Теги: одна таблица "Tag" (и "TagType") -> две таблицы "GroupTag" и "TeacherTag".

Теги групп и теги преподавателей теперь живут отдельно: у каждого вида своя
таблица, свои названия и цвета.

Что делает скрипт (повторный запуск безопасен):
  1. Создаёт таблицы "GroupTag" и "TeacherTag".
  2. Если старая таблица "Tag" ещё есть, переносит из неё только те теги,
     которые реально назначены: теги групп -> "GroupTag", теги преподавателей
     -> "TeacherTag" (повторы названий склеиваются). Назначения у групп и
     преподавателей перенаправляются на новые теги. Неиспользуемые старые теги
     не переносятся.
  3. Связи "Group".group_tag и "Teacher".teacher_tag указывают на новые таблицы.
  4. Удаляет старые таблицы "Tag" и "TagType".

Всё выполняется одной транзакцией: при ошибке в базе ничего не меняется.
Запускать ДО первого запуска нового кода:

    python database/split_tags.py

Перед запуском разумно сделать копию базы (pg_dump).
"""

import psycopg2

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "admin",
}

DB_NAME = "schedule_db"

CREATE_SQL = [
    """
    CREATE TABLE IF NOT EXISTS "GroupTag" (
        tag_id SERIAL PRIMARY KEY,
        tag_name VARCHAR(100) NOT NULL UNIQUE,
        tag_color VARCHAR(7) NOT NULL DEFAULT '#6b7280'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS "TeacherTag" (
        tag_id SERIAL PRIMARY KEY,
        tag_name VARCHAR(100) NOT NULL UNIQUE,
        tag_color VARCHAR(7) NOT NULL DEFAULT '#6b7280'
    );
    """,
]

# Выполняется, только если старая таблица "Tag" ещё существует
MIGRATE_SQL = [
    """
    INSERT INTO "GroupTag" (tag_name, tag_color)
    SELECT DISTINCT ON (t.tag_name) t.tag_name, t.tag_color
    FROM "Tag" t
    WHERE t.tag_id IN (SELECT group_tag FROM "Group" WHERE group_tag IS NOT NULL)
    ORDER BY t.tag_name, t.tag_id
    ON CONFLICT (tag_name) DO NOTHING;
    """,
    """
    INSERT INTO "TeacherTag" (tag_name, tag_color)
    SELECT DISTINCT ON (t.tag_name) t.tag_name, t.tag_color
    FROM "Tag" t
    WHERE t.tag_id IN (SELECT teacher_tag FROM "Teacher" WHERE teacher_tag IS NOT NULL)
    ORDER BY t.tag_name, t.tag_id
    ON CONFLICT (tag_name) DO NOTHING;
    """,
    # Старые связи на "Tag" снимаются (названия ограничений могут отличаться)
    """
    DO $$
    DECLARE r RECORD;
    BEGIN
        FOR r IN
            SELECT conrelid::regclass AS tbl, conname
            FROM pg_constraint
            WHERE contype = 'f' AND confrelid = '"Tag"'::regclass
        LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
        END LOOP;
    END $$;
    """,
    """
    UPDATE "Group" g
    SET group_tag = gt.tag_id
    FROM "Tag" t
    JOIN "GroupTag" gt ON gt.tag_name = t.tag_name
    WHERE g.group_tag = t.tag_id;
    """,
    """
    UPDATE "Teacher" te
    SET teacher_tag = tt.tag_id
    FROM "Tag" t
    JOIN "TeacherTag" tt ON tt.tag_name = t.tag_name
    WHERE te.teacher_tag = t.tag_id;
    """,
    # Назначения без нового тега (не должно быть) обнуляются, чтобы связь встала
    """
    UPDATE "Group" SET group_tag = NULL
    WHERE group_tag IS NOT NULL
      AND group_tag NOT IN (SELECT tag_id FROM "GroupTag");
    """,
    """
    UPDATE "Teacher" SET teacher_tag = NULL
    WHERE teacher_tag IS NOT NULL
      AND teacher_tag NOT IN (SELECT tag_id FROM "TeacherTag");
    """,
    'DROP TABLE "Tag";',
    'DROP TABLE IF EXISTS "TagType";',
]

# Связи на новые таблицы (если ещё не созданы)
LINK_SQL = [
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'group_group_tag_grouptag_fkey'
        ) THEN
            ALTER TABLE "Group"
                ADD CONSTRAINT group_group_tag_grouptag_fkey
                FOREIGN KEY (group_tag) REFERENCES "GroupTag"(tag_id);
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'teacher_teacher_tag_teachertag_fkey'
        ) THEN
            ALTER TABLE "Teacher"
                ADD CONSTRAINT teacher_teacher_tag_teachertag_fkey
                FOREIGN KEY (teacher_tag) REFERENCES "TeacherTag"(tag_id);
        END IF;
    END $$;
    """,
]


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in CREATE_SQL:
                    cursor.execute(query)

                cursor.execute("SELECT to_regclass('\"Tag\"') IS NOT NULL;")
                has_old = cursor.fetchone()[0]

                if has_old:
                    for query in MIGRATE_SQL:
                        cursor.execute(query)

                for query in LINK_SQL:
                    cursor.execute(query)
    finally:
        connection.close()

    print('Теги разделены: таблицы "GroupTag" и "TeacherTag" готовы.')


if __name__ == "__main__":
    main()
