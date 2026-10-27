"""Экспорт и импорт данных Кузни в CSV.

Формат один и тот же в обе стороны: файл, который выдал экспорт, импорт
читает без правок. Произвольные CSV не принимаются: заголовки столбцов
должны совпадать с теми, что выдаёт экспорт.

Наборы данных (в порядке, в котором их удобно загружать):

    bells     — звонки: время пар и перемен по сменам;
    teachers  — преподаватели;
    groups    — группы (курс, смена, куратор, тег);
    pairs     — пары: предмет группы с преподавателем;
    schedule  — расписание: какая пара стоит в какой день и на какой паре.

Файл: UTF-8 с BOM, разделитель «;» (так его открывает Excel). При загрузке
понимается и «,», а также кодировка Windows-1251 (Excel сохраняет в ней).

Импорт идёт по принципу «всё или ничего»: если в файле есть ошибки, не
сохраняется ни одна строка. Режим «только проверить» выполняет всё то же,
но в конце откатывает изменения. Ничего не удаляется: пустой набор строк
записи не стирает, а строки, которых нет в файле, остаются как были.
"""

import csv
import io
from datetime import datetime

from django.db import DatabaseError, transaction

from .dates import WEEKDAY_NAMES
from .models import (
    BreakHours,
    Course,
    Day,
    Group,
    Pair,
    Shift,
    StudyHours,
    Tag,
    TagType,
    Teacher,
    Weekday,
)

DELIMITER = ";"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000

KIND_STUDY = "Пара"
KIND_BREAK = "Перемена"

BOM = "﻿"


class RowError(Exception):
    """Проблема в одной строке файла (текст попадёт в отчёт)."""


# ---------------------------------------------------------------------------
# Мелкие помощники
# ---------------------------------------------------------------------------

def _clean(value):
    """Строка без лишних пробелов по краям и внутри."""
    return " ".join(str(value or "").split())


def _need(data, column, limit=None):
    value = _clean(data[column])

    if not value:
        raise RowError(f"столбец «{column}» пуст")

    if limit and len(value) > limit:
        raise RowError(f"«{column}» длиннее {limit} символов")

    return value


def _number(data, column, low, high):
    raw = _clean(data[column])

    try:
        number = int(raw)
    except ValueError:
        raise RowError(f"«{column}» должно быть числом, а не «{raw}»")

    if not low <= number <= high:
        raise RowError(f"«{column}» должно быть от {low} до {high}")

    return number


def _time(data, column):
    raw = _clean(data[column])

    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).time()
        except ValueError:
            continue

    raise RowError(f"«{column}»: время «{raw}» нужно записать как ЧЧ:ММ")


def _one(queryset, missing, several):
    """Единственная запись из выборки или понятная ошибка."""
    items = list(queryset[:2])

    if not items:
        raise RowError(missing)

    if len(items) > 1:
        raise RowError(several)

    return items[0]


def _teacher(name):
    return _one(
        Teacher.objects.filter(teacher_name=name),
        f"преподаватель «{name}» не найден (сначала загрузите «Преподаватели»)",
        f"в базе несколько преподавателей «{name}», по ФИО не выбрать",
    )


def _group(name):
    return _one(
        Group.objects.filter(group_name=name),
        f"группа «{name}» не найдена (сначала загрузите «Группы»)",
        f"в базе несколько групп «{name}», по названию не выбрать",
    )


def _tag(value):
    """«Тип: Название» -> тег (создаётся при необходимости); пусто -> None."""
    value = _clean(value)

    if not value:
        return None

    type_name, separator, tag_name = value.partition(":")
    type_name, tag_name = _clean(type_name), _clean(tag_name)

    if not separator or not type_name or not tag_name:
        raise RowError(f"тег «{value}» нужно записать как «Тип: Название»")

    if len(type_name) > 100 or len(tag_name) > 100:
        raise RowError(f"тег «{value}» слишком длинный")

    tag_type, _ = TagType.objects.get_or_create(tag_type_name=type_name)
    tag, _ = Tag.objects.get_or_create(tag_type=tag_type, tag_name=tag_name)

    return tag


def _tag_text(tag):
    return f"{tag.tag_type.tag_type_name}: {tag.tag_name}" if tag else ""


def _weekday(data, column):
    raw = _clean(data[column])
    names = {WEEKDAY_NAMES[number].casefold(): number for number in range(1, 7)}

    if raw.casefold() in names:
        return names[raw.casefold()]

    if raw.isdigit() and 1 <= int(raw) <= 6:
        return int(raw)

    raise RowError(
        f"«{column}»: «{raw}» не учебный день (нужен понедельник–суббота)"
    )


def _duplicate(state, key, line):
    if key in state["seen"]:
        raise RowError(f"повторяет строку {state['seen'][key]}")

    state["seen"][key] = line


def _upsert(model, lookup, defaults, report):
    """Создаёт или обновляет запись; возвращает «created/updated/unchanged»."""
    obj = model.objects.filter(**lookup).first()

    if obj is None:
        model.objects.create(**lookup, **defaults)
        status = "created"

    elif any(getattr(obj, name) != value for name, value in defaults.items()):
        for name, value in defaults.items():
            setattr(obj, name, value)

        obj.save()
        status = "updated"

    else:
        status = "unchanged"

    report[status] += 1

    return status


# ---------------------------------------------------------------------------
# Наборы данных: экспорт
# ---------------------------------------------------------------------------

def _hhmm(value):
    return value.strftime("%H:%M")


def _export_bells():
    rows = [
        [KIND_STUDY, hours.shift_id, hours.pair_number,
         _hhmm(hours.pair_start_time), _hhmm(hours.pair_end_time)]
        for hours in StudyHours.objects.order_by("shift_id", "pair_number")
    ]

    rows += [
        [KIND_BREAK, hours.shift_id, hours.break_number,
         _hhmm(hours.break_start_time), _hhmm(hours.break_end_time)]
        for hours in BreakHours.objects.order_by("shift_id", "break_number")
    ]

    return rows


def _export_teachers():
    return [
        [teacher.teacher_name, _tag_text(teacher.teacher_tag)]
        for teacher in Teacher.objects.select_related(
            "teacher_tag", "teacher_tag__tag_type"
        ).order_by("teacher_name", "teacher_id")
    ]


def _export_groups():
    return [
        [
            group.group_name,
            group.group_course_id,
            group.shift_id,
            group.group_curator.teacher_name if group.group_curator else "",
            _tag_text(group.group_tag),
        ]
        for group in Group.objects.select_related(
            "group_curator", "group_tag", "group_tag__tag_type"
        ).order_by("group_name", "group_id")
    ]


def _export_pairs():
    return [
        [pair.group.group_name, pair.pair_name, pair.teacher.teacher_name]
        for pair in Pair.objects.select_related(
            "group", "teacher"
        ).order_by("group__group_name", "pair_name", "pair_id")
    ]


def _export_schedule():
    return [
        [
            day.group.group_name,
            WEEKDAY_NAMES[day.weekday_id],
            day.pair_number,
            day.pair.pair_name,
            day.pair.teacher.teacher_name,
        ]
        for day in Day.objects.select_related(
            "group", "pair", "pair__teacher"
        ).order_by("group__group_name", "weekday_id", "pair_number")
    ]


# ---------------------------------------------------------------------------
# Наборы данных: загрузка одной строки
# ---------------------------------------------------------------------------

def _row_bells(line, data, report, state):
    kind = _clean(data["Вид"]).casefold()

    if kind not in (KIND_STUDY.casefold(), KIND_BREAK.casefold()):
        raise RowError(f"«Вид» должен быть «{KIND_STUDY}» или «{KIND_BREAK}»")

    is_break = kind == KIND_BREAK.casefold()
    shift_id = _number(data, "Смена", 1, 99)
    number = _number(data, "Номер", 1, 5 if is_break else 6)
    start = _time(data, "Начало")
    end = _time(data, "Конец")

    if not Shift.objects.filter(pk=shift_id).exists():
        raise RowError(f"смены {shift_id} нет")

    if start >= end:
        raise RowError("начало должно быть раньше конца")

    _duplicate(state, (is_break, shift_id, number), line)

    if is_break:
        _upsert(
            BreakHours,
            {"shift_id": shift_id, "break_number": number},
            {"break_start_time": start, "break_end_time": end},
            report,
        )
    else:
        _upsert(
            StudyHours,
            {"shift_id": shift_id, "pair_number": number},
            {"pair_start_time": start, "pair_end_time": end},
            report,
        )


def _row_teachers(line, data, report, state):
    name = _need(data, "ФИО", 150)
    _duplicate(state, name, line)

    count = Teacher.objects.filter(teacher_name=name).count()

    if count > 1:
        raise RowError(f"в базе несколько преподавателей «{name}»")

    tag = _tag(data["Тег"])

    _upsert(
        Teacher,
        {"teacher_name": name},
        {"teacher_tag_id": tag.pk if tag else None},
        report,
    )


def _row_groups(line, data, report, state):
    name = _need(data, "Название", 100)
    course_id = _number(data, "Курс", 1, 99)
    shift_id = _number(data, "Смена", 1, 99)
    curator_name = _clean(data["Куратор"])

    _duplicate(state, name, line)

    if Group.objects.filter(group_name=name).count() > 1:
        raise RowError(f"в базе несколько групп «{name}»")

    if not Course.objects.filter(pk=course_id).exists():
        raise RowError(f"курса {course_id} нет")

    if not Shift.objects.filter(pk=shift_id).exists():
        raise RowError(f"смены {shift_id} нет")

    curator = _teacher(curator_name) if curator_name else None
    tag = _tag(data["Тег"])

    _upsert(
        Group,
        {"group_name": name},
        {
            "group_course_id": course_id,
            "shift_id": shift_id,
            "group_curator_id": curator.pk if curator else None,
            "group_tag_id": tag.pk if tag else None,
        },
        report,
    )


def _row_pairs(line, data, report, state):
    group = _group(_need(data, "Группа", 100))
    subject = _need(data, "Предмет", 150)
    teacher = _teacher(_need(data, "Преподаватель", 150))

    _duplicate(state, (group.pk, subject, teacher.pk), line)

    # Пара — это связка «предмет + преподаватель» в группе; существующую
    # не трогаем, недостающую добавляем
    _upsert(
        Pair,
        {"group": group, "pair_name": subject, "teacher": teacher},
        {},
        report,
    )


def _row_schedule(line, data, report, state):
    group = _group(_need(data, "Группа", 100))
    weekday_id = _weekday(data, "День недели")
    number = _number(data, "Номер пары", 1, 6)
    subject = _need(data, "Предмет", 150)
    teacher = _teacher(_need(data, "Преподаватель", 150))

    _duplicate(state, (group.pk, weekday_id, number), line)

    pair = Pair.objects.filter(
        group=group, pair_name=subject, teacher=teacher
    ).order_by("pair_id").first()

    if pair is None:
        raise RowError(
            f"у группы «{group.group_name}» нет пары «{subject}» "
            f"с преподавателем «{teacher.teacher_name}» "
            f"(сначала загрузите «Пары»)"
        )

    status = _upsert(
        Day,
        {"group": group, "weekday_id": weekday_id, "pair_number": number},
        {"pair_id": pair.pk},
        report,
    )

    if status != "unchanged":
        state["touched"].add((weekday_id, number, teacher.pk))


def _after_schedule(report, state):
    """Преподаватель не может вести двух групп в одно время."""
    for weekday_id, number, teacher_id in sorted(state["touched"]):
        days = list(
            Day.objects.filter(
                weekday_id=weekday_id,
                pair_number=number,
                pair__teacher_id=teacher_id,
            ).select_related("group", "pair__teacher").order_by(
                "group__group_name"
            )
        )

        if len(days) > 1:
            names = ", ".join(day.group.group_name for day in days)

            report["errors"].append(
                f"{days[0].pair.teacher.teacher_name} в день "
                f"«{WEEKDAY_NAMES[weekday_id]}», пара {number} стоит сразу "
                f"у нескольких групп: {names}."
            )


# ---------------------------------------------------------------------------
# Реестр наборов
# ---------------------------------------------------------------------------

KINDS = {
    "bells": {
        "title": "Звонки",
        "headers": ["Вид", "Смена", "Номер", "Начало", "Конец"],
        "export": _export_bells,
        "row": _row_bells,
        "after": None,
    },
    "teachers": {
        "title": "Преподаватели",
        "headers": ["ФИО", "Тег"],
        "export": _export_teachers,
        "row": _row_teachers,
        "after": None,
    },
    "groups": {
        "title": "Группы",
        "headers": ["Название", "Курс", "Смена", "Куратор", "Тег"],
        "export": _export_groups,
        "row": _row_groups,
        "after": None,
    },
    "pairs": {
        "title": "Пары",
        "headers": ["Группа", "Предмет", "Преподаватель"],
        "export": _export_pairs,
        "row": _row_pairs,
        "after": None,
    },
    "schedule": {
        "title": "Расписание",
        "headers": [
            "Группа", "День недели", "Номер пары", "Предмет", "Преподаватель",
        ],
        "export": _export_schedule,
        "row": _row_schedule,
        "after": _after_schedule,
    },
}


def kind_choices():
    """[(ключ, название)] в порядке, удобном для загрузки."""
    return [(key, kind["title"]) for key, kind in KINDS.items()]


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------

def build_csv(kind, template=False):
    """Содержимое файла (байты, UTF-8 с BOM). template — только заголовки."""
    spec = KINDS[kind]

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=DELIMITER, lineterminator="\r\n")

    writer.writerow(spec["headers"])

    if not template:
        writer.writerows(spec["export"]())

    return (BOM + buffer.getvalue()).encode("utf-8")


# ---------------------------------------------------------------------------
# Чтение файла
# ---------------------------------------------------------------------------

def _decode(raw):
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError(
        "не удалось прочитать файл: нужна кодировка UTF-8 или Windows-1251"
    )


def _read_rows(text, headers):
    """[(номер строки, {столбец: значение})]. Ошибки формата — ValueError."""
    lines = [line for line in text.splitlines() if line.strip()]

    if not lines:
        raise ValueError("файл пустой")

    delimiter = DELIMITER if DELIMITER in lines[0] else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    found = None
    rows = []

    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue

            # Excel нередко дописывает пустые столбцы справа
            row = list(row)

            while row and not row[-1].strip() and (
                found is None or len(row) > len(found)
            ):
                row.pop()

            if found is None:
                found = [_clean(cell).casefold() for cell in row]
                expected = [header.casefold() for header in headers]

                missing = [h for h in headers if h.casefold() not in found]
                extra = [c for c in found if c not in expected]

                if missing or extra or len(set(found)) != len(found):
                    problems = []

                    if missing:
                        problems.append("нет столбцов: " + ", ".join(missing))

                    if extra:
                        problems.append("лишние столбцы: " + ", ".join(extra))

                    if len(set(found)) != len(found):
                        problems.append("столбцы повторяются")

                    raise ValueError(
                        "заголовок файла не подходит (" + "; ".join(problems)
                        + "). Нужны: " + ", ".join(headers)
                    )

                continue

            if len(row) > len(found):
                raise ValueError(
                    f"в строке {reader.line_num} больше значений, "
                    f"чем столбцов"
                )

            row = row + [""] * (len(found) - len(row))
            by_name = dict(zip(found, row))

            rows.append((
                reader.line_num,
                {header: by_name[header.casefold()] for header in headers},
            ))

            if len(rows) > MAX_ROWS:
                raise ValueError(f"в файле больше {MAX_ROWS} строк")

    except csv.Error as exc:
        raise ValueError(f"файл не похож на CSV ({exc})")

    return rows


# ---------------------------------------------------------------------------
# Импорт
# ---------------------------------------------------------------------------

def _new_report(kind, dry_run):
    spec = KINDS.get(kind)

    return {
        "kind": kind,
        "title": spec["title"] if spec else "",
        "dry_run": dry_run,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": [],
        "fatal": "",
        "saved": False,
    }


def _ensure_weekdays():
    """Таблица Weekday должна содержать шесть учебных дней."""
    for number in range(1, 7):
        Weekday.objects.get_or_create(
            weekday_id=number,
            defaults={"weekday_name": WEEKDAY_NAMES[number]},
        )


def import_csv(kind, raw, dry_run=False):
    """Загружает файл (байты) в выбранный набор. Возвращает отчёт."""
    report = _new_report(kind, dry_run)

    if kind not in KINDS:
        report["fatal"] = "неизвестный набор данных"
        return report

    if not raw:
        report["fatal"] = "файл пустой"
        return report

    if len(raw) > MAX_FILE_BYTES:
        report["fatal"] = "файл больше 2 МБ"
        return report

    spec = KINDS[kind]

    try:
        rows = _read_rows(_decode(raw), spec["headers"])
    except ValueError as exc:
        report["fatal"] = str(exc)
        return report

    if not rows:
        report["fatal"] = "в файле нет строк с данными"
        return report

    state = {"seen": {}, "touched": set()}

    try:
        with transaction.atomic():
            if kind == "schedule":
                _ensure_weekdays()

            for line, data in rows:
                try:
                    # Своя точка отката у каждой строки: сбой одной строки
                    # не ломает проверку остальных
                    with transaction.atomic():
                        spec["row"](line, data, report, state)

                except RowError as exc:
                    report["errors"].append(f"Строка {line}: {exc}.")

                except DatabaseError as exc:
                    report["errors"].append(
                        f"Строка {line}: не удалось сохранить ({exc})."
                    )

            if spec["after"] is not None and not report["errors"]:
                spec["after"](report, state)

            if report["errors"] or dry_run:
                transaction.set_rollback(True)

    except DatabaseError as exc:
        report["fatal"] = f"ошибка базы данных ({exc})"
        return report

    report["saved"] = not report["errors"] and not dry_run

    return report
