"""Создаёт таблицу Profile (роли пользователей, преподаватели и группы студентов).

Запускать после `python manage.py migrate`: таблицы ссылаются на auth_user,
которую создаёт Django. Повторный запуск безопасен. Если таблица Profile уже
была создана раньше, скрипт добавит в неё колонку "group" (группа студента).

    python database/add_access_tables.py
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
    CREATE TABLE IF NOT EXISTS "Profile" (
        profile_id SERIAL PRIMARY KEY,
        user_id INT NOT NULL UNIQUE,
        role VARCHAR(10) NOT NULL DEFAULT 'student',
        teacher INT,
        "group" INT,
        patronymic VARCHAR(150) NOT NULL DEFAULT '',

        FOREIGN KEY (user_id)
            REFERENCES auth_user(id)
            ON DELETE CASCADE,

        FOREIGN KEY (teacher)
            REFERENCES "Teacher"(teacher_id)
            ON DELETE SET NULL,

        FOREIGN KEY ("group")
            REFERENCES "Group"(group_id)
            ON DELETE SET NULL
    );
    """,

    # Для таблицы Profile, созданной раньше без группы студента.
    # Если колонка уже есть, команда ничего не делает.
    """
    ALTER TABLE "Profile"
        ADD COLUMN IF NOT EXISTS "group" INT
        REFERENCES "Group"(group_id) ON DELETE SET NULL;
    """,

    # Отчество (см. также database/add_patronymic_column.py)
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
                for query in TABLES_SQL:
                    cursor.execute(query)

        print("Таблица Profile (роли и группы студентов) готова.")

    finally:
        connection.close()


if __name__ == "__main__":
    main()
