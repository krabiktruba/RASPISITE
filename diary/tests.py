import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from schedule.models import (
    Course,
    Group,
    GroupTag,
    Pair,
    PairGroup,
    Shift,
    Teacher,
)


def make_pair(group, teacher, name):
    """Пара, привязанная к группе."""
    pair = Pair.objects.create(teacher=teacher, pair_name=name)
    PairGroup.objects.create(pair=pair, group=group)

    return pair

from .models import (
    MARK_ABSENT,
    MARK_ILL,
    MARK_SCORE,
    Mark,
)

PASSWORD = "Str0ng-pass-12345"
LESSON = date(2026, 10, 19)


class DiaryBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(course_id=1)
        shift = Shift.objects.create(shift_id=1)
        cls.group = Group.objects.create(
            group_name="А-01", group_course=course, shift=shift
        )
        cls.teacher = Teacher.objects.create(teacher_name="Иванов И. И.")
        cls.pair = make_pair(cls.group, cls.teacher, "Математика"
        )
        cls.student = get_user_model().objects.create_user(
            "student", password=PASSWORD
        )

    def mark(self, **fields):
        data = {
            "student": self.student,
            "pair": self.pair,
            "lesson_date": LESSON,
            "pair_number": 1,
            "kind": MARK_SCORE,
            "score": 85,
        }
        data.update(fields)

        return Mark.objects.create(**data)

    def rejected(self, **fields):
        try:
            with transaction.atomic():
                self.mark(**fields)
        except IntegrityError:
            return True

        return False


class MarkModelTests(DiaryBase):
    def test_score_label(self):
        self.assertEqual(self.mark().label, "85")
        self.assertEqual(self.mark(pair_number=2, score=0).label, "0")

    def test_absent_and_ill_labels(self):
        absent = self.mark(kind=MARK_ABSENT, score=None)
        ill = self.mark(kind=MARK_ILL, score=None, pair_number=2)

        self.assertEqual(absent.label, "Н")
        self.assertEqual(ill.label, "Б")

    def test_one_record_per_student_and_lesson(self):
        self.mark()

        self.assertTrue(self.rejected(score=90))

    def test_same_pair_may_stand_twice_in_a_day(self):
        self.mark(pair_number=1)
        self.mark(pair_number=2)

        self.assertEqual(Mark.objects.count(), 2)

    def test_other_student_or_date_is_a_different_record(self):
        other = get_user_model().objects.create_user("other", password=PASSWORD)
        self.mark()
        self.mark(student=other)
        self.mark(lesson_date=date(2026, 10, 20))

        self.assertEqual(Mark.objects.count(), 3)

    def test_database_rejects_score_out_of_range(self):
        self.assertTrue(self.rejected(score=101))
        self.assertTrue(self.rejected(score=-1))
        self.assertFalse(self.rejected(score=100, pair_number=2))
        self.assertFalse(self.rejected(score=0, pair_number=3))

    def test_database_rejects_score_without_points(self):
        self.assertTrue(self.rejected(score=None))

    def test_database_rejects_absent_with_points(self):
        self.assertTrue(self.rejected(kind=MARK_ABSENT, score=10))
        self.assertTrue(self.rejected(kind=MARK_ILL, score=0))

    def test_database_rejects_bad_pair_number(self):
        self.assertTrue(self.rejected(pair_number=0))
        self.assertTrue(self.rejected(pair_number=7))

    def test_clean_accepts_valid_records(self):
        Mark(
            student=self.student, pair=self.pair, lesson_date=LESSON,
            pair_number=1, kind=MARK_SCORE, score=50,
        ).clean()
        Mark(
            student=self.student, pair=self.pair, lesson_date=LESSON,
            pair_number=1, kind=MARK_ABSENT, score=None,
        ).clean()

    def test_clean_rejects_invalid_records(self):
        bad = (
            {"kind": MARK_SCORE, "score": None},
            {"kind": MARK_SCORE, "score": 101},
            {"kind": MARK_ABSENT, "score": 5},
            {"kind": "unknown", "score": None},
            {"kind": MARK_SCORE, "score": 5, "pair_number": 9},
        )

        for fields in bad:
            with self.subTest(fields=fields):
                data = {"pair_number": 1, **fields}

                with self.assertRaises(ValidationError):
                    Mark(
                        student=self.student, pair=self.pair,
                        lesson_date=LESSON, **data,
                    ).clean()

    def test_deleting_student_or_pair_removes_marks(self):
        self.mark()
        self.student.delete()
        self.assertEqual(Mark.objects.count(), 0)

        other = get_user_model().objects.create_user("other", password=PASSWORD)
        self.mark(student=other)
        self.pair.delete()
        self.assertEqual(Mark.objects.count(), 0)

    def test_deleting_a_substitute_teacher_keeps_the_mark(self):
        substitute = Teacher.objects.create(teacher_name="Замена З. З.")
        mark = self.mark(teacher=substitute)

        substitute.delete()
        mark.refresh_from_db()

        self.assertIsNone(mark.teacher_id)
        self.assertEqual(mark.score, 85)

    def test_related_names(self):
        mark = self.mark()

        self.assertEqual(list(self.student.diary_marks.all()), [mark])
        self.assertEqual(list(self.pair.diary_marks.all()), [mark])


class DiaryHookupTests(DiaryBase):
    def test_app_is_installed_and_routed(self):
        self.assertIn("diary", settings.INSTALLED_APPS)
        self.assertEqual(reverse("diary_home"), "/diary/")

    def test_tests_build_diary_tables_from_models(self):
        self.assertIn("diary", settings.MIGRATION_MODULES)
        self.assertIsNone(settings.MIGRATION_MODULES["diary"])

    def test_home_needs_login(self):
        response = self.client.get(reverse("diary_home"))

        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('diary_home')}"
        )

    def test_home_opens_for_logged_in_user(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("diary_home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "diary/index.html")
        self.assertContains(response, "<h1>ДНЕВНИК</h1>", count=1)

    def test_model_is_in_admin(self):
        from django.contrib import admin

        self.assertIn(Mark, admin.site._registry)


class SqlScriptTests(TestCase):
    """Скрипт создания таблицы согласован с моделью (базы тут нет)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(settings.BASE_DIR) / "database" / "add_diary_tables.py"
        cls.script = path.read_text(encoding="utf-8")

    def test_table_and_columns(self):
        self.assertIn(f'"{Mark._meta.db_table}"', self.script)

        for field in Mark._meta.concrete_fields:
            with self.subTest(column=field.column):
                self.assertIn(field.column, self.script)

    def test_constraints_and_indexes_have_model_names(self):
        for constraint in Mark._meta.constraints:
            with self.subTest(constraint=constraint.name):
                self.assertIn(constraint.name, self.script)

        for index in Mark._meta.indexes:
            with self.subTest(index=index.name):
                self.assertIn(index.name, self.script)

    def test_foreign_keys_point_at_existing_tables(self):
        for table in ("auth_user", '"Pairs"', '"Teacher"'):
            self.assertIn(f"REFERENCES {table}", self.script)

    def test_score_without_points_is_rejected_by_the_script_rule(self):
        self.assertIn("score IS NOT NULL", self.script)
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS diary_mark_kind_score", self.script
        )

    def test_script_is_safe_to_repeat(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS", self.script)
        self.assertEqual(
            self.script.count("CREATE INDEX IF NOT EXISTS"),
            len(Mark._meta.indexes),
        )


# ---------------------------------------------------------------------------
# Раунд 8: таблица группы с вводом оценок, главная страница раздела
# ---------------------------------------------------------------------------

from unittest import mock

from django.contrib.messages import get_messages
from django.test.utils import CaptureQueriesContext
from django.db import connection

from schedule.dates import week_start
from schedule.models import (
    ROLE_STUDENT,
    ROLE_TEACHER,
    Calendar,
    Day,
    Profile,
    ScheduleChange,
    Weekday,
)

from .lessons import (
    can_grade,
    teacher_day_lessons,
    teaches_in_group,
    week_lessons,
)
from .views import parse_mark

TUESDAY = date(2026, 10, 20)
SUNDAY = date(2026, 10, 18)
AFTER = date(2026, 10, 26)  # «сегодня» после всех занятий в тестах
WEEK = "?date=2026-10-19"  # неделя с занятием: нужна, когда «сегодня» уже позже


def today_is(value):
    return mock.patch("django.utils.timezone.localdate", return_value=value)


def make_teacher_user(name, email):
    user = get_user_model().objects.create_user(
        email, email=email, password=PASSWORD
    )
    teacher = Teacher.objects.create(teacher_name=name)
    Profile.objects.create(user=user, role=ROLE_TEACHER, teacher=teacher)

    return user, teacher


def make_student(username, last_name, first_name, group):
    user = get_user_model().objects.create_user(
        username, last_name=last_name, first_name=first_name,
        password=PASSWORD,
    )
    Profile.objects.create(user=user, role=ROLE_STUDENT, group=group)

    return user


class LessonBase(DiaryBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        for number in range(1, 7):
            Weekday.objects.create(
                weekday_id=number, weekday_name=f"День {number}"
            )

        Day.objects.create(
            group=cls.group, pair=cls.pair, pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )

        cls.teacher_user = get_user_model().objects.create_user(
            "ivanov@gmail.com", password=PASSWORD
        )
        Profile.objects.create(
            user=cls.teacher_user, role=ROLE_TEACHER, teacher=cls.teacher
        )
        cls.other_user, cls.other_teacher = make_teacher_user(
            "Петров П. П.", "petrov@gmail.com"
        )
        cls.admin = get_user_model().objects.create_user(
            "boss", password=PASSWORD, is_staff=True
        )

        cls.anna = make_student("anna", "Яблокова", "Анна", cls.group)
        cls.boris = make_student("boris", "Борисов", "Борис", cls.group)
        cls.vera = make_student("vera", "Ветрова", "Вера", cls.group)

        other_group = Group.objects.create(
            group_name="Б-02", group_course=cls.group.group_course,
            shift=cls.group.shift,
        )
        cls.stranger = make_student("stranger", "Чужой", "Иван", other_group)

    def marks(self):
        return {
            mark.student_id: mark
            for mark in Mark.objects.filter(pair=self.pair)
        }

    def change(self, kind, day=LESSON, pair=None, number=1, **fields):
        calendar, _ = Calendar.objects.get_or_create(
            date=day, defaults={"weekday": day.isoweekday()}
        )

        return ScheduleChange.objects.create(
            calendar=calendar, group=fields.pop("group", self.group),
            pair=pair or self.pair,
            old_pair_number=number, change_type=kind, **fields
        )

    def lesson(self, day=LESSON, number=1, group=None):
        """Проведённое занятие группы в слоте (день, номер) или None."""
        group = group or self.group

        for current, lessons in week_lessons(group.pk, week_start(day)):
            if current != day:
                continue

            for lesson in lessons:
                if lesson["number"] == number:
                    return lesson

        return None


class ParseMarkTests(TestCase):
    def test_scores(self):
        for text, score in (("0", 0), ("85", 85), (" 100 ", 100), ("007", 7)):
            with self.subTest(text=text):
                self.assertEqual(parse_mark(text), (MARK_SCORE, score))

    def test_absent_and_ill_in_any_case_and_layout(self):
        for text in ("н", "Н", "H", "n", "н."):
            with self.subTest(text=text):
                self.assertEqual(parse_mark(text), (MARK_ABSENT, None))

        for text in ("б", "Б", "b", "B"):
            with self.subTest(text=text):
                self.assertEqual(parse_mark(text), (MARK_ILL, None))

    def test_empty_means_no_record(self):
        self.assertIsNone(parse_mark(""))
        self.assertIsNone(parse_mark("   "))
        self.assertIsNone(parse_mark(None))

    def test_bad_values_are_rejected(self):
        for text in ("101", "-1", "85.5", "abc", "н5", "٣", "1 0", "²"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_mark(text)


class LessonLookupTests(LessonBase):
    def test_regular_lesson_is_found(self):
        lesson = self.lesson()

        self.assertEqual(lesson["subject"], "Математика")
        self.assertEqual(lesson["teacher_id"], self.teacher.pk)
        self.assertEqual(lesson["group_id"], self.group.pk)

    def test_wrong_slot_is_not_found(self):
        self.assertIsNone(self.lesson(number=2))
        self.assertIsNone(self.lesson(day=TUESDAY))

    def test_cancelled_lesson_is_not_found(self):
        self.change("CANCEL")

        self.assertIsNone(self.lesson())

    def test_moved_lesson_exists_only_in_the_new_slot(self):
        tuesday = Calendar.objects.create(date=TUESDAY, weekday=2)
        self.change("MOVE", new_calendar=tuesday, new_pair_number=3)

        self.assertIsNone(self.lesson())

        lesson = self.lesson(day=TUESDAY, number=3)

        self.assertEqual(lesson["teacher_id"], self.teacher.pk)
        self.assertEqual(lesson["status"], "moved-to")

    def test_holiday_is_not_a_lesson_day(self):
        Calendar.objects.create(
            date=LESSON, weekday=1, is_holiday=True, holiday_name="Праздник"
        )

        self.assertIsNone(self.lesson())

    def test_replacement_teacher_conducts_the_lesson(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        lesson = self.lesson()

        self.assertEqual(lesson["teacher_id"], self.other_teacher.pk)
        self.assertEqual(lesson["status"], "replace")

    def test_replacement_of_only_the_subject_keeps_the_teacher(self):
        self.change("REPLACE", new_pair_name="Физика")

        lesson = self.lesson()

        self.assertEqual(lesson["subject"], "Физика")
        self.assertEqual(lesson["teacher_id"], self.teacher.pk)

    def test_can_grade_rules(self):
        lesson = self.lesson()

        self.assertTrue(can_grade(self.teacher_user, lesson))
        self.assertTrue(can_grade(self.admin, lesson))
        self.assertFalse(can_grade(self.other_user, lesson))
        self.assertFalse(can_grade(self.anna, lesson))

    def test_substitute_may_grade_and_the_replaced_teacher_may_not(self):
        self.change("REPLACE", new_teacher=self.other_teacher)
        lesson = self.lesson()

        self.assertTrue(can_grade(self.other_user, lesson))
        self.assertFalse(can_grade(self.teacher_user, lesson))

    def test_teaches_in_group(self):
        self.assertTrue(teaches_in_group(self.teacher.pk, self.group.pk))
        self.assertFalse(teaches_in_group(self.other_teacher.pk, self.group.pk))
        self.assertFalse(teaches_in_group(None, self.group.pk))

    def test_substitute_teaches_in_group(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        self.assertTrue(teaches_in_group(self.other_teacher.pk, self.group.pk))


class TeacherDayTests(LessonBase):
    def test_teacher_sees_own_lessons_only(self):
        mine = teacher_day_lessons(self.teacher.pk, LESSON)

        self.assertEqual([item["number"] for item in mine], [1])
        self.assertEqual(teacher_day_lessons(self.other_teacher.pk, LESSON), [])

    def test_other_day_and_sunday_are_empty(self):
        self.assertEqual(teacher_day_lessons(self.teacher.pk, TUESDAY), [])
        self.assertEqual(teacher_day_lessons(self.teacher.pk, SUNDAY), [])

    def test_cancelled_lesson_is_hidden(self):
        self.change("CANCEL")

        self.assertEqual(teacher_day_lessons(self.teacher.pk, LESSON), [])

    def test_replacement_moves_the_lesson_to_the_substitute(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        self.assertEqual(teacher_day_lessons(self.teacher.pk, LESSON), [])
        self.assertEqual(
            len(teacher_day_lessons(self.other_teacher.pk, LESSON)), 1
        )

    def test_moved_lesson_shows_on_the_new_day(self):
        tuesday = Calendar.objects.create(date=TUESDAY, weekday=2)
        self.change("MOVE", new_calendar=tuesday, new_pair_number=2)

        self.assertEqual(teacher_day_lessons(self.teacher.pk, LESSON), [])

        moved = teacher_day_lessons(self.teacher.pk, TUESDAY)

        self.assertEqual([item["number"] for item in moved], [2])

    def test_progress_counts_students_and_marks(self):
        self.mark(student=self.anna)
        self.mark(student=self.boris, kind=MARK_ABSENT, score=None)

        lesson = teacher_day_lessons(self.teacher.pk, LESSON)[0]

        self.assertEqual(lesson["students"], 3)
        self.assertEqual(lesson["marked"], 2)

    def test_lessons_are_sorted_by_pair_number(self):
        second = make_pair(self.group, self.teacher, "Физика"
        )
        Day.objects.create(
            group=self.group, pair=second, pair_number=3,
            weekday=Weekday.objects.get(pk=1),
        )

        numbers = [
            item["number"]
            for item in teacher_day_lessons(self.teacher.pk, LESSON)
        ]

        self.assertEqual(numbers, [1, 3])

    def test_week_lessons_cover_monday_to_saturday(self):
        week = week_lessons(self.group.pk, LESSON)

        self.assertEqual(len(week), 6)
        self.assertEqual(week[0][0], LESSON)
        self.assertEqual(len(week[0][1]), 1)
        self.assertTrue(all(not lessons for _, lessons in week[1:]))


class GroupBase(LessonBase):
    def url_group(self, group=None, query=""):
        return reverse(
            "diary_group", kwargs={"group_id": (group or self.group).pk}
        ) + query

    def open_group(self, user, query=WEEK, today=LESSON):
        self.client.force_login(user)

        with today_is(today):
            return self.client.get(self.url_group(query=query))

    def field(self, student, pair=None, day=LESSON, number=1):
        return f"m_{student.pk}_{(pair or self.pair).pk}_{day.isoformat()}_{number}"

    def save(self, user, values, query=WEEK, today=AFTER, **slot):
        """POST таблицы: values — {студент: текст ячейки}."""
        self.client.force_login(user)
        data = {self.field(student, **slot): text
                for student, text in values.items()}

        with today_is(today):
            return self.client.post(self.url_group(query=query), data)


class GroupDiaryAccessTests(GroupBase):
    def test_anonymous_goes_to_login(self):
        response = self.client.get(self.url_group(query=WEEK))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_admin_curator_and_teacher_of_the_group_may_open(self):
        self.group.group_curator = self.other_teacher
        self.group.save()

        for user in (self.admin, self.other_user, self.teacher_user):
            with self.subTest(user=user.username):
                response = self.open_group(user)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "diary/group.html")

    def test_students_and_outsiders_are_refused(self):
        stranger_teacher, _ = make_teacher_user("Сидоров С. С.", "s@gmail.com")

        for user in (self.anna, self.stranger, stranger_teacher):
            with self.subTest(user=user.username):
                self.assertEqual(self.open_group(user).status_code, 403)

    def test_substitute_may_open(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        self.assertEqual(self.open_group(self.other_user).status_code, 200)

    def test_unknown_group_gives_404(self):
        self.client.force_login(self.admin)

        self.assertEqual(
            self.client.get(
                reverse("diary_group", kwargs={"group_id": 999})
            ).status_code,
            404,
        )

    def test_lesson_journal_page_is_gone(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("diary_lesson", kwargs={
                "pair_id": 1, "day": "2026-10-19", "number": 1,
            })


class GroupDiaryPageTests(GroupBase):
    def test_header_has_name_link_and_schedule_button(self):
        response = self.open_group(self.admin)
        page = response.content.decode()
        card = reverse("group_card", kwargs={"group_id": self.group.pk})
        schedule = reverse(
            "group_schedule", kwargs={"group_id": self.group.pk}
        ) + "?date=2026-10-19"

        # подпись «Дневник группы» над крупным названием-ссылкой на профиль
        self.assertLess(page.index("Дневник группы"), page.index("<h1>"))
        self.assertRegex(
            page,
            r'<h1><a class="page-head-link"\s+href="%s"\s+'
            r'title="Профиль группы">%s</a></h1>'
            % (re.escape(card), re.escape(self.group.group_name)),
        )
        # кнопки «Профиль группы» больше нет, остаётся «Расписание группы»
        self.assertNotContains(response, f'href="{card}">Профиль группы</a>')
        self.assertContains(response, f'href="{schedule}">Расписание группы</a>')

    def test_header_shows_course_and_shift_pills(self):
        response = self.open_group(self.admin)

        self.assertContains(response, 'class="group-pill"')

    def test_schedule_button_keeps_the_viewed_week(self):
        response = self.open_group(self.admin, "?date=2026-10-27")

        self.assertContains(response, "?date=2026-10-26\">Расписание группы")

    def test_students_are_alphabetical_and_only_of_this_group(self):
        response = self.open_group(self.admin)

        self.assertEqual(
            [row["name"] for row in response.context["rows"]],
            ["Борисов Борис", "Ветрова Вера", "Яблокова Анна"],
        )
        self.assertNotContains(response, "Чужой")

    def test_every_mark_column_is_declared_with_the_same_class(self):
        second = make_pair(self.group, self.teacher, "Физика"
        )
        Day.objects.create(
            group=self.group, pair=second, pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )

        response = self.open_group(self.admin)

        self.assertContains(response, 'class="diary-col-mark"', count=2)
        self.assertContains(response, 'class="diary-col-student"', count=1)
        self.assertEqual(
            response.context["table_width"],
            STUDENT_COLUMN_WIDTH + MARK_COLUMN_WIDTH * 2,
        )

    def test_marks_are_shown_in_student_rows(self):
        self.mark(student=self.anna, score=88)
        self.mark(student=self.boris, kind=MARK_ABSENT, score=None)
        self.mark(student=self.vera, kind=MARK_ILL, score=None)

        response = self.open_group(self.admin)
        rows = {row["name"]: row["values"] for row in response.context["rows"]}

        self.assertEqual(rows["Яблокова Анна"][0]["text"], "88")
        self.assertEqual(rows["Борисов Борис"][0]["text"], "Н")
        self.assertEqual(rows["Ветрова Вера"][0]["text"], "Б")
        self.assertContains(response, "mark-absent")
        self.assertContains(response, "mark-ill")

    def test_cells_are_inputs_for_the_lesson_teacher_and_admin(self):
        for user in (self.teacher_user, self.admin):
            with self.subTest(user=user.username):
                response = self.open_group(user, today=AFTER)

                self.assertContains(response, 'name="m_', count=3)
                self.assertContains(response, 'data-diary-col="', count=3)
                self.assertTrue(response.context["can_edit"])

    def test_inputs_are_prefilled(self):
        self.mark(student=self.anna, score=77)

        response = self.open_group(self.teacher_user)

        self.assertContains(response, 'value="77"')

    def test_curator_only_looks(self):
        self.group.group_curator = self.other_teacher
        self.group.save()
        self.mark(student=self.anna, score=77)

        response = self.open_group(self.other_user)

        self.assertNotContains(response, 'name="m_')
        self.assertNotContains(response, "Сохранить")
        self.assertContains(response, "только смотрите")
        self.assertEqual(
            response.context["rows"][2]["values"][0]["text"], "77"
        )

    def test_future_days_have_no_inputs(self):
        response = self.open_group(self.teacher_user, today=SUNDAY,
                                   query="?date=2026-10-19")

        self.assertNotContains(response, 'name="m_')
        self.assertFalse(response.context["can_edit"])
        self.assertNotContains(response, "Сохранить")

    def test_today_is_open_for_marks(self):
        response = self.open_group(self.teacher_user, today=LESSON)

        self.assertTrue(response.context["can_edit"])

    def test_teacher_who_is_not_curator_sees_only_own_columns(self):
        second = make_pair(self.group, self.other_teacher, "Физика"
        )
        Day.objects.create(
            group=self.group, pair=second, pair_number=2,
            weekday=Weekday.objects.get(pk=1),
        )
        self.mark(student=self.anna, pair=second, pair_number=2, score=55)

        mine = self.open_group(self.teacher_user)
        admin = self.open_group(self.admin)

        self.assertEqual(len(mine.context["rows"][0]["values"]), 1)
        self.assertNotContains(mine, "Физика")
        self.assertEqual(len(admin.context["rows"][0]["values"]), 2)
        self.assertContains(admin, "Физика")

    def test_curator_sees_all_columns_but_edits_only_own(self):
        second = make_pair(self.group, self.other_teacher, "Физика"
        )
        Day.objects.create(
            group=self.group, pair=second, pair_number=2,
            weekday=Weekday.objects.get(pk=1),
        )
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.open_group(self.teacher_user, today=AFTER)

        self.assertEqual(len(response.context["rows"][0]["values"]), 2)
        self.assertContains(response, 'name="m_', count=3)  # только Математика

    def test_week_is_chosen_by_date_and_marks_stay_in_their_week(self):
        self.mark(student=self.anna, score=88)

        this_week = self.open_group(self.admin, "?date=2026-10-22")
        next_week = self.open_group(self.admin, "?date=2026-10-27")

        def anna_cells(response):
            rows = {
                row["name"]: row["values"] for row in response.context["rows"]
            }

            return [cell["text"] for cell in rows["Яблокова Анна"]]

        # Занятие по понедельникам повторяется каждую неделю, поэтому
        # столбец есть и на следующей неделе, но оценка осталась на своей.
        self.assertEqual(anna_cells(this_week), ["88"])
        self.assertEqual(anna_cells(next_week), [""])

    def test_week_without_any_lessons_says_so(self):
        # Праздник в понедельник: занятий на неделе нет совсем
        Calendar.objects.create(
            date=date(2026, 10, 26), weekday=1, is_holiday=True,
            holiday_name="Праздник",
        )

        response = self.open_group(self.admin, "?date=2026-10-27")

        self.assertFalse(response.context["has_lessons"])
        self.assertContains(response, "нет занятий")

    def test_week_navigation_links(self):
        response = self.open_group(self.admin)

        self.assertEqual(response.context["prev_week_iso"], "2026-10-12")
        self.assertEqual(response.context["next_week_iso"], "2026-10-26")
        self.assertTrue(response.context["is_current_week"])

    def test_cancelled_lesson_has_no_column(self):
        self.change("CANCEL")

        response = self.open_group(self.admin)

        self.assertFalse(response.context["has_lessons"])

    def test_group_without_students_says_so(self):
        empty = Group.objects.create(
            group_name="В-03", group_course=self.group.group_course,
            shift=self.group.shift,
        )
        self.client.force_login(self.admin)

        response = self.client.get(self.url_group(empty, WEEK))

        self.assertContains(response, "нет студентов")

    def test_page_has_form_csrf_and_one_save_button(self):
        response = self.open_group(self.teacher_user, today=AFTER)
        form = response.content.decode().split("data-diary-form")[1]

        self.assertIn("csrfmiddlewaretoken", form.split("</form>")[0])
        self.assertContains(response, "Сохранить", count=1)

    def test_diary_script_is_loaded_with_the_base_script(self):
        response = self.open_group(self.admin)

        self.assertContains(response, "diary/diary.js")
        self.assertContains(response, "filter.js")

    def test_query_count_does_not_grow_with_students(self):
        self.client.force_login(self.admin)

        with today_is(LESSON):
            self.client.get(self.url_group(query=WEEK))  # прогрев

            with CaptureQueriesContext(connection) as small:
                self.client.get(self.url_group(query=WEEK))

            for index in range(8):
                student = make_student(
                    f"extra{index}", f"Новый{index}", "Студент", self.group
                )
                self.mark(student=student, score=50 + index)

            with CaptureQueriesContext(connection) as large:
                self.client.get(self.url_group(query=WEEK))

        self.assertEqual(len(large), len(small))


class DiaryScriptTests(TestCase):
    """diary.js лежит на месте и описывает нужные клавиши."""

    def source(self):
        path = Path(settings.BASE_DIR) / "diary" / "static" / "diary" / "diary.js"

        return path.read_text(encoding="utf-8")

    def test_script_handles_enter_and_arrows(self):
        source = self.source()

        for word in ("Enter", "ArrowDown", "ArrowUp", "data-diary-form",
                     "data-diary-col"):
            with self.subTest(word=word):
                self.assertIn(word, source)


class GroupSaveTests(GroupBase):
    def test_scores_absent_and_ill_are_saved(self):
        response = self.save(self.teacher_user, {
            self.anna: "85", self.boris: "н", self.vera: "Б",
        })

        self.assertRedirects(
            response, self.url_group(query=WEEK),
            fetch_redirect_response=False,
        )

        marks = self.marks()

        self.assertEqual(
            (marks[self.anna.pk].kind, marks[self.anna.pk].score),
            (MARK_SCORE, 85),
        )
        self.assertEqual(
            (marks[self.boris.pk].kind, marks[self.boris.pk].score),
            (MARK_ABSENT, None),
        )
        self.assertEqual(
            (marks[self.vera.pk].kind, marks[self.vera.pk].score),
            (MARK_ILL, None),
        )

    def test_redirect_keeps_the_viewed_week(self):
        response = self.save(
            self.teacher_user, {self.anna: "70"}, query="?date=2026-10-21"
        )

        self.assertRedirects(
            response, self.url_group(query="?date=2026-10-21"),
            fetch_redirect_response=False,
        )

    def test_mark_remembers_the_slot_and_the_teacher(self):
        self.save(self.teacher_user, {self.anna: "70"})

        mark = Mark.objects.get()

        self.assertEqual(mark.pair_id, self.pair.pk)
        self.assertEqual(mark.lesson_date, LESSON)
        self.assertEqual(mark.pair_number, 1)
        self.assertEqual(mark.teacher_id, self.teacher.pk)

    def test_substitute_is_recorded_as_the_author(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        self.save(self.other_user, {self.anna: "70"})

        self.assertEqual(Mark.objects.get().teacher_id, self.other_teacher.pk)

    def test_admin_may_grade_and_has_no_teacher_in_the_record(self):
        self.save(self.admin, {self.anna: "70"})

        self.assertIsNone(Mark.objects.get().teacher_id)

    def test_change_replaces_the_record_instead_of_adding_one(self):
        self.save(self.teacher_user, {self.anna: "70"})
        first = Mark.objects.get()

        self.save(self.teacher_user, {self.anna: "н"})

        self.assertEqual(Mark.objects.count(), 1)

        first.refresh_from_db()

        self.assertEqual(first.kind, MARK_ABSENT)
        self.assertIsNone(first.score)

        self.save(self.teacher_user, {self.anna: "95"})
        first.refresh_from_db()

        self.assertEqual((first.kind, first.score), (MARK_SCORE, 95))

    def test_empty_cell_deletes_the_record(self):
        self.mark(student=self.anna)

        self.save(self.teacher_user, {self.anna: ""})

        self.assertEqual(Mark.objects.count(), 0)

    def test_untouched_record_is_not_rewritten(self):
        mark = self.mark(student=self.anna, score=60)
        stamp = Mark.objects.filter(pk=mark.pk).values_list(
            "updated_at", flat=True
        ).get()

        response = self.save(self.teacher_user, {self.anna: "60"})
        messages = [str(m) for m in get_messages(response.wsgi_request)]

        self.assertEqual(
            Mark.objects.filter(pk=mark.pk).values_list(
                "updated_at", flat=True).get(),
            stamp,
        )
        self.assertEqual(messages, ["Изменений нет."])

    def test_saved_count_is_reported(self):
        response = self.save(
            self.teacher_user, {self.anna: "10", self.boris: "20"}
        )

        messages = [str(m) for m in get_messages(response.wsgi_request)]

        self.assertEqual(messages, ["Сохранено записей: 2."])

    def test_one_bad_value_saves_nothing_and_keeps_input(self):
        response = self.save(self.teacher_user, {
            self.anna: "90", self.boris: "101", self.vera: "abc",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Mark.objects.count(), 0)

        cells = {
            row["name"]: row["values"][0] for row in response.context["rows"]
        }

        self.assertEqual(cells["Яблокова Анна"]["text"], "90")
        self.assertEqual(cells["Яблокова Анна"]["error"], "")
        self.assertEqual(cells["Борисов Борис"]["text"], "101")
        self.assertNotEqual(cells["Борисов Борис"]["error"], "")
        self.assertNotEqual(cells["Ветрова Вера"]["error"], "")
        self.assertContains(response, "is-invalid", count=2)

    def test_bad_value_does_not_touch_existing_marks(self):
        self.mark(student=self.anna, score=50)

        self.save(self.teacher_user, {self.anna: "80", self.boris: "xx"})

        self.assertEqual(Mark.objects.get().score, 50)

    def test_missing_field_leaves_the_record_alone(self):
        self.mark(student=self.anna, score=50)

        self.save(self.teacher_user, {self.boris: "30"})

        self.assertEqual(self.marks()[self.anna.pk].score, 50)
        self.assertEqual(Mark.objects.count(), 2)

    def test_student_of_another_group_cannot_be_graded(self):
        self.save(self.teacher_user, {self.stranger: "90"})

        self.assertEqual(Mark.objects.count(), 0)

    def test_other_teacher_cannot_save(self):
        response = self.save(self.other_user, {self.anna: "90"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Mark.objects.count(), 0)

    def test_curator_cannot_save(self):
        self.group.group_curator = self.other_teacher
        self.group.save()

        response = self.save(self.other_user, {self.anna: "90"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Mark.objects.count(), 0)

    def test_student_cannot_save_own_mark(self):
        response = self.save(self.anna, {self.anna: "100"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Mark.objects.count(), 0)

    def test_cancelled_lesson_cannot_be_graded(self):
        self.change("CANCEL")

        response = self.save(self.teacher_user, {self.anna: "90"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Mark.objects.count(), 0)

    def test_future_day_cannot_be_graded(self):
        response = self.save(
            self.teacher_user, {self.anna: "90"}, today=SUNDAY,
            query="?date=2026-10-19",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Mark.objects.count(), 0)

    def test_field_for_a_foreign_lesson_is_ignored(self):
        second = make_pair(self.group, self.other_teacher, "Физика"
        )
        Day.objects.create(
            group=self.group, pair=second, pair_number=2,
            weekday=Weekday.objects.get(pk=1),
        )

        self.client.force_login(self.teacher_user)

        with today_is(AFTER):
            self.client.post(self.url_group(query=WEEK), {
                self.field(self.anna, pair=second, number=2): "90",
                self.field(self.boris): "70",
            })

        self.assertEqual(
            list(Mark.objects.values_list("pair_id", "score")),
            [(self.pair.pk, 70)],
        )

    def test_get_does_not_change_anything(self):
        self.open_group(self.teacher_user, today=AFTER)

        self.assertEqual(Mark.objects.count(), 0)


class DiaryHomeTests(LessonBase):
    def home(self, user, today=LESSON, query=""):
        self.client.force_login(user)

        with today_is(today):
            return self.client.get(reverse("diary_home") + query)

    def test_teacher_sees_todays_lessons_with_links_and_progress(self):
        self.mark(student=self.anna)

        response = self.home(self.teacher_user)
        link = reverse("diary_group", kwargs={"group_id": self.group.pk})

        self.assertContains(response, "Мои занятия")
        self.assertContains(response, f'href="{link}?date=2026-10-19"')
        self.assertContains(response, "записей 1 из 3")

    def test_teacher_can_choose_another_day(self):
        response = self.home(self.teacher_user, query="?date=2026-10-20")

        self.assertContains(response, "нет занятий")
        self.assertNotContains(response, "записей")

    def test_bad_date_falls_back_to_today(self):
        response = self.home(self.teacher_user, query="?date=oops")

        self.assertContains(response, "записей")

    def test_future_day_is_marked_as_not_yet_held(self):
        response = self.home(
            self.teacher_user, today=SUNDAY, query="?date=2026-10-19"
        )

        self.assertContains(response, "ещё не было")

    def test_curator_gets_a_link_to_the_group_diary(self):
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.home(self.teacher_user)

        self.assertContains(response, "Мои группы")

    def test_teacher_without_groups_gets_a_hint_instead_of_tiles(self):
        response = self.home(self.other_user)

        self.assertContains(response, "не закреплено ни одной группы")
        self.assertEqual(response.context["groups"], [])
        self.assertNotContains(response, "data-diary-choice")

    def test_admin_sees_all_groups(self):
        response = self.home(self.admin)

        self.assertContains(response, "А-01")
        self.assertContains(response, "Б-02")
        self.assertContains(
            response, reverse("diary_group", kwargs={"group_id": self.group.pk})
        )
        self.assertNotContains(response, "Мои занятия")

    def test_student_gets_own_diary_page(self):
        response = self.home(self.anna)

        self.assertTemplateUsed(response, "diary/student.html")
        self.assertNotContains(response, "Мои занятия")
        self.assertNotContains(response, "Группы</h2>")


# ---------------------------------------------------------------------------
# Раунд 16: оценки студента живут в «Дневнике», а не в расписании
# ---------------------------------------------------------------------------

from schedule.changes import resolve_group_day

from .student_marks import student_week, week_marks


class StudentDiaryBase(LessonBase):
    def page(self, user, query=WEEK):
        self.client.force_login(user)

        with today_is(AFTER):
            return self.client.get(reverse("diary_home") + query)

    def lessons(self, response, day=0):
        return response.context["days"][day]["lessons"]


class StudentDiaryTests(StudentDiaryBase):
    def test_student_sees_own_score_next_to_the_lesson(self):
        self.mark(student=self.anna, score=85)

        response = self.page(self.anna)

        self.assertTemplateUsed(response, "diary/student.html")
        self.assertEqual(self.lessons(response)[0]["mark"]["text"], "85")
        self.assertContains(response, 'class="student-mark mark-score"')
        self.assertContains(response, ">85</span>")
        self.assertContains(response, "Математика")

    def test_absent_and_ill_are_shown_as_letters(self):
        self.mark(student=self.anna, kind=MARK_ABSENT, score=None)
        self.mark(student=self.anna, kind=MARK_ILL, score=None,
                  lesson_date=TUESDAY)
        Day.objects.create(
            group=self.group, pair=self.pair, pair_number=1,
            weekday=Weekday.objects.get(pk=2),
        )

        response = self.page(self.anna)

        self.assertEqual(self.lessons(response, 0)[0]["mark"]["text"], "Н")
        self.assertEqual(self.lessons(response, 1)[0]["mark"]["text"], "Б")
        self.assertContains(response, "student-mark mark-absent")
        self.assertContains(response, "student-mark mark-ill")

    def test_lesson_without_mark_has_an_empty_cell(self):
        response = self.page(self.anna)

        self.assertIsNone(self.lessons(response)[0]["mark"])
        self.assertContains(response, 'class="student-mark"></span>')

    def test_zero_is_a_real_score(self):
        self.mark(student=self.anna, score=0)

        response = self.page(self.anna)

        self.assertEqual(self.lessons(response)[0]["mark"]["text"], "0")

    def test_other_students_marks_are_not_shown(self):
        self.mark(student=self.boris, score=90)

        response = self.page(self.anna)

        self.assertIsNone(self.lessons(response)[0]["mark"])
        self.assertNotContains(response, "mark-score")

    def test_only_days_with_lessons_are_listed(self):
        response = self.page(self.anna)

        self.assertEqual(len(response.context["days"]), 1)
        self.assertContains(response, "Понедельник, 19 октября 2026")

    def test_week_without_lessons(self):
        response = self.page(self.anna, query="?date=2026-10-17")

        self.assertEqual(response.context["days"], [])
        self.assertContains(response, "На этой неделе занятий нет.")

    def test_cancelled_lesson_is_not_listed_and_its_mark_is_hidden(self):
        self.mark(student=self.anna, score=85)
        self.change("CANCEL")

        response = self.page(self.anna)

        self.assertEqual(response.context["days"], [])
        self.assertNotContains(response, "mark-score")
        self.assertEqual(response.context["summary"]["scores"], 0)

    def test_moved_lesson_shows_the_mark_of_the_new_slot_only(self):
        tuesday = Calendar.objects.create(date=TUESDAY, weekday=2)
        self.change("MOVE", new_calendar=tuesday, new_pair_number=3)
        self.mark(student=self.anna, score=85)  # старый слот: пара 1, пн
        self.mark(student=self.anna, score=60, lesson_date=TUESDAY,
                  pair_number=3)

        response = self.page(self.anna)

        self.assertEqual(len(response.context["days"]), 1)
        lesson = self.lessons(response)[0]
        self.assertEqual(lesson["number"], 3)
        self.assertEqual(lesson["mark"]["text"], "60")

    def test_replaced_lesson_keeps_its_mark(self):
        self.mark(student=self.anna, score=75)
        self.change("REPLACE", new_teacher=self.other_teacher)

        response = self.page(self.anna)

        self.assertEqual(self.lessons(response)[0]["mark"]["text"], "75")
        self.assertContains(response, "Замена")

    def test_marks_stay_in_their_week(self):
        self.mark(student=self.anna, score=88, lesson_date=AFTER)

        this_week = self.page(self.anna)
        next_week = self.page(self.anna, query="?date=2026-10-26")

        self.assertIsNone(self.lessons(this_week)[0]["mark"])
        self.assertEqual(self.lessons(next_week)[0]["mark"]["text"], "88")

    def test_mark_has_a_tooltip(self):
        self.mark(student=self.anna, kind=MARK_ILL, score=None)

        response = self.page(self.anna)

        self.assertContains(response, 'title="Болел"')

    def test_week_navigation_and_schedule_button(self):
        response = self.page(self.anna)
        schedule = reverse(
            "group_schedule", kwargs={"group_id": self.group.pk}
        )

        self.assertContains(response, "?date=2026-10-12")
        self.assertContains(response, "?date=2026-10-26")
        self.assertContains(response, f'href="{schedule}?date=2026-10-19">Расписание')
        self.assertEqual(response.context["week_label"], "19 окт — 24 окт")

    def test_student_without_group(self):
        loner = get_user_model().objects.create_user("loner", password=PASSWORD)
        Profile.objects.create(user=loner, role=ROLE_STUDENT)

        response = self.page(loner)

        self.assertContains(response, "Вы пока не состоите в группе")
        self.assertEqual(response.context["days"], [])

    def test_query_count_does_not_grow_with_marks(self):
        self.client.force_login(self.anna)
        url = reverse("diary_home") + WEEK

        with today_is(AFTER):
            self.client.get(url)  # прогрев

            with CaptureQueriesContext(connection) as small:
                self.client.get(url)

            for number in range(2, 6):
                Day.objects.create(
                    group=self.group, pair=self.pair, pair_number=number,
                    weekday=Weekday.objects.get(pk=1),
                )
                self.mark(student=self.anna, pair_number=number,
                          score=50 + number)

            with CaptureQueriesContext(connection) as large:
                self.client.get(url)

        self.assertEqual(len(large), len(small))


class StudentSummaryTests(StudentDiaryBase):
    def setUp(self):
        for number in range(2, 5):
            Day.objects.create(
                group=self.group, pair=self.pair, pair_number=number,
                weekday=Weekday.objects.get(pk=1),
            )

    def test_average_counts_only_scores(self):
        self.mark(student=self.anna, pair_number=1, score=80)
        self.mark(student=self.anna, pair_number=2, score=91)
        self.mark(student=self.anna, pair_number=3, kind=MARK_ABSENT,
                  score=None)
        self.mark(student=self.anna, pair_number=4, kind=MARK_ILL, score=None)

        summary = self.page(self.anna).context["summary"]

        self.assertEqual(summary, {
            "average": 85.5, "scores": 2, "absent": 1, "ill": 1,
        })

    def test_no_scores_no_average(self):
        summary = self.page(self.anna).context["summary"]

        self.assertIsNone(summary["average"])
        self.assertContains(self.page(self.anna), "Средний балл за неделю")

    def test_zero_counts_in_the_average(self):
        self.mark(student=self.anna, pair_number=1, score=0)
        self.mark(student=self.anna, pair_number=2, score=100)

        self.assertEqual(self.page(self.anna).context["summary"]["average"], 50.0)


class ScheduleHasNoMarksTests(StudentDiaryBase):
    """В расписании студента оценок больше нет, они только в дневнике."""

    def schedule(self, user, query=WEEK):
        self.client.force_login(user)
        url = reverse(
            "group_schedule", kwargs={"group_id": self.group.pk}
        ) + query

        with today_is(AFTER):
            return self.client.get(url)

    def test_student_schedule_has_no_marks_column(self):
        self.mark(student=self.anna, score=85)

        response = self.schedule(self.anna)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "pair-mark")
        self.assertNotContains(response, "has-marks")
        self.assertNotContains(response, "ваши оценки")
        self.assertNotIn("show_marks", response.context)

    def test_style_sheet_has_no_marks_rules_for_schedule(self):
        css = (Path(__file__).resolve().parent.parent
               / "schedule" / "static" / "style.css").read_text(encoding="utf-8")

        self.assertNotIn(".pair-mark", css)
        self.assertNotIn(".pair.has-marks", css)
        self.assertIn(".student-mark", css)

    def test_main_page_still_offers_both_choices(self):
        self.client.force_login(self.anna)

        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("diary_home"))


class WeekMarksHelperTests(LessonBase):
    def test_week_marks_are_keyed_by_slot_and_limited_to_the_student(self):
        self.mark(student=self.anna, score=85)
        self.mark(student=self.boris, score=10)
        self.mark(student=self.anna, score=40, lesson_date=AFTER)

        marks = week_marks(self.anna, LESSON, date(2026, 10, 24))

        self.assertEqual(list(marks), [(self.pair.pk, LESSON, 1)])
        self.assertEqual(marks[(self.pair.pk, LESSON, 1)]["text"], "85")
        self.assertEqual(marks[(self.pair.pk, LESSON, 1)]["kind"], MARK_SCORE)
        self.assertEqual(marks[(self.pair.pk, LESSON, 1)]["score"], 85)

    def test_student_week_collects_days_lessons_and_summary(self):
        self.mark(student=self.anna, score=70)

        days, summary = student_week(self.anna, self.group.pk, LESSON)

        self.assertEqual([item["day"] for item in days], [LESSON])
        self.assertEqual(days[0]["lessons"][0]["subject"], "Математика")
        self.assertEqual(summary["average"], 70.0)


class ScheduleLessonPairIdTests(LessonBase):
    def slots(self, day):
        from schedule.changes import group_days_by_number, load_changes

        return resolve_group_day(
            self.group.pk, day,
            group_days_by_number(self.group.pk, day.isoweekday()),
            load_changes(day, day),
        )

    def test_regular_lesson_knows_its_pair(self):
        self.assertEqual(self.slots(LESSON)[1]["pair_id"], self.pair.pk)

    def test_moved_in_lesson_knows_its_pair(self):
        tuesday = Calendar.objects.create(date=TUESDAY, weekday=2)
        self.change("MOVE", new_calendar=tuesday, new_pair_number=4)

        self.assertEqual(self.slots(TUESDAY)[4]["pair_id"], self.pair.pk)

    def test_empty_slot_stays_empty(self):
        self.assertIsNone(self.slots(LESSON)[2])


# ---------------------------------------------------------------------------
# Раунд 21: выбор группы с поиском и единый «тонкий» вид дневника
# ---------------------------------------------------------------------------

from .views import MARK_COLUMN_WIDTH, STUDENT_COLUMN_WIDTH

STYLE = Path(__file__).resolve().parent.parent / "schedule/static/style.css"


class GroupChoiceTests(LessonBase):
    def home(self, user, q=None):
        self.client.force_login(user)

        with today_is(LESSON):
            return self.client.get(
                reverse("diary_home"), {"q": q} if q is not None else {}
            )

    def names(self, response):
        return [group.group_name for group in response.context["groups"]]

    def test_teacher_sees_only_groups_where_he_teaches(self):
        response = self.home(self.teacher_user)

        self.assertEqual(self.names(response), ["А-01"])
        self.assertContains(response, 'class="diary-group-tile"')
        self.assertContains(
            response,
            reverse("diary_group", kwargs={"group_id": self.group.pk}),
        )

    def test_replacement_teacher_gets_the_group_too(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        response = self.home(self.other_user)

        self.assertEqual(self.names(response), ["А-01"])

    def test_curated_group_is_listed_even_without_pairs(self):
        other = Group.objects.get(group_name="Б-02")
        other.group_curator = self.teacher
        other.save()

        response = self.home(self.teacher_user)

        self.assertEqual(self.names(response), ["А-01", "Б-02"])

    def test_admin_sees_all_groups_in_alphabetical_order(self):
        response = self.home(self.admin)

        self.assertEqual(self.names(response), ["А-01", "Б-02"])

    def test_search_by_name_ignores_case_and_extra_spaces(self):
        response = self.home(self.admin, "  б-0")

        self.assertEqual(self.names(response), ["Б-02"])
        self.assertContains(response, 'value="б-0"')
        self.assertContains(response, "Сбросить")

    def test_search_by_tag_name(self):
        tag = GroupTag.objects.create(tag_name="Программисты")
        self.group.group_tag = tag
        self.group.save()

        response = self.home(self.admin, "программ")

        self.assertEqual(self.names(response), ["А-01"])

    def test_nothing_found_message(self):
        response = self.home(self.admin, "ъъъ")

        self.assertContains(response, "групп не нашлось")
        self.assertEqual(response.context["groups"], [])

    def test_search_stays_inside_teachers_own_groups(self):
        response = self.home(self.teacher_user, "Б-02")

        self.assertEqual(self.names(response), [])

    def test_tile_has_only_name_and_tag(self):
        tag = GroupTag.objects.create(tag_name="Программирование")
        self.group.group_tag = tag
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.home(self.admin)

        self.assertContains(response, '<span class="diary-group-name">А-01</span>', html=True)
        self.assertContains(response, "Программирование")
        self.assertContains(response, "tag-chip")
        self.assertNotContains(response, "group-pills")
        self.assertNotContains(response, "студ.")
        self.assertNotContains(response, "куратор не назначен")

    def test_page_has_no_separate_student_picker(self):
        response = self.home(self.admin)

        self.assertNotContains(response, "Выберите студента")
        self.assertNotContains(response, "/diary/student/")

    def test_student_does_not_get_the_group_list(self):
        response = self.home(self.anna)

        self.assertNotContains(response, "diary-group-tile")
        self.assertNotContains(response, "diary-choice-search")


class ThinDiaryDesignTests(TestCase):
    def css(self):
        return STYLE.read_text(encoding="utf-8")

    def test_column_widths_in_css_match_the_view(self):
        css = self.css()

        self.assertIn(
            ".diary-col-student {\n    width: %dpx;" % STUDENT_COLUMN_WIDTH,
            css,
        )
        self.assertIn(
            ".diary-col-mark {\n    width: %dpx;" % MARK_COLUMN_WIDTH, css
        )

    def test_student_column_is_thinner_than_before(self):
        self.assertLess(STUDENT_COLUMN_WIDTH, 220)
        self.assertLess(MARK_COLUMN_WIDTH, 104)

    def test_cells_are_thin(self):
        css = self.css()

        self.assertIn("    height: 32px;\n    padding: 0;\n    border: 1px", css)
        self.assertNotIn("    height: 44px;\n    padding: 0;", css)

    def test_student_diary_uses_the_same_borders_as_the_group_table(self):
        css = self.css()
        block = css[css.index("Раунд 21"):]

        self.assertIn(".student-mark {", block)
        self.assertIn("border: 1px solid #e5e7eb;", block)


# ---------------------------------------------------------------------------
# Раунд 23: журнал (оценки нескольких групп за период, только просмотр)
# ---------------------------------------------------------------------------

from schedule.models import Semester

from .journal import average, format_average
from .journal_views import GROUP_COLUMN_WIDTH, STAT_COLUMN_WIDTH

DIARY_STATIC = Path(__file__).resolve().parent / "static/diary"


class JournalBase(LessonBase):
    def journal(self, user, params=None, today=LESSON):
        self.client.force_login(user)

        with today_is(today):
            return self.client.get(reverse("diary_journal"), params or {})

    def week_params(self, *groups, **extra):
        groups = groups or (self.group,)

        return {
            "f": "1", "week": "1", "date": "2026-10-19",
            "group": [group.pk for group in groups], **extra,
        }

    @property
    def other_group(self):
        return Group.objects.get(group_name="Б-02")

    def add_lesson(self, teacher, name, weekday=2, group=None, number=1):
        pair = make_pair(group or self.group, teacher, name)
        Day.objects.create(
            group=group or self.group, pair=pair, pair_number=number,
            weekday=Weekday.objects.get(pk=weekday),
        )

        return pair

    def semester(self, number=1, start=date(2026, 9, 1), end=date(2026, 12, 31)):
        return Semester.objects.create(
            semester_number=number, start_date=start, end_date=end
        )

    def names(self, response):
        return [row["name"] for row in response.context["rows"]]


class JournalAccessTests(JournalBase):
    def test_login_required(self):
        response = self.client.get(reverse("diary_journal"))

        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('diary_journal')}"
        )

    def test_student_has_no_journal(self):
        self.assertEqual(self.journal(self.anna).status_code, 403)

    def test_admin_and_teacher_open_it(self):
        self.assertEqual(self.journal(self.admin).status_code, 200)
        self.assertEqual(self.journal(self.teacher_user).status_code, 200)

    def test_admin_chooses_from_all_groups_teacher_from_his_own(self):
        names = lambda r: [g.group_name for g in r.context["groups"]]

        self.assertEqual(names(self.journal(self.admin)), ["А-01", "Б-02"])
        self.assertEqual(names(self.journal(self.teacher_user)), ["А-01"])

    def test_foreign_group_in_address_is_ignored(self):
        response = self.journal(
            self.teacher_user, self.week_params(self.other_group)
        )

        self.assertEqual(response.context["chosen"], [])
        self.assertEqual(response.context["rows"], [])
        self.assertNotContains(response, "Чужой")

    def test_bad_parameters_do_not_break_the_page(self):
        response = self.journal(self.admin, {
            "f": "1", "group": ["x", "-1", "99999999999"], "course": "9",
            "semester": "0", "date": "oops",
        })

        self.assertEqual(response.status_code, 200)

    def test_home_pages_link_to_the_journal(self):
        link = reverse("diary_journal")

        for user in (self.admin, self.teacher_user):
            self.client.force_login(user)

            with today_is(LESSON):
                self.assertContains(
                    self.client.get(reverse("diary_home")), f'href="{link}"'
                )

        self.client.force_login(self.anna)

        with today_is(LESSON):
            self.assertNotContains(
                self.client.get(reverse("diary_home")), f'href="{link}"'
            )

    def test_group_diary_links_to_group_journal(self):
        self.client.force_login(self.admin)

        with today_is(LESSON):
            response = self.client.get(
                reverse("diary_group", kwargs={"group_id": self.group.pk})
            )

        self.assertContains(response, "Журнал группы")
        self.assertContains(response, f"group={self.group.pk}")


class JournalTableTests(JournalBase):
    def test_default_is_current_week_and_asks_for_a_group(self):
        response = self.journal(self.admin)

        self.assertTrue(response.context["filters"].week)
        self.assertTrue(response.context["week_mode"])
        self.assertContains(response, "Выберите одну или несколько групп")
        self.assertFalse(response.context["has_filters"])

    def test_week_table_has_students_columns_and_marks(self):
        self.mark(student=self.anna, score=85)
        self.mark(student=self.boris, kind=MARK_ABSENT, score=None)
        self.mark(student=self.vera, kind=MARK_ILL, score=None)

        response = self.journal(self.admin, self.week_params())
        rows = {row["name"].split()[0]: row for row in response.context["rows"]}

        self.assertEqual(len(response.context["columns"]), 1)
        self.assertEqual(self.names(response)[0].split()[0], "Борисов")
        self.assertEqual(
            [row["name"].split()[0] for row in response.context["rows"]],
            ["Борисов", "Ветрова", "Яблокова"],
        )
        self.assertEqual(rows["Яблокова"]["cells"][0]["text"], "85")
        self.assertEqual(rows["Борисов"]["cells"][0]["kind"], MARK_ABSENT)
        self.assertEqual(rows["Ветрова"]["cells"][0]["kind"], MARK_ILL)
        # В «оценках» только баллы: Н и Б не считаются
        self.assertEqual(response.context["scores_count"], 1)
        self.assertEqual(response.context["average"], "85,0")
        self.assertContains(response, 'class="journal-flag is-absent"')
        self.assertContains(response, 'class="journal-flag is-ill"')

    def test_journal_is_read_only(self):
        response = self.journal(self.admin, self.week_params())

        self.assertNotContains(response, 'class="diary-input')

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(reverse("diary_journal"), {}).status_code, 200
        )
        self.assertEqual(Mark.objects.count(), 0)

    def test_empty_cell_when_no_mark(self):
        response = self.journal(self.admin, self.week_params())

        self.assertEqual(
            [row["cells"][0]["text"] for row in response.context["rows"]],
            ["", "", ""],
        )

    def test_marks_of_other_days_and_lessons_are_not_shown(self):
        self.mark(student=self.anna, lesson_date=TUESDAY)
        self.mark(student=self.anna, lesson_date=date(2026, 10, 12))

        response = self.journal(self.admin, self.week_params())

        self.assertEqual(response.context["scores_count"], 0)
        self.assertEqual(response.context["average"], "—")

    def test_cancelled_lesson_has_no_column(self):
        self.change("CANCEL")

        response = self.journal(self.admin, self.week_params())

        self.assertEqual(response.context["columns"], [])
        self.assertContains(response, "За выбранный период занятий нет")

    def test_group_without_students(self):
        Profile.objects.filter(group=self.group).delete()

        response = self.journal(self.admin, self.week_params())

        self.assertContains(response, "пока нет студентов")

    def test_week_navigation(self):
        response = self.journal(self.admin, self.week_params())

        self.assertIn("date=2026-10-12", response.context["prev_url"])
        self.assertIn("date=2026-10-26", response.context["next_url"])
        self.assertIn(f"group={self.group.pk}", response.context["next_url"])
        self.assertIn("week=1", response.context["prev_url"])
        self.assertTrue(response.context["is_current_week"])

    def test_other_week_offers_today_link(self):
        response = self.journal(self.admin, self.week_params(date="2026-10-12"))

        self.assertFalse(response.context["is_current_week"])
        self.assertContains(response, "Сегодня</a>")
        self.assertIn("date=2026-10-19", response.context["today_url"])

    def test_lesson_header_links_to_the_group_diary_of_that_week(self):
        response = self.journal(self.admin, self.week_params())
        link = reverse("diary_group", kwargs={"group_id": self.group.pk})

        self.assertContains(response, f'href="{link}?date=2026-10-19"')

    def test_groups_alternate_stripes(self):
        response = self.journal(
            self.admin, self.week_params(self.group, self.other_group)
        )
        rows = response.context["rows"]

        self.assertEqual(
            [(row["group"], row["stripe"]) for row in rows],
            [("А-01", "a")] * 3 + [("Б-02", "b")],
        )
        self.assertContains(response, "journal-stripe-a", count=3)
        self.assertContains(response, "journal-stripe-b", count=1)

    def test_group_without_that_lesson_gets_none_cells(self):
        response = self.journal(
            self.admin, self.week_params(self.group, self.other_group)
        )
        stranger = response.context["rows"][-1]

        self.assertTrue(stranger["cells"][0]["none"])
        self.assertContains(response, "is-none")

    def test_pair_shared_by_two_groups_is_one_column(self):
        PairGroup.objects.create(pair=self.pair, group=self.other_group)
        Day.objects.create(
            group=self.other_group, pair=self.pair, pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )
        self.mark(student=self.stranger, score=70)

        response = self.journal(
            self.admin, self.week_params(self.group, self.other_group)
        )
        stranger = response.context["rows"][-1]

        self.assertEqual(len(response.context["columns"]), 1)
        self.assertEqual(stranger["cells"][0]["text"], "70")
        self.assertFalse(stranger["cells"][0]["none"])

    def test_mark_is_taken_per_student_not_per_group(self):
        PairGroup.objects.create(pair=self.pair, group=self.other_group)
        Day.objects.create(
            group=self.other_group, pair=self.pair, pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )
        self.mark(student=self.anna, score=90)

        response = self.journal(
            self.admin, self.week_params(self.group, self.other_group)
        )
        by_name = {r["name"].split()[0]: r for r in response.context["rows"]}

        self.assertEqual(by_name["Яблокова"]["cells"][0]["text"], "90")
        self.assertEqual(by_name["Чужой"]["cells"][0]["text"], "")


class JournalSubjectTests(JournalBase):
    def setUp(self):
        self.physics = self.add_lesson(self.teacher, "Физика")

    def test_subject_options_only_when_a_group_is_chosen(self):
        none = self.journal(self.admin, {"f": "1", "week": "1"})
        some = self.journal(self.admin, self.week_params())

        self.assertEqual(none.context["subject_options"], [])
        self.assertNotContains(none, 'name="subject"')
        self.assertEqual(
            some.context["subject_options"], ["Математика", "Физика"]
        )
        self.assertContains(some, 'name="subject"', count=2)

    def test_subject_filter_narrows_columns(self):
        all_columns = self.journal(self.admin, self.week_params())
        physics = self.journal(
            self.admin, self.week_params(subject="Физика")
        )

        self.assertEqual(len(all_columns.context["columns"]), 2)
        self.assertEqual(
            [c["subject"] for c in physics.context["columns"]], ["Физика"]
        )
        self.assertEqual(physics.context["subjects"], ["Физика"])

    def test_several_subjects(self):
        response = self.journal(
            self.admin, self.week_params(subject=["Физика", "Математика"])
        )

        self.assertEqual(len(response.context["columns"]), 2)

    def test_unknown_subject_is_ignored_not_empty(self):
        response = self.journal(self.admin, self.week_params(subject="Химия"))

        self.assertEqual(response.context["subjects"], [])
        self.assertEqual(len(response.context["columns"]), 2)

    def test_columns_are_ordered_by_date_and_number(self):
        response = self.journal(self.admin, self.week_params())

        self.assertEqual(
            [(c["day"], c["subject"]) for c in response.context["columns"]],
            [(LESSON, "Математика"), (TUESDAY, "Физика")],
        )
        self.assertEqual(
            [h["count"] for h in response.context["day_heads"]], [1, 1]
        )


class JournalTeacherScopeTests(JournalBase):
    def setUp(self):
        self.foreign = self.add_lesson(self.other_teacher, "Химия")

    def columns(self, response):
        return [c["subject"] for c in response.context["columns"]]

    def test_teacher_sees_only_own_subjects(self):
        response = self.journal(self.teacher_user, self.week_params())

        self.assertEqual(self.columns(response), ["Математика"])
        self.assertEqual(response.context["subject_options"], ["Математика"])

    def test_other_teacher_sees_his_own(self):
        response = self.journal(self.other_user, self.week_params())

        self.assertEqual(self.columns(response), ["Химия"])

    def test_curator_sees_all_subjects_of_the_group(self):
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.journal(self.teacher_user, self.week_params())

        self.assertEqual(self.columns(response), ["Математика", "Химия"])

    def test_admin_sees_everything(self):
        response = self.journal(self.admin, self.week_params())

        self.assertEqual(self.columns(response), ["Математика", "Химия"])

    def test_replacement_goes_to_the_replacing_teacher(self):
        self.change("REPLACE", new_teacher=self.other_teacher)

        mine = self.journal(self.teacher_user, self.week_params())
        theirs = self.journal(self.other_user, self.week_params())

        self.assertEqual(self.columns(mine), [])
        self.assertEqual(self.columns(theirs), ["Математика", "Химия"])


class JournalPeriodTests(JournalBase):
    def params(self, **extra):
        return {"f": "1", "group": [self.group.pk], **extra}

    def count(self, response):
        return len(response.context["columns"])

    def test_semester_shows_lessons_up_to_today(self):
        self.semester()

        # Понедельники 7, 14, 21, 28 сентября, 5, 12, 19 октября
        self.assertEqual(
            self.count(self.journal(self.admin, self.params(semester=1))), 7
        )
        self.assertEqual(
            self.count(
                self.journal(self.admin, self.params(semester=1), today=AFTER)
            ),
            8,
        )

    def test_holiday_has_no_column(self):
        self.semester()
        Calendar.objects.update_or_create(
            date=date(2026, 10, 12),
            defaults={"weekday": 1, "is_holiday": True},
        )

        response = self.journal(self.admin, self.params(semester=1))

        self.assertEqual(self.count(response), 6)

    def test_course_means_its_two_semesters(self):
        self.semester(1)
        self.semester(2, date(2027, 1, 11), date(2027, 5, 31))

        # «Сегодня» внутри 2 семестра: к занятиям 1 семестра добавляются
        # понедельники 11, 18, 25 января; между семестрами занятий нет
        response = self.journal(
            self.admin, self.params(course=1), today=date(2027, 1, 26)
        )
        days = [c["day"] for c in response.context["columns"]]

        self.assertIn(date(2026, 10, 19), days)
        self.assertIn(date(2027, 1, 25), days)
        self.assertIn(date(2026, 12, 28), days)
        self.assertNotIn(date(2027, 1, 4), days)  # каникулы между семестрами

    def test_missing_semester_is_reported(self):
        self.semester(1)

        response = self.journal(self.admin, self.params(course=1))

        self.assertEqual(self.count(response), 7)
        self.assertTrue(
            any("Не заданы даты семестров: 2" in n
                for n in response.context["notes"])
        )
        self.assertContains(response, "Не заданы даты семестров: 2")

    def test_course_and_semester_are_exclusive(self):
        self.semester(1)

        response = self.journal(
            self.admin, self.params(course=1, semester=2)
        )

        self.assertEqual(response.context["filters"].semesters, [])
        self.assertEqual(response.context["filters"].courses, [1])
        self.assertIn(
            "Курс и семестр одновременно выбрать нельзя: использован курс.",
            response.context["notes"],
        )

    def test_without_choice_current_semester_is_used(self):
        self.semester()

        response = self.journal(self.admin, self.params())

        self.assertFalse(response.context["week_mode"])
        self.assertEqual(self.count(response), 7)

    def test_without_semesters_falls_back_to_current_week(self):
        response = self.journal(self.admin, self.params())

        self.assertTrue(response.context["week_mode"])
        self.assertTrue(
            any("Семестры не заданы" in n for n in response.context["notes"])
        )
        self.assertEqual(self.count(response), 1)

    def test_future_semester_has_no_lessons_yet(self):
        self.semester(1, date(2027, 9, 1), date(2027, 12, 31))

        response = self.journal(self.admin, self.params(semester=1))

        self.assertEqual(self.count(response), 0)
        self.assertContains(response, "За выбранный период занятий нет")

    def test_week_mode_overrides_course_and_semester(self):
        self.semester()

        response = self.journal(
            self.admin,
            self.week_params(semester=1),
        )

        self.assertEqual(self.count(response), 1)

    def test_semester_view_has_no_week_navigation(self):
        self.semester()

        response = self.journal(self.admin, self.params(semester=1))

        self.assertNotContains(response, "← Неделя")
        self.assertContains(response, "1 сем.")

    def test_query_count_does_not_grow_with_days(self):
        self.semester()
        self.add_lesson(self.teacher, "Физика")
        self.add_lesson(self.teacher, "Химия", weekday=3, group=self.other_group)

        self.client.force_login(self.admin)
        params = self.params(semester=1)
        params["group"] = [self.group.pk, self.other_group.pk]

        with today_is(AFTER):
            self.client.get(reverse("diary_journal"), params)

            with CaptureQueriesContext(connection) as short:
                self.client.get(reverse("diary_journal"), params)

        with today_is(date(2026, 12, 20)):
            with CaptureQueriesContext(connection) as long:
                self.client.get(reverse("diary_journal"), params)

        self.assertLess(len(long), 30)
        self.assertEqual(len(long), len(short))


class JournalDesignTests(TestCase):
    def css(self):
        return STYLE.read_text(encoding="utf-8")

    def test_absent_is_red_and_ill_is_blue_squares(self):
        css = self.css()

        self.assertIn(".journal-flag.is-absent {\n    background: #dc2626;", css)
        self.assertIn(".journal-flag.is-ill {\n    background: #2563eb;", css)
        self.assertIn("width: 20px;\n    height: 20px;", css)

    def test_groups_are_told_apart_by_gray_and_white_stripes(self):
        css = self.css()

        self.assertIn(
            ".journal-grid tr.journal-stripe-a td,\n"
            ".journal-grid tr.journal-stripe-a th {\n    background: #ffffff;",
            css,
        )
        self.assertIn(
            ".journal-grid tr.journal-stripe-b td,\n"
            ".journal-grid tr.journal-stripe-b th {\n    background: #eceef2;",
            css,
        )

    def test_group_column_width_and_offset_match_the_view(self):
        css = self.css()

        self.assertIn(
            ".journal-col-group {\n    width: %dpx;" % GROUP_COLUMN_WIDTH, css
        )
        self.assertIn(
            "position: sticky;\n    left: %dpx;" % STUDENT_COLUMN_WIDTH, css
        )

    def test_script_keeps_course_and_semester_exclusive(self):
        script = (DIARY_STATIC / "journal.js").read_text(encoding="utf-8")

        self.assertIn("data-journal-course", script)
        self.assertIn("data-journal-semester", script)


# ---------------------------------------------------------------------------
# Раунд 24: статистика (средний балл и число оценок) и общий журнал
# ---------------------------------------------------------------------------

class JournalStatsTests(JournalBase):
    def test_average_helpers(self):
        self.assertIsNone(average([]))
        self.assertEqual(average([80, 90]), 85)
        self.assertEqual(format_average(None), "—")
        self.assertEqual(format_average(78.54), "78,5")
        self.assertEqual(format_average(100), "100,0")
        self.assertEqual(format_average(0), "0,0")

    def test_student_row_has_count_and_average_without_absences(self):
        self.add_lesson(self.teacher, "Физика")
        self.mark(student=self.anna, score=85)
        self.mark(student=self.anna, pair=Pair.objects.get(pair_name="Физика"),
                  lesson_date=TUESDAY, score=70)
        self.mark(student=self.boris, kind=MARK_ABSENT, score=None)

        response = self.journal(self.admin, self.week_params())
        rows = {r["name"].split()[0]: r for r in response.context["rows"]}

        self.assertEqual(rows["Яблокова"]["scores"], 2)
        self.assertEqual(rows["Яблокова"]["average"], "77,5")
        self.assertEqual(rows["Борисов"]["scores"], 0)
        self.assertEqual(rows["Борисов"]["average"], "—")
        self.assertEqual(response.context["scores_count"], 2)
        self.assertEqual(response.context["average"], "77,5")

    def test_zero_is_a_real_score(self):
        self.mark(student=self.anna, score=0)
        self.mark(student=self.boris, score=100)

        response = self.journal(self.admin, self.week_params())

        self.assertEqual(response.context["scores_count"], 2)
        self.assertEqual(response.context["average"], "50,0")

    def test_stats_follow_the_subject_filter(self):
        self.add_lesson(self.teacher, "Физика")
        self.mark(student=self.anna, score=90)
        self.mark(student=self.anna, pair=Pair.objects.get(pair_name="Физика"),
                  lesson_date=TUESDAY, score=50)

        response = self.journal(
            self.admin, self.week_params(subject="Физика")
        )

        self.assertEqual(response.context["average"], "50,0")

    def test_table_shows_stat_columns_and_summary(self):
        self.mark(student=self.anna, score=85)

        response = self.journal(self.admin, self.week_params())

        self.assertContains(response, 'class="journal-col-stat"', count=2)
        self.assertContains(response, "Средний балл:")
        self.assertContains(response, "Оценок:")
        self.assertEqual(
            response.context["table_width"],
            STUDENT_COLUMN_WIDTH + GROUP_COLUMN_WIDTH + 2 * STAT_COLUMN_WIDTH
            + MARK_COLUMN_WIDTH,
        )

    def test_stat_column_width_matches_css(self):
        css = STYLE.read_text(encoding="utf-8")

        self.assertIn(
            ".journal-col-stat {\n    width: %dpx;" % STAT_COLUMN_WIDTH, css
        )


class GeneralJournalTests(JournalBase):
    def page(self, user, params=None, today=LESSON):
        self.client.force_login(user)

        with today_is(today):
            return self.client.get(reverse("diary_journal_general"), params or {})

    def stats(self, response):
        return {
            item["group"].group_name: item
            for item in response.context["group_stats"]
        }

    def test_access(self):
        url = reverse("diary_journal_general")

        self.assertRedirects(
            self.client.get(url), f"{reverse('login')}?next={url}"
        )
        self.assertEqual(self.page(self.anna).status_code, 403)
        self.assertEqual(self.page(self.admin).status_code, 200)
        self.assertEqual(self.page(self.teacher_user).status_code, 200)

    def test_without_choice_all_available_groups(self):
        admin = self.stats(self.page(self.admin))
        teacher = self.stats(self.page(self.teacher_user))

        self.assertEqual(sorted(admin), ["А-01", "Б-02"])
        self.assertEqual(sorted(teacher), ["А-01"])

    def test_group_rows_and_totals(self):
        self.mark(student=self.anna, score=80)
        self.mark(student=self.boris, score=60)
        self.mark(student=self.vera, kind=MARK_ILL, score=None)

        response = self.page(self.admin)
        stats = self.stats(response)

        self.assertEqual(
            (stats["А-01"]["students"], stats["А-01"]["lessons"],
             stats["А-01"]["scores"], stats["А-01"]["average"]),
            (3, 1, 2, "70,0"),
        )
        self.assertEqual(
            (stats["Б-02"]["students"], stats["Б-02"]["lessons"],
             stats["Б-02"]["scores"], stats["Б-02"]["average"]),
            (1, 0, 0, "—"),
        )
        self.assertEqual(
            response.context["totals"],
            {"students": 4, "scores": 2, "average": "70,0"},
        )
        self.assertContains(response, "Всего")

    def test_group_without_lessons_still_counts_students(self):
        response = self.page(self.admin, {"f": "1", "week": "1",
                                          "date": "2026-11-02"})

        self.assertEqual(self.stats(response)["А-01"]["students"], 3)
        self.assertEqual(self.stats(response)["А-01"]["lessons"], 0)

    def test_explicit_group_narrows_the_list(self):
        response = self.page(
            self.admin, self.week_params(self.other_group)
        )

        self.assertEqual(list(self.stats(response)), ["Б-02"])

    def test_subjects_are_offered_only_for_chosen_groups(self):
        self.add_lesson(self.teacher, "Физика")

        none = self.page(self.admin)
        some = self.page(self.admin, self.week_params(subject="Физика"))

        self.assertEqual(none.context["subject_options"], [])
        self.assertNotContains(none, 'name="subject"')
        self.assertEqual(
            some.context["subject_options"], ["Математика", "Физика"]
        )
        self.assertEqual(self.stats(some)["А-01"]["lessons"], 1)

    def test_teacher_stats_cover_only_own_subjects(self):
        foreign = self.add_lesson(self.other_teacher, "Химия")
        self.mark(student=self.anna, score=80)
        self.mark(student=self.anna, pair=foreign, lesson_date=TUESDAY, score=20)

        mine = self.stats(self.page(self.teacher_user))["А-01"]
        admin = self.stats(self.page(self.admin))["А-01"]

        self.assertEqual((mine["lessons"], mine["average"]), (1, "80,0"))
        self.assertEqual((admin["lessons"], admin["average"]), (2, "50,0"))

    def test_curator_stats_cover_all_subjects(self):
        foreign = self.add_lesson(self.other_teacher, "Химия")
        self.group.group_curator = self.teacher
        self.group.save()
        self.mark(student=self.anna, pair=foreign, lesson_date=TUESDAY, score=20)

        stats = self.stats(self.page(self.teacher_user))["А-01"]

        self.assertEqual((stats["lessons"], stats["scores"]), (2, 1))

    def test_semester_period(self):
        self.semester()

        response = self.page(self.admin, {"f": "1", "semester": "1"})

        self.assertFalse(response.context["week_mode"])
        self.assertEqual(self.stats(response)["А-01"]["lessons"], 7)

    def test_group_row_opens_its_journal_for_the_same_period(self):
        self.semester()
        response = self.page(self.admin, {"f": "1", "semester": "1"})
        url = self.stats(response)["А-01"]["url"]

        self.assertTrue(url.startswith(reverse("diary_journal") + "?"))
        self.assertIn(f"group={self.group.pk}", url)
        self.assertIn("semester=1", url)
        self.assertNotIn(f"group={self.other_group.pk}", url)

        opened = self.journal(self.admin, dict(
            part.split("=") for part in url.split("?")[1].split("&")
        ))
        self.assertEqual(len(opened.context["columns"]), 7)

    def test_week_navigation_stays_on_the_general_page(self):
        response = self.page(self.admin)

        self.assertTrue(
            response.context["next_url"].startswith(
                reverse("diary_journal_general")
            )
        )

    def test_buttons_connect_the_pages(self):
        general = self.page(self.admin)
        detail = self.journal(self.admin)
        home_link = reverse("diary_journal_general")

        self.assertContains(general, f'href="{reverse("diary_journal")}"')
        self.assertContains(detail, f'href="{home_link}"')

        self.client.force_login(self.teacher_user)

        with today_is(LESSON):
            self.assertContains(
                self.client.get(reverse("diary_home")), f'href="{home_link}"'
            )

    def test_student_still_gets_only_his_diary(self):
        for name in ("diary_journal", "diary_journal_general"):
            self.client.force_login(self.anna)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

        self.assertTemplateUsed(
            self.client.get(reverse("diary_home")), "diary/student.html"
        )

    def test_teacher_without_groups_gets_a_hint(self):
        nobody, _ = make_teacher_user("Сидоров С. С.", "sidorov@gmail.com")

        response = self.page(nobody)

        self.assertContains(response, "нет групп для журнала")

    def test_query_count_does_not_grow_with_groups(self):
        self.semester()

        self.client.force_login(self.admin)

        with today_is(AFTER):
            self.client.get(reverse("diary_journal_general"), {"f": "1"})

            with CaptureQueriesContext(connection) as few:
                self.client.get(reverse("diary_journal_general"), {"f": "1"})

        for number in range(5):
            group = Group.objects.create(
                group_name=f"В-0{number}", group_course=self.group.group_course,
                shift=self.group.shift,
            )
            make_student(f"s{number}", f"Студентов{number}", "Иван", group)
            self.add_lesson(self.teacher, f"Предмет{number}", weekday=3,
                            group=group)

        with today_is(AFTER):
            with CaptureQueriesContext(connection) as many:
                self.client.get(reverse("diary_journal_general"), {"f": "1"})

        self.assertLess(len(many), 30)
        self.assertEqual(len(many), len(few))


# ---------------------------------------------------------------------------
# Раунд 25: список групп дневника по макету (шапка: название, поиск, теги)
# ---------------------------------------------------------------------------

class GroupListDesignTests(LessonBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tag_a = GroupTag.objects.create(tag_name="Программирование")
        cls.tag_b = GroupTag.objects.create(tag_name="Дизайн")
        cls.group.group_tag = cls.tag_a
        cls.group.save()
        other = Group.objects.get(group_name="Б-02")
        other.group_tag = cls.tag_b
        other.save()

    def home(self, user, **params):
        self.client.force_login(user)

        with today_is(LESSON):
            return self.client.get(reverse("diary_home"), params)

    def names(self, response):
        return [group.group_name for group in response.context["groups"]]

    def test_header_has_search_and_tags_and_tiles_come_after(self):
        html = self.home(self.admin).content.decode()

        head = html.index("page-head-actions")
        search = html.index("diary-choice-search")
        select = html.index('name="tag"')
        tiles = html.index("diary-group-grid")

        self.assertLess(head, search)
        self.assertLess(search, select)
        self.assertLess(select, tiles)
        self.assertIn("<h1>ДНЕВНИК</h1>", html)

    def test_tag_select_lists_all_tags_and_marks_the_chosen_one(self):
        response = self.home(self.admin, tag=self.tag_b.pk)

        self.assertContains(response, "Все теги")
        self.assertContains(response, f'<option value="{self.tag_a.pk}">')
        self.assertContains(response, f'<option value="{self.tag_b.pk}" selected>')
        self.assertEqual(
            [tag.tag_name for tag in response.context["group_tags"]],
            ["Дизайн", "Программирование"],
        )

    def test_tag_select_has_only_tags_of_available_groups(self):
        response = self.home(self.teacher_user)

        self.assertEqual(
            [tag.tag_name for tag in response.context["group_tags"]],
            ["Программирование"],
        )

    def test_tag_filter(self):
        self.assertEqual(
            self.names(self.home(self.admin, tag=self.tag_b.pk)), ["Б-02"]
        )
        self.assertEqual(
            self.names(self.home(self.admin, tag=self.tag_a.pk)), ["А-01"]
        )

    def test_bad_or_foreign_tag_is_ignored(self):
        self.assertEqual(
            self.names(self.home(self.teacher_user, tag=self.tag_b.pk)),
            ["А-01"],
        )
        self.assertEqual(
            self.names(self.home(self.admin, tag="abc")), ["А-01", "Б-02"]
        )
        self.assertEqual(
            self.names(self.home(self.admin, tag="99999999999")),
            ["А-01", "Б-02"],
        )

    def test_tag_and_search_work_together(self):
        self.assertEqual(
            self.names(self.home(self.admin, tag=self.tag_a.pk, q="Б-")), []
        )
        self.assertEqual(
            self.names(self.home(self.admin, tag=self.tag_a.pk, q="А-")),
            ["А-01"],
        )

    def test_nothing_found_for_the_tag(self):
        response = self.home(self.admin, tag=self.tag_a.pk, q="Б-")

        self.assertContains(response, "групп не нашлось")
        self.assertContains(response, "Сбросить")

    def test_untagged_group_is_hidden_by_a_tag_filter(self):
        Group.objects.create(
            group_name="В-03", group_course=self.group.group_course,
            shift=self.group.shift,
        )

        response = self.home(self.admin, tag=self.tag_a.pk)

        self.assertEqual(self.names(response), ["А-01"])
        self.assertIn("В-03", self.names(self.home(self.admin)))

    def test_reset_link_only_when_something_is_chosen(self):
        self.assertNotContains(self.home(self.admin), "Сбросить")
        self.assertContains(self.home(self.admin, q="А"), "Сбросить")
        self.assertContains(
            self.home(self.admin, tag=self.tag_a.pk), "Сбросить"
        )

    def test_teachers_day_is_kept_in_the_form(self):
        response = self.home(self.teacher_user, date="2026-10-20")

        self.assertContains(
            response, '<input type="hidden" name="date" value="2026-10-20">',
            html=True,
        )
        self.assertContains(response, "?date=2026-10-19")  # день назад

    def test_search_and_tags_are_not_shown_without_groups(self):
        response = self.home(self.other_user)  # преподаватель без групп

        self.assertNotContains(response, "diary-choice-search")

    def test_teacher_day_navigation_moved_into_lessons_card(self):
        response = self.home(self.teacher_user)

        self.assertContains(response, "diary-lessons-head")
        self.assertContains(response, "← День")

    def test_journal_links_are_a_separate_row(self):
        response = self.home(self.admin)

        self.assertContains(response, 'class="diary-subnav"')
        self.assertContains(response, reverse("diary_journal"))
        self.assertContains(response, reverse("diary_journal_general"))


class GroupListCssTests(TestCase):
    def css(self):
        return STYLE.read_text(encoding="utf-8")

    def test_three_columns_centered_tiles_with_big_name(self):
        css = self.css()

        self.assertIn(
            ".diary-group-grid {\n    display: grid;\n"
            "    grid-template-columns: repeat(3, minmax(0, 1fr));",
            css,
        )
        self.assertIn("    text-align: center;\n    text-decoration: none;", css)
        self.assertIn(".diary-group-name {\n    font-size: 26px;", css)

    def test_grid_gets_narrower_on_small_screens(self):
        css = self.css()
        block = css[css.index(".diary-group-grid {"):]

        self.assertIn("repeat(2, minmax(0, 1fr))", block)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", block)

    def test_script_submits_the_form_on_tag_change(self):
        script = (DIARY_STATIC / "choice.js").read_text(encoding="utf-8")

        self.assertIn("data-diary-choice", script)
        self.assertIn("form.submit()", script)
