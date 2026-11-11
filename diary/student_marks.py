"""Страница «Дневник» студента: его занятия за неделю и свои оценки."""

from datetime import timedelta

from schedule.dates import full_label

from .lessons import week_lessons
from .models import MARK_ABSENT, MARK_ILL, MARK_SCORE, Mark

MARK_TITLES = {
    MARK_SCORE: "Оценка",
    MARK_ABSENT: "Не был",
    MARK_ILL: "Болел",
}


def week_marks(student, first, last):
    """Записи студента за период: {(пара, дата, номер пары): запись для показа}.

    Запись для показа: {"text": «85» / «Н» / «Б», "kind": вид, "title": подсказка,
    "score": баллы или None}.
    """
    result = {}

    for mark in Mark.objects.filter(
        student=student, lesson_date__range=(first, last)
    ):
        result[(mark.pair_id, mark.lesson_date, mark.pair_number)] = {
            "text": mark.label,
            "kind": mark.kind,
            "title": MARK_TITLES.get(mark.kind, ""),
            "score": mark.score,
        }

    return result


def student_week(student, group_id, monday):
    """Дневник студента за неделю.

    Возвращает (дни, итоги). Дни — только те, в которые были занятия:
    {"day", "label", "lessons": [{number, subject, teacher, note,
    status_label, mark}]}; mark — запись для показа или None (оценки нет).
    Итоги — {"average", "scores", "absent", "ill"}; average — средний балл
    или None, если оценок с баллами за неделю нет.

    Занятия берутся из дневника группы (week_lessons): отменённые и ушедшие
    по переносу в них не входят, праздники и воскресенье пропускаются.
    """
    saturday = monday + timedelta(days=5)
    marks = week_marks(student, monday, saturday)
    days = []
    scores = []
    absent = ill = 0

    for day, lessons in week_lessons(group_id, monday):
        rows = []

        for lesson in lessons:
            mark = marks.get((lesson["pair_id"], day, lesson["number"]))

            if mark is not None:
                if mark["kind"] == MARK_SCORE:
                    scores.append(mark["score"])
                elif mark["kind"] == MARK_ABSENT:
                    absent += 1
                elif mark["kind"] == MARK_ILL:
                    ill += 1

            rows.append({
                "number": lesson["number"],
                "subject": lesson["subject"],
                "teacher": lesson["teacher"],
                "note": lesson["note"],
                "status_label": lesson["status_label"],
                "mark": mark,
            })

        if rows:
            days.append({"day": day, "label": full_label(day), "lessons": rows})

    summary = {
        "average": round(sum(scores) / len(scores), 1) if scores else None,
        "scores": len(scores),
        "absent": absent,
        "ill": ill,
    }

    return days, summary
