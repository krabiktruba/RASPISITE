"""Индексы для страницы «Пользователи»: фильтры по роли, группе, кураторам.

Список пользователей фильтруется и разбивается на страницы в базе. Индексы
ускоряют фильтры по роли, группе, преподавателю, куратору и сортировку по
ФИО, когда студентов становится много. Повторный запуск безопасен.

    python database/add_users_indexes.py

Поиск по части ФИО или почты (LIKE '%слово%') обычными индексами не
ускоряется; при нескольких тысячах пользователей он выполняется за
миллисекунды. Если людей станут десятки тысяч, можно добавить триграммный
индекс (расширение pg_trgm), но сейчас в этом нет нужды.
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
    # Фильтр «роль»
    """
    CREATE INDEX IF NOT EXISTS profile_role_idx
        ON "Profile" (role);
    """,

    # Фильтр «группа» и «студенты без группы»
    """
    CREATE INDEX IF NOT EXISTS profile_group_idx
        ON "Profile" ("group");
    """,

    # Связь пользователя с преподавателем (профиль, «Кураторы»)
    """
    CREATE INDEX IF NOT EXISTS profile_teacher_idx
        ON "Profile" (teacher);
    """,

    # Кто курирует какую группу
    """
    CREATE INDEX IF NOT EXISTS group_curator_idx
        ON "Group" (group_curator_id);
    """,

    # Порядок «по алфавиту»
    """
    CREATE INDEX IF NOT EXISTS auth_user_name_idx
        ON auth_user (last_name, first_name, username);
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

    print("Индексы страницы «Пользователи» готовы.")


if __name__ == "__main__":
    main()
