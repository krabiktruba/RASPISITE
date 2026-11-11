import re
from datetime import date, time
from io import StringIO
from pathlib import Path
from unittest import mock
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.messages import constants as message_levels
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from diary.models import Mark

from .access import (
    full_name,
    home_group_id,
    own_group_id,
    own_teacher_id,
    real_teachers,
    set_group,
    set_role,
    user_role,
)
from .forms import MAX_FAILED_LOGINS
from .group_names import check_group_name, is_standard_name
from .names import person_html, short_name, split_name
from .gmail import normalize_gmail
from .tags import (
    TAG_COLOR_DEFAULT,
    build_tag_css,
    color_class,
    normalize_color,
    text_color,
)
from .models import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    BreakHours,
    Calendar,
    Course,
    Day,
    Group,
    Pair,
    PairGroup,
    Profile,
    ScheduleChange,
    Semester,
    Shift,
    StudyHours,
    GroupTag,
    TeacherTag,
    Teacher,
    Weekday,
)

MONDAY = date(2026, 10, 19)
SUNDAY = date(2026, 10, 18)
PASSWORD = "Str0ng-pass-12345"


def fake_today(value):
    """Подменяет «сегодня» на нужную дату."""
    return mock.patch(
        "schedule.views.timezone.localdate",
        return_value=value,
    )


def make_pair(group, teacher, name):
    """Пара, привязанная к группе (пары самостоятельные, связь отдельная)."""
    pair = Pair.objects.create(teacher=teacher, pair_name=name)
    PairGroup.objects.create(pair=pair, group=group)

    return pair


def make_teacher(name, email):
    """Преподаватель с учётной записью (как после формы «Пользователи»)."""
    user = get_user_model().objects.create_user(
        email, email=email, password=PASSWORD
    )
    teacher = Teacher.objects.create(teacher_name=name)
    Profile.objects.create(user=user, role=ROLE_TEACHER, teacher=teacher)

    return teacher


class BaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        names = [
            "Понедельник", "Вторник", "Среда",
            "Четверг", "Пятница", "Суббота",
        ]

        for number, name in enumerate(names, start=1):
            Weekday.objects.create(weekday_id=number, weekday_name=name)

        cls.course = Course.objects.create(course_id=1)
        cls.shift = Shift.objects.create(shift_id=1)

        StudyHours.objects.create(
            shift=cls.shift,
            pair_number=1,
            pair_start_time=time(8, 30),
            pair_end_time=time(10, 0),
        )

        cls.teacher = make_teacher("Иванов И. И.", "teacherivanov@gmail.com")

        cls.group = Group.objects.create(
            group_name="А-01",
            group_course=cls.course,
            shift=cls.shift,
        )

        pair = cls.pair = make_pair(cls.group, cls.teacher, "Математика")

        # Понедельник, первая пара
        Day.objects.create(
            group=cls.group,
            pair=pair,
            pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )

        User = get_user_model()
        cls.user = User.objects.create_user("student", password=PASSWORD)
        cls.admin = User.objects.create_user(
            "admin2", password=PASSWORD, is_staff=True
        )


class AuthTests(BaseTestCase):
    def test_anonymous_is_redirected_to_login(self):
        for name in ("groups", "teachers", "calendar", "workshop"):
            response = self.client.get(reverse(name))

            self.assertRedirects(
                response,
                f"{reverse('login')}?next={reverse(name)}",
            )

    def test_home_opens_main_page(self):
        self.client.force_login(self.user)
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mainpage.html")

    def test_home_requires_login(self):
        response = self.client.get("/")

        self.assertRedirects(
            response,
            f"{reverse('login')}?next=/",
            fetch_redirect_response=False,
        )

    def test_brand_link_leads_to_main_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("groups"))

        self.assertContains(response, 'href="/" class="brand"')

    def test_workshop_is_staff_only(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("workshop")).status_code, 403
        )

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("workshop")).status_code, 200
        )


class GmailNormalizeTests(TestCase):
    """Проверка и приведение адреса Gmail к единому виду."""

    def test_case_spaces_and_dots_are_normalized(self):
        self.assertEqual(
            normalize_gmail("  Ivan.Petrov77@Gmail.COM "),
            "ivanpetrov77@gmail.com",
        )

    def test_googlemail_is_gmail(self):
        self.assertEqual(
            normalize_gmail("ivanpetrov77@googlemail.com"),
            "ivanpetrov77@gmail.com",
        )

    def test_bad_addresses_are_rejected(self):
        bad = [
            "",
            "   ",
            "ivanpetrov77",
            "ivanpetrov77@",
            "@gmail.com",
            "ivan@petrov77@gmail.com",
            "ivanpetrov77@yahoo.com",
            "ivanpetrov77@gmail.com.ru",
            "ivanpetrov77@mail.gmail.com",
            "ivanpetrov77+tag@gmail.com",
            "iv..anpetrov@gmail.com",
            ".ivanpetrov@gmail.com",
            "ivanpetrov.@gmail.com",
            "ivan@gmail.com",
            "a" * 31 + "@gmail.com",
            "иванпетров@gmail.com",
            "ivan petrov@gmail.com",
            "ivan_petrov@gmail.com",
            "a" * 250 + "@gmail.com",
        ]

        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_gmail(value)

    def test_none_is_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_gmail(None)


class LoginTests(TestCase):
    """Вход по почте Gmail, без регистрации."""

    EMAIL = "ivanpetrov77@gmail.com"

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            self.EMAIL, password=PASSWORD
        )

    def tearDown(self):
        cache.clear()

    def login(self, username, password=PASSWORD):
        return self.client.post(reverse("login"), {
            "username": username,
            "password": password,
        })

    def is_logged_in(self):
        return self.client.get(reverse("groups")).status_code == 200

    def test_login_and_logout(self):
        response = self.login(self.EMAIL)

        self.assertRedirects(
            response, reverse("groups"), fetch_redirect_response=False
        )
        self.assertTrue(self.is_logged_in())

        self.client.post(reverse("logout"))

        self.assertFalse(self.is_logged_in())

    def test_case_and_dots_do_not_matter(self):
        for variant in (
            "IVANPETROV77@GMAIL.COM",
            "ivan.petrov77@gmail.com",
            "  Ivan.Petrov77@Gmail.com ",
            "ivanpetrov77@googlemail.com",
        ):
            with self.subTest(variant=variant):
                self.client.post(reverse("logout"))
                response = self.login(variant)

                self.assertRedirects(
                    response,
                    reverse("groups"),
                    fetch_redirect_response=False,
                )
                self.assertTrue(self.is_logged_in())

    def test_not_gmail_is_rejected_even_with_right_password(self):
        get_user_model().objects.create_user(
            "student@mail.ru", password=PASSWORD
        )

        response = self.login("student@mail.ru")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вход возможен только с почтой Gmail")
        self.assertFalse(self.is_logged_in())

    def test_bad_format_messages(self):
        for value, text in (
            ("ivanpetrov77", "Введите адрес вида имя@gmail.com."),
            ("ivanpetrov77@mail.ru", "только с почтой Gmail"),
            ("ivanpetrov77+x@gmail.com", "знака «+»"),
        ):
            with self.subTest(value=value):
                response = self.login(value)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)

    def test_wrong_password_and_unknown_user_look_the_same(self):
        wrong = self.login(self.EMAIL, "wrong-password")
        unknown = self.login("nobodyhere77@gmail.com")

        for response in (wrong, unknown):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Неверная почта или пароль.")

        self.assertFalse(self.is_logged_in())

    def test_empty_fields(self):
        response = self.login("", "")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.is_logged_in())

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()

        response = self.login(self.EMAIL)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.is_logged_in())

    def test_too_many_failures_lock_login(self):
        for _ in range(MAX_FAILED_LOGINS):
            self.login(self.EMAIL, "wrong-password")

        response = self.login(self.EMAIL)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Слишком много неудачных попыток")
        self.assertFalse(self.is_logged_in())

    def test_lock_is_per_address(self):
        for _ in range(MAX_FAILED_LOGINS):
            self.login(self.EMAIL, "wrong-password")

        other = "second.user77@gmail.com"
        get_user_model().objects.create_user(
            "seconduser77@gmail.com", password=PASSWORD
        )

        response = self.login(other)

        self.assertRedirects(
            response, reverse("groups"), fetch_redirect_response=False
        )

    def test_success_resets_failures(self):
        for _ in range(MAX_FAILED_LOGINS - 1):
            self.login(self.EMAIL, "wrong-password")

        self.login(self.EMAIL)
        self.client.post(reverse("logout"))

        for _ in range(MAX_FAILED_LOGINS - 1):
            self.login(self.EMAIL, "wrong-password")

        self.assertRedirects(
            self.login(self.EMAIL),
            reverse("groups"),
            fetch_redirect_response=False,
        )

    def test_empty_password_does_not_reset_failures(self):
        for _ in range(MAX_FAILED_LOGINS):
            self.login(self.EMAIL, "wrong-password")

        self.login(self.EMAIL, "")

        self.assertContains(
            self.login(self.EMAIL), "Слишком много неудачных попыток"
        )

    def test_login_page_asks_for_gmail(self):
        page = self.client.get(reverse("login"))

        self.assertContains(page, "Почта Gmail")
        self.assertContains(page, 'type="email"')

    def test_no_registration(self):
        page = self.client.get(reverse("login"))

        self.assertNotContains(page, "Регистрация")
        self.assertNotContains(page, "Создать аккаунт")
        self.assertNotContains(page, "/register/")

        with self.assertRaises(NoReverseMatch):
            reverse("register")

        self.assertEqual(self.client.get("/register/").status_code, 404)
        self.assertEqual(
            self.client.post("/register/", {
                "username": "newbie77@gmail.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            }).status_code,
            404,
        )
        self.assertFalse(
            get_user_model().objects.filter(
                username="newbie77@gmail.com"
            ).exists()
        )

    def test_logged_in_user_skips_login_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("login"))

        self.assertRedirects(
            response, reverse("groups"), fetch_redirect_response=False
        )


class SetLoginCommandTests(TestCase):
    """Команда переноса старых логинов на Gmail."""

    def run_command(self, *args):
        out = StringIO()
        call_command("set_login", *args, stdout=out)

        return out.getvalue()

    def test_renames_user(self):
        User = get_user_model()
        User.objects.create_user("oldadmin", password=PASSWORD)

        self.run_command("oldadmin", "Admin.Boss77@gmail.com")

        user = User.objects.get(username="adminboss77@gmail.com")
        self.assertEqual(user.email, "adminboss77@gmail.com")
        self.assertTrue(user.check_password(PASSWORD))

    def test_rejects_not_gmail(self):
        get_user_model().objects.create_user("oldadmin")

        with self.assertRaises(CommandError):
            self.run_command("oldadmin", "boss@mail.ru")

    def test_rejects_taken_login(self):
        User = get_user_model()
        User.objects.create_user("oldadmin")
        User.objects.create_user("bossboss77@gmail.com")

        with self.assertRaises(CommandError):
            self.run_command("oldadmin", "boss.boss77@gmail.com")

    def test_unknown_user(self):
        with self.assertRaises(CommandError):
            self.run_command("nobody", "bossboss77@gmail.com")

    def test_list_shows_only_old_logins(self):
        User = get_user_model()
        User.objects.create_user("oldadmin")
        User.objects.create_user("bossboss77@gmail.com")

        out = self.run_command("--list")

        self.assertIn("oldadmin", out)
        self.assertNotIn("bossboss77@gmail.com", out)


class CalendarTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_month_matches_real_calendar(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("calendar"), {"year": 2026, "month": 10}
            )

        self.assertEqual(response.status_code, 200)

        weeks = response.context["calendar_weeks"]
        self.assertEqual(len(weeks), 5)

        # 1 октября 2026 — четверг
        self.assertEqual(weeks[0][3]["number"], 1)
        self.assertTrue(weeks[0][3]["in_month"])
        self.assertFalse(weeks[0][0]["in_month"])

        # Строки таблицы Calendar создаются с верным днём недели
        self.assertTrue(Calendar.objects.exists())

        for record in Calendar.objects.all():
            self.assertEqual(record.weekday, record.date.isoweekday())

    def test_wrong_weekday_is_fixed(self):
        Calendar.objects.create(date=date(2026, 10, 1), weekday=1)
        Calendar.objects.filter(date=date(2026, 10, 1)).update(weekday=1)

        with fake_today(MONDAY):
            self.client.get(
                reverse("calendar"), {"year": 2026, "month": 10}
            )

        self.assertEqual(
            Calendar.objects.get(date=date(2026, 10, 1)).weekday, 4
        )

    def test_holiday_is_shown(self):
        Calendar.objects.create(
            date=date(2026, 10, 20),
            weekday=2,
            is_holiday=True,
            holiday_name="Тестовый праздник",
        )

        with fake_today(MONDAY):
            response = self.client.get(
                reverse("calendar"), {"year": 2026, "month": 10}
            )

        self.assertContains(response, "Тестовый праздник")

    def test_today_is_marked(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("calendar"), {"year": 2026, "month": 10}
            )

        marked = [
            cell
            for week in response.context["calendar_weeks"]
            for cell in week
            if cell["is_today"]
        ]

        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0]["iso"], "2026-10-19")

    def test_invalid_params_fall_back_to_today(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("calendar"), {"year": "abc", "month": 99}
            )

        self.assertEqual(response.context["month"], 10)
        self.assertEqual(response.context["year"], 2026)


class DatesNextToWeekdaysTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.user)

    def test_groups_tabs_have_dates(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("groups"), {"date": "2026-10-21"}
            )

        tabs = response.context["week_tabs"]

        self.assertEqual(tabs[0]["label"], "19 окт")
        self.assertEqual(tabs[2]["label"], "21 окт")
        self.assertTrue(tabs[2]["is_selected"])
        self.assertTrue(tabs[0]["is_today"])

    def test_group_schedule_cards_have_dates(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("group_schedule", args=[self.group.pk])
            )

        days = response.context["schedule_days"]

        self.assertEqual(days[0]["label"], "19 окт")
        self.assertEqual(days[5]["label"], "24 окт")
        self.assertContains(response, "19 окт")
        self.assertContains(response, "Математика")


class PaginationTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.user)

        for number in range(2, 8):
            Group.objects.create(
                group_name=f"Б-{number:02d}",
                group_course=self.course,
                shift=self.shift,
            )

    def test_first_and_second_page(self):
        with fake_today(MONDAY):
            first = self.client.get(reverse("groups"))
            second = self.client.get(reverse("groups"), {"page": 2})

        self.assertEqual(len(first.context["schedule_groups"]), 6)
        self.assertEqual(len(second.context["schedule_groups"]), 1)
        self.assertTrue(first.context["page_obj"].has_next())

    def test_bad_page_number_does_not_fail(self):
        with fake_today(MONDAY):
            response = self.client.get(reverse("groups"), {"page": "zzz"})
            far = self.client.get(reverse("groups"), {"page": 999})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(far.context["page_obj"].number, 2)


class TeachersWeekTests(BaseTestCase):
    """Страница преподавателей всегда показывает неделю."""

    def setUp(self):
        self.client.force_login(self.user)

    def test_default_is_current_week(self):
        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        days = response.context["schedule_days"]
        self.assertEqual(len(days), 6)
        self.assertEqual(days[0]["label"], "19 окт")
        self.assertEqual(days[-1]["label"], "24 окт")

        lessons = days[0]["pairs"][0]["lessons"]
        self.assertEqual(lessons[0]["subject"], "Математика")

    def test_today_is_highlighted(self):
        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        flags = [day["is_today"] for day in response.context["schedule_days"]]
        self.assertEqual(flags, [True, False, False, False, False, False])

    def test_no_day_week_switch(self):
        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        self.assertNotContains(response, "view-switch")
        self.assertNotIn("view_mode", response.context)

    def test_view_parameter_is_ignored(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("teachers"), {"view": "day"}
            )

        self.assertEqual(len(response.context["schedule_days"]), 6)

    def test_sunday_shows_week_that_ended(self):
        with fake_today(SUNDAY):
            response = self.client.get(reverse("teachers"))

        days = response.context["schedule_days"]
        self.assertEqual(len(days), 6)
        self.assertEqual(days[-1]["label"], "17 окт")

    def test_holiday_is_marked_in_week(self):
        Calendar.objects.create(
            date=MONDAY,
            weekday=1,
            is_holiday=True,
            holiday_name="Тестовый праздник",
        )

        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        monday = response.context["schedule_days"][0]
        self.assertIn("Тестовый праздник", monday["off_reason"])

    def test_other_date_shows_its_week(self):
        with fake_today(SUNDAY):
            response = self.client.get(
                reverse("teachers"), {"date": "2026-10-21"}
            )

        days = response.context["schedule_days"]
        self.assertEqual(days[0]["label"], "19 окт")
        self.assertEqual(
            days[0]["pairs"][0]["lessons"][0]["subject"], "Математика"
        )

    def test_navigation_moves_by_week(self):
        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        self.assertIn("date=2026-10-12", response.context["prev_url"])
        self.assertIn("date=2026-10-26", response.context["next_url"])
        self.assertNotIn("date=", response.context["today_url"])


class RolesTests(BaseTestCase):
    def test_users_page_is_staff_only(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("users")).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("users")).status_code, 200)

    def test_admin_sets_teacher_role(self):
        self.client.force_login(self.admin)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
        })

        user = get_user_model().objects.get(pk=self.user.pk)

        self.assertEqual(user_role(user), ROLE_TEACHER)
        self.assertIsNotNone(own_teacher_id(user))
        self.assertNotEqual(own_teacher_id(user), self.teacher.pk)
        self.assertFalse(user.is_staff)

    def test_teacher_role_without_choice_creates_teacher_from_name(self):
        self.client.force_login(self.admin)
        self.user.last_name = "Сидоров"
        self.user.first_name = "Сидор"
        self.user.save()

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
        })

        user = get_user_model().objects.get(pk=self.user.pk)
        teacher = Teacher.objects.get(pk=own_teacher_id(user))

        self.assertEqual(teacher.teacher_name, "Сидоров Сидор")
        self.assertIn(teacher, real_teachers())

    def test_teacher_field_is_ignored(self):
        """Привязка к старому преподавателю убрана: поле игнорируется."""
        self.client.force_login(self.admin)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
            "teacher": self.teacher.pk,
        })

        user = get_user_model().objects.get(pk=self.user.pk)

        self.assertNotEqual(own_teacher_id(user), self.teacher.pk)
        self.assertIsNotNone(own_teacher_id(user))

    def test_admin_role_sets_staff_and_can_be_removed(self):
        self.client.force_login(self.admin)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_ADMIN,
        })

        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertTrue(user.is_staff)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": "student",
        })

        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertFalse(user.is_staff)
        self.assertEqual(user_role(user), "student")

    def test_cannot_change_own_role_or_superuser(self):
        superuser = get_user_model().objects.create_superuser(
            "root", password=PASSWORD
        )
        self.client.force_login(self.admin)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.admin.pk,
            "role": "student",
        })
        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": superuser.pk,
            "role": "student",
        })

        self.assertTrue(
            get_user_model().objects.get(pk=self.admin.pk).is_staff
        )
        self.assertTrue(
            get_user_model().objects.get(pk=superuser.pk).is_staff
        )

    def test_student_cannot_change_roles(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_ADMIN,
        })

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            get_user_model().objects.get(pk=self.user.pk).is_staff
        )

    def test_access_codes_are_gone(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get("/code/").status_code, 404)

        page = self.client.get(reverse("users"))
        self.assertNotContains(page, "код доступа")
        self.assertNotContains(page, "Новый код")

        self.client.force_login(self.user)
        page = self.client.get(reverse("groups"))
        self.assertNotContains(page, "Ввести код")

    def test_teacher_sees_own_schedule_by_default(self):
        second = Teacher.objects.create(teacher_name="Петров П. П.")
        set_role(self.user, ROLE_TEACHER, second)
        self.client.force_login(
            get_user_model().objects.get(pk=self.user.pk)
        )

        with fake_today(MONDAY):
            response = self.client.get(reverse("teachers"))

        self.assertEqual(response.context["selected_teacher_id"], second.pk)


class GroupEditTests(BaseTestCase):
    def url(self):
        return reverse("group_edit", args=[self.group.pk])

    def test_staff_only(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), {}).status_code, 403)

    def test_form_shows_current_assignment(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)

        monday = response.context["edit_days"][0]
        self.assertEqual(monday["rows"][0]["selected"], self.pair.pk)
        self.assertIsNone(monday["rows"][1]["selected"])

    def test_save_assigns_and_clears(self):
        self.client.force_login(self.admin)

        response = self.client.post(self.url(), {
            "cell_1_1": self.pair.pk,
            "cell_2_3": self.pair.pk,
        })

        self.assertRedirects(response, self.url())
        self.assertEqual(Day.objects.filter(group=self.group).count(), 2)
        self.assertTrue(
            Day.objects.filter(
                group=self.group, weekday_id=2, pair_number=3
            ).exists()
        )

        # Пустая форма очищает расписание
        self.client.post(self.url(), {})
        self.assertEqual(Day.objects.filter(group=self.group).count(), 0)

    def test_teacher_in_two_places_is_saved_with_a_warning(self):
        other_group = Group.objects.create(
            group_name="Б-01",
            group_course=self.course,
            shift=self.shift,
        )
        other_pair = make_pair(other_group, self.teacher, "Физика")
        Day.objects.create(
            group=other_group,
            pair=other_pair,
            pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )

        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {
            "cell_2_2": self.pair.pk,
        }, follow=True)

        self.assertContains(response, "Расписание группы сохранено.")
        self.assertContains(response, "Совпадение у преподавателя")
        self.assertContains(response, "в это же время ведёт занятие")
        self.assertContains(response, "Б-01")
        self.assertTrue(
            Day.objects.filter(
                group=self.group, weekday_id=2, pair_number=2
            ).exists()
        )

    def test_editor_offers_only_attached_pairs(self):
        other_group = Group.objects.create(
            group_name="Б-01", group_course=self.course, shift=self.shift,
        )
        make_pair(other_group, self.teacher, "Физика")
        self.client.force_login(self.admin)

        response = self.client.get(self.url())

        self.assertEqual(
            [pair.pair_name for pair in response.context["pair_options"]],
            ["Математика"],
        )
        self.assertEqual(response.context["attached_count"], 1)

    def test_shared_pair_can_stand_in_two_groups(self):
        other_group = Group.objects.create(
            group_name="Б-01", group_course=self.course, shift=self.shift,
        )
        PairGroup.objects.create(pair=self.pair, group=other_group)
        self.client.force_login(self.admin)

        self.client.post(self.url(), {"cell_3_1": self.pair.pk})
        self.client.post(
            reverse("group_edit", args=[other_group.pk]),
            {"cell_3_1": self.pair.pk},
        )

        self.assertEqual(Day.objects.filter(pair=self.pair, weekday_id=3).count(), 2)

    def test_group_without_pairs_explains_what_to_do(self):
        lonely = Group.objects.create(
            group_name="В-01", group_course=self.course, shift=self.shift,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("group_edit", args=[lonely.pk]))

        self.assertContains(response, "не привязано ни одной пары")

    def test_unattached_pair_still_in_schedule_stays_in_list(self):
        PairGroup.objects.all().delete()
        self.client.force_login(self.admin)

        response = self.client.get(self.url())

        self.assertEqual(len(response.context["pair_options"]), 1)
        self.assertContains(response, "(не привязана)")

    def test_foreign_pair_is_rejected(self):
        other_group = Group.objects.create(
            group_name="Б-01",
            group_course=self.course,
            shift=self.shift,
        )
        foreign = make_pair(other_group, Teacher.objects.create(teacher_name="Сидоров С. С."), "Химия")

        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {
            "cell_3_1": foreign.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "недопустимая пара")
        self.assertEqual(
            Day.objects.filter(group=self.group, weekday_id=3).count(), 0
        )


STUDENT_PASSWORD = "Parol-Krepkiy-77"


class NewUserCreateTests(BaseTestCase):
    """Создание студента и преподавателя на странице «Пользователи»."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.second = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )

    def post_user(self, page=None, **overrides):
        data = {
            "role": "student",
            "last_name": "Иванов",
            "first_name": "Иван",
            "patronymic": "Иванович",
            "username": "ivanov77@gmail.com",
            "password": STUDENT_PASSWORD,
            "group": self.group.pk,
        }
        data.update(overrides)
        data = {f"new_user-{name}": value for name, value in data.items()}
        data["action"] = "create_user"

        if page is not None:
            data["page"] = page

        return self.client.post(reverse("users"), data)

    def get_user(self, username="ivanov77@gmail.com"):
        return get_user_model().objects.get(username=username)

    def exists(self, username="ivanov77@gmail.com"):
        return get_user_model().objects.filter(username=username).exists()

    # --- студент ----------------------------------------------------------

    def test_admin_creates_student(self):
        response = self.post_user()
        student = self.get_user()

        # После создания список открывается с подсветкой нового пользователя
        self.assertRedirects(
            response, f"{reverse('users')}?new={student.pk}"
        )
        self.assertTrue(student.check_password(STUDENT_PASSWORD))
        self.assertFalse(student.is_staff)
        self.assertEqual(student.last_name, "Иванов")
        self.assertEqual(student.first_name, "Иван")
        self.assertEqual(student.email, "ivanov77@gmail.com")
        self.assertEqual(student.profile.patronymic, "Иванович")
        self.assertEqual(user_role(student), "student")
        self.assertEqual(own_group_id(student), self.group.pk)
        self.assertEqual(full_name(student), "Иванов Иван Иванович")

        # Новый студент может войти со своими данными
        self.assertIsNotNone(
            authenticate(
                username="ivanov77@gmail.com", password=STUDENT_PASSWORD
            )
        )

    def test_patronymic_is_optional(self):
        self.post_user(patronymic="")

        student = self.get_user()
        self.assertEqual(student.profile.patronymic, "")
        self.assertEqual(full_name(student), "Иванов Иван")

    def test_student_requires_group(self):
        response = self.post_user(group="")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["new_user_form"].errors["group"])
        self.assertFalse(self.exists())

    def test_unknown_group_is_rejected(self):
        response = self.post_user(group=9999)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.exists())

    def test_student_may_join_group_with_curator(self):
        self.group.group_curator = self.teacher
        self.group.save()

        self.post_user()

        self.assertEqual(own_group_id(self.get_user()), self.group.pk)

    # --- преподаватель ----------------------------------------------------

    def test_admin_creates_teacher_without_group(self):
        response = self.post_user(
            role="teacher",
            last_name="Петров",
            first_name="Пётр",
            patronymic="Петрович",
            username="petrov77@gmail.com",
            group="",
        )

        user = self.get_user("petrov77@gmail.com")

        self.assertRedirects(response, f"{reverse('users')}?new={user.pk}")
        self.assertEqual(user_role(user), ROLE_TEACHER)
        self.assertFalse(user.is_staff)
        self.assertIsNone(user.profile.group_id)

        teacher = Teacher.objects.get(pk=own_teacher_id(user))
        self.assertEqual(teacher.teacher_name, "Петров Пётр Петрович")
        self.assertIn(teacher, real_teachers())
        self.assertIsNone(home_group_id(user))

    def test_teacher_becomes_curator_of_chosen_group(self):
        self.post_user(
            role="teacher",
            last_name="Петров",
            first_name="Пётр",
            patronymic="",
            username="petrov77@gmail.com",
            group=self.second.pk,
        )

        user = self.get_user("petrov77@gmail.com")
        self.second.refresh_from_db()

        self.assertEqual(self.second.group_curator_id, own_teacher_id(user))
        self.assertEqual(
            self.second.group_curator.teacher_name, "Петров Пётр"
        )
        self.assertEqual(home_group_id(user), self.second.pk)

    def test_teacher_cannot_take_group_that_has_curator(self):
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.post_user(
            role="teacher",
            username="petrov77@gmail.com",
            group=self.group.pk,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже есть куратор")
        self.assertFalse(self.exists("petrov77@gmail.com"))
        self.assertEqual(
            Teacher.objects.filter(teacher_name__startswith="Иванов Иван").count(),
            0,
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.group_curator_id, self.teacher.pk)

    def test_new_teacher_can_log_in(self):
        self.post_user(
            role="teacher", username="petrov77@gmail.com", group=""
        )

        self.assertTrue(
            self.client_class().login(
                username="petrov77@gmail.com", password=STUDENT_PASSWORD
            )
        )

    # --- имена -------------------------------------------------------------

    def test_names_are_required(self):
        for name in ("last_name", "first_name"):
            with self.subTest(name=name):
                response = self.post_user(**{name: "   "})

                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context["new_user_form"].errors[name]
                )
                self.assertFalse(self.exists())

    def test_bad_names_are_rejected(self):
        for bad in ("Ив4н", "Иван!", "Иван_", "<b>", "-Иван", "Иван-"):
            with self.subTest(bad=bad):
                response = self.post_user(first_name=bad)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.context["new_user_form"].errors["first_name"]
                )

        self.assertFalse(self.exists())

    def test_double_names_and_extra_spaces(self):
        self.post_user(
            last_name="  Римский   Корсаков ",
            first_name="Анна-Мария",
            patronymic="",
        )

        student = self.get_user()
        self.assertEqual(student.last_name, "Римский Корсаков")
        self.assertEqual(student.first_name, "Анна-Мария")

    def test_too_long_full_name_is_rejected(self):
        part = "а" * 60
        response = self.post_user(
            last_name=part, first_name=part, patronymic=part
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ФИО слишком длинное")
        self.assertFalse(self.exists())

    # --- логин, пароль -------------------------------------------------------

    def test_login_is_normalized_gmail(self):
        self.post_user(username=" Ivan.Ivanov77@GMAIL.com ")

        student = self.get_user("ivanivanov77@gmail.com")
        self.assertEqual(student.email, "ivanivanov77@gmail.com")

    def test_non_gmail_login_is_rejected(self):
        before = get_user_model().objects.count()

        for bad in ("ivanov", "ivanov77@mail.ru", "ivanov+x@gmail.com"):
            response = self.post_user(username=bad)

            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                response.context["new_user_form"].errors["username"]
            )

        self.assertEqual(get_user_model().objects.count(), before)

    def test_duplicate_login_keeps_form(self):
        # Регистр и точки в адресе Gmail не важны
        get_user_model().objects.create_user("duplicate77@gmail.com")
        before = get_user_model().objects.count()

        response = self.post_user(username="Dupli.Cate77@Gmail.com")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Такой логин уже занят.")
        self.assertEqual(get_user_model().objects.count(), before)

        form = response.context["new_user_form"]
        self.assertEqual(form["username"].value(), "Dupli.Cate77@Gmail.com")
        self.assertEqual(form["last_name"].value(), "Иванов")
        self.assertEqual(form["group"].value(), str(self.group.pk))

        # Пароль обратно в страницу не попадает
        self.assertNotContains(response, STUDENT_PASSWORD)

    def test_weak_password_is_rejected(self):
        response = self.post_user(password="12345678")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["new_user_form"].errors["password"])
        self.assertFalse(self.exists())

    # --- роль и группа не сбрасываются -------------------------------------

    def test_success_message_does_not_contain_password(self):
        response = self.post_user()
        page = self.client.get(response.url)

        self.assertContains(page, "Студент Иванов Иван Иванович")
        self.assertContains(page, "добавлен в группу А-01")
        self.assertNotContains(page, STUDENT_PASSWORD)

    def test_role_and_group_stay_selected_after_adding_student(self):
        self.post_user(group=self.second.pk)

        form = self.client.get(reverse("users")).context["new_user_form"]

        self.assertEqual(form["role"].value(), "student")
        self.assertEqual(form["group"].value(), self.second.pk)

        # Остальные поля очищаются
        for name in ("last_name", "first_name", "patronymic", "username"):
            self.assertIn(form[name].value(), (None, ""))

        # Повторное обновление страницы ничего не сбрасывает
        form = self.client.get(reverse("users")).context["new_user_form"]
        self.assertEqual(form["group"].value(), self.second.pk)

    def test_teacher_role_stays_selected_but_group_does_not(self):
        self.post_user(
            role="teacher", username="petrov77@gmail.com", group=self.second.pk
        )

        form = self.client.get(reverse("users")).context["new_user_form"]

        self.assertEqual(form["role"].value(), "teacher")
        # Группа уже получила куратора, поэтому заново не предлагается
        self.assertIsNone(form["group"].value())

    def test_role_and_group_are_kept_after_error(self):
        self.post_user(group=self.second.pk, password="12345678")

        form = self.client.get(reverse("users")).context["new_user_form"]

        self.assertEqual(form["role"].value(), "student")
        self.assertEqual(form["group"].value(), self.second.pk)

    def test_group_choices_show_curators(self):
        self.group.group_curator = self.teacher
        self.group.save()

        page = self.client.get(reverse("users"))

        self.assertContains(page, "А-01 · куратор: Иванов И. И.")
        self.assertContains(page, "Б-02 · без куратора")

    def test_redirect_shows_the_new_user(self):
        response = self.post_user(page="3")

        self.assertRedirects(
            response, reverse("users") + f"?new={self.get_user().pk}"
        )

    # --- список пользователей --------------------------------------------

    def test_users_table_shows_short_name_with_full_name_on_hover(self):
        self.post_user()

        page = self.client.get(reverse("users"))

        self.assertContains(
            page,
            '<span class="person-name" title="Иванов Иван Иванович">'
            'Иванович И. И.</span>',
        )
        self.assertContains(page, "ivanov77@gmail.com")

    # --- доступ --------------------------------------------------------------

    def test_old_create_student_action_does_nothing(self):
        before = get_user_model().objects.count()

        response = self.client.post(reverse("users"), {
            "action": "create_student",
            "student-username": "ivanov77@gmail.com",
            "student-group": self.group.pk,
            "student-password": STUDENT_PASSWORD,
        }, follow=True)

        self.assertContains(response, "Неизвестное действие.")
        self.assertEqual(get_user_model().objects.count(), before)

    def test_non_staff_cannot_create(self):
        self.client.force_login(self.user)

        response = self.post_user()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.exists())


class StudentGroupTests(BaseTestCase):
    """Привязка пользователей к группам и смена группы."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.second = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )

    def change_group(self, user, group_value, **extra):
        return self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": user.pk,
            "group": group_value,
            **extra,
        }, follow=True)

    def fresh(self, user):
        return get_user_model().objects.get(pk=user.pk)

    def test_bind_student_without_profile(self):
        # Самостоятельно зарегистрированный пользователь не имеет профиля
        self.assertFalse(Profile.objects.filter(user=self.user).exists())

        self.change_group(self.user, self.group.pk)

        self.assertEqual(own_group_id(self.fresh(self.user)), self.group.pk)

    def test_change_group_any_time_and_unbind(self):
        self.change_group(self.user, self.group.pk)
        self.change_group(self.user, self.second.pk)
        self.assertEqual(own_group_id(self.fresh(self.user)), self.second.pk)

        self.change_group(self.user, "")
        self.assertIsNone(own_group_id(self.fresh(self.user)))

    def test_invalid_group_is_rejected(self):
        self.change_group(self.user, self.group.pk)

        response = self.change_group(self.user, "9999")

        self.assertContains(response, "Выберите группу из списка.")
        self.assertEqual(own_group_id(self.fresh(self.user)), self.group.pk)

    def test_only_students_have_groups(self):
        set_role(self.user, ROLE_TEACHER, self.teacher)

        response = self.change_group(self.user, self.group.pk)

        self.assertContains(response, "только студентам")
        self.assertIsNone(self.fresh(self.user).profile.group_id)

    def test_changing_role_clears_group(self):
        self.change_group(self.user, self.group.pk)

        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
        })
        self.assertIsNone(self.fresh(self.user).profile.group_id)

        # Возврат в студенты не восстанавливает прежнюю группу
        self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.user.pk,
            "role": "student",
        })
        self.assertIsNone(own_group_id(self.fresh(self.user)))

    def test_only_students_get_group_form(self):
        set_group(self.user, self.group)
        teacher_user = get_user_model().objects.create_user(
            "teacher1", password=PASSWORD
        )
        set_role(teacher_user, ROLE_TEACHER, self.teacher)

        page = self.client.get(reverse("users"))
        rows = {row["user"].username: row for row in page.context["rows"]}

        self.assertTrue(rows["student"]["is_student"])
        self.assertEqual(rows["student"]["group"], self.group)
        self.assertFalse(rows["teacher1"]["is_student"])

    def test_menu_link_leads_to_own_group(self):
        set_group(self.user, self.second)
        self.client.force_login(self.fresh(self.user))

        page = self.client.get(reverse("groups"))

        self.assertContains(page, "Моё расписание")
        self.assertContains(
            page, reverse("group_schedule", args=[self.second.pk])
        )

    def test_menu_without_group(self):
        self.client.force_login(self.user)

        page = self.client.get(reverse("groups"))

        self.assertNotContains(page, "Моё расписание")
        self.assertNotContains(
            page, reverse("group_schedule", args=[1])
        )


class MenuScheduleLinkTests(BaseTestCase):
    """Пункт меню «Расписание» ведёт в группу пользователя, а не в группу 1."""

    def setUp(self):
        self.second = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )
        self.third = Group.objects.create(
            group_name="В-03",
            group_course=self.course,
            shift=self.shift,
        )
        User = get_user_model()
        self.teacher_user = User.objects.create_user(
            "teacher1", password=PASSWORD
        )
        set_role(self.teacher_user, ROLE_TEACHER, self.teacher)

    def menu_link(self, user, group):
        self.client.force_login(user)
        page = self.client.get(reverse("teachers"))
        return page, reverse("group_schedule", args=[group.pk])

    def test_student_gets_own_group(self):
        set_group(self.user, self.third)
        self.assertEqual(home_group_id(self.user), self.third.pk)

        page, link = self.menu_link(self.user, self.third)

        self.assertContains(page, "Моё расписание")
        self.assertContains(page, f'href="{link}"')

    def test_teacher_gets_curated_group(self):
        self.third.group_curator = self.teacher
        self.third.save()
        self.assertEqual(home_group_id(self.teacher_user), self.third.pk)

        page, link = self.menu_link(self.teacher_user, self.third)

        self.assertContains(page, "Моё расписание")
        self.assertContains(page, f'href="{link}"')

    def test_teacher_with_several_groups_gets_first_by_name(self):
        for group in (self.third, self.second):
            group.group_curator = self.teacher
            group.save()

        self.assertEqual(home_group_id(self.teacher_user), self.second.pk)

    def test_teacher_without_group_gets_plain_link(self):
        self.assertIsNone(home_group_id(self.teacher_user))
        self.client.force_login(self.teacher_user)

        page = self.client.get(reverse("teachers"))

        self.assertNotContains(page, "Моё расписание")
        self.assertContains(page, f'href="{reverse("groups")}"')

    def test_curator_of_other_teacher_is_not_used(self):
        other = Teacher.objects.create(teacher_name="Петров П. П.")
        self.second.group_curator = other
        self.second.save()

        self.assertIsNone(home_group_id(self.teacher_user))

    def test_admin_and_student_without_group_have_no_group(self):
        self.assertIsNone(home_group_id(self.admin))
        self.assertIsNone(home_group_id(self.user))

    def test_anonymous_has_no_group(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertIsNone(home_group_id(AnonymousUser()))

    def test_calendar_target_is_not_changed(self):
        # Число в календаре у преподавателя по-прежнему ведёт на «Преподавателей»
        self.third.group_curator = self.teacher
        self.third.save()
        self.client.force_login(self.teacher_user)

        page = self.client.get(reverse("calendar"))

        self.assertContains(page, reverse("teachers"))

    def test_login_page_has_no_group_link(self):
        page = self.client.get(reverse("login"))

        self.assertNotContains(page, "group_schedule")
        self.assertNotContains(page, "/groups/1/schedule/")


class GroupCuratorTests(BaseTestCase):
    """На странице групп виден куратор."""

    def setUp(self):
        self.client.force_login(self.user)

    def test_curator_name_is_shown(self):
        self.group.group_curator = self.teacher
        self.group.save()

        with fake_today(MONDAY):
            page = self.client.get(reverse("groups"))

        self.assertContains(page, "Куратор:")
        self.assertContains(page, "Иванов И. И.")
        self.assertNotContains(page, "не назначен")

    def test_group_without_curator(self):
        with fake_today(MONDAY):
            page = self.client.get(reverse("groups"))

        self.assertContains(page, "Куратор:")
        self.assertContains(page, "не назначен")

    def test_curators_do_not_add_queries_per_group(self):
        def count_queries():
            with fake_today(MONDAY):
                with CaptureQueriesContext(connection) as ctx:
                    self.client.get(reverse("groups"))
            return len(ctx)

        for number in range(2):
            Group.objects.create(
                group_name=f"К-{number}",
                group_course=self.course,
                shift=self.shift,
                group_curator=self.teacher,
            )
        many = count_queries()

        for number in range(2, 5):
            Group.objects.create(
                group_name=f"К-{number}",
                group_course=self.course,
                shift=self.shift,
                group_curator=self.teacher,
            )
        # Страница из шести групп (1 + 5) не требует больше запросов,
        # чем страница из трёх: куратор берётся тем же запросом
        self.assertEqual(count_queries(), many)


class WorkshopNoStudentsTests(BaseTestCase):
    """В Кузне нет ни студентов, ни преподавателей: их добавляют в «Пользователях»."""

    def setUp(self):
        self.client.force_login(self.admin)

    def test_no_student_or_teacher_card(self):
        response = self.client.get(reverse("workshop"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<h2>Студенты</h2>")
        self.assertNotContains(response, "<h2>Преподаватели</h2>")
        self.assertNotIn("student_form", response.context)
        self.assertNotIn("teacher_form", response.context)
        self.assertNotIn("teacher", response.context["cards"])

    def test_add_student_action_does_nothing(self):
        before = get_user_model().objects.count()

        self.client.post(reverse("workshop"), {
            "action": "add_student",
            "student-username": "sidorov77@gmail.com",
            "student-group": self.group.pk,
            "student-password": STUDENT_PASSWORD,
        }, follow=True)

        self.assertEqual(get_user_model().objects.count(), before)

    def test_add_teacher_action_does_nothing(self):
        before = Teacher.objects.count()

        self.client.post(reverse("workshop"), {
            "action": "add_teacher",
            "teacher-teacher_name": "Новый Н. Н.",
            "teacher-teacher_tag": "",
        }, follow=True)

        self.assertEqual(Teacher.objects.count(), before)

    def test_teacher_edit_mode_does_not_exist(self):
        response = self.client.get(
            reverse("workshop"), {"edit": "teacher", "item": self.teacher.pk}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(card["editing"] for card in response.context["cards"].values())
        )

    def test_users_are_created_on_users_page(self):
        response = self.client.post(reverse("users"), {
            "action": "create_user",
            "new_user-role": "student",
            "new_user-last_name": "Сидоров",
            "new_user-first_name": "Сидор",
            "new_user-patronymic": "",
            "new_user-username": "sidorov77@gmail.com",
            "new_user-group": self.group.pk,
            "new_user-password": STUDENT_PASSWORD,
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            get_user_model().objects.filter(
                username="sidorov77@gmail.com"
            ).exists()
        )


class WorkshopLayoutTests(BaseTestCase):
    """Порядок столбцов Кузни: звонки, группы и пары, теги."""

    def test_columns_are_swapped(self):
        self.client.force_login(self.admin)

        html = self.client.get(reverse("workshop")).content.decode()

        bells = html.index("Расписание звонков")
        data = html.index("Импорт и экспорт")
        groups = html.index("<h2>Группы</h2>")
        pairs = html.index("<h2>Пары</h2>")
        tags = html.index("<h2>Теги групп</h2>")
        teacher_tags = html.index("<h2>Теги преподавателей</h2>")

        self.assertLess(bells, data)
        self.assertLess(data, groups)
        self.assertLess(groups, pairs)
        self.assertLess(pairs, tags)
        self.assertLess(tags, teacher_tags)
        self.assertLess(teacher_tags, html.index("<h2>Семестры</h2>"))
        self.assertNotIn("Типы тегов", html)


class TagColorHelpersTests(TestCase):
    """Проверка цвета тега и подбор цвета текста."""

    def test_normalize_color(self):
        self.assertEqual(normalize_color(" #FFAA00 "), "#ffaa00")

        for bad in ("", "red", "#12345", "#1234567", "ffaa00", "#gggggg",
                    "#ffaa00;}", "javascript:alert(1)"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_color(bad)

    def test_text_color_is_readable(self):
        self.assertEqual(text_color("#ffffff"), "#111827")
        self.assertEqual(text_color("#ffee00"), "#111827")
        self.assertEqual(text_color("#000000"), "#ffffff")
        self.assertEqual(text_color("#3655d9"), "#ffffff")

    def test_broken_color_falls_back_to_default(self):
        self.assertEqual(color_class("не цвет"), color_class(TAG_COLOR_DEFAULT))
        self.assertEqual(color_class("#FF0000"), "tag-c-ff0000")

    def test_css_has_one_rule_per_color_and_the_default(self):
        css = build_tag_css(["#ff0000", "#FF0000", "#00ff00"])

        self.assertEqual(css.count("{"), 3)
        self.assertIn(
            ".tag-chip.tag-c-ff0000 { background: #ff0000; color: #ffffff; }",
            css,
        )
        self.assertIn("tag-c-6b7280", css)

    def test_css_skips_dangerous_values(self):
        css = build_tag_css(["red;}body{display:none", "#00ff00"])

        self.assertNotIn("body", css)
        self.assertNotIn("display", css)
        self.assertIn("tag-c-00ff00", css)


class TagColorTests(BaseTestCase):
    """Цвет тега в Кузне и таблица стилей tags.css."""

    def setUp(self):
        self.client.force_login(self.admin)

    def add_tag(self, **fields):
        data = {
            "action": "add_group_tag",
            "group_tag-tag_name": "Очное",
        }
        data.update(fields)

        return self.client.post(reverse("workshop"), data, follow=True)

    def test_new_tag_has_default_color(self):
        tag = GroupTag.objects.create(tag_name="Очное")

        self.assertEqual(tag.tag_color, TAG_COLOR_DEFAULT)
        self.assertEqual(tag.full_name, "Очное")

    def test_form_has_color_picker_without_javascript(self):
        page = self.client.get(reverse("workshop"))

        self.assertContains(page, 'type="color"')
        self.assertContains(page, "Цвет тега")
        # Встроенный скрипт один — общий для всех страниц (класс «js» в шапке,
        # main.html); подключён только внешний filter.js (предпросмотр цвета),
        # выбор цвета работает и без него
        self.assertContains(page, "<script>", count=1)

    def test_tag_is_added_with_chosen_color(self):
        response = self.add_tag(**{"group_tag-tag_color": "#FF0000"})

        self.assertContains(response, "Запись добавлена.")
        self.assertEqual(
            GroupTag.objects.get(tag_name="Очное").tag_color, "#ff0000"
        )

    def test_tag_without_color_gets_default(self):
        self.add_tag()

        self.assertEqual(
            GroupTag.objects.get(tag_name="Очное").tag_color, TAG_COLOR_DEFAULT
        )

    def test_bad_colors_are_rejected(self):
        for bad in ("red", "#12345", "#gggggg", "#ff0000;}"):
            with self.subTest(bad=bad):
                response = self.add_tag(**{"group_tag-tag_color": bad})

                self.assertContains(response, "Проверьте заполненные поля.")

        self.assertFalse(GroupTag.objects.exists())

    def test_color_can_be_changed(self):
        tag = GroupTag.objects.create(tag_name="Очное")

        response = self.client.post(reverse("workshop"), {
            "action": "update_item",
            "edit": "group_tag",
            "item": tag.pk,
            "group_tag-tag_name": "Очное",
            "group_tag-tag_color": "#00AA55",
        }, follow=True)

        self.assertContains(response, "Изменения сохранены")
        tag.refresh_from_db()
        self.assertEqual(tag.tag_color, "#00aa55")

    def test_tag_lists_show_only_the_name(self):
        GroupTag.objects.create(tag_name="Очное")

        page = self.client.get(reverse("workshop"))

        self.assertContains(page, "Очное")
        self.assertNotContains(page, "Отделение: Очное")

    def test_teacher_tag_is_added_with_its_own_color(self):
        response = self.client.post(reverse("workshop"), {
            "action": "add_teacher_tag",
            "teacher_tag-tag_name": "Высшая",
            "teacher_tag-tag_color": "#00AA55",
        }, follow=True)

        self.assertContains(response, "Запись добавлена.")
        self.assertEqual(
            TeacherTag.objects.get(tag_name="Высшая").tag_color, "#00aa55"
        )
        self.assertFalse(GroupTag.objects.exists())

    # --- tags.css -------------------------------------------------------------

    def test_stylesheet_has_rule_for_each_used_color(self):
        GroupTag.objects.create(
            tag_name="Очное", tag_color="#ffee00"
        )
        GroupTag.objects.create(
            tag_name="Заочное", tag_color="#3655d9"
        )

        response = self.client.get(reverse("tag_styles"))
        TeacherTag.objects.create(tag_name="Высшая", tag_color="#12ab34")
        css = self.client.get(reverse("tag_styles")).content.decode()

        # Цвета и тегов групп, и тегов преподавателей
        self.assertIn("tag-c-12ab34", css)

        response = self.client.get(reverse("tag_styles"))
        css = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertIn(
            ".tag-chip.tag-c-ffee00 { background: #ffee00; color: #111827; }",
            css,
        )
        self.assertIn(
            ".tag-chip.tag-c-3655d9 { background: #3655d9; color: #ffffff; }",
            css,
        )

    def test_stylesheet_ignores_broken_color_in_database(self):
        GroupTag.objects.create(
            tag_name="Плохой",
            tag_color="x;}body",
        )

        css = self.client.get(reverse("tag_styles")).content.decode()

        self.assertNotIn("body", css)

    def test_stylesheet_is_available_without_login_and_linked_in_pages(self):
        page = self.client.get(reverse("groups"))
        self.assertContains(page, reverse("tag_styles"))

        self.client.logout()

        self.assertEqual(self.client.get(reverse("tag_styles")).status_code, 200)
        self.assertContains(self.client.get(reverse("login")), "/tags.css")


class TagChipTests(BaseTestCase):
    """Метка тега рядом с группой и преподавателем, полное имя при наведении."""

    def setUp(self):
        self.client.force_login(self.user)
        self.group_tag = GroupTag.objects.create(
            tag_name="Очное", tag_color="#ff0000"
        )
        self.teacher_tag = TeacherTag.objects.create(
            tag_name="Высшая", tag_color="#00aa55"
        )

    def test_group_tag_on_groups_page(self):
        self.group.group_tag = self.group_tag
        self.group.save()

        with fake_today(MONDAY):
            page = self.client.get(reverse("groups"))

        self.assertContains(page, 'class="tag-chip tag-c-ff0000"')
        self.assertContains(page, 'title="Очное"')
        self.assertContains(page, ">Очное</span>")

    def test_group_tag_on_group_schedule_page(self):
        self.group.group_tag = self.group_tag
        self.group.save()

        page = self.client.get(
            reverse("group_schedule", args=[self.group.pk]),
            {"date": "2026-10-19"},
        )

        self.assertContains(page, 'class="tag-chip tag-c-ff0000"')
        self.assertContains(page, 'title="Очное"')

    def test_teacher_tag_next_to_teacher(self):
        self.teacher.teacher_tag = self.teacher_tag
        self.teacher.save()

        with fake_today(MONDAY):
            page = self.client.get(
                reverse("teachers"), {"teacher": self.teacher.pk}
            )

        self.assertContains(page, 'class="tag-chip tag-c-00aa55"')
        self.assertContains(page, 'title="Высшая"')

    def test_no_chip_without_tag(self):
        with fake_today(MONDAY):
            groups = self.client.get(reverse("groups"))
            teachers = self.client.get(
                reverse("teachers"), {"teacher": self.teacher.pk}
            )

        self.assertNotContains(groups, 'class="tag-chip')
        self.assertNotContains(teachers, 'class="tag-chip')

    def test_tags_do_not_add_queries_per_group(self):
        def count_queries():
            with fake_today(MONDAY):
                with CaptureQueriesContext(connection) as ctx:
                    self.client.get(reverse("groups"))
            return len(ctx)

        for number in range(2):
            Group.objects.create(
                group_name=f"К-{number}",
                group_course=self.course,
                shift=self.shift,
                group_tag=self.group_tag,
            )
        few = count_queries()

        for number in range(2, 5):
            Group.objects.create(
                group_name=f"К-{number}",
                group_course=self.course,
                shift=self.shift,
                group_tag=self.group_tag,
            )

        self.assertEqual(count_queries(), few)


class TeacherTagAssignTests(BaseTestCase):
    """Тег преподавателя ставится на странице «Пользователи»."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.tag = TeacherTag.objects.create(tag_name="Высшая")
        self.teacher_user = get_user_model().objects.get(
            username="teacherivanov@gmail.com"
        )

    def post_tag(self, user, tag):
        return self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": user.pk,
            "tag": tag,
        }, follow=True)

    def test_teacher_gets_and_loses_tag(self):
        response = self.post_tag(self.teacher_user, self.tag.pk)

        self.assertContains(response, "тег Высшая.")
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.teacher_tag_id, self.tag.pk)

        self.post_tag(self.teacher_user, "")
        self.teacher.refresh_from_db()
        self.assertIsNone(self.teacher.teacher_tag_id)

    def test_only_teachers_have_tags(self):
        response = self.post_tag(self.user, self.tag.pk)

        self.assertContains(response, "только преподавателям")

    def test_unknown_tag_is_rejected(self):
        response = self.post_tag(self.teacher_user, 9999)

        self.assertContains(response, "Выберите тег из списка.")
        self.teacher.refresh_from_db()
        self.assertIsNone(self.teacher.teacher_tag_id)

    def test_tag_select_is_shown_for_teachers_only(self):
        page = self.client.get(reverse("users"))

        html = page.content.decode()

        # Выбор тега только у строки преподавателя; ещё один select
        # с таким именем — в фильтрах над списком.
        self.assertEqual(
            len(re.findall(r'<select name="tag"\s+aria-label', html)), 1
        )
        self.assertEqual(html.count('<select name="tag">'), 1)
        self.assertContains(page, "Высшая")
        self.assertNotContains(page, "Отделение: Высшая")

    def test_non_staff_cannot_set_tag(self):
        self.client.force_login(self.user)

        response = self.post_tag(self.teacher_user, self.tag.pk)

        self.assertEqual(response.status_code, 403)


class UserRowSaveTests(BaseTestCase):
    """Одна кнопка «Сохранить» в строке пользователя (action=save_user)."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.tag = TeacherTag.objects.create(tag_name="Высшая")
        self.teacher_user = get_user_model().objects.get(
            username="teacherivanov@gmail.com"
        )

    def save(self, user, **fields):
        return self.client.post(reverse("users"), {
            "action": "save_user", "user_id": user.pk, **fields,
        }, follow=True)

    def fresh(self, user):
        return get_user_model().objects.get(pk=user.pk)

    def test_each_row_has_one_form_and_one_save_button(self):
        html = self.client.get(reverse("users")).content.decode()
        rows = get_user_model().objects.count()

        self.assertEqual(html.count('value="save_user"'), rows)
        self.assertNotIn('value="set_role"', html)
        self.assertNotIn('value="set_group"', html)
        self.assertNotIn('value="set_teacher_tag"', html)
        self.assertEqual(html.count(">Сохранить</button>"), rows)

    def test_old_teacher_select_is_gone(self):
        Teacher.objects.create(teacher_name="Старый С. С.")

        page = self.client.get(reverse("users"))

        self.assertNotContains(page, 'name="teacher"')
        self.assertNotContains(page, "Старый С. С.")
        self.assertNotContains(page, "по ФИО пользователя")

    def test_student_row_has_role_and_group_selects(self):
        html = self.client.get(reverse("users")).content.decode()
        row = html[html.index("student</td>"):]
        row = row[:row.index("</tr>")]

        self.assertIn('name="role"', row)
        self.assertIn('name="group"', row)
        self.assertNotIn('name="tag"', row)

    def test_group_is_saved_with_unchanged_role(self):
        response = self.save(self.user, role="student", group=self.group.pk)

        self.assertContains(response, "группа А-01")
        self.assertEqual(own_group_id(self.fresh(self.user)), self.group.pk)

    def test_tag_is_saved_with_unchanged_role(self):
        response = self.save(
            self.teacher_user, role=ROLE_TEACHER, tag=self.tag.pk
        )

        self.assertContains(response, "тег Высшая.")
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.teacher_tag_id, self.tag.pk)

    def test_role_change_ignores_group_in_same_request(self):
        set_group(self.user, self.group)

        self.save(self.user, role=ROLE_TEACHER, group=self.group.pk)

        user = self.fresh(self.user)
        self.assertEqual(user_role(user), ROLE_TEACHER)
        self.assertIsNone(user.profile.group_id)

    def test_invalid_role_is_rejected(self):
        response = self.save(self.user, role="boss")

        self.assertContains(response, "Выберите роль из списка.")
        self.assertEqual(user_role(self.fresh(self.user)), "student")

    def test_unknown_user(self):
        response = self.client.post(reverse("users"), {
            "action": "save_user", "user_id": 99999, "role": "student",
        }, follow=True)

        self.assertContains(response, "Пользователь не найден.")

    def test_nothing_to_change(self):
        response = self.save(self.user, role="student")

        self.assertContains(response, "изменений нет")

    def test_own_role_is_locked(self):
        response = self.save(self.admin, role="student")

        self.assertContains(response, "Свою роль изменить нельзя.")
        self.assertTrue(self.fresh(self.admin).is_staff)

    def test_own_row_has_no_role_select(self):
        html = self.client.get(reverse("users")).content.decode()
        row = html[html.index("admin2</td>"):]
        row = row[:row.index("</tr>")]

        self.assertNotIn('name="role"', row)
        self.assertIn("это вы", row)

    def test_unknown_action_is_rejected(self):
        response = self.client.post(reverse("users"), {
            "action": "set_role", "user_id": self.user.pk, "role": "admin",
        }, follow=True)

        self.assertContains(response, "Неизвестное действие.")
        self.assertFalse(self.fresh(self.user).is_staff)

    def test_demoted_teacher_keeps_teacher_record(self):
        teacher_id = own_teacher_id(self.teacher_user)

        self.save(self.teacher_user, role="student")
        demoted = self.fresh(self.teacher_user)

        self.assertEqual(user_role(demoted), "student")
        self.assertEqual(demoted.profile.teacher_id, teacher_id)
        self.assertNotIn(self.teacher, real_teachers())

        self.save(self.teacher_user, role=ROLE_TEACHER)
        again = self.fresh(self.teacher_user)

        self.assertEqual(own_teacher_id(again), teacher_id)
        self.assertEqual(
            Teacher.objects.filter(teacher_name="Иванов И. И.").count(), 1
        )
        self.assertIn(self.teacher, real_teachers())
        self.assertTrue(Pair.objects.filter(teacher_id=teacher_id).exists())

    def test_student_name_links_to_card(self):
        page = self.client.get(reverse("users"))

        self.assertContains(
            page, reverse("student_card", args=[self.user.pk])
        )
        self.assertNotContains(
            page, reverse("student_card", args=[self.teacher_user.pk])
        )


class StudentCardTests(BaseTestCase):
    """Профиль студента: /students/<id>/profile/."""

    def setUp(self):
        set_group(self.user, self.group)
        self.group.group_curator = self.teacher
        self.group.save()
        self.teacher_user = get_user_model().objects.get(
            username="teacherivanov@gmail.com"
        )
        self.url = reverse("student_card", args=[self.user.pk])

    def open(self, who):
        self.client.force_login(who)

        return self.client.get(self.url)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_admin_sees_card(self):
        response = self.open(self.admin)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "student_card.html")
        self.assertContains(response, "Профиль студента")
        self.assertContains(response, "А-01")
        self.assertContains(response, "Иванов И. И.")
        self.assertNotContains(response, "К пользователям")

    def test_student_sees_own_card(self):
        response = self.open(self.fresh_user())

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "К пользователям")

    def test_curator_sees_card_of_own_group(self):
        self.assertEqual(self.open(self.teacher_user).status_code, 200)

    def test_other_teacher_gets_403(self):
        other = make_teacher("Петров П. П.", "petrov@gmail.com")
        other_user = get_user_model().objects.get(username="petrov@gmail.com")

        self.assertEqual(self.open(other_user).status_code, 403)

    def test_curator_of_another_group_gets_403(self):
        self.group.group_curator = None
        self.group.save()

        self.assertEqual(self.open(self.teacher_user).status_code, 403)

    def test_other_student_gets_403(self):
        stranger = get_user_model().objects.create_user(
            "stranger", password=PASSWORD
        )

        self.assertEqual(self.open(stranger).status_code, 403)

    def test_card_of_non_student_is_404(self):
        self.client.force_login(self.admin)

        for target in (self.admin, self.teacher_user):
            url = reverse("student_card", args=[target.pk])
            self.assertEqual(self.client.get(url).status_code, 404)

        missing = reverse("student_card", args=[99999])
        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_student_without_group(self):
        set_group(self.user, None)

        response = self.open(self.admin)

        self.assertContains(response, "не назначена")

    def test_menu_link_is_only_for_students(self):
        page = self.open(self.fresh_user())
        self.assertContains(page, "Мой профиль")
        self.assertContains(page, self.url)

        for who in (self.admin, self.teacher_user):
            self.client.force_login(who)
            self.assertNotContains(self.client.get(reverse("groups")),
                                   "Мой профиль")

    def fresh_user(self):
        return get_user_model().objects.get(pk=self.user.pk)


class TeacherCardTests(BaseTestCase):
    """Профиль преподавателя: /teachers/<id>/profile/."""

    def setUp(self):
        self.group.group_curator = self.teacher
        self.group.save()
        self.teacher_user = get_user_model().objects.get(
            username="teacherivanov@gmail.com"
        )
        self.url = reverse("teacher_card", args=[self.teacher.pk])

    def open(self, who, url=None):
        self.client.force_login(who)

        return self.client.get(url or self.url)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_card_shows_groups_pairs_and_tag(self):
        tag = TeacherTag.objects.create(tag_name="Высшая")
        self.teacher.teacher_tag = tag
        self.teacher.save()

        response = self.open(self.user)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher_card.html")
        self.assertContains(response, "Иванов И. И.")
        self.assertContains(response, "Высшая")
        self.assertContains(response, "Математика")
        self.assertContains(
            response, reverse("group_card", args=[self.group.pk])
        )
        self.assertContains(
            response, f"{reverse('teachers')}?teacher={self.teacher.pk}"
        )
        self.assertNotContains(response, "К пользователям")

    def test_login_is_visible_to_admin_and_to_the_teacher_only(self):
        login = "teacherivanov@gmail.com"

        self.assertContains(self.open(self.admin), login)
        self.assertContains(self.open(self.teacher_user), login)
        self.assertNotContains(self.open(self.user), login)
        self.assertContains(self.open(self.user), "есть")

    def test_profile_head_has_no_back_to_users_button(self):
        self.assertNotContains(self.open(self.admin), "К пользователям")

    def test_old_teacher_without_account_opens(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")

        response = self.open(
            self.admin, reverse("teacher_card", args=[legacy.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Старый С. С.")
        self.assertContains(response, "осталась от прежних версий")
        self.assertContains(response, "Пар нет.")

    def test_demoted_teacher_has_no_account_in_card(self):
        set_role(self.teacher_user, "student")

        response = self.open(self.admin)

        self.assertContains(response, "осталась от прежних версий")
        self.assertNotContains(response, "teacherivanov@gmail.com</dd>")

    def test_missing_teacher_is_404(self):
        response = self.open(
            self.admin, reverse("teacher_card", args=[99999])
        )

        self.assertEqual(response.status_code, 404)

    def test_entry_points(self):
        self.client.force_login(self.user)

        page = self.client.get(
            reverse("teachers"), {"teacher": self.teacher.pk}
        )
        self.assertContains(page, self.url)

        with fake_today(MONDAY):
            page = self.client.get(reverse("groups"))
        self.assertContains(page, self.url)

        self.client.force_login(self.admin)
        page = self.client.get(reverse("users"))
        self.assertContains(page, self.url)


class GroupCardTests(BaseTestCase):
    """Профиль группы: /groups/<id>/profile/."""

    def setUp(self):
        self.group.group_curator = self.teacher
        self.group.save()
        self.teacher_user = get_user_model().objects.get(
            username="teacherivanov@gmail.com"
        )
        User = get_user_model()
        self.zed = User.objects.create_user(
            "zed", password=PASSWORD, last_name="Яковлев", first_name="Яков"
        )
        self.abe = User.objects.create_user(
            "abe", password=PASSWORD, last_name="Абрамов", first_name="Аркадий"
        )
        set_group(self.zed, self.group)
        set_group(self.abe, self.group)
        set_group(self.user, self.group)
        self.url = reverse("group_card", args=[self.group.pk])

    def open(self, who, url=None):
        self.client.force_login(who)

        return self.client.get(url or self.url)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_card_shows_curator_pairs_and_teachers(self):
        response = self.open(self.admin)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "group_card.html")
        self.assertContains(response, "А-01")
        self.assertContains(response, "Математика")
        self.assertContains(
            response, reverse("teacher_card", args=[self.teacher.pk]), count=2
        )
        self.assertContains(
            response, reverse("group_schedule", args=[self.group.pk])
        )

    def test_group_without_curator(self):
        self.group.group_curator = None
        self.group.save()

        self.assertContains(self.open(self.admin), "не назначен")

    def test_admin_sees_students_sorted_by_name(self):
        html = self.open(self.admin).content.decode()

        self.assertIn(reverse("student_card", args=[self.zed.pk]), html)
        self.assertLess(html.index("Абрамов"), html.index("Яковлев"))
        self.assertContains(self.open(self.admin), "Студенты")

    def test_curator_sees_students(self):
        response = self.open(self.teacher_user)

        self.assertContains(response, "Абрамов Аркадий")
        self.assertContains(response, "Яковлев Яков")

    def test_other_users_see_only_the_number_of_students(self):
        stranger = get_user_model().objects.create_user(
            "stranger", password=PASSWORD
        )

        for who in (self.user, stranger):
            response = self.open(who)

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Абрамов")
            self.assertNotContains(response, "Яковлев")
            self.assertEqual(response.context["student_count"], 3)
            self.assertContains(response, "Список студентов видят")

    def test_other_teacher_does_not_see_students(self):
        make_teacher("Петров П. П.", "petrov@gmail.com")
        other = get_user_model().objects.get(username="petrov@gmail.com")

        response = self.open(other)

        self.assertNotContains(response, "Абрамов")

    def test_empty_group(self):
        empty = Group.objects.create(
            group_name="Пустая", group_course=self.course, shift=self.shift
        )

        response = self.open(
            self.admin, reverse("group_card", args=[empty.pk])
        )

        self.assertContains(response, "В группе пока нет студентов.")
        self.assertContains(response, "Пар нет.")

    def test_missing_group_is_404(self):
        response = self.open(
            self.admin, reverse("group_card", args=[99999])
        )

        self.assertEqual(response.status_code, 404)

    def test_students_do_not_add_queries(self):
        self.client.force_login(self.admin)

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self.url)
            return len(ctx)

        few = count_queries()

        for number in range(5):
            user = get_user_model().objects.create_user(
                f"extra{number}", password=PASSWORD
            )
            set_group(user, self.group)

        self.assertEqual(count_queries(), few)

    def test_entry_points(self):
        self.client.force_login(self.user)

        with fake_today(MONDAY):
            page = self.client.get(reverse("groups"))
        self.assertContains(page, self.url)

        page = self.client.get(
            reverse("group_schedule", args=[self.group.pk])
        )
        self.assertContains(page, self.url)


class GroupSearchTests(BaseTestCase):
    """Поиск групп на странице «Группы»: ?q= (на сервере)."""

    def setUp(self):
        self.client.force_login(self.user)
        self.group.group_curator = self.teacher
        self.group.save()

        for number in range(2, 10):
            Group.objects.create(
                group_name=f"Б-{number:02d}",
                group_course=self.course,
                shift=self.shift,
            )

    def search(self, query, **params):
        with fake_today(MONDAY):
            return self.client.get(reverse("groups"), {"q": query, **params})

    def names(self, response):
        return [
            item["group"].group_name
            for item in response.context["schedule_groups"]
        ]

    def test_without_query_all_groups_are_listed(self):
        for query in ("", "   "):
            response = self.search(query)

            self.assertEqual(response.context["found_count"], 9)
            self.assertNotContains(response, "group-search-info")

    def test_search_by_name_is_case_insensitive(self):
        response = self.search("а-01")

        self.assertEqual(self.names(response), ["А-01"])
        self.assertContains(response, "найдено групп: 1")

    def test_search_by_curator(self):
        response = self.search("иванов")

        self.assertEqual(self.names(response), ["А-01"])

    def test_search_works_across_pages(self):
        first = self.search("Б-0")
        second = self.search("Б-0", page=2)

        self.assertEqual(first.context["found_count"], 8)
        self.assertEqual(len(self.names(first)), 6)
        self.assertEqual(len(self.names(second)), 2)

    def test_nothing_found(self):
        response = self.search("Яблоко")

        self.assertEqual(self.names(response), [])
        self.assertContains(response, "групп не найдено")

    def test_percent_and_underscore_are_plain_characters(self):
        self.assertEqual(self.names(self.search("%")), [])
        self.assertEqual(self.names(self.search("_")), [])

    def test_very_long_query_is_cut_not_failed(self):
        response = self.search("я" * 500)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.context["q"]), 100)

    def test_links_keep_the_query(self):
        response = self.search("Б-0", date="2026-10-20")
        encoded = quote("Б-0")

        self.assertContains(response, f"date=2026-10-19&amp;q={encoded}")
        self.assertContains(response, f"page=2&amp;q={encoded}")
        self.assertContains(response, f"?q={encoded}\">Сегодня")

    def test_form_keeps_value_and_date(self):
        response = self.search("Б-0", date="2026-10-20")

        self.assertContains(response, 'name="q" value="Б-0"')
        self.assertContains(response, 'name="date" value="2026-10-20"')
        self.assertContains(response, "Сбросить")

    def test_search_on_day_off_does_not_fail(self):
        with fake_today(SUNDAY):
            response = self.client.get(reverse("groups"), {"q": "Б"})

        self.assertEqual(response.status_code, 200)

    def test_html_in_query_is_escaped(self):
        response = self.search("<b>x</b>")

        self.assertNotContains(response, "<b>x</b>")


class SeparateTagTests(BaseTestCase):
    """Теги групп и теги преподавателей — две отдельные таблицы и карточки."""

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, kind, name, **fields):
        return self.client.post(reverse("workshop"), {
            "action": f"add_{kind}_tag", f"{kind}_tag-tag_name": name,
            **fields,
        }, follow=True)

    def edit(self, kind, tag, **fields):
        return self.client.post(reverse("workshop"), {
            "action": "update_item", "edit": f"{kind}_tag", "item": tag.pk,
            **fields,
        }, follow=True)

    def test_two_tag_cards_and_no_type_field(self):
        page = self.client.get(reverse("workshop"))

        self.assertContains(page, "Теги групп")
        self.assertContains(page, "Теги преподавателей")
        self.assertContains(page, 'name="group_tag-tag_name"')
        self.assertContains(page, 'name="teacher_tag-tag_name"')
        self.assertNotContains(page, "tag_type")
        self.assertNotContains(page, 'value="add_tag"')

    def test_each_card_adds_to_its_own_table(self):
        self.assertContains(self.add("group", "Очное"), "Запись добавлена.")
        self.add("teacher", "Высшая")

        self.assertEqual(
            list(GroupTag.objects.values_list("tag_name", flat=True)), ["Очное"]
        )
        self.assertEqual(
            list(TeacherTag.objects.values_list("tag_name", flat=True)),
            ["Высшая"],
        )

    def test_same_name_is_allowed_in_both_tables(self):
        self.add("group", "Очное")
        response = self.add("teacher", "Очное")

        self.assertContains(response, "Запись добавлена.")
        self.assertEqual(GroupTag.objects.count(), 1)
        self.assertEqual(TeacherTag.objects.count(), 1)

    def test_name_is_unique_ignoring_case_and_spaces_within_a_table(self):
        self.add("group", "Очное")

        response = self.add("group", "  очное ")

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertContains(response, "уже есть")
        self.assertEqual(GroupTag.objects.count(), 1)

    def test_name_is_normalised(self):
        self.add("teacher", "  Высшая   категория ")

        self.assertTrue(
            TeacherTag.objects.filter(tag_name="Высшая категория").exists()
        )

    def test_empty_name_is_rejected(self):
        response = self.add("group", "   ")

        self.assertContains(response, "Заполните форму перед добавлением.")
        self.assertFalse(GroupTag.objects.exists())

    def test_editing_changes_name_and_color(self):
        tag = GroupTag.objects.create(tag_name="Очное")

        response = self.edit(
            "group", tag,
            **{"group_tag-tag_name": "Дневное", "group_tag-tag_color": "#112233"},
        )

        self.assertContains(response, "Изменения сохранены")
        tag.refresh_from_db()
        self.assertEqual(tag.tag_name, "Дневное")
        self.assertEqual(tag.tag_color, "#112233")

    def test_renaming_to_an_existing_name_is_rejected(self):
        GroupTag.objects.create(tag_name="Очное")
        other = GroupTag.objects.create(tag_name="Заочное")

        response = self.edit("group", other, **{"group_tag-tag_name": "очное"})

        self.assertContains(response, "Проверьте заполненные поля.")
        other.refresh_from_db()
        self.assertEqual(other.tag_name, "Заочное")

    def test_group_form_offers_only_group_tags(self):
        GroupTag.objects.create(tag_name="Очное")
        TeacherTag.objects.create(tag_name="Высшая")

        form = self.client.get(reverse("workshop")).context["cards"]["group"]["form"]

        names = [str(tag) for tag in form.fields["group_tag"].queryset]
        self.assertEqual(names, ["Очное"])

    def test_tag_lists_are_sorted_by_name(self):
        GroupTag.objects.create(tag_name="Я-тег")
        GroupTag.objects.create(tag_name="Б-тег")
        TeacherTag.objects.create(tag_name="Д-тег")

        html = self.client.get(reverse("users")).content.decode()

        self.assertLess(html.index("Б-тег"), html.index("Я-тег"))

    def test_users_page_offers_both_kinds_of_tags(self):
        group_tag = GroupTag.objects.create(tag_name="Очное")
        teacher_tag = TeacherTag.objects.create(tag_name="Высшая")

        html = self.client.get(reverse("users")).content.decode()

        self.assertIn(f'value="g{group_tag.pk}"', html)
        self.assertIn(f'value="t{teacher_tag.pk}"', html)
        self.assertIn('label="Теги групп"', html)
        self.assertIn('label="Теги преподавателей"', html)


class YearCalendarTests(BaseTestCase):
    """Годовой календарь: 12 маленьких месяцев, /calendar/year/."""

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("calendar_year")

    def get(self, **params):
        with fake_today(MONDAY):
            return self.client.get(self.url, params)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()

        self.assertRedirects(
            self.client.get(self.url), f"{reverse('login')}?next={self.url}"
        )

    def test_twelve_months_of_the_requested_year(self):
        response = self.get(year=2026)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "calendar_year.html")
        self.assertEqual(response.context["year"], 2026)
        self.assertEqual(len(response.context["months"]), 12)
        self.assertEqual(
            [month["number"] for month in response.context["months"]],
            list(range(1, 13)),
        )
        self.assertContains(response, 'class="mini-month', count=12)
        self.assertContains(response, "2026 год")

    def test_every_day_of_the_year_is_present_once(self):
        for year, days in ((2026, 365), (2028, 366)):
            with self.subTest(year=year):
                months = self.get(year=year).context["months"]
                cells = [
                    cell
                    for month in months
                    for week in month["weeks"]
                    for cell in week
                    if cell
                ]

                self.assertEqual(len(cells), days)

    def test_october_2026_starts_on_thursday(self):
        october = self.get(year=2026).context["months"][9]

        self.assertEqual(october["name"], "Октябрь")
        self.assertEqual(len(october["weeks"]), 5)
        self.assertEqual(
            [bool(cell) for cell in october["weeks"][0]],
            [False, False, False, True, True, True, True],
        )
        self.assertEqual(october["weeks"][0][3]["number"], 1)

    def test_today_and_current_month_are_marked(self):
        months = self.get(year=2026).context["months"]

        self.assertTrue(months[9]["is_current"])
        self.assertEqual(sum(month["is_current"] for month in months), 1)

        today = [
            cell for week in months[9]["weeks"] for cell in week
            if cell and cell["is_today"]
        ]
        self.assertEqual([cell["number"] for cell in today], [19])

        other = self.get(year=2025).context["months"]
        self.assertFalse(any(month["is_current"] for month in other))

    def test_sundays_are_marked(self):
        october = self.get(year=2026).context["months"][9]
        sundays = [
            cell["number"] for week in october["weeks"] for cell in week
            if cell and cell["is_sunday"]
        ]

        self.assertEqual(sundays, [4, 11, 18, 25])

    def test_holidays_come_from_the_calendar_table(self):
        Calendar.objects.create(
            date=date(2026, 1, 1), weekday=4,
            is_holiday=True, holiday_name="Новый год",
        )

        response = self.get(year=2026)
        january = response.context["months"][0]
        first = january["weeks"][0][3]

        self.assertTrue(first["is_holiday"])
        self.assertEqual(first["holiday_name"], "Новый год")
        self.assertContains(response, 'title="Новый год"')

    def test_page_only_reads_the_calendar_table(self):
        self.get(year=2026)

        self.assertFalse(Calendar.objects.exists())

    def test_day_links_follow_the_role(self):
        group = self.group
        self.client.force_login(self.user)
        set_group(self.user, group)

        with fake_today(MONDAY):
            student = self.client.get(self.url, {"year": 2026})

        self.assertContains(
            student,
            reverse("group_schedule", args=[group.pk]) + "?date=2026-10-20",
        )

        with fake_today(MONDAY):
            self.client.force_login(self.admin)
            admin = self.client.get(self.url, {"year": 2026})

        self.assertContains(admin, reverse("groups") + "?date=2026-10-20")

    def test_month_names_link_to_month_view(self):
        response = self.get(year=2026)

        self.assertContains(
            response, f"{reverse('calendar')}?year=2026&amp;month=10"
        )

    def test_bad_year_falls_back_to_current(self):
        for value in ("abc", "1999", "2101", ""):
            with self.subTest(year=value):
                response = self.get(year=value)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["year"], 2026)

    def test_year_navigation_and_limits(self):
        middle = self.get(year=2030).context

        self.assertEqual(middle["prev_year"], 2029)
        self.assertEqual(middle["next_year"], 2031)
        self.assertFalse(middle["is_current_year"])
        self.assertEqual(self.get(year=2000).context["prev_year"], None)
        self.assertEqual(self.get(year=2100).context["next_year"], None)
        today_link = rf'href="{reverse("calendar_year")}">\s*Сегодня'

        self.assertNotRegex(self.get(year=2026).content.decode(), today_link)
        self.assertRegex(self.get(year=2030).content.decode(), today_link)

    def test_month_button_returns_to_a_sensible_month(self):
        current = self.get(year=2026)
        other = self.get(year=2030)

        button = (
            rf'href="{reverse("calendar")}\?year={{year}}&amp;month={{month}}">'
            r"\s*Месячный"
        )

        self.assertRegex(
            current.content.decode(), button.format(year=2026, month=10)
        )
        self.assertRegex(
            other.content.decode(), button.format(year=2030, month=1)
        )

    def test_query_count_is_constant(self):
        with CaptureQueriesContext(connection) as ctx:
            self.get(year=2026)

        self.assertLess(len(ctx), 12)

    def test_month_page_has_the_year_button(self):
        with fake_today(MONDAY):
            page = self.client.get(
                reverse("calendar"), {"year": 2027, "month": 3}
            )

        self.assertContains(page, f"{reverse('calendar_year')}?year=2027")
        self.assertContains(page, "Годовой")


class UsersFilterTests(BaseTestCase):
    """Страница «Пользователи»: поиск, фильтры, порядок, подсветка нового."""

    def setUp(self):
        self.client.force_login(self.admin)
        User = get_user_model()

        self.group_b = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        self.group.group_curator = self.teacher
        self.group.save()

        self.teacher_tag = TeacherTag.objects.create(tag_name="Высшая")
        self.group_tag = GroupTag.objects.create(tag_name="Очное")
        self.teacher.teacher_tag = self.teacher_tag
        self.teacher.save()
        self.group_b.group_tag = self.group_tag
        self.group_b.save()

        self.abe = User.objects.create_user(
            "abe", password=PASSWORD, last_name="Абрамов",
            first_name="Аркадий", email="abe@gmail.com",
        )
        self.yak = User.objects.create_user(
            "yak", password=PASSWORD, last_name="Яковлев", first_name="Яков",
        )
        self.bor = User.objects.create_user(
            "bor", password=PASSWORD, last_name="Борисов", first_name="Борис",
        )
        set_group(self.abe, self.group)
        set_group(self.yak, self.group_b)
        Profile.objects.create(user=self.bor, role="student", patronymic="Петрович")

        make_teacher("Петров П. П.", "petrov@gmail.com")
        self.teacher_user = User.objects.get(username="teacherivanov@gmail.com")
        self.petrov = User.objects.get(username="petrov@gmail.com")

    def names(self, response):
        return [row["user"].username for row in response.context["rows"]]

    def get(self, **params):
        return self.client.get(reverse("users"), params)

    # --- порядок -----------------------------------------------------------

    def test_newest_first_by_default(self):
        names = self.names(self.get())

        self.assertEqual(names[:3], ["petrov@gmail.com", "bor", "yak"])

    def test_sort_by_name(self):
        names = self.names(self.get(sort="name", q="ов"))

        self.assertEqual(names, ["abe", "bor", "yak"])

    # --- поиск -------------------------------------------------------------

    def test_search_by_surname_email_patronymic_and_login(self):
        self.assertEqual(self.names(self.get(q="абрамов")), ["abe"])
        self.assertEqual(self.names(self.get(q="ABE@gmail")), ["abe"])
        self.assertEqual(self.names(self.get(q="петрович")), ["bor"])
        self.assertEqual(self.names(self.get(q="yak")), ["yak"])

    def test_search_words_must_all_match(self):
        self.assertEqual(self.names(self.get(q="абрамов аркадий")), ["abe"])
        self.assertEqual(self.names(self.get(q="абрамов яков")), [])

    def test_percent_is_a_plain_character(self):
        self.assertEqual(self.names(self.get(q="%")), [])

    # --- роль, группа, теги, кураторы -------------------------------------

    def test_role_filters(self):
        admins = self.names(self.get(role="admin"))
        teachers = self.names(self.get(role="teacher"))
        students = self.names(self.get(role="student"))

        self.assertEqual(admins, ["admin2"])
        self.assertCountEqual(
            teachers, ["teacherivanov@gmail.com", "petrov@gmail.com"]
        )
        self.assertCountEqual(
            students, ["student", "abe", "yak", "bor"]
        )

    def test_group_filter(self):
        self.assertEqual(self.names(self.get(group=self.group.pk)), ["abe"])
        self.assertEqual(self.names(self.get(group=self.group_b.pk)), ["yak"])

    def test_students_without_group(self):
        names = self.names(self.get(nogroup=1))

        self.assertCountEqual(names, ["student", "bor"])

    def test_curators(self):
        self.assertEqual(
            self.names(self.get(curators=1)), ["teacherivanov@gmail.com"]
        )

    def test_tag_of_teacher_and_tag_of_student_group(self):
        self.assertEqual(
            self.names(self.get(tag=f"t{self.teacher_tag.pk}")),
            ["teacherivanov@gmail.com"],
        )
        self.assertEqual(
            self.names(self.get(tag=f"g{self.group_tag.pk}")), ["yak"]
        )

    def test_tag_filter_needs_a_kind_letter(self):
        # Номера в двух таблицах независимы: вид тега решает буква g / t
        self.assertEqual(self.get(tag="x1").context["filters"]["tag"], "")
        self.assertEqual(self.get(tag="1").context["filters"]["tag"], "")
        self.assertEqual(
            self.get(tag=f"g{self.group_tag.pk}").context["filters"]["tag"],
            f"g{self.group_tag.pk}",
        )

    def test_filters_combine(self):
        self.assertEqual(
            self.names(self.get(role="student", q="я")), ["yak"]
        )
        self.assertEqual(
            self.names(self.get(role="teacher", nogroup=1)), []
        )

    def test_unknown_values_are_ignored(self):
        everyone = get_user_model().objects.count()

        response = self.get(role="boss", group="abc", tag="g99999", sort="zzz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["found_count"], everyone)
        self.assertFalse(response.context["has_filters"])
        self.assertEqual(response.context["filters"]["sort"], "new")

    def test_nothing_found_message(self):
        response = self.get(q="Яблоко")

        self.assertContains(response, "Никого не найдено.")
        self.assertContains(response, "Найдено: 0.")

    def test_form_shows_filters_and_keeps_values(self):
        response = self.get(q="аб", role="student", group=self.group.pk,
                            nogroup=1, curators=1)

        self.assertContains(response, 'name="q" value="аб"')
        self.assertContains(response, "Все роли")
        self.assertContains(response, "Все группы")
        self.assertContains(response, "Все теги")
        self.assertContains(response, "Студенты без группы")
        self.assertContains(response, "Кураторы")
        self.assertContains(response, "Сбросить")
        self.assertContains(response, 'name="nogroup" value="1" checked')

    # --- страницы ----------------------------------------------------------

    def make_many(self, count=20, start=0):
        for number in range(start, start + count):
            get_user_model().objects.create_user(
                f"extra{number:02d}", password=PASSWORD,
                last_name=f"Лишний{number:02d}",
            )

    def test_pages_keep_filters(self):
        self.make_many()

        response = self.get(q="Лишний")
        encoded = quote("Лишний")

        self.assertEqual(response.context["found_count"], 20)
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertContains(response, f"?q={encoded}&amp;page=2")

        second = self.get(q="Лишний", page=2)

        self.assertEqual(len(second.context["rows"]), 5)

    # --- сохранение сохраняет фильтры ---------------------------------------

    def test_saving_a_row_keeps_page_and_filters(self):
        response = self.client.post(reverse("users"), {
            "action": "save_user",
            "user_id": self.yak.pk,
            "role": "student",
            "group": self.group.pk,
            "page": "1",
            "qs": "role=student&sort=name&junk=1",
        })

        self.assertRedirects(
            response, reverse("users") + "?role=student&sort=name&page=1"
        )
        fresh = get_user_model().objects.get(pk=self.yak.pk)
        self.assertEqual(own_group_id(fresh), self.group.pk)

    def test_rows_carry_the_current_filters(self):
        html = self.get(role="student", sort="name").content.decode()

        self.assertIn('name="qs" value="role=student&amp;sort=name"', html)

    # --- новый пользователь -------------------------------------------------

    def create(self, username="newbie@gmail.com"):
        return self.client.post(reverse("users"), {
            "action": "create_user",
            "new_user-role": "student",
            "new_user-last_name": "Новиков",
            "new_user-first_name": "Никита",
            "new_user-patronymic": "",
            "new_user-username": username,
            "new_user-group": self.group.pk,
            "new_user-password": STUDENT_PASSWORD,
            "page": "2",
        })

    def test_new_user_is_first_and_highlighted(self):
        response = self.create()
        new = get_user_model().objects.get(username="newbie@gmail.com")

        self.assertRedirects(response, reverse("users") + f"?new={new.pk}")

        page = self.client.get(response["Location"])
        rows = page.context["rows"]

        self.assertEqual(rows[0]["user"].pk, new.pk)
        self.assertTrue(rows[0]["is_new"])
        self.assertFalse(any(row["is_new"] for row in rows[1:]))
        self.assertContains(page, '<tr class="is-new">', count=1)

    def test_highlight_needs_a_real_user_id(self):
        page = self.get(new="abc")

        self.assertNotContains(page, 'class="is-new"')

    def test_new_user_is_visible_even_after_filters(self):
        self.get(q="Яблоко")

        response = self.create()
        page = self.client.get(response["Location"])

        self.assertIn(
            "newbie@gmail.com", self.names(page)
        )

    # --- раунд 17: вид страницы ------------------------------------------------

    def test_search_has_magnifier_button_and_no_text_button(self):
        html = self.get().content.decode()

        self.assertIn('class="search-go" type="submit"', html)
        self.assertIn('aria-label="Найти"', html)
        self.assertNotIn(">Показать<", html)
        self.assertNotIn("user-filter-button", html)

    def test_checkboxes_are_pills_in_the_common_filter_grid(self):
        html = self.get().content.decode()
        grid = html[html.index("user-filter-grid"):html.index("</form>")]

        self.assertEqual(grid.count('class="user-filter-pill"'), 2)
        self.assertIn('name="nogroup"', grid)
        self.assertIn('name="curators"', grid)
        self.assertEqual(grid.count("<select"), 4)

    def test_student_row_shows_group_pills_and_curator(self):
        response = self.get(q="abe")
        html = response.content.decode()
        row = html[html.index("<tr"):]

        self.assertIn(reverse("group_card", args=[self.group.pk]), row)
        self.assertIn('class="group-pills"', row)
        self.assertIn("Куратор:", row)
        self.assertIn(reverse("teacher_card", args=[self.teacher.pk]), row)

    def test_student_without_group_and_group_without_curator(self):
        self.assertContains(
            self.get(q="bor"), '<span class="users-hint">без группы</span>'
        )
        self.assertContains(
            self.get(q="yak"), '<span class="users-hint">не назначен</span>'
        )

    def test_teacher_row_lists_curated_groups(self):
        self.group_b.group_curator = self.teacher
        self.group_b.save()

        response = self.get(q="teacherivanov")

        self.assertContains(response, "Курирует:")
        self.assertContains(
            response, reverse("group_card", args=[self.group.pk])
        )
        self.assertContains(
            response, reverse("group_card", args=[self.group_b.pk])
        )
        self.assertContains(self.get(q="petrov"), "никого не курирует")

    def test_last_login_is_shown_or_never(self):
        self.assertContains(self.get(q="abe"), "не входил")

        self.abe.last_login = timezone.now()
        self.abe.save()

        response = self.get(q="abe")
        expected = timezone.localtime(self.abe.last_login).strftime(
            "%d.%m.%Y %H:%M"
        )

        self.assertContains(response, expected)
        self.assertNotContains(response, "не входил")

    def test_empty_result_spans_all_five_columns(self):
        self.assertContains(self.get(q="нет-такого"), 'colspan="5"')

    def test_css_has_equal_grid_and_search_button(self):
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
        block = css[css.index("Раунд 17"):]

        self.assertRegex(block, r"\.user-filter-grid \{\s*display: grid;")
        self.assertIn("repeat(auto-fill, minmax(170px, 1fr))", block)
        self.assertRegex(block, r"\.search-go \{\s*position: absolute;")
        self.assertIn(".user-filter-pill input:checked + span", block)
        self.assertNotIn("user-filter-row", css)

    # --- скорость ------------------------------------------------------------

    def test_queries_do_not_grow_with_users_on_the_page(self):
        def count_queries(**params):
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("users"), params)
            return len(ctx)

        self.make_many(3)
        few = count_queries(q="Лишний")
        self.make_many(11, start=3)

        self.assertEqual(count_queries(q="Лишний"), few)


class UsersIndexesTests(TestCase):
    """Скрипт индексов согласован с моделями."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(settings.BASE_DIR) / "database" / "add_users_indexes.py"
        cls.script = path.read_text(encoding="utf-8")

    def test_model_indexes_are_in_the_script(self):
        names = [index.name for index in Profile._meta.indexes]
        names += [index.name for index in Group._meta.indexes]

        self.assertEqual(
            sorted(names),
            ["group_curator_idx", "profile_group_idx", "profile_role_idx",
             "profile_teacher_idx"],
        )

        for name in names:
            with self.subTest(index=name):
                self.assertIn(name, self.script)

    def test_script_covers_filters_and_name_order(self):
        self.assertIn('"Profile" (role)', self.script)
        self.assertIn('"Profile" ("group")', self.script)
        self.assertIn("group_curator_id", self.script)
        self.assertIn("(last_name, first_name, username)", self.script)

    def test_script_is_safe_to_repeat(self):
        self.assertEqual(self.script.count("CREATE INDEX IF NOT EXISTS"), 5)


class SplitTagsScriptTests(TestCase):
    """Скрипты базы согласованы с моделями GroupTag и TeacherTag."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR) / "database"
        cls.split = (base / "split_tags.py").read_text(encoding="utf-8")
        cls.create = (base / "create_db.py").read_text(encoding="utf-8")

    def test_tables_and_columns_match_models(self):
        for model in (GroupTag, TeacherTag):
            with self.subTest(model=model.__name__):
                table = f'"{model._meta.db_table}"'

                for script in (self.split, self.create):
                    self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", script)

                for field in model._meta.concrete_fields:
                    self.assertIn(field.column, self.split)

    def test_old_tables_are_gone_from_create_script(self):
        self.assertNotIn('"TagType"', self.create)
        self.assertNotIn('"Tag"', self.create)
        self.assertIn('REFERENCES "GroupTag"(tag_id)', self.create)
        self.assertIn('REFERENCES "TeacherTag"(tag_id)', self.create)

    def test_split_script_moves_data_and_drops_old_tables(self):
        self.assertIn('DROP TABLE "Tag"', self.split)
        self.assertIn('DROP TABLE IF EXISTS "TagType"', self.split)
        self.assertIn("to_regclass", self.split)
        self.assertIn("ON CONFLICT (tag_name) DO NOTHING", self.split)

    def test_models_point_to_new_tables(self):
        self.assertIs(Group._meta.get_field("group_tag").related_model, GroupTag)
        self.assertIs(
            Teacher._meta.get_field("teacher_tag").related_model, TeacherTag
        )


class SharedPairTests(BaseTestCase):
    """Пара — самостоятельный объект: одна пара у нескольких групп."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.other = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        PairGroup.objects.create(pair=self.pair, group=self.other)
        Day.objects.create(
            group=self.other, pair=self.pair, pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )

    def add_pair(self, **fields):
        data = {
            "action": "add_pair",
            "pair-pair_name": "Физика",
            "pair-teacher": self.teacher.pk,
            "pair-groups": [self.group.pk, self.other.pk],
        }
        data.update(fields)

        return self.client.post(reverse("workshop"), data, follow=True)

    # --- модель и форма -------------------------------------------------------

    def test_pair_has_no_group_and_two_attachments(self):
        self.assertFalse(hasattr(self.pair, "group"))
        self.assertEqual(self.pair.groups.count(), 2)
        self.assertEqual(self.group.pairs.get(), self.pair)

    def test_attachment_is_unique(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            PairGroup.objects.create(pair=self.pair, group=self.group)

    def test_form_creates_pair_with_several_groups(self):
        response = self.add_pair()

        self.assertContains(response, "Запись добавлена.")
        pair = Pair.objects.get(pair_name="Физика")
        self.assertEqual(
            set(pair.groups.values_list("pk", flat=True)),
            {self.group.pk, self.other.pk},
        )

    def test_pair_can_be_created_without_groups(self):
        self.add_pair(**{"pair-groups": []})

        self.assertEqual(Pair.objects.get(pair_name="Физика").groups.count(), 0)

    def test_same_teacher_and_name_is_rejected(self):
        response = self.add_pair(**{"pair-pair_name": " математика "})

        self.assertContains(response, "уже есть пара с таким названием")
        self.assertEqual(Pair.objects.count(), 1)

    def test_other_teacher_may_have_pair_with_same_name(self):
        petrov = make_teacher("Петров П. П.", "teacherpetrov@gmail.com")

        response = self.add_pair(**{
            "pair-pair_name": "Математика", "pair-teacher": petrov.pk,
        })

        self.assertContains(response, "Запись добавлена.")
        self.assertEqual(Pair.objects.filter(pair_name="Математика").count(), 2)

    def test_pair_form_shows_groups_as_checkboxes(self):
        html = self.client.get(reverse("workshop")).content.decode()

        self.assertIn('name="pair-groups"', html)
        self.assertIn('type="checkbox"', html)
        self.assertNotIn('name="pair-group"', html)

    def test_edit_form_has_current_groups_checked(self):
        response = self.client.get(
            reverse("workshop"), {"edit": "pair", "item": self.pair.pk}
        )
        form = response.context["cards"]["pair"]["form"]

        self.assertEqual(
            set(form.initial["groups"]), {self.group.pk, self.other.pk}
        )

    def test_picker_label_has_name_and_teacher(self):
        response = self.client.get(reverse("workshop"), {"edit": "pair"})
        items = response.context["cards"]["pair"]["items"]

        self.assertEqual(items[0]["label"], "Математика · Иванов И. И.")

    def test_deleting_a_pair_asks_what_goes_with_it(self):
        response = self.client.post(reverse("workshop"), {
            "action": "delete_item", "edit": "pair", "item": self.pair.pk,
        })
        text = response.context["cards"]["pair"]["confirm_text"]

        self.assertIn("занятия в расписании — 2", text)

    # --- правки расписания по группам -----------------------------------------

    def cancel(self, group):
        return self.client.post(
            reverse("group_change", args=[group.pk]),
            {"kind": "cancel", "date": "2026-10-19", "number": 1},
        )

    def lesson(self, group):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("group_schedule", args=[group.pk]),
                {"date": "2026-10-19"},
            )

        return response.context["schedule_days"][0]["pairs"][0]["lesson"]

    def test_change_remembers_its_group(self):
        self.cancel(self.group)

        self.assertEqual(ScheduleChange.objects.get().group_id, self.group.pk)

    def test_cancel_in_one_group_does_not_touch_the_other(self):
        self.cancel(self.group)

        self.assertEqual(self.lesson(self.group)["status"], "cancel")
        self.assertEqual(self.lesson(self.other)["status"], "")

    def test_changes_of_two_groups_are_independent(self):
        self.cancel(self.group)
        self.cancel(self.other)

        self.assertEqual(ScheduleChange.objects.count(), 2)

        self.client.post(
            reverse("group_change", args=[self.group.pk]),
            {"kind": "restore", "date": "2026-10-19", "number": 1},
        )

        self.assertEqual(ScheduleChange.objects.get().group_id, self.other.pk)
        self.assertEqual(self.lesson(self.group)["status"], "")
        self.assertEqual(self.lesson(self.other)["status"], "cancel")

    def test_teacher_sees_both_groups_in_the_same_slot(self):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("teachers"),
                {"teacher": self.teacher.pk, "date": "2026-10-19"},
            )

        lessons = response.context["schedule_days"][0]["pairs"][0]["lessons"]

        self.assertEqual(
            sorted(item["group"].group_name for item in lessons),
            ["А-01", "Б-02"],
        )

    # --- страницы -------------------------------------------------------------

    def test_group_card_lists_attached_pairs_with_count(self):
        PairGroup.objects.filter(pair=self.pair, group=self.other).delete()
        make_pair(self.group, self.teacher, "Физика")

        response = self.client.get(reverse("group_card", args=[self.group.pk]))

        self.assertContains(response, 'Пары <span class="profile-count">2</span>')

    def test_teacher_card_shows_groups_of_each_pair(self):
        response = self.client.get(reverse("teacher_card", args=[self.teacher.pk]))

        self.assertContains(response, "Математика")
        self.assertContains(response, reverse("group_card", args=[self.group.pk]))
        self.assertContains(response, reverse("group_card", args=[self.other.pk]))

    def test_admin_sees_group_stats_student_does_not(self):
        with fake_today(MONDAY):
            page = self.client.get(reverse("group_schedule", args=[self.group.pk]))

        stats = page.context["group_stats"]
        self.assertEqual(stats["attached"], 1)
        self.assertEqual(stats["lessons"], 1)
        self.assertEqual(stats["teachers"], 1)
        self.assertEqual(stats["unused"], 0)
        self.assertContains(page, "Сводка")

        make_pair(self.group, self.teacher, "Лишняя")
        with fake_today(MONDAY):
            page = self.client.get(reverse("group_schedule", args=[self.group.pk]))
        self.assertEqual(page.context["group_stats"]["unused"], 1)

        self.client.force_login(self.user)
        with fake_today(MONDAY):
            page = self.client.get(reverse("group_schedule", args=[self.group.pk]))

        self.assertIsNone(page.context["group_stats"])
        self.assertNotContains(page, "Сводка")

    def test_group_pages_do_not_query_per_pair(self):
        def count():
            with fake_today(MONDAY):
                with CaptureQueriesContext(connection) as ctx:
                    self.client.get(
                        reverse("teacher_card", args=[self.teacher.pk])
                    )
            return len(ctx)

        count()
        few = count()

        for index in range(5):
            pair = make_pair(self.group, self.teacher, f"Предмет {index}")
            PairGroup.objects.create(pair=pair, group=self.other)

        self.assertEqual(count(), few)

    def test_deleting_a_group_keeps_its_pairs(self):
        self.other.delete()

        self.assertTrue(Pair.objects.filter(pk=self.pair.pk).exists())
        self.assertEqual(self.pair.groups.count(), 1)

    def test_css_has_stats_and_warning_styles(self):
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
        block = css[css.index("Раунд 19"):]

        self.assertIn(".group-stats", block)
        self.assertIn("#id_pair-groups", block)
        self.assertIn(".message.warning", css)


class PairGroupsScriptTests(TestCase):
    """Скрипт add_pair_groups.py согласован с моделями."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR) / "database"
        cls.script = (base / "add_pair_groups.py").read_text(encoding="utf-8")
        cls.create = (base / "create_db.py").read_text(encoding="utf-8")

    def test_table_and_columns_match_models(self):
        self.assertEqual(PairGroup._meta.db_table, "PairGroup")

        for script in (self.script, self.create):
            self.assertIn('CREATE TABLE IF NOT EXISTS "PairGroup"', script)

        for field in PairGroup._meta.concrete_fields:
            self.assertIn(field.column, self.script)

        self.assertIn("unique_pair_group", self.script)

    def test_pairs_lose_group_column_and_changes_get_it(self):
        self.assertNotIn("group", [f.name for f in Pair._meta.concrete_fields])
        self.assertIn('DROP COLUMN "group"', self.script)
        self.assertIn('"ScheduleChange"', self.script)
        self.assertIn('ADD COLUMN IF NOT EXISTS "group"', self.script)
        self.assertIn('"group" INT NOT NULL', self.create)

    def test_script_asks_for_confirmation_and_wipes_old_data(self):
        self.assertIn("pg_dump", self.script)
        self.assertIn("input(", self.script)
        self.assertIn("ДА", self.script)
        self.assertIn('TRUNCATE TABLE "Day", "ScheduleChange", "Pairs"', self.script)
        self.assertIn("DiaryMark", self.script)

    def test_script_is_safe_to_repeat(self):
        self.assertIn("has_old_structure", self.script)
        self.assertIn("Структура уже новая", self.script)


class ZipExportTests(BaseTestCase):
    """«Скачать всё одним ZIP»: те же CSV, что и по одному."""

    def setUp(self):
        self.client.force_login(self.admin)

    def archive(self):
        import io
        import zipfile

        response = self.client.get(reverse("workshop_export_all"))

        return response, zipfile.ZipFile(io.BytesIO(response.content))

    def test_zip_has_every_set_once(self):
        response, archive = self.archive()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertRegex(
            response["Content-Disposition"],
            r'^attachment; filename="raspisite_\d{4}-\d{2}-\d{2}\.zip"$',
        )
        self.assertEqual(
            archive.namelist(),
            [f"raspisite_{kind}.csv" for kind in
             ("bells", "teachers", "groups", "pairs", "schedule", "diary")],
        )

    def test_files_are_the_same_as_single_exports(self):
        _, archive = self.archive()

        for kind in ("teachers", "pairs", "schedule"):
            single = self.client.get(
                reverse("workshop_export"), {"kind": kind}
            ).content

            self.assertEqual(archive.read(f"raspisite_{kind}.csv"), single)

    def test_each_file_starts_with_bom_and_headers(self):
        _, archive = self.archive()

        self.assertTrue(
            archive.read("raspisite_groups.csv").startswith(
                "\ufeffНазвание;Курс;Смена;Куратор;Тег".encode("utf-8")
            )
        )

    def test_staff_only(self):
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(reverse("workshop_export_all")).status_code, 403
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("workshop_export_all")).status_code, 302
        )

    def test_workshop_has_zip_link_and_diary_card(self):
        page = self.client.get(reverse("workshop"))

        self.assertContains(page, reverse("workshop_export_all"))
        self.assertContains(page, "Дневник: экспорт и импорт")
        self.assertNotIn("diary", [key for key, _ in page.context["data_kinds"]])
        self.assertNotContains(page, '<option value="diary"')


class DiaryCsvTests(BaseTestCase):
    """Оценки дневника в CSV: выгрузка, загрузка «наложить» и «заменить»."""

    HEAD = (
        "Студент (логин);ФИО;Дата;Номер пары;Предмет;Преподаватель;"
        "Оценка;Поставил\r\n"
    )
    DAY = date(2026, 10, 19)

    def setUp(self):
        self.client.force_login(self.admin)
        self.second = get_user_model().objects.create_user(
            "second", password=PASSWORD, last_name="Второв", first_name="Иван"
        )

    def mark(self, student=None, number=1, kind="score", score=85, **extra):
        return Mark.objects.create(
            student=student or self.user, pair=self.pair,
            lesson_date=self.DAY, pair_number=number, kind=kind,
            score=score, **extra,
        )

    def row(self, login="student", day="19.10.2026", number=1, mark="85",
            giver=""):
        return (
            f"{login};;{day};{number};Математика;Иванов И. И.;{mark};{giver}\r\n"
        )

    def upload(self, content, dry_run=False, mode="overlay", confirm=False):
        post = {
            "action": "import_csv", "kind": "diary", "mode": mode,
            "file": SimpleUploadedFile(
                "marks.csv", content.encode("utf-8-sig"),
                content_type="text/csv",
            ),
        }

        if dry_run:
            post["dry_run"] = "1"

        if confirm:
            post["confirm_replace"] = "1"

        return self.client.post(reverse("workshop"), post)

    def report(self, response):
        return response.context["import_report"]

    def export(self):
        return self.client.get(
            reverse("workshop_export"), {"kind": "diary"}
        ).content.decode("utf-8-sig").splitlines()

    # --- выгрузка -----------------------------------------------------------

    def test_export_rows(self):
        self.mark(teacher=self.teacher)
        self.mark(self.second, kind="absent", score=None)
        self.mark(number=2, kind="ill", score=None)

        lines = self.export()

        self.assertEqual(lines[0], self.HEAD.strip())
        self.assertEqual(
            lines[1],
            "student;;19.10.2026;1;Математика;Иванов И. И.;85;Иванов И. И.",
        )
        self.assertEqual(
            lines[2], "second;Второв Иван;19.10.2026;1;Математика;Иванов И. И.;Н;"
        )
        self.assertEqual(
            lines[3], "student;;19.10.2026;2;Математика;Иванов И. И.;Б;"
        )

    def test_headers_match_the_module(self):
        from diary.csv_marks import HEADERS
        from schedule.csv_io import KINDS

        self.assertEqual(KINDS["diary"]["headers"], HEADERS)

    def test_template_has_only_headers(self):
        response = self.client.get(
            reverse("workshop_export"), {"kind": "diary", "template": "1"}
        )

        self.assertEqual(
            response.content.decode("utf-8-sig").strip(), self.HEAD.strip()
        )

    def test_export_round_trip(self):
        self.mark(teacher=self.teacher)
        self.mark(self.second, kind="absent", score=None)
        before = self.export()

        Mark.objects.all().delete()
        report = self.report(self.upload("\n".join(before)))

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["created"], 2)
        self.assertEqual(self.export(), before)

    # --- «наложить» ---------------------------------------------------------

    def test_overlay_creates_updates_and_keeps_others(self):
        keep = self.mark(self.second, number=3)
        old = self.mark()

        report = self.report(self.upload(
            self.HEAD
            + self.row(mark="Н")
            + self.row(number=2, mark="70", giver="Иванов И. И.")
        ))

        self.assertEqual(
            (report["created"], report["updated"], report["unchanged"]),
            (1, 1, 0),
        )
        self.assertEqual(report["deleted"], 0)
        old.refresh_from_db()
        self.assertEqual(old.kind, "absent")
        self.assertIsNone(old.score)
        self.assertTrue(Mark.objects.filter(pk=keep.pk).exists())
        self.assertEqual(
            Mark.objects.get(pair_number=2).teacher_id, self.teacher.pk
        )

    def test_same_row_is_unchanged(self):
        self.mark()

        report = self.report(self.upload(self.HEAD + self.row()))

        self.assertEqual(report["unchanged"], 1)
        self.assertEqual(Mark.objects.count(), 1)

    def test_iso_date_and_letters_are_understood(self):
        report = self.report(self.upload(
            self.HEAD + self.row(day="2026-10-19", mark="б")
        ))

        self.assertEqual(report["errors"], [])
        self.assertEqual(Mark.objects.get().kind, "ill")

    # --- «заменить полностью» -----------------------------------------------

    def test_replace_needs_confirmation(self):
        self.mark()

        report = self.report(self.upload(
            self.HEAD + self.row(login="second"), mode="replace"
        ))

        self.assertIn("подтверждения", report["fatal"])
        self.assertEqual(Mark.objects.count(), 1)
        self.assertEqual(Mark.objects.get().student_id, self.user.pk)

    def test_replace_removes_old_marks_and_keeps_only_the_file(self):
        self.mark()
        self.mark(number=4)

        report = self.report(self.upload(
            self.HEAD + self.row(login="second"), mode="replace",
            confirm=True,
        ))

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["deleted"], 2)
        self.assertEqual(report["created"], 1)
        self.assertTrue(report["saved"])
        self.assertEqual(
            list(Mark.objects.values_list("student__username", flat=True)),
            ["second"],
        )

    def test_replace_with_errors_deletes_nothing(self):
        self.mark()

        report = self.report(self.upload(
            self.HEAD + self.row(login="second") + self.row(login="нет"),
            mode="replace", confirm=True,
        ))

        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(Mark.objects.count(), 1)
        self.assertEqual(Mark.objects.get().student_id, self.user.pk)

    def test_dry_run_changes_nothing_in_both_modes(self):
        self.mark()

        for mode in ("overlay", "replace"):
            with self.subTest(mode=mode):
                report = self.report(self.upload(
                    self.HEAD + self.row(login="second"),
                    dry_run=True, mode=mode, confirm=True,
                ))

                self.assertEqual(report["errors"], [])
                self.assertFalse(report["saved"])
                self.assertEqual(Mark.objects.count(), 1)

    def test_overlay_mode_ignores_replace_confirmation_without_mode(self):
        self.mark()

        self.upload(self.HEAD + self.row(login="second"), confirm=True)

        self.assertEqual(Mark.objects.count(), 2)

    # --- ошибки --------------------------------------------------------------

    def test_row_errors_are_listed_and_nothing_is_saved(self):
        report = self.report(self.upload(
            self.HEAD
            + self.row(login="нет")
            + self.row(login="admin2")
            + self.row(day="31.02.2026")
            + self.row(number=9)
            + self.row(mark="101")
            + self.row(mark="")
            + "student;;19.10.2026;1;Химия;Иванов И. И.;5;\r\n"
            + self.row(giver="Нет Н. Н.")
            + self.row()
        ))

        errors = report["errors"]

        self.assertEqual(len(errors), 8)
        self.assertIn("студент «нет» не найден", errors[0])
        self.assertIn("студент «admin2» не найден", errors[1])
        self.assertIn("ДД.ММ.ГГГГ", errors[2])
        self.assertIn("от 1 до 6", errors[3])
        self.assertIn("«Оценка»", errors[4])
        self.assertIn("пуст", errors[5])
        self.assertIn("не найдена", errors[6])
        self.assertIn("преподаватель «Нет Н. Н.» не найден", errors[7])
        self.assertFalse(Mark.objects.exists())

    def test_duplicate_row_is_an_error(self):
        report = self.report(self.upload(
            self.HEAD + self.row() + self.row(mark="90")
        ))

        self.assertIn("повторяет строку", report["errors"][0])
        self.assertFalse(Mark.objects.exists())

    def test_wrong_headers_are_rejected(self):
        report = self.report(self.upload("Логин;Оценка\r\nstudent;5\r\n"))

        self.assertIn("заголовок файла не подходит", report["fatal"])

    # --- страница ------------------------------------------------------------

    def test_report_is_shown_in_the_diary_card(self):
        self.mark()

        response = self.upload(
            self.HEAD + self.row(login="second"), mode="replace", confirm=True
        )
        html = response.content.decode()
        card = html[html.index("Дневник: экспорт и импорт"):]

        self.assertIn("Дневник (оценки)", card)
        self.assertIn("прежних оценок: 1", card)
        # Отчёт один и показан только в карточке дневника
        self.assertEqual(html.count("workshop-report-title"), 1)

    def test_diary_import_has_bigger_limits(self):
        from schedule.csv_io import max_bytes

        self.assertGreater(max_bytes("diary"), max_bytes("bells"))
        self.assertEqual(max_bytes("bells"), 2 * 1024 * 1024)

    def test_staff_only(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("workshop_export"), {"kind": "diary"}
        )

        self.assertEqual(response.status_code, 403)

    def test_import_does_not_query_per_row_for_lookups(self):
        rows = "".join(self.row(number=n) for n in range(1, 7))
        self.upload(self.HEAD + rows)
        Mark.objects.all().delete()

        with CaptureQueriesContext(connection) as ctx:
            self.upload(self.HEAD + rows)

        pair_lookups = [
            q for q in ctx.captured_queries if '"Pairs"' in q["sql"]
            and "WHERE" in q["sql"] and "pair_name" in q["sql"]
        ]

        self.assertEqual(len(pair_lookups), 1)


class MainPageTests(BaseTestCase):
    """Главная страница: слово RASPISITE, надпись ЕКЕБ и 12 звёзд по кругу."""

    def setUp(self):
        self.client.force_login(self.user)

    def test_title_subtitle_and_twelve_stars(self):
        response = self.client.get(reverse("home"))
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mainpage.html")
        self.assertIn('class="hero-title" aria-label="RASPISITE"', html)
        self.assertIn('<p class="hero-sub">ЕКЕБ</p>', html)
        self.assertEqual(html.count('class="star"'), 12)

    def test_stars_are_arranged_in_a_circle(self):
        html = self.client.get(reverse("home")).content.decode()

        points = re.findall(r'class="star" href="#star" transform="translate\(([-\d.]+) ([-\d.]+)\)"', html)
        radii = {round((float(x) ** 2 + float(y) ** 2) ** 0.5) for x, y in points}

        self.assertEqual(len(points), 12)
        self.assertEqual(len(radii), 1)

    def test_no_script_and_no_advantages_list(self):
        html = self.client.get(reverse("home")).content.decode()

        # Скрипты шапки (класс «js», меню) есть на всех страницах; в самой
        # главной части страницы (внутри <main>) скриптов нет
        self.assertNotIn("<script", html[html.index("<main"):])
        self.assertNotIn("<ul", html)
        self.assertNotIn("преимущ", html.lower())

    def test_page_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.get(reverse("home")).status_code, 302)


class CuratorChoiceTests(BaseTestCase):
    """В Кузне куратором группы нельзя выбрать действующего куратора."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.second = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )
        self.petrov = make_teacher("Петров П. П.", "teacherpetrov@gmail.com")

    def offered(self, **params):
        response = self.client.get(reverse("workshop"), params)
        form = response.context["cards"]["group"]["form"]

        return set(
            form.fields["group_curator"].queryset.values_list(
                "pk", flat=True
            )
        )

    def test_everyone_is_offered_when_nobody_curates(self):
        self.assertEqual(
            self.offered(), {self.teacher.pk, self.petrov.pk}
        )

    def test_active_curator_is_not_offered(self):
        self.group.group_curator = self.teacher
        self.group.save()

        self.assertEqual(self.offered(), {self.petrov.pk})

    def test_teacher_without_account_is_not_offered(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")

        self.assertNotIn(legacy.pk, self.offered())

    def test_former_teacher_is_not_offered(self):
        former = Teacher.objects.create(teacher_name="Бывший Б. Б.")
        set_role(self.user, ROLE_TEACHER, former)
        self.assertIn(former.pk, self.offered())

        set_role(self.user, "student")

        self.assertNotIn(former.pk, self.offered())

    def test_own_curator_stays_in_edit_form(self):
        self.group.group_curator = self.teacher
        self.group.save()
        self.second.group_curator = self.petrov
        self.second.save()

        ids = self.offered(edit="group", item=self.group.pk)

        self.assertEqual(ids, {self.teacher.pk})

    def test_group_keeps_its_old_curator_without_account(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")
        self.group.group_curator = legacy
        self.group.save()

        ids = self.offered(edit="group", item=self.group.pk)

        self.assertIn(legacy.pk, ids)

        # Другим группам он не предлагается
        self.assertNotIn(legacy.pk, self.offered(edit="group", item=self.second.pk))

    def test_new_group_cannot_take_active_curator(self):
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.client.post(reverse("workshop"), {
            "action": "add_group",
            "group-group_name": "ПО-103",
            "group-group_course": 1,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.teacher.pk,
        }, follow=True)

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertFalse(Group.objects.filter(group_name="ПО-103").exists())

    def test_new_group_takes_free_curator(self):
        self.client.post(reverse("workshop"), {
            "action": "add_group",
            "group-group_name": "ПО-103",
            "group-group_course": 1,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.petrov.pk,
        }, follow=True)

        self.assertEqual(
            Group.objects.get(group_name="ПО-103").group_curator_id,
            self.petrov.pk,
        )

    def test_other_groups_curator_cannot_be_set_on_update(self):
        self.second.group_curator = self.petrov
        self.second.save()

        response = self.client.post(reverse("workshop"), {
            "action": "update_item",
            "edit": "group",
            "item": self.group.pk,
            "group-group_name": "А-01",
            "group-group_course": 1,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.petrov.pk,
        }, follow=True)

        self.assertContains(response, "Проверьте заполненные поля.")
        self.group.refresh_from_db()
        self.assertIsNone(self.group.group_curator_id)


class PairTeacherChoiceTests(BaseTestCase):
    """Преподавателя пары выбирают среди настоящих пользователей."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.petrov = make_teacher("Петров П. П.", "teacherpetrov@gmail.com")
        self.legacy = Teacher.objects.create(teacher_name="Старый С. С.")

    def add_pair(self, teacher):
        return self.client.post(reverse("workshop"), {
            "action": "add_pair",
            "pair-pair_name": "Физика",
            "pair-groups": self.group.pk,
            "pair-teacher": teacher.pk,
        }, follow=True)

    def test_only_real_teachers_are_offered(self):
        response = self.client.get(reverse("workshop"))
        form = response.context["cards"]["pair"]["form"]
        ids = set(
            form.fields["teacher"].queryset.values_list("pk", flat=True)
        )

        self.assertEqual(ids, {self.teacher.pk, self.petrov.pk})

    def test_real_teacher_can_be_chosen(self):
        response = self.add_pair(self.petrov)

        self.assertContains(response, "Запись добавлена.")
        self.assertTrue(
            Pair.objects.filter(
                pair_name="Физика", teacher=self.petrov
            ).exists()
        )

    def test_teacher_without_account_is_rejected(self):
        response = self.add_pair(self.legacy)

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertFalse(Pair.objects.filter(pair_name="Физика").exists())

    def test_existing_pair_keeps_its_old_teacher(self):
        pair = make_pair(self.group, self.legacy, "История")

        response = self.client.post(reverse("workshop"), {
            "action": "update_item",
            "edit": "pair",
            "item": pair.pk,
            "pair-pair_name": "Всеобщая история",
            "pair-groups": self.group.pk,
            "pair-teacher": self.legacy.pk,
        }, follow=True)

        self.assertContains(response, "Изменения сохранены")
        pair.refresh_from_db()
        self.assertEqual(pair.pair_name, "Всеобщая история")
        self.assertEqual(pair.teacher_id, self.legacy.pk)


class CalendarLinkTests(BaseTestCase):
    """Число в календаре ведёт на расписание, подходящее роли."""

    def first_url(self, user):
        self.client.force_login(user)

        with fake_today(MONDAY):
            response = self.client.get(
                reverse("calendar"), {"year": 2026, "month": 10}
            )

        # 19 октября 2026 — понедельник третьей недели
        cell = response.context["calendar_weeks"][3][0]
        self.assertEqual(cell["iso"], "2026-10-19")

        return cell["url"], response

    def test_admin_goes_to_all_groups(self):
        url, response = self.first_url(self.admin)

        self.assertEqual(url, "/groups/?date=2026-10-19")
        self.assertContains(response, 'href="/groups/?date=2026-10-19"')

    def test_student_goes_to_own_group(self):
        set_group(self.user, self.group)
        url, _ = self.first_url(get_user_model().objects.get(pk=self.user.pk))

        self.assertEqual(
            url, f"/groups/{self.group.pk}/schedule/?date=2026-10-19"
        )

    def test_student_without_group_goes_to_all_groups(self):
        url, _ = self.first_url(self.user)

        self.assertEqual(url, "/groups/?date=2026-10-19")

    def test_teacher_goes_to_own_schedule(self):
        set_role(self.user, ROLE_TEACHER, self.teacher)
        url, _ = self.first_url(get_user_model().objects.get(pk=self.user.pk))

        self.assertEqual(
            url, f"/teachers/?teacher={self.teacher.pk}&date=2026-10-19"
        )

    def test_unlinked_teacher_goes_to_teachers_page(self):
        # Старая запись: роль «Преподаватель» без привязки к Teacher
        Profile.objects.update_or_create(
            user=self.user, defaults={"role": ROLE_TEACHER, "teacher": None}
        )
        url, _ = self.first_url(get_user_model().objects.get(pk=self.user.pk))

        self.assertEqual(url, "/teachers/?date=2026-10-19")

    def test_teacher_link_opens_that_week(self):
        set_role(self.user, ROLE_TEACHER, self.teacher)
        user = get_user_model().objects.get(pk=self.user.pk)
        url, _ = self.first_url(user)

        with fake_today(SUNDAY):
            response = self.client.get(url)

        self.assertEqual(
            response.context["selected_teacher_id"], self.teacher.pk
        )
        self.assertEqual(
            response.context["schedule_days"][0]["label"], "19 окт"
        )


TUESDAY = date(2026, 10, 20)


class ChangeTestCase(BaseTestCase):
    """Общие помощники для тестов разовых правок."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.teacher2 = make_teacher("Петров П. П.", "teacherpetrov@gmail.com")

    def schedule_url(self, day="2026-10-19", **extra):
        query = "".join(f"&{name}={value}" for name, value in extra.items())

        return (
            reverse("group_schedule", args=[self.group.pk])
            + f"?date={day}&edit=1{query}"
        )

    def open_form(self, act, number=1, day="2026-10-19", **extra):
        """Страница расписания с раскрытой формой переноса или замены."""
        with fake_today(MONDAY):
            return self.client.get(
                self.schedule_url(
                    day, act=act, slot=day, number=number, **extra
                )
            )

    def errors(self, response):
        """Тексты ошибок, оставленные ответом (сообщения)."""
        return [
            str(message)
            for message in get_messages(response.wsgi_request)
            if message.level == message_levels.ERROR
        ]

    def post(self, kind, number=1, day="2026-10-19", **extra):
        return self.client.post(
            reverse("group_change", args=[self.group.pk]),
            {"kind": kind, "date": day, "number": number, **extra},
        )

    def group_lesson(self, weekday_index, number):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("group_schedule", args=[self.group.pk]),
                {"date": "2026-10-19"},
            )

        day = response.context["schedule_days"][weekday_index]
        return day["pairs"][number - 1]["lesson"]

    def teacher_lessons(self, teacher, weekday_index, number):
        with fake_today(MONDAY):
            response = self.client.get(
                reverse("teachers"),
                {"teacher": teacher.pk, "date": "2026-10-19"},
            )

        day = response.context["schedule_days"][weekday_index]
        return day["pairs"][number - 1]["lessons"]

    def busy_teacher2_on_monday(self):
        """Второй преподаватель занят в другой группе в понедельник, пара 1."""
        other_group = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )
        other_pair = make_pair(other_group, self.teacher2, "Физика")
        Day.objects.create(
            group=other_group,
            pair=other_pair,
            pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )


class ChangeAccessTests(ChangeTestCase):
    def test_four_buttons_are_visible_to_admin_in_edit_mode(self):
        response = self.client.get(self.schedule_url())
        html = response.content.decode()

        # Одна пара в неделе: перенос, отмена, замена и сброс
        self.assertEqual(html.count('class="square-button'), 4)
        self.assertContains(response, "act=move")
        self.assertContains(response, "act=replace")
        self.assertContains(response, 'value="cancel"')
        self.assertContains(response, 'value="restore"')

    def test_teacher_column_is_replaced_by_buttons(self):
        edit = self.client.get(self.schedule_url())
        plain = self.client.get(
            reverse("group_schedule", args=[self.group.pk]),
            {"date": "2026-10-19"},
        )

        self.assertNotContains(edit, 'class="pair-teacher"')
        self.assertContains(edit, 'class="pair-actions"')
        self.assertContains(plain, 'class="pair-teacher"')

        # Преподаватель при этом виден под названием пары
        self.assertContains(edit, "Иванов И. И.")

    def test_reset_is_disabled_until_there_is_a_change(self):
        before = self.client.get(self.schedule_url()).content.decode()
        self.assertIn("disabled", before)

        self.post("cancel")

        after = self.client.get(self.schedule_url()).content.decode()
        self.assertNotIn("disabled", after)

    def test_buttons_are_not_shown_for_empty_slots(self):
        response = self.client.get(self.schedule_url())

        # Занятие только одно, остальные слоты кнопок не получают
        self.assertEqual(
            response.content.decode().count('aria-label="Перенос пары'), 1
        )

    def test_no_buttons_without_edit_mode(self):
        response = self.client.get(
            reverse("group_schedule", args=[self.group.pk]),
            {"date": "2026-10-19"},
        )

        self.assertNotContains(response, "square-button")

    def test_student_has_no_buttons_and_cannot_change(self):
        self.client.force_login(self.user)

        response = self.client.get(
            self.schedule_url(act="move", slot="2026-10-19", number=1)
        )
        self.assertNotContains(response, "square-button")
        self.assertNotContains(response, 'name="new_date"')
        self.assertIsNone(response.context["edit_form"])

        response = self.post("cancel")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ScheduleChange.objects.exists())

    def test_anonymous_is_redirected(self):
        self.client.logout()
        response = self.post("cancel")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ScheduleChange.objects.exists())

    def test_unknown_kind_and_bad_params(self):
        self.assertEqual(self.post("explode").status_code, 404)
        self.assertEqual(self.post("cancel", number=9).status_code, 404)
        self.assertEqual(self.post("cancel", day="oops").status_code, 404)


class CancelTests(ChangeTestCase):
    def test_cancel_marks_lesson_and_keeps_base_schedule(self):
        response = self.post("cancel")

        self.assertRedirects(
            response, self.schedule_url(), fetch_redirect_response=False
        )

        change = ScheduleChange.objects.get()
        self.assertEqual(change.change_type, "CANCEL")
        self.assertEqual(change.old_pair_number, 1)
        self.assertEqual(change.calendar.date, MONDAY)

        lesson = self.group_lesson(0, 1)
        self.assertEqual(lesson["status"], "cancel")

        # Постоянное расписание не тронуто
        self.assertEqual(Day.objects.filter(group=self.group).count(), 1)

    def test_cancel_applies_only_to_its_date(self):
        self.post("cancel")

        with fake_today(MONDAY):
            response = self.client.get(
                reverse("group_schedule", args=[self.group.pk]),
                {"date": "2026-10-26"},
            )

        lesson = response.context["schedule_days"][0]["pairs"][0]["lesson"]
        self.assertEqual(lesson["status"], "")

    def test_cancel_twice_keeps_one_record(self):
        self.post("cancel")
        self.post("cancel")

        self.assertEqual(ScheduleChange.objects.count(), 1)

    def test_restore_removes_change(self):
        self.post("cancel")
        self.post("restore")

        self.assertFalse(ScheduleChange.objects.exists())
        self.assertEqual(self.group_lesson(0, 1)["status"], "")

    def test_empty_slot_cannot_be_cancelled(self):
        response = self.post("cancel", number=2)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ScheduleChange.objects.exists())

    def test_holiday_cannot_be_changed(self):
        Calendar.objects.create(
            date=MONDAY, weekday=1, is_holiday=True, holiday_name="Праздник"
        )
        self.post("cancel")

        self.assertFalse(ScheduleChange.objects.exists())

    def test_cancel_is_visible_on_all_groups_and_teacher_pages(self):
        self.post("cancel")

        with fake_today(MONDAY):
            response = self.client.get(
                reverse("groups"), {"date": "2026-10-19"}
            )

        lesson = response.context["schedule_groups"][0]["pairs"][0]["lesson"]
        self.assertEqual(lesson["status"], "cancel")

        lessons = self.teacher_lessons(self.teacher, 0, 1)
        self.assertEqual(lessons[0]["status"], "cancel")


class ReplaceTests(ChangeTestCase):
    def test_form_opens_under_the_pair(self):
        response = self.open_form("replace")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="new_teacher"')
        self.assertContains(response, 'name="new_pair_name"')
        self.assertContains(response, "Математика")
        self.assertNotContains(response, 'name="new_date"')
        self.assertTemplateUsed(response, "days.html")

        # Кнопка «Замена» подсвечена как открытая
        self.assertContains(response, "is-active")

    def test_only_real_teachers_are_offered(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")

        response = self.open_form("replace")
        ids = set(
            response.context["edit_form"]["teachers"].values_list(
                "pk", flat=True
            )
        )

        self.assertEqual(ids, {self.teacher.pk, self.teacher2.pk})
        self.assertNotIn(legacy.pk, ids)

    def test_teacher_without_account_is_rejected(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")

        response = self.post("replace", new_teacher=legacy.pk)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_replace_teacher(self):
        response = self.post("replace", new_teacher=self.teacher2.pk)

        self.assertRedirects(
            response, self.schedule_url(), fetch_redirect_response=False
        )

        change = ScheduleChange.objects.get()
        self.assertEqual(change.change_type, "REPLACE")
        self.assertEqual(change.new_teacher_id, self.teacher2.pk)

        lesson = self.group_lesson(0, 1)
        self.assertEqual(lesson["status"], "replace")
        self.assertEqual(lesson["teacher"], "Петров П. П.")
        self.assertEqual(lesson["subject"], "Математика")

    def test_replace_name_only(self):
        self.post("replace", new_pair_name="  Алгебра   и начала ")

        lesson = self.group_lesson(0, 1)
        self.assertEqual(lesson["subject"], "Алгебра и начала")
        self.assertEqual(lesson["teacher"], "Иванов И. И.")

    def test_replacement_shows_on_teacher_pages(self):
        self.post("replace", new_teacher=self.teacher2.pk)

        new = self.teacher_lessons(self.teacher2, 0, 1)
        self.assertEqual(new[0]["status"], "replace-in")
        self.assertEqual(new[0]["group"].pk, self.group.pk)

        old = self.teacher_lessons(self.teacher, 0, 1)
        self.assertEqual(old[0]["status"], "replace-out")

    def test_nothing_to_replace_is_an_error(self):
        response = self.post("replace")

        # Ошибка — сообщением, форма остаётся раскрытой
        self.assertRedirects(
            response,
            self.schedule_url(act="replace", slot="2026-10-19", number=1),
            fetch_redirect_response=False,
        )
        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_error_is_shown_on_page_with_open_form(self):
        response = self.client.post(
            reverse("group_change", args=[self.group.pk]),
            {
                "kind": "replace", "date": "2026-10-19", "number": 1,
                "new_pair_name": "  ",
            },
            follow=True,
        )

        self.assertContains(response, "Выберите другого преподавателя")
        self.assertContains(response, 'name="new_teacher"')
        self.assertIsNotNone(response.context["edit_form"])

    def test_same_values_are_not_a_replacement(self):
        response = self.post(
            "replace",
            new_teacher=self.teacher.pk,
            new_pair_name="Математика",
        )

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_busy_teacher_is_rejected(self):
        self.busy_teacher2_on_monday()
        response = self.post("replace", new_teacher=self.teacher2.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn("Б-02", self.errors(response)[0])
        self.assertFalse(ScheduleChange.objects.exists())

    def test_teacher_busy_group_can_be_freed_by_cancel(self):
        self.busy_teacher2_on_monday()

        other_group = Group.objects.get(group_name="Б-02")
        self.client.post(
            reverse("group_change", args=[other_group.pk]),
            {"kind": "cancel", "date": "2026-10-19", "number": 1},
        )

        self.post("replace", new_teacher=self.teacher2.pk)
        self.assertEqual(
            ScheduleChange.objects.filter(change_type="REPLACE").count(), 1
        )

    def test_unknown_teacher_is_rejected(self):
        response = self.post("replace", new_teacher=99999)

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_replacing_cancel_with_replace_keeps_one_change(self):
        self.post("cancel")
        self.post("replace", new_teacher=self.teacher2.pk)

        change = ScheduleChange.objects.get()
        self.assertEqual(change.change_type, "REPLACE")

    def test_form_remembers_current_replacement(self):
        self.post("replace", new_teacher=self.teacher2.pk)

        response = self.open_form("replace")

        self.assertEqual(
            response.context["edit_form"]["values"]["new_teacher"],
            self.teacher2.pk,
        )

    def test_form_keeps_typed_values_after_error(self):
        response = self.post(
            "replace",
            new_teacher=self.teacher2.pk,
            new_pair_name="x" * 151,
        )

        page = self.client.get(response.url)
        values = page.context["edit_form"]["values"]

        self.assertEqual(values["new_teacher"], str(self.teacher2.pk))
        self.assertEqual(values["new_pair_name"], "x" * 151)


class MoveTests(ChangeTestCase):
    def test_form_opens_under_the_pair(self):
        response = self.open_form("move")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="new_date"')
        self.assertContains(response, 'name="new_number"')
        self.assertNotContains(response, 'name="new_teacher"')
        self.assertTemplateUsed(response, "days.html")

    def test_only_one_form_is_open(self):
        response = self.open_form("move")
        html = response.content.decode()

        self.assertEqual(html.count('class="change-inline"'), 1)

    def test_form_is_not_opened_for_empty_slot_or_bad_link(self):
        for params in (
            {"act": "move", "slot": "2026-10-19", "number": 2},
            {"act": "move", "slot": "2026-10-19", "number": 9},
            {"act": "move", "slot": "oops", "number": 1},
            {"act": "explode", "slot": "2026-10-19", "number": 1},
            {"act": "move", "slot": "2026-11-30", "number": 1},
        ):
            with self.subTest(params=params):
                response = self.client.get(
                    self.schedule_url(**params)
                )

                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["edit_form"])
                self.assertNotContains(response, 'class="change-inline"')

    def test_form_is_not_opened_for_moved_in_pair(self):
        self.post("move", new_date="2026-10-20", new_number=2)

        response = self.open_form("move", number=2, day="2026-10-20")

        self.assertIsNone(response.context["edit_form"])

    def test_old_change_page_address_redirects_to_edit_mode(self):
        response = self.client.get(
            reverse("group_change", args=[self.group.pk]),
            {"kind": "move", "date": "2026-10-19", "number": 1},
        )

        self.assertRedirects(
            response,
            self.schedule_url(act="move", slot="2026-10-19", number=1),
            fetch_redirect_response=False,
        )

        response = self.client.get(
            reverse("group_change", args=[self.group.pk]),
            {"kind": "cancel", "date": "2026-10-19", "number": 1},
        )

        self.assertRedirects(
            response, self.schedule_url(), fetch_redirect_response=False
        )
        self.assertFalse(ScheduleChange.objects.exists())

    def test_form_remembers_current_move(self):
        self.post("move", new_date="2026-10-20", new_number=3)

        values = self.open_form("move").context["edit_form"]["values"]

        self.assertEqual(values["new_date"], "2026-10-20")
        self.assertEqual(values["new_number"], 3)

    def test_form_keeps_typed_values_after_error(self):
        response = self.post("move", new_date="2026-10-20", new_number=9)

        page = self.client.get(response.url)
        values = page.context["edit_form"]["values"]

        self.assertEqual(values["new_date"], "2026-10-20")
        self.assertEqual(values["new_number"], "9")

    def test_move_to_another_day(self):
        response = self.post("move", new_date="2026-10-20", new_number=2)

        self.assertRedirects(
            response, self.schedule_url(), fetch_redirect_response=False
        )

        change = ScheduleChange.objects.get()
        self.assertEqual(change.change_type, "MOVE")
        self.assertEqual(change.calendar.date, MONDAY)
        self.assertEqual(change.new_calendar.date, TUESDAY)
        self.assertEqual(change.new_pair_number, 2)

        self.assertEqual(self.group_lesson(0, 1)["status"], "moved-from")

        moved = self.group_lesson(1, 2)
        self.assertEqual(moved["status"], "moved-to")
        self.assertEqual(moved["subject"], "Математика")
        self.assertFalse(moved["editable"])

        # Постоянное расписание не тронуто
        self.assertEqual(Day.objects.filter(group=self.group).count(), 1)

    def test_moved_pair_shows_on_teacher_page(self):
        self.post("move", new_date="2026-10-20", new_number=2)

        lessons = self.teacher_lessons(self.teacher, 1, 2)
        self.assertEqual(lessons[0]["status"], "moved-to")

        old = self.teacher_lessons(self.teacher, 0, 1)
        self.assertEqual(old[0]["status"], "moved-from")

    def test_move_within_same_day(self):
        self.post("move", new_date="2026-10-19", new_number=3)

        self.assertEqual(self.group_lesson(0, 1)["status"], "moved-from")
        self.assertEqual(self.group_lesson(0, 3)["status"], "moved-to")

    def test_move_to_same_slot_is_rejected(self):
        response = self.post("move", new_date="2026-10-19", new_number=1)

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_move_to_busy_slot_of_group_is_rejected(self):
        Day.objects.create(
            group=self.group,
            pair=self.pair,
            pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )

        response = self.post("move", new_date="2026-10-20", new_number=2)

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_move_to_slot_freed_by_cancel_is_allowed(self):
        Day.objects.create(
            group=self.group,
            pair=self.pair,
            pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )
        self.post("cancel", number=2, day="2026-10-20")

        self.post("move", new_date="2026-10-20", new_number=2)

        self.assertEqual(
            ScheduleChange.objects.filter(change_type="MOVE").count(), 1
        )

    def test_move_to_sunday_is_rejected(self):
        response = self.post("move", new_date="2026-10-25", new_number=1)

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_move_to_holiday_is_rejected(self):
        Calendar.objects.create(
            date=TUESDAY, weekday=2, is_holiday=True, holiday_name="Праздник"
        )
        response = self.post("move", new_date="2026-10-20", new_number=2)

        self.assertTrue(self.errors(response))
        self.assertFalse(ScheduleChange.objects.exists())

    def test_move_when_teacher_is_busy_is_rejected(self):
        # Иванов ведёт у другой группы во вторник, пара 2
        other_group = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )
        other_pair = make_pair(other_group, self.teacher, "Алгебра")
        Day.objects.create(
            group=other_group,
            pair=other_pair,
            pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )

        response = self.post("move", new_date="2026-10-20", new_number=2)

        self.assertIn("Б-02", self.errors(response)[0])
        self.assertFalse(ScheduleChange.objects.exists())

    def test_bad_number_and_date_are_rejected(self):
        response = self.post("move", new_date="2026-10-20", new_number=9)
        self.assertTrue(self.errors(response))

        response = self.post("move", new_date="", new_number=2)
        self.assertTrue(self.errors(response))

        self.assertFalse(ScheduleChange.objects.exists())

    def test_moved_in_pair_cannot_be_edited_on_new_day(self):
        self.post("move", new_date="2026-10-20", new_number=2)

        response = self.post("cancel", number=2, day="2026-10-20")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ScheduleChange.objects.count(), 1)

    def test_restore_returns_moved_pair(self):
        self.post("move", new_date="2026-10-20", new_number=2)
        self.post("restore")

        self.assertFalse(ScheduleChange.objects.exists())
        self.assertEqual(self.group_lesson(0, 1)["status"], "")
        self.assertIsNone(self.group_lesson(1, 2))


class WorkshopEditTests(BaseTestCase):
    """Изменение и удаление записей в Кузне (режим редактирования)."""

    def setUp(self):
        self.client.force_login(self.admin)

    def edit_url(self, key, item=None, **extra):
        query = {"edit": key, **extra}

        if item is not None:
            query["item"] = item

        return reverse("workshop") + "?" + "&".join(
            f"{name}={value}" for name, value in query.items()
        )

    def post(self, action, key, item, **fields):
        data = {"action": action, "edit": key, "item": item}
        data.update(fields)

        return self.client.post(reverse("workshop"), data, follow=True)

    def group_fields(self, **overrides):
        data = {
            "group-group_name": "А-01",
            "group-group_course": 2,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.teacher.pk,
        }
        data.update(overrides)

        return data

    def second_group_with_pair(self):
        other_teacher = make_teacher("Петров П. П.", "teacherpetrov@gmail.com")
        other_group = Group.objects.create(
            group_name="Б-02",
            group_course=self.course,
            shift=self.shift,
        )
        other_pair = make_pair(other_group, other_teacher, "Физика")
        Day.objects.create(
            group=other_group,
            pair=other_pair,
            pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )

        return other_teacher, other_group, other_pair

    # --- вход в режим и выбор записи --------------------------------------

    def test_edit_button_is_under_add_in_every_card(self):
        response = self.client.get(reverse("workshop"))

        for key in ("group_tag", "teacher_tag", "group", "pair", "semester"):
            self.assertContains(response, f"edit={key}")
            self.assertFalse(response.context["cards"][key]["editing"])

        self.assertNotContains(response, 'value="update_item"')
        self.assertNotContains(response, 'value="delete_item"')

    def test_edit_mode_lists_existing_items_instead_of_form(self):
        response = self.client.get(self.edit_url("group"))
        card = response.context["cards"]["group"]

        self.assertTrue(card["editing"])
        self.assertIsNone(card["item"])
        self.assertEqual(
            [entry["label"] for entry in card["items"]], ["А-01"]
        )

        # Пока запись не выбрана, есть только «Отменить»
        self.assertContains(response, "Отменить")
        self.assertNotContains(response, 'value="update_item"')
        self.assertNotContains(response, 'value="delete_item"')

    def test_choosing_group_shows_its_settings(self):
        self.group.group_curator = self.teacher
        self.group.save()

        response = self.client.get(self.edit_url("group", self.group.pk))
        card = response.context["cards"]["group"]

        self.assertEqual(card["item"].pk, self.group.pk)
        self.assertEqual(card["form"]["group_name"].value(), "А-01")
        self.assertEqual(
            card["form"]["group_curator"].value(), self.teacher.pk
        )
        self.assertEqual(card["form"]["shift"].value(), self.shift.pk)

        self.assertContains(response, 'value="update_item"')
        self.assertContains(response, 'value="delete_item"')
        self.assertContains(response, "Отменить")

    def test_only_one_form_is_in_edit_mode(self):
        response = self.client.get(self.edit_url("group", self.group.pk))
        cards = response.context["cards"]

        self.assertTrue(cards["group"]["editing"])

        for key in ("group_tag", "teacher_tag", "pair"):
            self.assertFalse(cards[key]["editing"])

        self.assertEqual(response.content.count(b'value="update_item"'), 1)

    def test_unknown_edit_key_is_ignored(self):
        response = self.client.get(self.edit_url("user"))

        self.assertEqual(response.status_code, 200)

        for card in response.context["cards"].values():
            self.assertFalse(card["editing"])

    def test_missing_item_is_reported(self):
        response = self.client.get(self.edit_url("group", 9999))

        self.assertContains(response, "Запись не найдена")
        self.assertIsNone(response.context["cards"]["group"]["item"])

    def test_cancel_link_leaves_edit_mode(self):
        response = self.client.get(self.edit_url("group", self.group.pk))
        cancel = response.context["cards"]["group"]["cancel_url"]

        self.assertNotIn("edit=", cancel)

        page = self.client.get(cancel)
        self.assertFalse(page.context["cards"]["group"]["editing"])

    def test_bell_settings_are_kept_in_edit_links(self):
        response = self.client.get(
            self.edit_url("group", time_shift=2, time_mode="break")
        )

        self.assertEqual(response.context["selected_shift_id"], 2)
        self.assertEqual(response.context["selected_time_mode"], "break")
        self.assertIn(
            "time_shift=2", response.context["cards"]["group"]["cancel_url"]
        )

    # --- изменение ---------------------------------------------------------

    def test_update_group_curator_course_and_shift(self):
        second_shift = Shift.objects.get_or_create(shift_id=2)[0]

        response = self.post(
            "update_item", "group", self.group.pk,
            **self.group_fields(**{"group-shift": second_shift.pk}),
        )

        self.assertContains(response, "Изменения сохранены")

        group = Group.objects.get(pk=self.group.pk)
        self.assertEqual(group.group_course_id, 2)
        self.assertEqual(group.shift_id, second_shift.pk)
        self.assertEqual(group.group_curator_id, self.teacher.pk)

        # После сохранения запись остаётся выбранной, форма показывает новое
        card = response.context["cards"]["group"]
        self.assertEqual(card["item"].pk, self.group.pk)
        self.assertEqual(card["form"]["group_course"].value(), 2)

    def test_update_with_empty_name_is_rejected(self):
        response = self.post(
            "update_item", "group", self.group.pk,
            **self.group_fields(**{"group-group_name": ""}),
        )

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertEqual(Group.objects.get(pk=self.group.pk).group_name, "А-01")

        card = response.context["cards"]["group"]
        self.assertTrue(card["editing"])
        self.assertTrue(card["form"].errors)

    def test_pair_groups_can_be_attached_and_detached(self):
        _, other_group, _ = self.second_group_with_pair()

        self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Алгебра",
                "pair-groups": [self.group.pk, other_group.pk],
                "pair-teacher": self.teacher.pk,
            },
        )

        pair = Pair.objects.get(pk=self.pair.pk)
        self.assertEqual(pair.pair_name, "Алгебра")
        self.assertEqual(
            set(pair.groups.values_list("pk", flat=True)),
            {self.group.pk, other_group.pk},
        )

    def test_pair_cannot_be_detached_from_group_where_it_is_scheduled(self):
        _, other_group, _ = self.second_group_with_pair()

        response = self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Математика",
                "pair-groups": [other_group.pk],
                "pair-teacher": self.teacher.pk,
            },
        )

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertContains(response, "стоит в расписании группы А-01")
        self.assertTrue(
            PairGroup.objects.filter(pair=self.pair, group=self.group).exists()
        )

    def test_pair_teacher_change_warns_about_schedule_conflict(self):
        other_teacher, _, _ = self.second_group_with_pair()

        # Понедельник, пара 1: у Петрова уже стоит Физика у группы Б-02
        response = self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Математика",
                "pair-groups": self.group.pk,
                "pair-teacher": other_teacher.pk,
            },
        )

        self.assertContains(response, "Изменения сохранены")
        self.assertContains(response, "Совпадение у преподавателя")
        self.assertContains(response, "в это же время ведёт занятие у группы Б-02")
        self.assertEqual(
            Pair.objects.get(pk=self.pair.pk).teacher_id, other_teacher.pk
        )

    def test_pair_teacher_change_without_conflict(self):
        other_teacher = make_teacher("Сидоров С. С.", "teachersidorov@gmail.com")

        self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Математика",
                "pair-groups": self.group.pk,
                "pair-teacher": other_teacher.pk,
            },
        )

        self.assertEqual(
            Pair.objects.get(pk=self.pair.pk).teacher_id, other_teacher.pk
        )

    # --- удаление ----------------------------------------------------------

    def test_delete_asks_for_confirmation_first(self):
        set_group(self.user, self.group)

        response = self.post("delete_item", "group", self.group.pk)

        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

        card = response.context["cards"]["group"]
        self.assertTrue(card["confirm"])
        self.assertIn("привязки пар — 1", card["confirm_text"])
        self.assertIn("занятия в расписании — 1", card["confirm_text"])
        self.assertIn("Сами пары останутся", card["confirm_text"])
        self.assertIn("Студенты (1)", card["confirm_text"])
        self.assertContains(response, 'value="delete_confirmed"')
        self.assertNotContains(response, 'value="update_item"')

    def test_confirmed_delete_removes_group_with_its_schedule(self):
        set_group(self.user, self.group)

        response = self.post("delete_confirmed", "group", self.group.pk)

        self.assertContains(response, "Удалено: А-01.")
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())
        # Пара самостоятельная: остаётся, исчезает только привязка
        self.assertTrue(Pair.objects.filter(pk=self.pair.pk).exists())
        self.assertFalse(PairGroup.objects.filter(pair=self.pair).exists())
        self.assertFalse(Day.objects.filter(group_id=self.group.pk).exists())

        # Студент остаётся, но без группы
        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertIsNone(own_group_id(user))

        # Режим редактирования остаётся: можно удалять дальше
        card = response.context["cards"]["group"]
        self.assertTrue(card["editing"])
        self.assertIsNone(card["item"])

    def test_cancel_in_confirmation_returns_to_item(self):
        response = self.post("delete_item", "group", self.group.pk)
        back = response.context["cards"]["group"]["back_url"]

        page = self.client.get(back)

        self.assertFalse(page.context["cards"]["group"]["confirm"])
        self.assertEqual(
            page.context["cards"]["group"]["item"].pk, self.group.pk
        )

    def test_tag_is_deleted_and_removed_from_teacher(self):
        tag = TeacherTag.objects.create(tag_name="Очное")
        self.teacher.teacher_tag = tag
        self.teacher.save()

        response = self.post("delete_item", "teacher_tag", tag.pk)
        self.assertIn(
            "преподавателей: 1",
            response.context["cards"]["teacher_tag"]["confirm_text"],
        )

        self.post("delete_confirmed", "teacher_tag", tag.pk)

        self.assertFalse(TeacherTag.objects.filter(pk=tag.pk).exists())
        self.assertIsNone(Teacher.objects.get(pk=self.teacher.pk).teacher_tag_id)

    def test_group_tag_is_deleted_and_removed_from_group(self):
        tag = GroupTag.objects.create(tag_name="Очное")
        self.group.group_tag = tag
        self.group.save()

        response = self.post("delete_item", "group_tag", tag.pk)
        self.assertIn(
            "у групп: 1", response.context["cards"]["group_tag"]["confirm_text"]
        )

        self.post("delete_confirmed", "group_tag", tag.pk)

        self.assertFalse(GroupTag.objects.filter(pk=tag.pk).exists())
        self.assertIsNone(Group.objects.get(pk=self.group.pk).group_tag_id)

    def test_pair_delete_removes_its_lessons(self):
        self.post("delete_confirmed", "pair", self.pair.pk)

        self.assertFalse(Pair.objects.filter(pk=self.pair.pk).exists())
        self.assertFalse(Day.objects.filter(group=self.group).exists())
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_delete_of_missing_item_does_nothing(self):
        response = self.post("delete_confirmed", "group", 9999)

        self.assertContains(response, "Запись не найдена")
        self.assertEqual(Group.objects.count(), 1)

    # --- права -------------------------------------------------------------

    def test_student_cannot_edit_or_delete(self):
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(self.edit_url("group")).status_code, 403
        )

        response = self.post("delete_confirmed", "group", self.group.pk)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Group.objects.filter(pk=self.group.pk).exists())

    def test_adding_still_works(self):
        response = self.client.post(reverse("workshop"), {
            "action": "add_group_tag",
            "group_tag-tag_name": "Очное",
        }, follow=True)

        self.assertContains(response, "Запись добавлена.")
        self.assertTrue(GroupTag.objects.filter(tag_name="Очное").exists())


class WorkshopRedirectTests(BaseTestCase):
    """После успешного действия Кузня переходит на обычную страницу (GET)."""

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("workshop")

    def post(self, data, **extra):
        return self.client.post(self.url, data, **extra)

    def add_tag_data(self, name="Очное"):
        return {
            "action": "add_group_tag", "group_tag-tag_name": name,
            "time_shift": 2, "time_mode": "break",
        }

    def test_successful_add_redirects_and_keeps_shift_and_mode(self):
        response = self.post(self.add_tag_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], f"{self.url}?time_shift=2&time_mode=break"
        )
        self.assertTrue(GroupTag.objects.filter(tag_name="Очное").exists())

    def test_reload_after_add_does_not_add_again_and_form_is_empty(self):
        self.post(self.add_tag_data())

        first = self.client.get(self.url)
        second = self.client.get(self.url)

        self.assertContains(first, "Запись добавлена.")
        self.assertNotContains(second, "Запись добавлена.")
        self.assertEqual(GroupTag.objects.count(), 1)
        self.assertNotContains(second, 'value="Очное"')

    def test_message_is_shown_once_after_redirect(self):
        response = self.post(self.add_tag_data(), follow=True)

        self.assertContains(response, "Запись добавлена.", count=1)

    def test_add_forms_switch_browser_autofill_off(self):
        html = self.client.get(self.url).content.decode()

        self.assertGreaterEqual(html.count('method="post" autocomplete="off"'), 4)

    def test_error_keeps_the_page_and_the_typed_values(self):
        GroupTag.objects.create(tag_name="Очное")

        response = self.post(self.add_tag_data("очное"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertContains(response, 'value="очное"')

    def test_update_redirects_back_to_the_same_item(self):
        tag = TeacherTag.objects.create(tag_name="Высшая")

        response = self.post({
            "action": "update_item", "edit": "teacher_tag", "item": tag.pk,
            "teacher_tag-tag_name": "Первая", "teacher_tag-tag_color": "#112233",
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn("edit=teacher_tag", response["Location"])
        self.assertIn(f"item={tag.pk}", response["Location"])

    def test_delete_redirects_to_the_picker_without_item(self):
        tag = TeacherTag.objects.create(tag_name="Высшая")

        response = self.post({
            "action": "delete_confirmed", "edit": "teacher_tag",
            "item": tag.pk,
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn("edit=teacher_tag", response["Location"])
        self.assertNotIn("item=", response["Location"])

    def test_saving_bells_redirects(self):
        response = self.post({
            "action": "save_times", "time_shift": 1, "time_mode": "study",
            "time_1_start": "08:00", "time_1_end": "08:45",
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn("time_shift=1", response["Location"])

    def test_show_time_and_import_do_not_redirect(self):
        response = self.post({
            "action": "show_time", "time_shift": 1, "time_mode": "study",
        })

        self.assertEqual(response.status_code, 200)


class CsvTests(BaseTestCase):
    """Импорт и экспорт CSV в Кузне: файл экспорта читается импортом."""

    ORDER = ("bells", "teachers", "groups", "pairs", "schedule")

    def setUp(self):
        self.client.force_login(self.admin)

    def export(self, kind, **extra):
        return self.client.get(
            reverse("workshop_export"), {"kind": kind, **extra}
        )

    def text(self, kind):
        return self.export(kind).content.decode("utf-8-sig")

    def lines(self, kind):
        return self.text(kind).splitlines()

    def upload(self, kind, content, dry_run=False, encoding="utf-8-sig"):
        data = content.encode(encoding) if isinstance(content, str) else content
        post = {
            "action": "import_csv",
            "kind": kind,
            "file": SimpleUploadedFile(
                "data.csv", data, content_type="text/csv"
            ),
        }

        if dry_run:
            post["dry_run"] = "1"

        return self.client.post(reverse("workshop"), post, follow=True)

    def report(self, response):
        return response.context["import_report"]

    def fill_extra_data(self):
        teacher_tag = TeacherTag.objects.create(tag_name="Очное")
        group_tag = GroupTag.objects.create(tag_name="Очное")

        self.teacher.teacher_tag = teacher_tag
        self.teacher.save()
        self.group.group_curator = self.teacher
        self.group.group_tag = group_tag
        self.group.save()

        BreakHours.objects.create(
            shift=self.shift,
            break_number=1,
            break_start_time=time(10, 0),
            break_end_time=time(10, 10),
        )

    def second_pair(self, name="Физика"):
        return make_pair(self.group, self.teacher, name)

    # --- экспорт -----------------------------------------------------------

    def test_export_is_excel_friendly(self):
        response = self.export("teachers")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="raspisite_teachers.csv"',
        )
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.lines("teachers")[0], "ФИО;Тег")

    def test_export_rows_of_every_kind(self):
        self.assertEqual(self.lines("teachers")[1], "Иванов И. И.;")
        self.assertEqual(self.lines("groups")[1], "А-01;1;1;;")
        self.assertEqual(
            self.lines("pairs")[1], "А-01;Математика;Иванов И. И."
        )
        self.assertEqual(
            self.lines("schedule")[1],
            "А-01;Понедельник;1;Математика;Иванов И. И.",
        )
        self.assertEqual(self.lines("bells")[1], "Пара;1;1;08:30;10:00")

    def test_template_has_only_headers(self):
        response = self.export("groups", template=1)

        self.assertEqual(
            response.content.decode("utf-8-sig").splitlines(),
            ["Название;Курс;Смена;Куратор;Тег"],
        )
        self.assertIn("_template", response["Content-Disposition"])

    def test_unknown_kind_is_404(self):
        self.assertEqual(self.export("users").status_code, 404)
        self.assertEqual(
            self.client.get(reverse("workshop_export")).status_code, 404
        )

    def test_student_cannot_export_or_import(self):
        self.client.force_login(self.user)

        self.assertEqual(self.export("teachers").status_code, 403)
        self.assertEqual(
            self.upload("teachers", "ФИО;Тег\r\nХ;\r\n").status_code, 403
        )

    def test_card_is_in_workshop(self):
        response = self.client.get(reverse("workshop"))

        self.assertContains(response, "Импорт и экспорт")
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="file"')
        self.assertContains(response, reverse("workshop_export"))

    # --- круг: экспорт -> импорт ------------------------------------------

    def test_export_can_be_imported_into_empty_database(self):
        self.fill_extra_data()
        # Новая группа при загрузке должна называться по образцу (ПО-101)
        self.group.group_name = "ПО-101"
        self.group.save()
        files = {kind: self.text(kind) for kind in self.ORDER}

        Day.objects.all().delete()
        PairGroup.objects.all().delete()
        Pair.objects.all().delete()
        Group.objects.all().delete()
        Teacher.objects.all().delete()
        GroupTag.objects.all().delete()
        TeacherTag.objects.all().delete()
        StudyHours.objects.all().delete()
        BreakHours.objects.all().delete()

        for kind in self.ORDER:
            report = self.report(self.upload(kind, files[kind]))

            self.assertEqual(report["fatal"], "", kind)
            self.assertEqual(report["errors"], [], kind)
            self.assertTrue(report["saved"], kind)

        teacher = Teacher.objects.get(teacher_name="Иванов И. И.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Очное")

        group = Group.objects.get(group_name="ПО-101")
        self.assertEqual(group.group_course_id, 1)
        self.assertEqual(group.shift_id, 1)
        self.assertEqual(group.group_curator_id, teacher.pk)
        self.assertEqual(group.group_tag.tag_name, "Очное")
        self.assertEqual(TeacherTag.objects.count(), 1)
        self.assertEqual(GroupTag.objects.count(), 1)

        day = Day.objects.get()
        self.assertEqual(day.pair.pair_name, "Математика")
        self.assertEqual(
            list(day.pair.groups.values_list("group_name", flat=True)),
            ["ПО-101"],
        )
        self.assertEqual((day.weekday_id, day.pair_number), (1, 1))

        self.assertEqual(
            StudyHours.objects.get().pair_start_time, time(8, 30)
        )
        self.assertEqual(
            BreakHours.objects.get().break_end_time, time(10, 10)
        )

    def test_importing_own_export_changes_nothing(self):
        self.fill_extra_data()

        for kind in self.ORDER:
            report = self.report(self.upload(kind, self.text(kind)))

            self.assertEqual(report["errors"], [], kind)
            self.assertEqual(report["created"], 0, kind)
            self.assertEqual(report["updated"], 0, kind)
            self.assertGreaterEqual(report["unchanged"], 1, kind)

    # --- режим «только проверить» и ошибки --------------------------------

    def test_dry_run_saves_nothing(self):
        response = self.upload(
            "teachers", "ФИО;Тег\r\nНовый Н. Н.;\r\n", dry_run=True
        )
        report = self.report(response)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["created"], 1)
        self.assertFalse(report["saved"])
        self.assertContains(response, "Добавилось бы")
        self.assertFalse(
            Teacher.objects.filter(teacher_name="Новый Н. Н.").exists()
        )

    def test_one_bad_row_saves_nothing(self):
        response = self.upload(
            "teachers",
            "ФИО;Тег\r\nХороший Х. Х.;\r\nПлохой П. П.;" + "Т" * 101 + "\r\n",
        )
        report = self.report(response)

        self.assertEqual(len(report["errors"]), 1)
        self.assertTrue(report["errors"][0].startswith("Строка 3:"))
        self.assertFalse(report["saved"])
        self.assertContains(response, "Ничего не сохранено")
        self.assertFalse(
            Teacher.objects.filter(teacher_name="Хороший Х. Х.").exists()
        )

    def test_wrong_header_is_rejected(self):
        response = self.upload("teachers", "Имя;Тег\r\nХ;\r\n")
        self.assertIn("заголовок файла не подходит", self.report(response)["fatal"])

        # Файл другого набора и файл с лишним столбцом
        response = self.upload("teachers", self.text("groups"))
        self.assertIn("заголовок файла не подходит", self.report(response)["fatal"])

        response = self.upload("teachers", "ФИО;Тег;Лишний\r\nХ;;\r\n")
        self.assertIn("лишние столбцы", self.report(response)["fatal"])

    def test_garbage_file_is_rejected(self):
        response = self.upload("teachers", b"\x00\x01\x02 not a csv")

        self.assertTrue(self.report(response)["fatal"])
        self.assertEqual(Teacher.objects.count(), 1)

    def test_missing_and_empty_file(self):
        response = self.client.post(reverse("workshop"), {
            "action": "import_csv", "kind": "teachers",
        })
        self.assertEqual(self.report(response)["fatal"], "выберите файл CSV")
        self.assertContains(response, "Выберите файл CSV.")

        response = self.upload("teachers", b"")
        self.assertTrue(self.report(response)["fatal"])

        response = self.upload("teachers", "ФИО;Тег\r\n")
        self.assertEqual(
            self.report(response)["fatal"], "в файле нет строк с данными"
        )

    def test_unknown_kind_is_reported(self):
        response = self.upload("users", "ФИО;Тег\r\nХ;\r\n")

        self.assertEqual(
            self.report(response)["fatal"], "неизвестный набор данных"
        )

    def test_duplicate_rows_are_reported(self):
        response = self.upload(
            "teachers", "ФИО;Тег\r\nПовтор П. П.;\r\nПовтор П. П.;\r\n"
        )

        self.assertIn("повторяет строку 2", self.report(response)["errors"][0])
        self.assertFalse(
            Teacher.objects.filter(teacher_name="Повтор П. П.").exists()
        )

    # --- форматы файла -----------------------------------------------------

    def test_windows_1251_and_comma_are_understood(self):
        response = self.upload(
            "teachers", "ФИО,Тег\r\nСидоров С. С.,\r\n", encoding="cp1251"
        )

        self.assertEqual(self.report(response)["errors"], [])
        self.assertTrue(
            Teacher.objects.filter(teacher_name="Сидоров С. С.").exists()
        )

    def test_empty_columns_added_by_excel_are_ignored(self):
        response = self.upload(
            "teachers", "ФИО;Тег;;\r\nСидоров С. С.;;;\r\n;;;\r\n"
        )

        self.assertEqual(self.report(response)["errors"], [])
        self.assertEqual(self.report(response)["created"], 1)

    def test_columns_may_be_in_any_order(self):
        response = self.upload(
            "teachers", "Тег;ФИО\r\nОчное;Сидоров С. С.\r\n"
        )

        self.assertEqual(self.report(response)["errors"], [])
        teacher = Teacher.objects.get(teacher_name="Сидоров С. С.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Очное")

    # --- содержимое наборов ------------------------------------------------

    def test_tag_is_created_from_name(self):
        self.upload("teachers", "ФИО;Тег\r\nНовый Н. Н.;Заочное\r\n")

        teacher = Teacher.objects.get(teacher_name="Новый Н. Н.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Заочное")
        self.assertTrue(TeacherTag.objects.filter(tag_name="Заочное").exists())
        self.assertFalse(GroupTag.objects.exists())

    def test_existing_tag_is_reused_by_name(self):
        tag = TeacherTag.objects.create(tag_name="Очное")

        self.upload(
            "teachers", "ФИО;Тег\r\nПервый П. П.;очное\r\n"
        )

        teacher = Teacher.objects.get(teacher_name="Первый П. П.")
        self.assertEqual(teacher.teacher_tag_id, tag.pk)
        self.assertEqual(TeacherTag.objects.count(), 1)

    def test_group_tag_of_same_name_is_a_different_tag(self):
        # Тег группы «Очное» не подходит преподавателю: таблицы раздельны
        group_tag = GroupTag.objects.create(tag_name="Очное")

        self.upload("teachers", "ФИО;Тег\r\nВторой В. В.;Очное\r\n")

        teacher = Teacher.objects.get(teacher_name="Второй В. В.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Очное")
        self.assertEqual(TeacherTag.objects.count(), 1)
        self.assertEqual(GroupTag.objects.get().pk, group_tag.pk)

    def test_export_has_only_the_tag_name(self):
        self.fill_extra_data()

        self.assertEqual(
            self.lines("teachers")[1], "Иванов И. И.;Очное"
        )

    def test_group_is_updated_by_name(self):
        response = self.upload(
            "groups", "Название;Курс;Смена;Куратор;Тег\r\n"
            "А-01;2;1;Иванов И. И.;\r\n"
        )
        report = self.report(response)

        self.assertEqual((report["created"], report["updated"]), (0, 1))

        group = Group.objects.get(group_name="А-01")
        self.assertEqual(group.group_course_id, 2)
        self.assertEqual(group.group_curator_id, self.teacher.pk)

    def test_group_references_are_checked(self):
        response = self.upload(
            "groups", "Название;Курс;Смена;Куратор;Тег\r\n"
            "Б-02;9;1;;\r\n"
            "В-03;1;1;Неизвестный;\r\n"
            "Г-04;1;7;;\r\n"
        )
        errors = self.report(response)["errors"]

        self.assertEqual(len(errors), 3)
        self.assertIn("курса 9 нет", errors[0])
        self.assertIn("преподаватель «Неизвестный» не найден", errors[1])
        self.assertIn("смены 7 нет", errors[2])
        self.assertEqual(Group.objects.count(), 1)

    def test_pairs_add_missing_and_keep_existing(self):
        response = self.upload(
            "pairs", "Группа;Предмет;Преподаватель\r\n"
            "А-01;Математика;Иванов И. И.\r\n"
            "А-01;Физика;Иванов И. И.\r\n"
        )
        report = self.report(response)

        self.assertEqual((report["created"], report["unchanged"]), (1, 1))
        self.assertEqual(Pair.objects.filter(groups=self.group).count(), 2)

    def test_shared_pair_is_one_pair_with_two_groups(self):
        other = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )

        self.upload(
            "pairs", "Группа;Предмет;Преподаватель\r\n"
            "А-01;Математика;Иванов И. И.\r\n"
            "Б-02;Математика;Иванов И. И.\r\n"
        )

        self.assertEqual(Pair.objects.filter(pair_name="Математика").count(), 1)
        self.assertEqual(self.pair.groups.count(), 2)
        self.assertTrue(PairGroup.objects.filter(pair=self.pair, group=other).exists())

    def test_pair_without_group_is_exported_and_imported(self):
        Pair.objects.create(teacher=self.teacher, pair_name="Свободная")

        self.assertIn(";Свободная;Иванов И. И.", self.text("pairs"))

        Pair.objects.filter(pair_name="Свободная").delete()
        report = self.report(self.upload(
            "pairs", "Группа;Предмет;Преподаватель\r\n"
            ";Свободная;Иванов И. И.\r\n"
        ))

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["created"], 1)
        self.assertEqual(Pair.objects.get(pair_name="Свободная").groups.count(), 0)

    def test_export_has_row_per_group_of_shared_pair(self):
        other = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        PairGroup.objects.create(pair=self.pair, group=other)

        lines = self.lines("pairs")

        self.assertIn("А-01;Математика;Иванов И. И.", lines)
        self.assertIn("Б-02;Математика;Иванов И. И.", lines)

    def test_pairs_need_existing_group_and_teacher(self):
        response = self.upload(
            "pairs", "Группа;Предмет;Преподаватель\r\n"
            "Нет;Физика;Иванов И. И.\r\n"
            "А-01;Физика;Нет Н. Н.\r\n"
        )

        self.assertEqual(len(self.report(response)["errors"]), 2)

    def test_schedule_fills_empty_cells(self):
        self.second_pair()

        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "А-01;Вторник;2;Физика;Иванов И. И.\r\n"
            "А-01;3;1;Физика;Иванов И. И.\r\n"
        )

        self.assertEqual(self.report(response)["errors"], [])
        self.assertEqual(self.report(response)["created"], 2)
        self.assertEqual(
            Day.objects.get(weekday_id=2, pair_number=2).pair.pair_name,
            "Физика",
        )
        self.assertTrue(Day.objects.filter(weekday_id=3, pair_number=1).exists())

    def test_schedule_replaces_pair_in_a_cell(self):
        physics = self.second_pair()

        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "А-01;Понедельник;1;Физика;Иванов И. И.\r\n"
        )

        self.assertEqual(self.report(response)["updated"], 1)
        self.assertEqual(Day.objects.get(group=self.group).pair_id, physics.pk)

    def test_schedule_unknown_pair_and_sunday(self):
        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "А-01;Вторник;1;Химия;Иванов И. И.\r\n"
            "А-01;Воскресенье;1;Математика;Иванов И. И.\r\n"
            "А-01;Вторник;9;Математика;Иванов И. И.\r\n"
        )
        errors = self.report(response)["errors"]

        self.assertEqual(len(errors), 3)
        self.assertIn("не привязана пара «Химия»", errors[0])
        self.assertIn("не учебный день", errors[1])
        self.assertIn("от 1 до 6", errors[2])

    def test_schedule_teacher_in_two_groups_is_only_a_warning(self):
        other_group = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        make_pair(other_group, self.teacher, "Физика")

        # Понедельник, пара 1: у Иванова уже стоит Математика у А-01
        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "Б-02;Понедельник;1;Физика;Иванов И. И.\r\n"
        )
        report = self.report(response)

        self.assertEqual(report["errors"], [])
        self.assertTrue(report["saved"])
        self.assertIn("стоит сразу у нескольких групп", report["warnings"][0])
        self.assertContains(response, "Предупреждения")
        self.assertTrue(Day.objects.filter(group=other_group).exists())

    def test_schedule_takes_only_pairs_attached_to_the_group(self):
        other_group = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        make_pair(other_group, self.teacher, "Физика")

        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "А-01;Вторник;1;Физика;Иванов И. И.\r\n"
        )

        self.assertIn("не привязана пара «Физика»", self.report(response)["errors"][0])

    def test_bells_update_and_create(self):
        response = self.upload(
            "bells", "Вид;Смена;Номер;Начало;Конец\r\n"
            "Пара;1;1;08:00;09:30\r\n"
            "Пара;1;2;9:40;11:10\r\n"
            "Перемена;1;1;09:30;09:40\r\n"
        )
        report = self.report(response)

        self.assertEqual((report["created"], report["updated"]), (2, 1))
        self.assertEqual(
            StudyHours.objects.get(shift_id=1, pair_number=1).pair_start_time,
            time(8, 0),
        )
        self.assertEqual(
            StudyHours.objects.get(shift_id=1, pair_number=2).pair_start_time,
            time(9, 40),
        )

    def test_bells_bad_values(self):
        response = self.upload(
            "bells", "Вид;Смена;Номер;Начало;Конец\r\n"
            "Пара;1;1;10:00;09:00\r\n"
            "Обед;1;1;12:00;13:00\r\n"
            "Перемена;1;6;10:00;10:10\r\n"
            "Пара;1;2;25:00;26:00\r\n"
        )
        errors = self.report(response)["errors"]

        self.assertEqual(len(errors), 4)
        self.assertIn("начало должно быть раньше конца", errors[0])
        self.assertEqual(
            StudyHours.objects.get().pair_start_time, time(8, 30)
        )

    def test_import_remembers_chosen_kind(self):
        response = self.upload(
            "groups", "Название;Курс;Смена;Куратор;Тег\r\n"
            "А-01;1;1;;\r\n"
        )

        self.assertEqual(response.context["import_kind"], "groups")


class CsrfFailureTests(TestCase):
    """Устаревшая форма: понятная страница и свои имена cookie."""

    EMAIL = "csrfcheck77@gmail.com"

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_user(self.EMAIL, password=PASSWORD)
        # Клиент, который проверяет CSRF-токен так же, как браузер.
        self.client = Client(enforce_csrf_checks=True)

    def tearDown(self):
        cache.clear()

    def post_without_token(self, **extra):
        return self.client.post(
            reverse("login"),
            {"username": self.EMAIL, "password": PASSWORD},
            **extra,
        )

    def test_cookie_names_are_own(self):
        self.assertEqual(settings.CSRF_COOKIE_NAME, "raspisite_csrftoken")
        self.assertEqual(settings.SESSION_COOKIE_NAME, "raspisite_sessionid")

        self.client.get(reverse("login"))

        self.assertIn("raspisite_csrftoken", self.client.cookies)
        self.assertNotIn("csrftoken", self.client.cookies)

    def test_failure_shows_friendly_page(self):
        response = self.post_without_token()

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "csrf_failed.html")
        self.assertContains(response, "Форма устарела", status_code=403)
        self.assertNotContains(
            response, "CSRF token", status_code=403
        )

    def test_failure_does_not_log_in(self):
        self.post_without_token()

        self.assertNotIn("_auth_user_id", self.client.session)

    def test_back_link_goes_to_same_site_page(self):
        response = self.post_without_token(
            HTTP_REFERER="http://testserver/login/"
        )

        self.assertEqual(
            response.context["back_url"], "http://testserver/login/"
        )

    def test_back_link_ignores_foreign_site(self):
        response = self.post_without_token(
            HTTP_REFERER="http://evil.example/steal/"
        )

        self.assertEqual(response.context["back_url"], reverse("home"))

    def test_back_link_without_referer_goes_home(self):
        response = self.post_without_token()

        self.assertEqual(response.context["back_url"], reverse("home"))

    def test_valid_token_still_works(self):
        self.client.get(reverse("login"))
        token = self.client.cookies[settings.CSRF_COOKIE_NAME].value

        response = self.client.post(
            reverse("login"),
            {
                "username": self.EMAIL,
                "password": PASSWORD,
                "csrfmiddlewaretoken": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class ScheduleOverviewTests(BaseTestCase):
    """Общее расписание: группы по строкам, дни по столбцам."""

    def add_group(self, name, shift=None, subject=None, weekday=1, number=1):
        group = Group.objects.create(
            group_name=name,
            group_course=self.course,
            shift=shift or self.shift,
        )

        if subject:
            pair = make_pair(group, self.teacher, subject
            )
            Day.objects.create(
                group=group,
                pair=pair,
                pair_number=number,
                weekday=Weekday.objects.get(pk=weekday),
            )

        return group

    def open(self, **params):
        self.client.force_login(self.admin)

        return self.client.get(reverse("overview"), params)

    def test_staff_only(self):
        url = reverse("overview")

        self.assertRedirects(
            self.client.get(url), f"{reverse('login')}?next={url}"
        )

        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "overview.html")

    def test_card_is_in_workshop_under_tags(self):
        self.client.force_login(self.admin)

        html = self.client.get(reverse("workshop")).content.decode()

        self.assertLess(
            html.index("<h2>Теги преподавателей</h2>"),
            html.index("<h2>Общее расписание</h2>"),
        )
        self.assertIn(f'href="{reverse("overview")}"', html)

    def test_columns_are_six_days(self):
        response = self.open()

        self.assertEqual(
            [column["name"] for column in response.context["columns"]],
            ["Понедельник", "Вторник", "Среда",
             "Четверг", "Пятница", "Суббота"],
        )

        for row in response.context["rows"]:
            self.assertEqual(len(row["cells"]), 6)

    def test_pairs_are_in_the_right_cell(self):
        self.add_group("Б-02", subject="Физика", weekday=3, number=2)

        rows = {
            row["group"].group_name: row
            for row in self.open().context["rows"]
        }

        monday = rows["А-01"]["cells"][0]["lessons"]
        self.assertEqual(
            [(item["number"], item["name"]) for item in monday],
            [(1, "Математика")],
        )

        wednesday = rows["Б-02"]["cells"][2]["lessons"]
        self.assertEqual(
            [(item["number"], item["name"]) for item in wednesday],
            [(2, "Физика")],
        )
        self.assertEqual(rows["Б-02"]["cells"][0]["lessons"], [])

    def test_pairs_are_sorted_by_number(self):
        group = self.add_group("Б-02", subject="Физика", number=3)
        pair = make_pair(group, self.teacher, "Химия"
        )
        Day.objects.create(
            group=group, pair=pair, pair_number=1,
            weekday=Weekday.objects.get(pk=1),
        )

        rows = {
            row["group"].group_name: row
            for row in self.open().context["rows"]
        }

        self.assertEqual(
            [item["number"] for item in rows["Б-02"]["cells"][0]["lessons"]],
            [1, 3],
        )

    def test_checkerboard(self):
        self.add_group("Б-02")
        self.add_group("В-03")

        rows = self.open().context["rows"]
        tones = [[cell["tone"] for cell in row["cells"]] for row in rows]

        self.assertEqual(len(tones), 3)

        for row_index, row in enumerate(tones):
            for column_index, tone in enumerate(row):
                self.assertEqual(tone, (row_index + column_index) % 2)

                # Соседи по строке и по столбцу всегда другого тона
                if column_index:
                    self.assertNotEqual(tone, row[column_index - 1])

                if row_index:
                    self.assertNotEqual(tone, tones[row_index - 1][column_index])

    def test_cells_lead_to_group_editor(self):
        html = self.open().content.decode()

        edit_url = reverse("group_edit", args=[self.group.group_id])

        self.assertEqual(html.count(f'href="{edit_url}"'), 6)
        self.assertContains(
            self.open(),
            reverse("group_schedule", args=[self.group.group_id]),
        )

    def test_empty_cell_is_marked(self):
        self.add_group("Б-02")

        self.assertContains(self.open(), 'class="overview-empty"')

    def test_shift_filter(self):
        shift2 = Shift.objects.create(shift_id=2)
        self.add_group("Б-02", shift=shift2)

        def names(**params):
            return [
                row["group"].group_name
                for row in self.open(**params).context["rows"]
            ]

        self.assertEqual(names(), ["А-01", "Б-02"])
        self.assertEqual(names(shift="2"), ["Б-02"])
        self.assertEqual(names(shift="1"), ["А-01"])

        # Неверное значение смены не ломает страницу
        self.assertEqual(names(shift="abc"), ["А-01", "Б-02"])
        self.assertEqual(names(shift="99"), ["А-01", "Б-02"])

    def test_no_groups_message(self):
        Group.objects.all().delete()

        response = self.open()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Групп пока нет")

    def test_query_count_does_not_grow_with_groups(self):
        self.client.force_login(self.admin)

        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("overview"))

        for index in range(5):
            self.add_group(f"Г-{index}", subject=f"Предмет {index}")

        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("overview"))

        self.assertEqual(len(many), len(few))

    def test_one_off_changes_are_not_shown(self):
        ScheduleChange.objects.create(
            calendar=Calendar.objects.create(date=MONDAY, weekday=1),
            group=self.group,
            pair=self.pair,
            old_pair_number=1,
            change_type="CANCEL",
        )

        monday = self.open().context["rows"][0]["cells"][0]["lessons"]

        self.assertEqual([item["name"] for item in monday], ["Математика"])


STATIC_DIR = Path(__file__).resolve().parent / "static"


def header_of(response):
    """Блок заголовка страницы (между <header ...page-head> и </header>)."""
    html = response.content.decode()
    match = re.search(
        r'<header class="daily-header page-head">.*?</header>', html, re.S
    )

    assert match, "на странице нет блока заголовка"

    return match.group(0)


class PageHeaderTests(BaseTestCase):
    """Единый блок заголовка: слева название, справа действия."""

    def open(self, name, args=None, params=None, admin=True):
        self.client.force_login(self.admin if admin else self.user)

        with fake_today(MONDAY):
            return self.client.get(reverse(name, args=args), params or {})

    def test_every_page_has_header_block_with_title(self):
        group = [self.group.pk]
        pages = [
            ("calendar", None, "КАЛЕНДАРЬ"),
            ("groups", None, "ГРУППЫ"),
            ("teachers", None, "ПРЕПОДАВАТЕЛИ"),
            ("users", None, "ПОЛЬЗОВАТЕЛИ"),
            ("workshop", None, "КУЗНЯ"),
            ("overview", None, "ОБЩЕЕ РАСПИСАНИЕ"),
            ("group_edit", group, "РЕДАКТОР РАСПИСАНИЯ"),
        ]

        for name, args, title in pages:
            with self.subTest(page=name):
                response = self.open(name, args)

                self.assertContains(response, f"<h1>{title}</h1>", count=1)
                self.assertIn(f"<h1>{title}</h1>", header_of(response))
                self.assertNotContains(response, "schedule-header")

    def test_title_is_in_the_left_part_and_actions_on_the_right(self):
        group = [self.group.pk]

        for name, args in (
            ("calendar", None),
            ("groups", None),
            ("teachers", None),
            ("overview", None),
            ("group_schedule", group),
            ("group_edit", group),
        ):
            with self.subTest(page=name):
                head = header_of(self.open(name, args))

                self.assertLess(
                    head.index('class="page-head-title"'),
                    head.index('class="page-head-actions"'),
                )
                self.assertLess(
                    head.index("<h1>"), head.index('class="page-head-actions"')
                )

    def test_group_schedule_has_name_as_big_heading_with_caption_above(self):
        response = self.open("group_schedule", [self.group.pk])
        head = header_of(response)
        card = reverse("group_card", args=[self.group.pk])

        self.assertContains(response, "<h1>", count=1)
        self.assertLess(
            head.index("Расписание группы"), head.index("<h1>")
        )
        self.assertRegex(
            head,
            r'<h1><a class="page-head-link"\s+href="%s"[^>]*>%s</a></h1>'
            % (re.escape(card), re.escape(self.group.group_name)),
        )
        self.assertNotIn("РАСПИСАНИЕ", head)

    def test_teacher_schedule_has_name_as_big_heading_with_caption_above(self):
        response = self.open("teachers", params={"teacher": self.teacher.pk})
        head = header_of(response)
        card = reverse("teacher_card", args=[self.teacher.pk])

        self.assertLess(
            head.index("Расписание преподавателя"), head.index("<h1>")
        )
        self.assertRegex(
            head,
            r'<h1><a class="page-head-link"\s+href="%s"[^>]*>%s</a></h1>'
            % (re.escape(card), re.escape(self.teacher.teacher_name)),
        )
        self.assertNotIn("ПРЕПОДАВАТЕЛИ", head)

    def test_teachers_page_without_choice_keeps_static_title(self):
        head = header_of(self.open("teachers"))

        self.assertIn("<h1>ПРЕПОДАВАТЕЛИ</h1>", head)
        self.assertNotIn("page-head-link", head)

    def test_no_profile_buttons_in_actions(self):
        for name, args, params in (
            ("group_schedule", [self.group.pk], None),
            ("teachers", None, {"teacher": self.teacher.pk}),
        ):
            head = header_of(self.open(name, args, params))
            actions = head[head.index("page-head-actions"):]

            self.assertNotIn("Профиль", actions)
            self.assertNotIn("/profile/", actions)

    def test_group_page_buttons_are_in_actions_for_admin_only(self):
        args = [self.group.pk]

        admin_head = header_of(self.open("group_schedule", args))
        student_head = header_of(
            self.open("group_schedule", args, admin=False)
        )

        actions = admin_head[admin_head.index("page-head-actions"):]

        self.assertIn("Правки на даты", actions)
        self.assertIn("Редактировать", actions)
        self.assertNotIn("Правки на даты", student_head)
        self.assertNotIn("Редактировать", student_head)

    def test_workshop_header_keeps_daily_header_class(self):
        """Правило «Не трогать» в CSS привязано к .daily-header в Кузне."""
        response = self.open("workshop")

        self.assertContains(response, 'id="workshop-page"')
        self.assertContains(response, 'class="daily-header page-head"')

        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("/* Не трогать */", css)
        self.assertIn("#workshop-page .daily-header", css)


class TeacherSearchTests(BaseTestCase):
    """Поиск преподавателя на странице «Преподаватели»."""

    def open(self, **params):
        self.client.force_login(self.user)

        with fake_today(MONDAY):
            return self.client.get(reverse("teachers"), params)

    def open_logged_in(self, **params):
        with fake_today(MONDAY):
            return self.client.get(reverse("teachers"), params)

    def test_script_is_connected(self):
        self.assertContains(self.open(), "filter.js")

    def test_search_field_replaces_the_teacher_selector(self):
        response = self.open()

        self.assertContains(response, "data-filter-input")
        self.assertNotContains(response, '<select id="teacher"')

    def test_every_teacher_is_a_link_including_old_records(self):
        old = Teacher.objects.create(teacher_name="Старый Преподаватель")
        html = self.open().content.decode()

        for teacher in (self.teacher, old):
            self.assertIn(f'href="?teacher={teacher.pk}"', html)

        self.assertIn('data-filter-text="Старый Преподаватель"', html)

    def test_links_keep_date_and_shift(self):
        html = self.open(
            date="2026-10-19", shift=self.shift.pk
        ).content.decode()

        self.assertIn(
            f'href="?teacher={self.teacher.pk}&amp;date=2026-10-19'
            f'&amp;shift={self.shift.pk}"',
            html,
        )

    def test_only_selected_teacher_is_marked(self):
        Teacher.objects.create(teacher_name="Другой Преподаватель")
        response = self.open(teacher=self.teacher.pk)

        self.assertContains(
            response, 'class="teacher-result is-selected"', count=1
        )

    def test_empty_message_is_hidden_while_there_are_teachers(self):
        html = self.open().content.decode()
        match = re.search(r'<li class="teacher-empty"[^>]*>', html)

        self.assertIsNotNone(match)
        self.assertIn("hidden", match.group(0))

    def test_query_count_does_not_grow_with_teachers(self):
        # Вход и первый запрос в подсчёт не входят: считаются два одинаковых
        # запроса страницы подряд
        self.open()

        with CaptureQueriesContext(connection) as few:
            self.open_logged_in()

        tag = TeacherTag.objects.create(tag_name="Высшая")

        for index in range(5):
            Teacher.objects.create(
                teacher_name=f"Преподаватель {index}", teacher_tag=tag
            )

        with CaptureQueriesContext(connection) as many:
            self.open_logged_in()

        self.assertEqual(len(many), len(few))


class FilterScriptTests(TestCase):
    """Файл static/filter.js и его согласованность с серверным кодом."""

    def script(self):
        return (STATIC_DIR / "filter.js").read_text(encoding="utf-8")

    def test_script_has_the_hooks_used_by_templates(self):
        script = self.script()

        for hook in (
            "data-filter",
            "data-filter-input",
            "data-filter-item",
            "data-filter-text",
            "data-filter-empty",
            "data-tag-preview",
            "data-tag-preview-chip",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, script)

    def test_text_color_rule_matches_python(self):
        """Предпросмотр считает цвет текста так же, как tags.text_color."""
        script = self.script()

        self.assertIn("299 * red + 587 * green + 114 * blue", script)
        self.assertIn("> 140", script)
        self.assertIn('"#111827"', script)
        self.assertIn('"#ffffff"', script)
        self.assertEqual(text_color("#ffffff"), "#111827")
        self.assertEqual(text_color("#000000"), "#ffffff")


class TagPreviewTests(BaseTestCase):
    """Предпросмотр метки тега в форме Кузни."""

    MARK = 'data-tag-preview hidden'

    def open(self, **params):
        self.client.force_login(self.admin)

        return self.client.get(reverse("workshop"), params)

    def test_add_form_has_preview_after_the_color_field(self):
        html = self.open().content.decode()

        # Две формы добавления тегов — два предпросмотра
        self.assertEqual(html.count(self.MARK), 2)
        self.assertLess(
            html.index('name="group_tag-tag_color"'), html.index(self.MARK)
        )

    def test_edit_form_has_preview(self):
        tag = TeacherTag.objects.create(tag_name="Высшая")

        html = self.open(edit="teacher_tag", item=tag.pk).content.decode()

        # Своя форма изменения и форма добавления тега групп
        self.assertEqual(html.count(self.MARK), 2)
        self.assertIn('name="teacher_tag-tag_color"', html)

    def test_no_preview_before_a_tag_is_chosen_for_editing(self):
        html = self.open(edit="teacher_tag").content.decode()

        # Остаётся только форма добавления тега групп
        self.assertEqual(html.count(self.MARK), 1)

    def test_other_cards_have_no_preview(self):
        html = self.open(edit="group").content.decode()

        # Только формы добавления тегов
        self.assertEqual(html.count(self.MARK), 2)


class TeacherShiftButtonsTests(BaseTestCase):
    """Кнопки «Все смены / 1 смена / 2 смена» на странице преподавателей."""

    def setUp(self):
        Shift.objects.create(shift_id=2)

    def open(self, **params):
        self.client.force_login(self.user)

        with fake_today(MONDAY):
            return self.client.get(reverse("teachers"), params)

    def selected(self, response):
        return [link["selected"] for link in response.context["shift_links"]]

    def test_all_shifts_and_each_shift_get_a_button(self):
        response = self.open()
        links = response.context["shift_links"]

        self.assertEqual(
            [link["label"] for link in links],
            ["Все смены", "1 смена", "2 смена"],
        )
        self.assertEqual(self.selected(response), [True, False, False])
        self.assertNotContains(response, '<select id="shift"')

    def test_selected_shift_is_marked(self):
        response = self.open(shift=2)

        self.assertEqual(self.selected(response), [False, False, True])
        self.assertContains(
            response, 'class="shift-button is-selected"', count=1
        )

    def test_unknown_shift_means_all_shifts(self):
        self.assertEqual(self.selected(self.open(shift=99)), [True, False, False])
        self.assertEqual(self.selected(self.open(shift="abc")), [True, False, False])

    def test_links_keep_teacher_and_date(self):
        response = self.open(
            teacher=self.teacher.pk, date="2026-10-19", shift=2
        )
        base = f"{reverse('teachers')}?teacher={self.teacher.pk}&date=2026-10-19"
        urls = [link["url"] for link in response.context["shift_links"]]

        self.assertEqual(urls, [base, f"{base}&shift=1", f"{base}&shift=2"])

    def test_date_is_not_added_when_it_was_not_requested(self):
        urls = [
            link["url"] for link in self.open().context["shift_links"]
        ]
        base = f"{reverse('teachers')}?teacher={self.teacher.pk}"

        self.assertEqual(urls, [base, f"{base}&shift=1", f"{base}&shift=2"])

    def test_shift_does_not_change_the_teacher_list(self):
        Teacher.objects.create(teacher_name="Другой Преподаватель")

        every = self.open().context["teachers"]
        second = self.open(shift=2).context["teachers"]

        self.assertEqual(len(every), 2)
        self.assertEqual(
            [teacher.pk for teacher in second],
            [teacher.pk for teacher in every],
        )

    def test_shift_limits_only_the_shown_pairs(self):
        def monday_lessons(response):
            day = response.context["schedule_days"][0]

            return day["pairs"][0]["lessons"]

        self.assertEqual(len(monday_lessons(self.open())), 1)
        self.assertEqual(len(monday_lessons(self.open(shift=1))), 1)
        self.assertEqual(len(monday_lessons(self.open(shift=2))), 0)

    def test_teacher_links_keep_the_chosen_shift(self):
        html = self.open(shift=2).content.decode()

        self.assertIn(
            f'href="?teacher={self.teacher.pk}&amp;shift=2"', html
        )


class EmptyCellTests(BaseTestCase):
    """Пустые клетки таблиц — пустые, а не с тире; подписи в списках остались."""

    LONELY_DASH = re.compile(r">\s*—\s*<")

    def page(self, name, args=None, params=None):
        self.client.force_login(self.admin)

        with fake_today(MONDAY):
            return self.client.get(reverse(name, args=args), params or {})

    def test_no_lonely_dashes_in_tables(self):
        group = [self.group.pk]

        for name, args in (
            ("groups", None),
            ("group_schedule", group),
            ("teachers", None),
            ("group_edit", group),
            ("overview", None),
            ("users", None),
        ):
            with self.subTest(page=name):
                html = self.page(name, args).content.decode()

                self.assertIsNone(self.LONELY_DASH.search(html))

    def test_empty_slots_are_non_breaking_spaces(self):
        for name, args in (
            ("groups", None),
            ("group_schedule", [self.group.pk]),
            ("teachers", None),
        ):
            with self.subTest(page=name):
                self.assertContains(self.page(name, args), "&nbsp;")

    def test_user_without_name_has_blank_name_cell(self):
        html = self.page("users").content.decode()

        self.assertIn('<span class="users-hint">&nbsp;</span>', html)

    def test_dropdown_placeholders_keep_their_dashes(self):
        self.assertContains(
            self.page("group_edit", [self.group.pk]), "— нет пары —"
        )
        self.assertContains(self.page("users"), "— не назначена —")
        self.assertContains(self.page("workshop"), "— Выберите —")


class MainPageChoiceTests(BaseTestCase):
    """Главная: выбор между расписанием и дневником."""

    def open(self, user=None):
        self.client.force_login(user or self.user)

        return self.client.get(reverse("home"))

    def test_schedule_and_diary_are_offered(self):
        html = self.open().content.decode()

        self.assertIn('class="hero-choice"', html)
        self.assertIn("Расписание", html)
        self.assertIn("Дневник", html)

    def test_diary_leads_to_the_diary(self):
        html = self.open().content.decode()

        self.assertIn(
            f'<a class="hero-choice-link" href="{reverse("diary_home")}">',
            html,
        )
        self.assertNotIn("is-soon", html)

    def test_menu_has_the_diary_link(self):
        self.client.force_login(self.user)

        page = self.client.get(reverse("groups"))

        self.assertContains(page, f'href="{reverse("diary_home")}"')

    def schedule_link(self):
        html = self.open().content.decode()
        match = re.search(
            r'<a class="hero-choice-link"\s+href="([^"]+)"', html
        )

        self.assertIsNotNone(match)

        return match.group(1)

    def test_schedule_leads_to_groups_without_a_group(self):
        self.assertEqual(self.schedule_link(), reverse("groups"))

    def test_schedule_leads_to_the_students_own_group(self):
        set_group(self.user, self.group)

        self.assertEqual(
            self.schedule_link(),
            reverse("group_schedule", args=[self.group.pk]),
        )

    def test_script_is_only_on_other_pages(self):
        self.assertNotContains(self.open(), "filter.js")

        page = self.client.get(reverse("groups"))

        self.assertContains(page, "filter.js")


class CourseShiftPillsTests(BaseTestCase):
    """Курс и смена рядом с группой: цифры в плашках, полный текст в title."""

    PILLS = (
        '<span class="group-pill" title="1 курс">1</span>'
        '<span class="group-pill" title="1 смена">1</span>'
    )

    def page(self, name, *args):
        self.client.force_login(self.admin)

        with fake_today(MONDAY):
            return self.client.get(reverse(name, args=args))

    def test_pills_on_every_page_with_a_group_name(self):
        pages = [
            page
            for page in (
                self.page("group_schedule", self.group.pk),
                self.page("groups"),
                self.page("overview"),
                self.page("group_edit", self.group.pk),
            )
        ]

        for page in pages:
            self.assertContains(page, self.PILLS)

    def test_old_gray_text_is_gone(self):
        page = self.page("group_schedule", self.group.pk)

        self.assertNotContains(page, "1 курс\n")
        self.assertNotContains(page, "1 курс ·")

    def test_pill_style_is_dark_digits_on_light_gray(self):
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
        rule = re.search(r"\.group-pill \{(.*?)\}", css, re.S).group(1)

        self.assertIn("background: #e5e7eb", rule)
        self.assertIn("color: #111827", rule)
        self.assertIn("font-weight: 700", rule)


class ProfileNamingTests(BaseTestCase):
    """«Карточка» переименована в «Профиль»: надписи и адреса."""

    def test_urls_end_with_profile(self):
        self.assertEqual(
            reverse("student_card", args=[5]), "/students/5/profile/"
        )
        self.assertEqual(
            reverse("teacher_card", args=[5]), "/teachers/5/profile/"
        )
        self.assertEqual(
            reverse("group_card", args=[5]), "/groups/5/profile/"
        )

    def test_no_card_word_on_pages(self):
        self.client.force_login(self.admin)

        with fake_today(MONDAY):
            for name, args in (
                ("group_schedule", [self.group.pk]),
                ("groups", []),
                ("teachers", []),
                ("group_card", [self.group.pk]),
                ("teacher_card", [self.teacher.pk]),
            ):
                page = self.client.get(reverse(name, args=args))

                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "арточк")

        self.assertContains(page, "Профиль")

    def test_group_schedule_name_leads_to_profile(self):
        self.client.force_login(self.admin)

        with fake_today(MONDAY):
            page = self.client.get(
                reverse("group_schedule", args=[self.group.pk])
            )

        self.assertContains(
            page,
            reverse("group_card", args=[self.group.pk]),
        )
        self.assertContains(page, 'title="Профиль группы"')


class LinkStyleTests(TestCase):
    """Ссылки без подчёркивания: оно появляется только при наведении."""

    def setUp(self):
        self.css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    def test_links_are_not_underlined_by_default(self):
        rule = re.search(r"\na \{(.*?)\}", self.css, re.S).group(1)

        self.assertIn("text-decoration: none", rule)

    def test_underline_on_hover_only(self):
        rule = re.search(
            r"\na:hover,\na:focus-visible \{(.*?)\}", self.css, re.S
        ).group(1)

        self.assertIn("text-decoration: underline", rule)

    def test_buttons_are_not_underlined_on_hover(self):
        self.assertRegex(
            self.css, r"a\.date-nav-link:hover,[^{]*\{\s*text-decoration: none"
        )


class GroupNameRuleTests(BaseTestCase):
    """Название группы: две заглавные русские буквы, дефис, три цифры."""

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, name, course=1):
        return self.client.post(reverse("workshop"), {
            "action": "add_group",
            "group-group_name": name,
            "group-group_course": course,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": "",
        }, follow=True)

    def rename(self, group, name, course=1):
        return self.client.post(reverse("workshop"), {
            "action": "update_item",
            "edit": "group",
            "item": group.pk,
            "group-group_name": name,
            "group-group_course": course,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": "",
        }, follow=True)

    def test_valid_names_pass(self):
        for name in ("ПО-107", "ИС-101", "ЁЖ-399", "АБ-199"):
            self.assertEqual(check_group_name(name), name)

    def test_wrong_names_are_rejected(self):
        for name in (
            "ПО107", "по-107", "По-107", "ПО-17", "ПО-1070", "П-107",
            "ПОО-107", "ПО–107", "ПО 107", "PO-107", "ПO-107", "ПО-1O7",
            "ПО-100", "ПО-007", "", "А-01",
        ):
            with self.assertRaises(ValueError, msg=name):
                check_group_name(name)

    def test_course_is_compared_with_the_first_digit_only(self):
        self.assertEqual(check_group_name("ПО-307", 3), "ПО-307")

        with self.assertRaises(ValueError):
            check_group_name("ПО-307", 1)

    def test_standard_name_helper(self):
        self.assertTrue(is_standard_name("ПО-107"))
        self.assertFalse(is_standard_name("А-01"))
        self.assertFalse(is_standard_name(None))

    def test_form_creates_group_with_good_name(self):
        self.add("ПО-107")

        self.assertTrue(Group.objects.filter(group_name="ПО-107").exists())

    def test_form_rejects_bad_name_with_hint(self):
        response = self.add("Группа-1")

        self.assertContains(response, "Проверьте заполненные поля.")

        form = response.context["cards"]["group"]["form"]
        self.assertIn("две заглавные русские буквы", form.errors["group_name"][0])
        self.assertFalse(Group.objects.filter(group_name="Группа-1").exists())

    def test_form_rejects_latin_and_lowercase(self):
        for name in ("PO-107", "по-107"):
            self.add(name)

            self.assertFalse(Group.objects.filter(group_name=name).exists())

    def test_form_rejects_course_mismatch(self):
        Course.objects.get_or_create(course_id=2)

        response = self.add("ПО-107", course=2)

        self.assertContains(response, "не совпадает с выбранным курсом")
        self.assertFalse(Group.objects.filter(group_name="ПО-107").exists())

    def test_old_group_can_be_edited_while_its_name_stays(self):
        Course.objects.get_or_create(course_id=2)

        response = self.rename(self.group, "А-01", course=2)

        self.assertContains(response, "Изменения сохранены")
        self.assertEqual(
            Group.objects.get(pk=self.group.pk).group_course_id, 2
        )

    def test_old_group_cannot_be_renamed_to_a_bad_name(self):
        response = self.rename(self.group, "А-02")

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertEqual(Group.objects.get(pk=self.group.pk).group_name, "А-01")

    def test_old_group_can_be_renamed_to_a_good_name(self):
        self.rename(self.group, "ПО-101")

        self.assertEqual(
            Group.objects.get(pk=self.group.pk).group_name, "ПО-101"
        )

    def test_good_name_cannot_get_a_wrong_course_later(self):
        Course.objects.get_or_create(course_id=2)
        self.rename(self.group, "ПО-101")

        response = self.rename(self.group, "ПО-101", course=2)

        self.assertContains(response, "не совпадает с выбранным курсом")

    def test_name_field_shows_example(self):
        response = self.client.get(reverse("workshop"))

        self.assertContains(response, 'placeholder="ПО-107"')

    def upload(self, text):
        return self.client.post(reverse("workshop"), {
            "action": "import_csv",
            "kind": "groups",
            "file": SimpleUploadedFile(
                "data.csv", text.encode("utf-8-sig"), content_type="text/csv"
            ),
        })

    def test_csv_creates_new_group_with_good_name(self):
        response = self.upload(
            "Название;Курс;Смена;Куратор;Тег\r\nПО-107;1;1;;\r\n"
        )

        self.assertEqual(response.context["import_report"]["created"], 1)
        self.assertTrue(Group.objects.filter(group_name="ПО-107").exists())

    def test_csv_rejects_new_group_with_bad_name_or_course(self):
        response = self.upload(
            "Название;Курс;Смена;Куратор;Тег\r\n"
            "Новая;1;1;;\r\n"
            "ПО-207;1;1;;\r\n"
        )
        report = response.context["import_report"]

        self.assertEqual(len(report["errors"]), 2)
        self.assertIn("две заглавные русские буквы", report["errors"][0])
        self.assertIn("не совпадает", report["errors"][1])
        self.assertEqual(Group.objects.count(), 1)

    def test_csv_still_updates_old_group_by_its_name(self):
        response = self.upload(
            "Название;Курс;Смена;Куратор;Тег\r\nА-01;1;1;;\r\n"
        )
        report = response.context["import_report"]

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["created"], 0)


class ShortNameTests(TestCase):
    """«Фамилия Имя Отчество» в списках: «Отчество И. И.»."""

    def test_full_name_becomes_patronymic_and_two_initials(self):
        self.assertEqual(
            short_name("Иванов Иван Константинович"), "Константинович И. И."
        )
        self.assertEqual(
            short_name("Салтыков-Щедрин Михаил Евграфович"), "Евграфович М. С."
        )

    def test_without_patronymic_it_is_surname_and_initial(self):
        self.assertEqual(short_name("Петров Пётр"), "Петров П.")

    def test_unparsable_and_already_short_names_stay(self):
        for name in ("Иванов И. И.", "Иванов И.", "Иванов", "А Б В Г", ""):
            self.assertEqual(short_name(name), name)

    def test_extra_spaces_are_ignored(self):
        self.assertEqual(short_name("  Иванов   Иван  Иванович "), "Иванович И. И.")

    def test_split_name(self):
        self.assertEqual(
            split_name("Иванов Иван Иванович"), ("Иванов", "Иван", "Иванович")
        )
        self.assertEqual(split_name("Иванов Иван"), ("Иванов", "Иван", ""))
        self.assertIsNone(split_name("Иванов И. И."))

    def test_html_has_full_name_in_title(self):
        html = person_html("Иванов Иван Константинович")

        self.assertEqual(
            str(html),
            '<span class="person-name" title="Иванов Иван Константинович">'
            "Константинович И. И.</span>",
        )

    def test_html_is_escaped_and_plain_when_nothing_to_shorten(self):
        self.assertEqual(str(person_html("Иванов И. И.")), "Иванов И. И.")
        self.assertNotIn("<script>", str(person_html('Х<script> Ы"')))


class FullNameDisplayTests(BaseTestCase):
    """Где ФИО короткое, а где полное."""

    FULL = "Иванов Иван Константинович"
    SHORT = "Константинович И. И."

    def setUp(self):
        self.teacher.teacher_name = self.FULL
        self.teacher.save()
        self.group.group_curator = self.teacher
        self.group.save()
        self.client.force_login(self.admin)

    def get(self, name, *args, **params):
        with fake_today(MONDAY):
            return self.client.get(reverse(name, args=args), params)

    def test_schedule_cell_shows_short_name_with_full_hint(self):
        page = self.get("group_schedule", self.group.pk)

        self.assertContains(page, self.SHORT)
        self.assertContains(page, f'title="{self.FULL}"')

    def test_teacher_list_shows_short_name_but_search_uses_full(self):
        page = self.get("teachers")

        self.assertContains(page, self.SHORT)
        self.assertContains(page, f'data-filter-text="{self.FULL}"')

    def test_big_headings_show_full_name(self):
        page = self.get("teacher_card", self.teacher.pk)
        self.assertContains(page, f">{self.FULL}</h1>")

        page = self.get("teachers", teacher=self.teacher.pk)
        self.assertContains(page, f">{self.FULL}</a></h1>")

    def test_group_card_list_is_short_and_curator_tile_is_full(self):
        page = self.get("group_card", self.group.pk)

        self.assertContains(page, f">{self.FULL}</a>")
        self.assertContains(page, self.SHORT)

    def test_select_options_show_full_name(self):
        page = self.get("users")

        self.assertContains(page, f"А-01 · куратор: {self.FULL}")

    def test_new_user_form_order_is_name_patronymic_surname(self):
        form = self.get("users").context["new_user_form"]

        self.assertEqual(
            list(form.fields),
            ["role", "first_name", "patronymic", "last_name",
             "username", "password", "group"],
        )

    def test_patronymic_label_does_not_say_optional(self):
        form = self.get("users").context["new_user_form"]

        self.assertEqual(form.fields["patronymic"].label, "Отчество")
        self.assertFalse(form.fields["patronymic"].required)
        self.assertEqual(form.fields["first_name"].label, "Имя")
        self.assertEqual(form.fields["last_name"].label, "Фамилия")

    def test_form_page_lists_fields_in_that_order(self):
        html = self.get("users").content.decode()

        positions = [
            html.index(f'name="new_user-{name}"')
            for name in ("first_name", "patronymic", "last_name")
        ]

        self.assertEqual(positions, sorted(positions))


class SiteHeaderTests(BaseTestCase):
    """Шапка: «Выйти» без почты, меню-бургер и кнопка «Назад»."""

    def get(self, name, *args, who=None, **params):
        if who is not None:
            self.client.force_login(who)

        with fake_today(MONDAY):
            return self.client.get(reverse(name, args=args), params)

    def back_url(self, response):
        return response.context["nav_back_url"]

    def test_logout_button_has_no_email(self):
        page = self.get("groups", who=self.admin)

        self.assertRegex(page.content.decode(), r">\s*Выйти\s*</button>")
        self.assertNotContains(page, "Выйти (")

    def test_email_is_a_muted_line_in_the_menu_and_a_hint_on_the_button(self):
        page = self.get("groups", who=self.admin)

        self.assertContains(
            page, f'<p class="nav-user">{self.admin.get_username()}</p>'
        )
        self.assertContains(
            page, f'title="{self.admin.get_username()}"'
        )

    def test_menu_button_and_script_for_logged_in_users_only(self):
        page = self.get("groups", who=self.admin)

        self.assertContains(page, "data-nav-toggle")
        self.assertContains(page, 'aria-expanded="false"')
        self.assertContains(page, "nav.js")
        self.assertContains(page, 'className += " js"')

        self.client.logout()
        page = self.client.get(reverse("login"))

        self.assertNotContains(page, "data-nav-toggle")

    def test_back_button_is_after_logout_in_the_header(self):
        html = self.get("groups", who=self.admin).content.decode()

        self.assertIn("data-back", html)
        self.assertLess(html.index("nav-logout"), html.index("nav-back"))
        self.assertLess(html.index("nav-back"), html.index("</header>"))

    def test_no_back_button_on_home_and_login(self):
        self.client.force_login(self.admin)

        with fake_today(MONDAY):
            home = self.client.get("/")

        self.assertNotContains(home, "data-back")
        self.assertIsNone(home.context["nav_back_url"])

        self.client.logout()

        self.assertNotContains(self.client.get(reverse("login")), "data-back")

    def test_fallback_parent_of_each_page(self):
        group = self.group.pk
        cases = (
            (("group_schedule", group), reverse("groups")),
            (("group_edit", group), reverse("group_schedule", args=[group])),
            (("group_card", group), reverse("groups")),
            (("teacher_card", self.teacher.pk), reverse("teachers")),
            (("student_card", self.user.pk), reverse("users")),
            (("groups",), reverse("home")),
            (("users",), reverse("home")),
            (("calendar",), reverse("home")),
        )

        self.client.force_login(self.admin)

        for (name, *args), expected in cases:
            with self.subTest(page=name):
                self.assertEqual(
                    self.back_url(self.get(name, *args)), expected
                )

    def test_page_own_back_url_is_not_overwritten(self):
        # change.html сам передаёт back_url: шапка использует отдельное имя
        response = self.get("groups", who=self.admin)

        self.assertIn("nav_back_url", response.context)
        self.assertNotIn("back_url", response.context)

    def test_script_goes_back_in_history_only_within_the_site(self):
        script = (STATIC_DIR / "nav.js").read_text(encoding="utf-8")

        self.assertIn("history.back()", script)
        self.assertIn("document.referrer", script)
        self.assertIn("from.origin === window.location.origin", script)
        self.assertIn("data-back", script)
        self.assertIn("Escape", script)

    def test_burger_breakpoint_is_in_css(self):
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 1340px)", css)
        self.assertRegex(css, r"\.js \.nav-toggle ~ \.main-nav \{\s*display: none")
        self.assertRegex(css, r"\.nav-toggle,\s*\.nav-user \{\s*display: none")

    def test_profile_heads_have_no_back_buttons(self):
        for name, args in (
            ("student_card", [self.user.pk]),
            ("teacher_card", [self.teacher.pk]),
            ("group_card", [self.group.pk]),
        ):
            with self.subTest(page=name):
                head = header_of(self.get(name, *args, who=self.admin))

                self.assertNotIn("←", head)
                self.assertNotIn("К пользователям", head)


def tile_labels(response):
    """Подписи плиток профиля по порядку."""
    return re.findall(r"<dt>(.*?)</dt>", response.content.decode())


class ProfileTilesTests(BaseTestCase):
    """Профили плитками: состав, ссылки, «Редактировать», «Расписание»."""

    def setUp(self):
        User = get_user_model()
        self.group.group_curator = self.teacher
        self.group.save()
        set_group(self.user, self.group)
        self.teacher_user = User.objects.get(username="teacherivanov@gmail.com")
        self.student_url = reverse("student_card", args=[self.user.pk])
        self.teacher_url = reverse("teacher_card", args=[self.teacher.pk])
        self.group_url = reverse("group_card", args=[self.group.pk])

    def open(self, who, url):
        self.client.force_login(who)

        return self.client.get(url)

    def tag_group(self):
        self.tag = GroupTag.objects.create(tag_name="Высшая")
        self.group.group_tag = self.tag
        self.group.save()

    # --- состав плиток -------------------------------------------------------

    def test_student_has_four_tiles(self):
        response = self.open(self.admin, self.student_url)

        self.assertEqual(
            tile_labels(response),
            ["Учится в группе", "Куратор студента", "Курс и смена",
             "Почта (логин)"],
        )
        self.assertContains(response, "profile-facts cols-4")
        self.assertContains(response, "1 курс<span")
        self.assertContains(response, "|</span>1 смена")

    def test_student_tiles_link_to_group_and_curator(self):
        response = self.open(self.admin, self.student_url)

        self.assertContains(
            response, f'href="{self.group_url}">А-01</a>'
        )
        self.assertContains(
            response, f'href="{self.teacher_url}">{self.teacher.teacher_name}</a>'
        )

    def test_student_without_group_has_placeholders(self):
        set_group(self.user, None)

        response = self.open(self.admin, self.student_url)

        self.assertEqual(len(tile_labels(response)), 4)
        self.assertContains(response, "не назначена")
        self.assertContains(response, "нет</span>")
        self.assertNotContains(response, "курс<span")

    def test_teacher_has_three_tiles_and_pairs_section(self):
        response = self.open(self.admin, self.teacher_url)

        self.assertEqual(
            tile_labels(response),
            ["Тег преподавателя", "Курирует", "Учётная запись"],
        )
        self.assertContains(response, "profile-facts cols-3")
        self.assertContains(
            response,
            'Пары <span class="profile-count">1</span>',
        )
        self.assertContains(response, f'href="{self.group_url}">А-01</a>', count=2)

    def test_group_has_three_tiles_and_two_sections_with_counts(self):
        response = self.open(self.admin, self.group_url)

        self.assertEqual(
            tile_labels(response), ["Тег группы", "Куратор", "Курс и смена"]
        )
        self.assertContains(
            response, 'Студенты <span class="profile-count">1</span>'
        )
        self.assertContains(
            response, 'Пары <span class="profile-count">1</span>'
        )
        self.assertContains(response, "1 курс<span")

    def test_group_students_list_is_numbered_and_short(self):
        self.user.last_name, self.user.first_name = "Абрамов", "Аркадий"
        self.user.save()

        response = self.open(self.admin, self.group_url)

        self.assertContains(response, '<span class="num">1</span>')
        self.assertContains(response, 'title="Абрамов Аркадий">Абрамов А.</span>')

    def test_group_student_count_is_shown_to_everyone(self):
        stranger = get_user_model().objects.create_user(
            "stranger", password=PASSWORD
        )

        response = self.open(stranger, self.group_url)

        self.assertContains(
            response, 'Студенты <span class="profile-count">1</span>'
        )
        self.assertContains(response, "Список студентов видят")

    # --- ссылки на тег ---------------------------------------------------------

    def test_tag_tile_leads_admin_to_users_with_that_tag(self):
        self.tag_group()
        link = f'href="{reverse("users")}?tag=g{self.tag.pk}"'

        self.assertContains(self.open(self.admin, self.group_url), link)

        for who in (self.user, self.teacher_user):
            self.assertNotContains(self.open(who, self.group_url), link)

    def test_tag_link_opens_filtered_users_page(self):
        self.tag_group()

        response = self.open(
            self.admin, f"{reverse('users')}?tag=g{self.tag.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.student_url)

    # --- кнопки ---------------------------------------------------------------

    def head(self, who, url):
        html = self.open(who, url).content.decode()

        return html[html.index("page-head-actions"):html.index("</header>")]

    def test_every_profile_has_schedule_button(self):
        self.assertIn(
            reverse("group_schedule", args=[self.group.pk]),
            self.head(self.admin, self.student_url),
        )
        self.assertIn(
            f"{reverse('teachers')}?teacher={self.teacher.pk}",
            self.head(self.admin, self.teacher_url),
        )
        self.assertIn(
            reverse("group_schedule", args=[self.group.pk]),
            self.head(self.admin, self.group_url),
        )

        for url in (self.student_url, self.teacher_url, self.group_url):
            self.assertIn("Расписание", self.head(self.admin, url))

    def test_edit_button_is_for_admin_only(self):
        for url in (self.student_url, self.teacher_url, self.group_url):
            self.assertIn("Редактировать", self.head(self.admin, url))
            self.assertNotIn("Редактировать", self.head(self.teacher_user, url))

    def test_group_edit_button_leads_to_group_editor(self):
        self.assertIn(
            reverse("group_edit", args=[self.group.pk]),
            self.head(self.admin, self.group_url),
        )

    def test_user_edit_button_leads_to_the_row_on_users_page(self):
        head = self.head(self.admin, self.student_url)
        query = urlencode({"q": self.user.get_username(), "new": self.user.pk})

        self.assertIn(f"{reverse('users')}?{query}".replace("&", "&amp;"), head)

        response = self.open(
            self.admin, f"{reverse('users')}?{query}"
        )

        self.assertContains(response, self.student_url)
        self.assertContains(response, 'class="is-new"')

    def test_teacher_without_account_has_no_edit_button(self):
        legacy = Teacher.objects.create(teacher_name="Старый С. С.")

        head = self.head(
            self.admin, reverse("teacher_card", args=[legacy.pk])
        )

        self.assertNotIn("Редактировать", head)

    # --- стили ----------------------------------------------------------------

    def test_css_has_two_tile_wide_lists_and_mobile_rows(self):
        css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

        self.assertRegex(css, r"\.profile-section \{\s*grid-column: span 2;")
        self.assertRegex(css, r"\.profile-facts\.cols-4 \{\s*grid-template-columns: repeat\(4")
        self.assertIn("flex-direction: row;", css[css.index("Раунд 15"):])


# ---------------------------------------------------------------------------
# Раунд 22: семестры (основа для фильтров журнала)
# ---------------------------------------------------------------------------

from .semesters import (
    course_of_semester,
    semester_at,
    semester_ranges,
    semesters_of_course,
)


def semester_data(number, start, end, prefix="semester"):
    return {
        f"{prefix}-semester_number": number,
        f"{prefix}-start_date": start,
        f"{prefix}-end_date": end,
    }


class SemesterKuznyaTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, number, start, end):
        return self.client.post(
            reverse("workshop"),
            {"action": "add_semester", **semester_data(number, start, end)},
        )

    def form_errors(self, response):
        return response.context["cards"]["semester"]["form"].errors

    def test_card_is_on_the_page(self):
        response = self.client.get(reverse("workshop"))

        self.assertContains(response, "<h2>Семестры</h2>", html=True)
        self.assertContains(response, 'name="semester-start_date"')
        self.assertContains(response, 'type="date"')

    def test_add_semester(self):
        response = self.add(1, "2026-09-01", "2026-12-31")

        self.assertEqual(response.status_code, 302)
        item = Semester.objects.get()

        self.assertEqual(item.semester_number, 1)
        self.assertEqual(item.start_date, date(2026, 9, 1))
        self.assertEqual(item.course, 1)

    def test_number_must_be_from_one_to_eight(self):
        for bad in (0, 9, "x"):
            with self.subTest(bad=bad):
                response = self.add(bad, "2026-09-01", "2026-12-31")

                self.assertIn("semester_number", self.form_errors(response))

        self.assertFalse(Semester.objects.exists())

    def test_one_interval_per_semester(self):
        self.add(1, "2026-09-01", "2026-12-31")
        response = self.add(1, "2027-01-10", "2027-05-31")

        self.assertIn("уже задан", str(self.form_errors(response)))
        self.assertEqual(Semester.objects.count(), 1)

    def test_end_before_start_is_an_error(self):
        response = self.add(1, "2026-12-31", "2026-09-01")

        self.assertIn("раньше начала", str(self.form_errors(response)))
        self.assertFalse(Semester.objects.exists())

    def test_one_day_semester_is_allowed(self):
        self.add(1, "2026-09-01", "2026-09-01")

        self.assertEqual(Semester.objects.count(), 1)

    def test_overlap_is_an_error_and_names_the_other_semester(self):
        self.add(1, "2026-09-01", "2026-12-31")
        response = self.add(2, "2026-12-20", "2027-05-31")

        self.assertIn("пересекаются с 1 семестром", str(self.form_errors(response)))
        self.assertEqual(Semester.objects.count(), 1)

    def test_shared_boundary_day_counts_as_overlap(self):
        self.add(1, "2026-09-01", "2026-12-31")
        self.add(2, "2026-12-31", "2027-05-31")

        self.assertEqual(Semester.objects.count(), 1)

    def test_adjacent_semesters_are_fine(self):
        self.add(1, "2026-09-01", "2026-12-31")
        self.add(2, "2027-01-01", "2027-05-31")

        self.assertEqual(Semester.objects.count(), 2)

    def test_edit_can_move_dates_but_not_into_another_semester(self):
        self.add(1, "2026-09-01", "2026-12-31")
        self.add(2, "2027-01-10", "2027-05-31")
        first = Semester.objects.get(semester_number=1)

        def update(start, end):
            return self.client.post(reverse("workshop"), {
                "action": "update_item", "edit": "semester", "item": first.pk,
                **semester_data(1, start, end),
            })

        # Свой же прежний интервал пересечением не считается
        update("2026-09-02", "2026-12-30")
        first.refresh_from_db()
        self.assertEqual(first.start_date, date(2026, 9, 2))

        # Заезд на второй семестр — ошибка, дат не меняет
        response = update("2026-09-02", "2027-02-01")
        first.refresh_from_db()

        self.assertEqual(first.end_date, date(2026, 12, 30))
        self.assertContains(response, "Проверьте заполненные поля")

    def test_picker_lists_semesters_in_order(self):
        self.add(2, "2027-01-10", "2027-05-31")
        self.add(1, "2026-09-01", "2026-12-31")

        response = self.client.get(reverse("workshop"), {"edit": "semester"})
        labels = [e["label"] for e in response.context["cards"]["semester"]["items"]]

        self.assertEqual(labels, [
            "1 семестр · 01.09.2026 — 31.12.2026",
            "2 семестр · 10.01.2027 — 31.05.2027",
        ])

    def test_delete_with_confirmation(self):
        self.add(1, "2026-09-01", "2026-12-31")
        item = Semester.objects.get()
        url = reverse("workshop")
        fields = {"edit": "semester", "item": item.pk}

        response = self.client.post(url, {"action": "delete_item", **fields})
        self.assertContains(response, "Удалить «1 семестр")
        self.assertTrue(Semester.objects.exists())

        self.client.post(url, {"action": "delete_confirmed", **fields})
        self.assertFalse(Semester.objects.exists())

    def test_only_staff_can_add(self):
        self.client.force_login(self.user)
        response = self.add(1, "2026-09-01", "2026-12-31")

        self.assertFalse(Semester.objects.exists())
        self.assertNotEqual(response.status_code, 200)


class SemesterHelpersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Semester.objects.create(
            semester_number=1, start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 31))
        Semester.objects.create(
            semester_number=2, start_date=date(2027, 1, 11),
            end_date=date(2027, 5, 31))

    def test_course_numbers(self):
        self.assertEqual(semesters_of_course(1), (1, 2))
        self.assertEqual(semesters_of_course(4), (7, 8))
        self.assertEqual(
            [course_of_semester(n) for n in range(1, 9)],
            [1, 1, 2, 2, 3, 3, 4, 4],
        )

    def test_semester_at_date(self):
        self.assertEqual(semester_at(date(2026, 9, 1)).semester_number, 1)
        self.assertEqual(semester_at(date(2026, 12, 31)).semester_number, 1)
        self.assertEqual(semester_at(date(2027, 3, 3)).semester_number, 2)

    def test_holidays_have_no_semester(self):
        self.assertIsNone(semester_at(date(2027, 1, 5)))
        self.assertIsNone(semester_at(date(2026, 8, 31)))

    def test_ranges_for_chosen_semesters(self):
        self.assertEqual(
            semester_ranges([2]), [(date(2027, 1, 11), date(2027, 5, 31))]
        )
        self.assertEqual(len(semester_ranges()), 2)
        self.assertEqual(semester_ranges([7, 8]), [])
        self.assertEqual(semester_ranges([99]), [])

    def test_database_constraint_rejects_reversed_dates(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Semester.objects.create(
                semester_number=3, start_date=date(2027, 6, 2),
                end_date=date(2027, 6, 1))


class SemesterScriptTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR) / "database"
        cls.script = (base / "add_semesters.py").read_text(encoding="utf-8")
        cls.create = (base / "create_db.py").read_text(encoding="utf-8")

    def test_table_and_columns_match_model(self):
        self.assertEqual(Semester._meta.db_table, "Semester")

        for script in (self.script, self.create):
            self.assertIn('CREATE TABLE IF NOT EXISTS "Semester"', script)
            self.assertIn("semester_dates_order", script)

            for field in Semester._meta.concrete_fields:
                self.assertIn(field.column, script)

    def test_script_is_safe_to_rerun(self):
        self.assertNotIn("DROP", self.script)
        self.assertNotIn("DELETE", self.script)
