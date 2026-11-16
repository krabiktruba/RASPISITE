# RASPISITE

Сайт колледжа: расписание групп и преподавателей, дневник с оценками,
«Кузня» администратора (группы, пары, семестры, звонки, дежурные, CSV).
Django, PostgreSQL, два приложения: `schedule` и `diary`, настройки в `config`.

Таблицы приложений `schedule` и `diary` создаются SQL-скриптами из
`database/`, а не миграциями Django. Поэтому `makemigrations` для них
запускать не нужно.

## Первый запуск

1. Python 3.12+ и PostgreSQL. Установить пакеты:

       pip install django psycopg2-binary tblib

2. Создать базу и таблицы (порядок важен):

       python database/create_db.py
       python manage.py migrate
       python database/add_access_tables.py
       python database/add_diary_tables.py
       python database/add_users_indexes.py

   `create_db.py` создаёт базу `schedule_db` и таблицы расписания (в том числе
   семестры, кабинеты, дежурных и верхнюю/нижнюю неделю). Остальные
   `add_*.py` нужны либо для таблиц, которые ссылаются на `auth_user`
   (после `migrate`), либо для старых баз.

3. Администратор и запуск:

       python manage.py createsuperuser
       python manage.py runserver

   Администратор — пользователь с признаком `is_staff`. Остальных создаёт
   администратор на странице «Пользователи» или студент сам на `/register/`.

## Обновление старой базы

Каждое обновление из `CHANGES_ScheduleChange.txt` говорит, какой скрипт
запустить. Все `database/add_*.py` безопасны при повторном запуске, кроме
двух, которые удаляют данные и спрашивают подтверждение:

- `add_pair_groups.py` удаляет пары и недельное расписание (сначала `pg_dump`);
- `limit_pairs.py` удаляет данные пар 5–6 после ввода «ДА».

Перед любым скриптом делайте копию:

    pg_dump -U postgres schedule_db > backup.sql

## Настройки (переменные окружения)

Без переменных сайт работает в режиме разработки. На сервере задайте:

| Переменная | Значение |
|---|---|
| `DJANGO_DEBUG` | `0` — рабочий режим (по умолчанию `1`) |
| `DJANGO_SECRET_KEY` | свой секретный ключ, обязателен при `DJANGO_DEBUG=0` |
| `DJANGO_ALLOWED_HOSTS` | адреса сайта через запятую, обязательны при `DJANGO_DEBUG=0` |
| `DJANGO_HTTPS` | `1`/`0`: cookie только по https (по умолчанию включено при `DEBUG=0`) |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | доступ к PostgreSQL (по умолчанию `schedule_db`, `postgres`, `admin`, `localhost`, `5432`) |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Gmail сайта и пароль приложения Google; без них письма печатаются в окно сервера |

Скрипты в `database/` читают доступ к базе из словаря `DB_CONFIG` в начале
каждого файла: при другом пароле поправьте его там.

При `DJANGO_DEBUG=0` статические файлы собирает `python manage.py collectstatic`
(папка `staticfiles/`), раздавать их должен веб-сервер.

## Тесты

    python manage.py test --noinput --parallel

Тестовая база создаётся из моделей (миграции в тестах отключены), нужен
пакет `tblib`.

## Устройство

- `schedule/` — расписание, правки на даты, кабинеты, дежурные, пользователи,
  вход и регистрация, «Кузня», «Справочник», CSV (`csv_io.py`).
- `diary/` — оценки, дневник студента, журналы, CSV оценок.
- `schedule/limits.py` — число пар в дне и другие пределы (одно место).
- `schedule/weeks.py` — верхняя и нижняя недели.
- `CHANGES_ScheduleChange.txt` — журнал изменений по частям.
