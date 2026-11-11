"""Оценки дневника в CSV: выгрузка и загрузка (набор «diary» в Кузне).

Формат один и тот же в обе стороны. Столбцы:

    Студент (логин) — логин студента (его почта);
    ФИО             — для удобства чтения, при загрузке не проверяется;
    Дата            — дата занятия (ДД.ММ.ГГГГ; ГГГГ-ММ-ДД тоже принимается);
    Номер пары      — номер пары в этот день (1–6);
    Предмет, Преподаватель — какая пара (название и ФИО её преподавателя);
    Оценка          — число 0–100, «Н» (не был) или «Б» (болеет);
    Поставил        — ФИО преподавателя, поставившего оценку (можно пусто).

Одна строка — одна запись дневника. Запись определяется студентом, парой,
датой и номером пары: при загрузке такая запись обновляется, а новых
записи добавляются. Загрузка вызывается из schedule.csv_io (общая рамка
импорта: «всё или ничего», проверка без сохранения).
"""

from datetime import datetime

from django.contrib.auth import get_user_model

from schedule.access import full_name
from schedule.models import Pair, Teacher

from .models import (
    FIRST_PAIR_NUMBER,
    LAST_PAIR_NUMBER,
    Mark,
)

HEADERS = [
    "Студент (логин)", "ФИО", "Дата", "Номер пары",
    "Предмет", "Преподаватель", "Оценка", "Поставил",
]

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")


def _label(mark):
    return mark.label


def export_rows():
    """Все записи дневника: по дате, номеру пары и студенту."""
    marks = Mark.objects.select_related(
        "student", "student__profile", "pair", "pair__teacher", "teacher"
    ).order_by(
        "lesson_date", "pair_number", "student__last_name",
        "student__first_name", "student__username", "pk",
    )

    return [
        [
            mark.student.get_username(),
            full_name(mark.student),
            mark.lesson_date.strftime("%d.%m.%Y"),
            mark.pair_number,
            mark.pair.pair_name,
            mark.pair.teacher.teacher_name,
            _label(mark),
            mark.teacher.teacher_name if mark.teacher_id else "",
        ]
        for mark in marks.iterator(chunk_size=2000)
    ]


def _parse_date(raw, error):
    raw = " ".join(str(raw or "").split())

    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue

    raise error(f"«Дата»: «{raw}» нужно записать как ДД.ММ.ГГГГ")


def import_row(line, data, report, state, helpers):
    """Загружает одну строку. helpers — функции из schedule.csv_io."""
    clean, error = helpers["clean"], helpers["RowError"]
    from .views import parse_mark

    login = clean(data["Студент (логин)"])

    if not login:
        raise error("столбец «Студент (логин)» пуст")

    users = state.setdefault("users", {})

    if login not in users:
        users[login] = get_user_model().objects.filter(
            username=login, is_staff=False
        ).first()

    student = users[login]

    if student is None:
        raise error(f"студент «{login}» не найден")

    day = _parse_date(data["Дата"], error)
    number = helpers["number"](data, "Номер пары", FIRST_PAIR_NUMBER,
                               LAST_PAIR_NUMBER)
    subject = helpers["need"](data, "Предмет", 150)
    teacher_name = helpers["need"](data, "Преподаватель", 150)

    pairs = state.setdefault("pairs", {})
    key = (subject, teacher_name)

    if key not in pairs:
        pairs[key] = Pair.objects.filter(
            pair_name=subject, teacher__teacher_name=teacher_name
        ).order_by("pair_id").first()

    pair = pairs[key]

    if pair is None:
        raise error(
            f"пара «{subject}» с преподавателем «{teacher_name}» не найдена "
            f"(сначала загрузите «Пары»)"
        )

    raw_mark = clean(data["Оценка"])

    if not raw_mark:
        raise error("столбец «Оценка» пуст")

    try:
        kind, score = parse_mark(raw_mark)
    except ValueError as exc:
        raise error(f"«Оценка»: «{raw_mark}» — {exc}")

    teacher = None
    giver = clean(data["Поставил"])

    if giver:
        teachers = state.setdefault("givers", {})

        if giver not in teachers:
            teachers[giver] = Teacher.objects.filter(
                teacher_name=giver
            ).order_by("teacher_id").first()

        teacher = teachers[giver]

        if teacher is None:
            raise error(f"преподаватель «{giver}» не найден")

    helpers["duplicate"](state, (student.pk, pair.pk, day, number), line)

    mark = Mark.objects.filter(
        student=student, pair=pair, lesson_date=day, pair_number=number
    ).first()

    if mark is None:
        Mark.objects.create(
            student=student, pair=pair, lesson_date=day, pair_number=number,
            kind=kind, score=score, teacher=teacher,
        )
        report["created"] += 1
        return

    if (mark.kind, mark.score, mark.teacher_id) == (
        kind, score, teacher.pk if teacher else None
    ):
        report["unchanged"] += 1
        return

    mark.kind, mark.score, mark.teacher = kind, score, teacher
    mark.save()
    report["updated"] += 1
