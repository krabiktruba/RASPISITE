import psycopg2
from psycopg2 import sql

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "admin",
}

DB_NAME = "schedule_db"

TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS "TagType" (
        tag_type_id SERIAL PRIMARY KEY,
        tag_type_name VARCHAR(100) NOT NULL UNIQUE
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Tag" (
        tag_id SERIAL PRIMARY KEY,
        tag_type INT NOT NULL,
        tag_name VARCHAR(100) NOT NULL,

        FOREIGN KEY (tag_type)
            REFERENCES "TagType"(tag_type_id),

        UNIQUE (tag_type, tag_name)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Teacher" (
        teacher_id SERIAL PRIMARY KEY,
        teacher_tag INT,
        teacher_name VARCHAR(150) NOT NULL,

        FOREIGN KEY (teacher_tag)
            REFERENCES "Tag"(tag_id)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Course" (
        course_id SMALLINT PRIMARY KEY
            CHECK (course_id BETWEEN 1 AND 4)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Shift" (
        shift_id SERIAL PRIMARY KEY
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Group" (
        group_id SERIAL PRIMARY KEY,
        group_tag INT,
        group_curator_id INT,
        group_course SMALLINT NOT NULL,
        shift INT NOT NULL,
        group_name VARCHAR(100) NOT NULL,

        FOREIGN KEY (group_tag)
            REFERENCES "Tag"(tag_id),

        FOREIGN KEY (group_curator_id)
            REFERENCES "Teacher"(teacher_id),

        FOREIGN KEY (group_course)
            REFERENCES "Course"(course_id),

        FOREIGN KEY (shift)
            REFERENCES "Shift"(shift_id)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Pairs" (
        pair_id SERIAL PRIMARY KEY,
        "group" INT NOT NULL,
        teacher INT NOT NULL,
        pair_name VARCHAR(150) NOT NULL,

        FOREIGN KEY ("group")
            REFERENCES "Group"(group_id),

        FOREIGN KEY (teacher)
            REFERENCES "Teacher"(teacher_id)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Weekday" (
        weekday_id SMALLINT PRIMARY KEY
            CHECK (weekday_id BETWEEN 1 AND 6),
        weekday_name VARCHAR(20) NOT NULL UNIQUE
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Day" (
        day_id SERIAL PRIMARY KEY,
        "group" INT NOT NULL,
        pair_id INT NOT NULL,
        pair_number INT NOT NULL,
        weekday SMALLINT NOT NULL,

        FOREIGN KEY (weekday)
        REFERENCES "Weekday"(weekday_id),

        FOREIGN KEY ("group")
            REFERENCES "Group"(group_id),

        FOREIGN KEY (pair_id)
            REFERENCES "Pairs"(pair_id),

        UNIQUE ("group", weekday, pair_number)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "Calendar" (
        calendar_id SERIAL PRIMARY KEY,
        date DATE NOT NULL UNIQUE,
        weekday SMALLINT NOT NULL
            CHECK (weekday BETWEEN 1 AND 7),
        is_holiday BOOLEAN NOT NULL DEFAULT FALSE,
        holiday_name VARCHAR(150)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "StudyHours" (
        study_hours_id SERIAL PRIMARY KEY,
        shift INT NOT NULL,
        pair_number INT NOT NULL,
        pair_start_time TIME NOT NULL,
        pair_end_time TIME NOT NULL,

        FOREIGN KEY (shift)
            REFERENCES "Shift"(shift_id),

        UNIQUE (shift, pair_number),

        CHECK (pair_start_time < pair_end_time)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "BreakHours" (
        break_hours_id SERIAL PRIMARY KEY,
        shift INT NOT NULL,
        break_number INT NOT NULL,
        break_start_time TIME NOT NULL,
        break_end_time TIME NOT NULL,

        FOREIGN KEY (shift)
            REFERENCES "Shift"(shift_id),

        UNIQUE (shift, break_number),

        CHECK (break_start_time < break_end_time)
    );
    """,

    """
    CREATE TABLE IF NOT EXISTS "ScheduleChange" (
        change_id SERIAL PRIMARY KEY,

        calendar INT NOT NULL,
        pair INT NOT NULL,
        old_pair_number INT,

        new_calendar_id INT,
        new_teacher INT,
        new_pair_number INT,
        new_pair_name VARCHAR(150),

        study_hours_id INT,

        change_type VARCHAR(20) NOT NULL
            CHECK (
                change_type IN (
                    'CANCEL',
                    'MOVE',
                    'REPLACE',
                    'SWITCH'
                )
            ),

        FOREIGN KEY (calendar)
            REFERENCES "Calendar"(calendar_id),

        FOREIGN KEY (pair)
            REFERENCES "Pairs"(pair_id),

        FOREIGN KEY (new_calendar_id)
            REFERENCES "Calendar"(calendar_id),

        FOREIGN KEY (new_teacher)
            REFERENCES "Teacher"(teacher_id),

        FOREIGN KEY (study_hours_id)
            REFERENCES "StudyHours"(study_hours_id)
    );
    """
]

INITIAL_DATA_SQL = [
    """
    INSERT INTO "Course" (course_id)
    VALUES (1), (2), (3), (4)
    ON CONFLICT (course_id) DO NOTHING;
    """,

    """
    INSERT INTO "Shift" (shift_id)
    VALUES (1), (2)
    ON CONFLICT (shift_id) DO NOTHING;
    """,

    """
    INSERT INTO "Weekday" (weekday_id, weekday_name)
    VALUES
        (1, 'Понедельник'),
        (2, 'Вторник'),
        (3, 'Среда'),
        (4, 'Четверг'),
        (5, 'Пятница'),
        (6, 'Суббота')
    ON CONFLICT (weekday_id) DO NOTHING;
    """,
]


def insert_initial_data():
    """Добавляет начальные данные."""
    connection = psycopg2.connect(
        dbname=DB_NAME,
        **DB_CONFIG
    )

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in INITIAL_DATA_SQL:
                    cursor.execute(query)

        print("Начальные данные добавлены.")

    finally:
        connection.close()


def create_database():
    """Создаёт базу данных, если её ещё нет."""
    connection = psycopg2.connect(
        dbname="postgres",
        **DB_CONFIG
    )

    connection.autocommit = True

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}")
                .format(sql.Identifier(DB_NAME))
            )
            print(f"База данных '{DB_NAME}' создана.")

    except psycopg2.errors.DuplicateDatabase:
        print(f"База данных '{DB_NAME}' уже существует.")

    finally:
        connection.close()


def create_tables():
    """Создаёт все таблицы."""
    connection = psycopg2.connect(
        dbname=DB_NAME,
        **DB_CONFIG
    )

    try:
        with connection:
            with connection.cursor() as cursor:
                for query in TABLES_SQL:
                    cursor.execute(query)

        print("Все таблицы успешно созданы.")

    finally:
        connection.close()


def main():
    create_database()
    create_tables()
    insert_initial_data()
    print("Готово.")


if __name__ == "__main__":
    main()