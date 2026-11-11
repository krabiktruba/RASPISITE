"""Дневник: таблица группы, где ставятся оценки, и главная страница раздела."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from schedule.access import (
    can_view_group_students,
    full_name,
    own_group_id,
    own_teacher_id,
    user_role,
)
from schedule.dates import (
    WEEKDAY_SHORT,
    full_label,
    parse_date,
    short_label,
    week_start,
)
from schedule.models import (
    ROLE_ADMIN,
    ROLE_STUDENT,
    ROLE_TEACHER,
    Group,
    GroupTag,
    Profile,
)

from .lessons import (
    can_grade,
    teacher_day_lessons,
    teacher_group_ids,
    teaches_in_group,
    week_lessons,
)
from .student_marks import student_week
from .models import (
    MARK_ABSENT,
    MARK_ILL,
    MARK_SCORE,
    MAX_SCORE,
    MIN_SCORE,
    Mark,
)

# Что вводят вместо числа: «Н» (не был) и «Б» (болеет). Латинские буквы
# принимаются потому, что H/N и B на клавиатуре легко спутать с русскими.
ABSENT_WORDS = {"н", "h", "n"}
ILL_WORDS = {"б", "b"}

# Ширина столбцов таблицы, px (те же числа в style.css: .diary-grid)
STUDENT_COLUMN_WIDTH = 184
MARK_COLUMN_WIDTH = 88

INPUT_HINT = "Число от 0 до 100, «Н» или «Б»; пусто — без оценки"


# ---------------------------------------------------------------------------
# Разбор введённой оценки
# ---------------------------------------------------------------------------

def parse_mark(text):
    """Разбирает ячейку таблицы: (вид, баллы) или None для пустой.

    Бросает ValueError с текстом для пользователя, если значение неверно.
    """
    value = (text or "").strip().rstrip(".").strip().lower()

    if not value:
        return None

    if value in ABSENT_WORDS:
        return MARK_ABSENT, None

    if value in ILL_WORDS:
        return MARK_ILL, None

    if value.isascii() and value.isdigit():
        score = int(value)

        if MIN_SCORE <= score <= MAX_SCORE:
            return MARK_SCORE, score

    raise ValueError(INPUT_HINT)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _students_of(group):
    """Студенты группы по алфавиту: [{user, name}]."""
    profiles = Profile.objects.filter(
        group=group, role=ROLE_STUDENT, user__is_staff=False
    ).select_related("user")

    ordered = sorted(
        profiles,
        key=lambda profile: (
            profile.user.last_name.lower(),
            profile.user.first_name.lower(),
            profile.patronymic.lower(),
            profile.user.username.lower(),
        ),
    )

    return [
        {
            "user": profile.user,
            "name": full_name(profile.user, profile)
            or profile.user.get_username(),
        }
        for profile in ordered
    ]


def _group_url(group_id, day):
    return (
        reverse("diary_group", kwargs={"group_id": group_id})
        + f"?date={day.isoformat()}"
    )


def _save_marks(user, items, marks):
    """Записывает оценки таблицы, возвращает число изменённых записей.

    items — [(id студента, занятие, (вид, баллы) или None)]; None удаляет
    запись. marks — уже существующие записи {(студент, пара, дата, номер)}.
    Всё сохраняется одной транзакцией; запись без изменений не трогается.
    """
    teacher_id = own_teacher_id(user)
    changed = 0

    with transaction.atomic():
        for student_id, lesson, value in items:
            slot = (student_id, lesson["pair_id"], lesson["day"],
                    lesson["number"])
            current = marks.get(slot)

            if value is None:
                if current is not None:
                    current.delete()
                    changed += 1

                continue

            kind, score = value

            if current is None:
                Mark.objects.create(
                    student_id=student_id,
                    pair_id=lesson["pair_id"],
                    lesson_date=lesson["day"],
                    pair_number=lesson["number"],
                    kind=kind,
                    score=score,
                    teacher_id=teacher_id,
                )
                changed += 1

            elif current.kind != kind or current.score != score:
                current.kind = kind
                current.score = score
                current.teacher_id = teacher_id
                current.save()
                changed += 1

    return changed


# ---------------------------------------------------------------------------
# Таблица группы
# ---------------------------------------------------------------------------

@login_required
def diary_group(request, group_id):
    """Дневник группы за неделю: студенты строками, занятия столбцами.

    Оценки ставятся прямо в ячейках. Открыть таблицу могут администратор,
    куратор группы и преподаватели, которые в ней что-то ведут.

    Администратор и куратор видят все занятия недели. Остальные
    преподаватели видят только свои занятия: чужие оценки им не нужны.
    Ставить оценки можно в столбцах своих занятий (администратор — в любых)
    и только за дни, которые уже наступили.
    """
    group = get_object_or_404(
        Group.objects.select_related("group_curator", "group_tag"), pk=group_id
    )

    sees_all = can_view_group_students(request.user, group)
    teacher_id = own_teacher_id(request.user)

    if not sees_all and not teaches_in_group(teacher_id, group.pk):
        raise PermissionDenied

    today = timezone.localdate()
    chosen = parse_date(request.GET.get("date"), today)
    monday = week_start(chosen)
    saturday = monday + timedelta(days=5)

    students = _students_of(group)
    days = []
    columns = []

    for day, lessons in week_lessons(group.pk, monday):
        cells = []

        for lesson in lessons:
            if not sees_all and lesson["teacher_id"] != teacher_id:
                continue

            column = {
                "lesson": lesson,
                "number": lesson["number"],
                "subject": lesson["subject"],
                "teacher": lesson["teacher"],
                "note": lesson["note"],
                "name": (
                    f"{lesson['pair_id']}_{day.isoformat()}_"
                    f"{lesson['number']}"
                ),
                "editable": can_grade(request.user, lesson) and day <= today,
                "first_of_day": not cells,
            }
            cells.append(column)
            columns.append(column)

        days.append({
            "name": WEEKDAY_SHORT[day.isoweekday()],
            "label": short_label(day),
            "is_today": day == today,
            "lessons": cells,
        })

    marks = {}

    if students and columns:
        for mark in Mark.objects.filter(
            student_id__in=[student["user"].pk for student in students],
            lesson_date__range=(monday, saturday),
        ):
            marks[
                (mark.student_id, mark.pair_id, mark.lesson_date,
                 mark.pair_number)
            ] = mark

    def slot(student, column):
        lesson = column["lesson"]

        return (student["user"].pk, lesson["pair_id"], lesson["day"],
                lesson["number"])

    editable_columns = [column for column in columns if column["editable"]]
    entered = {}
    errors = {}

    if request.method == "POST":
        if not editable_columns:
            raise PermissionDenied

        items = []

        for column in editable_columns:
            for student in students:
                field = f"m_{student['user'].pk}_{column['name']}"

                if field not in request.POST:
                    continue

                entered[field] = request.POST[field]

                try:
                    value = parse_mark(request.POST[field])
                except ValueError as error:
                    errors[field] = str(error)
                    continue

                items.append((student["user"].pk, column["lesson"], value))

        if not errors:
            saved = _save_marks(request.user, items, marks)
            messages.success(
                request,
                f"Сохранено записей: {saved}." if saved else "Изменений нет.",
            )

            return redirect(request.get_full_path())

        messages.error(
            request,
            "Исправьте отмеченные ячейки: пока ничего не сохранено.",
        )

    rows = []

    for student in students:
        values = []

        for column in columns:
            mark = marks.get(slot(student, column))
            field = f"m_{student['user'].pk}_{column['name']}"

            values.append({
                "field": field,
                "column": column["name"],
                "text": entered.get(field, mark.label if mark else ""),
                "kind": mark.kind if mark else "",
                "editable": column["editable"],
                "error": errors.get(field, ""),
                "first_of_day": column["first_of_day"],
            })

        rows.append({
            "user": student["user"],
            "name": student["name"],
            "values": values,
        })

    prev_monday = monday - timedelta(days=7)
    next_monday = monday + timedelta(days=7)

    context = {
        "group": group,
        "days": [day for day in days if day["lessons"]],
        "rows": rows,
        "has_lessons": bool(columns),
        "can_edit": bool(editable_columns),
        "table_width": STUDENT_COLUMN_WIDTH + MARK_COLUMN_WIDTH * len(columns),
        "input_hint": INPUT_HINT,
        "week_date": monday.isoformat(),
        "week_label": f"{short_label(monday)} — {short_label(saturday)}",
        "prev_week_iso": prev_monday.isoformat(),
        "next_week_iso": next_monday.isoformat(),
        "is_current_week": monday == week_start(today),
    }

    return render(request, "diary/group.html", context)


def _group_list(groups, query, raw_tag):
    """Плитки групп для выбора дневника.

    Поиск — по названию группы и тега; выпадающий список — по тегу. В списке
    только теги доступных групп. Неверный или чужой тег игнорируется.
    """
    total = groups.count()
    tags = list(
        GroupTag.objects.filter(group__in=groups)
        .distinct().order_by("tag_name", "tag_id")
    )
    tag = next((item for item in tags if str(item.pk) == raw_tag), None)

    if query:
        groups = groups.filter(
            Q(group_name__icontains=query)
            | Q(group_tag__tag_name__icontains=query)
        )

    if tag is not None:
        groups = groups.filter(group_tag=tag)

    return {
        "groups": list(groups.order_by("group_name", "group_id")),
        "groups_total": total,
        "group_tags": tags,
        "tag": tag,
        "q": query,
    }


# ---------------------------------------------------------------------------
# Главная страница раздела
# ---------------------------------------------------------------------------

@login_required
def diary_home(request):
    """Главная страница дневника, своя для каждой роли."""
    role = user_role(request.user)
    today = timezone.localdate()
    context = {"role": role, "ROLE_ADMIN": ROLE_ADMIN,
               "ROLE_TEACHER": ROLE_TEACHER}

    query = " ".join(request.GET.get("q", "").split())[:100]
    groups = Group.objects.select_related("group_curator", "group_tag")

    if role == ROLE_TEACHER:
        teacher_id = own_teacher_id(request.user)
        chosen = parse_date(request.GET.get("date"), today)
        lessons = teacher_day_lessons(teacher_id, chosen) if teacher_id else []

        for lesson in lessons:
            lesson["url"] = _group_url(lesson["group_id"], chosen)
            lesson["is_future"] = chosen > today

        # «Мои группы»: куратор, ведёт пару или заменял в группе
        groups = groups.filter(pk__in=teacher_group_ids(teacher_id))

        context.update({
            "day": chosen,
            "day_label": full_label(chosen),
            "lessons": lessons,
            "prev_iso": (chosen - timedelta(days=1)).isoformat(),
            "next_iso": (chosen + timedelta(days=1)).isoformat(),
            "is_today": chosen == today,
            "groups_title": "Мои группы",
        })

    elif role == ROLE_ADMIN:
        context["groups_title"] = "Группы"

    else:
        return _student_diary(request, today)

    context.update(_group_list(groups, query, request.GET.get("tag", "")))

    return render(request, "diary/index.html", context)


def _student_diary(request, today):
    """Дневник студента: занятия его группы за неделю и его оценки."""
    group_id = own_group_id(request.user)
    monday = week_start(parse_date(request.GET.get("date"), today))
    saturday = monday + timedelta(days=5)
    days, summary = (
        student_week(request.user, group_id, monday) if group_id else ([], None)
    )

    return render(request, "diary/student.html", {
        "group_id": group_id,
        "days": days,
        "summary": summary,
        "week_date": monday.isoformat(),
        "week_label": f"{short_label(monday)} — {short_label(saturday)}",
        "prev_week_iso": (monday - timedelta(days=7)).isoformat(),
        "next_week_iso": (monday + timedelta(days=7)).isoformat(),
        "is_current_week": monday == week_start(today),
        "input_hint": INPUT_HINT,
    })
