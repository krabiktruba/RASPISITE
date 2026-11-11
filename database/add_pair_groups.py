"""Пары — самостоятельный объект, привязываемый к нескольким группам.

Что меняется в базе:
  • "Pairs": убирается колонка "group" (пара больше не принадлежит одной
    группе), остаются преподаватель и название;
  • новая таблица "PairGroup": какие пары привязаны к каким группам;
  • "ScheduleChange": новая колонка "group" (у какой группы меняется занятие).

ВНИМАНИЕ. Старые пары не переносятся: они принадлежали одной группе, а новые
привязываются к группам отдельно. Поэтому скрипт УДАЛЯЕТ:
  • все пары ("Pairs"),
  • недельное расписание всех групп ("Day"),
  • разовые правки расписания ("ScheduleChange"),
  • оценки дневника ("DiaryMark", если таблица есть).
Группы, преподаватели, пользователи, звонки, календарь и теги остаются.
После запуска пары создаются заново в Кузне (карточка «Пары»), привязываются
к группам и ставятся в расписание групп. Либо загрузите CSV «Пары» и
«Расписание» (формат файлов прежний, а для общей пары в файле «Пары»
пишется по строке на каждую группу).

ПЕРЕД ЗАПУСКОМ СДЕЛАЙТЕ КОПИЮ БАЗЫ:

    pg_dump -U postgres -d schedule_db -F c -f schedule_db_before_pairs.dump

Запуск (скрипт попросит подтверждение, нужно ввести слово ДА):

    python database/add_pair_groups.py

Повторный запуск ничего не удаляет: если изменения уже внесены, скрипт
просто сообщает об этом.
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
    CREATE TABLE IF NOT EXISTS "PairGroup" (
        pair_group_id SERIAL PRIMARY KEY,
        pair_id INT NOT NULL,
        group_id INT NOT NULL,

        FOREIGN KEY (pair_id)
            REFERENCES "Pairs"(pair_id)
            ON DELETE CASCADE,

        FOREIGN KEY (group_id)
            REFERENCES "Group"(group_id)
            ON DELETE CASCADE,

        CONSTRAINT unique_pair_group UNIQUE (pair_id, group_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS pair_group_group_idx
        ON "PairGroup" (group_id);
    """,
]

# Только если в "Pairs" ещё есть колонка "group" (старая структура)
WIPE_SQL = [
    'TRUNCATE TABLE "Day", "ScheduleChange", "Pairs" RESTART IDENTITY CASCADE;',
]

DIARY_WIPE_SQL = 'TRUNCATE TABLE "DiaryMark" RESTART IDENTITY;'

ALTER_SQL = [
    'ALTER TABLE "Pairs" DROP COLUMN "group";',
    """
    ALTER TABLE "ScheduleChange"
        ADD COLUMN IF NOT EXISTS "group" INT NOT NULL
            REFERENCES "Group"(group_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS schedule_change_group_idx
        ON "ScheduleChange" ("group");
    """,
]


def has_old_structure(cursor):
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'Pairs' AND column_name = 'group'
        """
    )

    return cursor.fetchone() is not None


def confirmed():
    print(__doc__.split("ПЕРЕД ЗАПУСКОМ")[0])
    answer = input('Удалить старые пары, расписание, правки и оценки? '
                   'Введите ДА: ')

    return answer.strip().upper() == "ДА"


def main():
    connection = psycopg2.connect(dbname=DB_NAME, **DB_CONFIG)

    try:
        with connection:
            with connection.cursor() as cursor:
                if has_old_structure(cursor):
                    if not confirmed():
                        print("Отменено: в базе ничего не изменено.")
                        return

                    for query in WIPE_SQL:
                        cursor.execute(query)

                    cursor.execute("SELECT to_regclass('\"DiaryMark\"')")

                    if cursor.fetchone()[0] is not None:
                        cursor.execute(DIARY_WIPE_SQL)

                    for query in ALTER_SQL:
                        cursor.execute(query)

                    print("Старые данные пар удалены, структура обновлена.")
                else:
                    print("Структура уже новая: ничего не удалено.")

                for query in CREATE_SQL:
                    cursor.execute(query)

                # На случай базы, где колонку "group" уже убрали вручную
                cursor.execute(
                    """
                    ALTER TABLE "ScheduleChange"
                        ADD COLUMN IF NOT EXISTS "group" INT
                            REFERENCES "Group"(group_id)
                    """
                )
    finally:
        connection.close()

    print('Таблица "PairGroup" готова.')


if __name__ == "__main__":
    main()
