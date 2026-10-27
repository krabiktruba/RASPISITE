from datetime import date, time
from io import StringIO
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.contrib.messages import constants as message_levels
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse

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
from .gmail import normalize_gmail
from .models import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    BreakHours,
    Calendar,
    Course,
    Day,
    Group,
    Pair,
    Profile,
    ScheduleChange,
    Shift,
    StudyHours,
    Tag,
    TagType,
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

        pair = cls.pair = Pair.objects.create(
            group=cls.group,
            teacher=cls.teacher,
            pair_name="Математика",
        )

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
        get_user_model().objects.create_user("student", password=PASSWORD)

        response = self.login("student")

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
        free = Teacher.objects.create(teacher_name="Свободный С. С.")

        self.client.post(reverse("users"), {
            "action": "set_role",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
            "teacher": free.pk,
        })

        user = get_user_model().objects.get(pk=self.user.pk)

        self.assertEqual(user_role(user), ROLE_TEACHER)
        self.assertEqual(own_teacher_id(user), free.pk)
        self.assertFalse(user.is_staff)

    def test_teacher_role_without_choice_creates_teacher_from_name(self):
        self.client.force_login(self.admin)
        self.user.last_name = "Сидоров"
        self.user.first_name = "Сидор"
        self.user.save()

        self.client.post(reverse("users"), {
            "action": "set_role",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
            "teacher": "",
        })

        user = get_user_model().objects.get(pk=self.user.pk)
        teacher = Teacher.objects.get(pk=own_teacher_id(user))

        self.assertEqual(teacher.teacher_name, "Сидоров Сидор")
        self.assertIn(teacher, real_teachers())

    def test_teacher_of_another_user_cannot_be_taken(self):
        self.client.force_login(self.admin)

        self.client.post(reverse("users"), {
            "action": "set_role",
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
            "action": "set_role",
            "user_id": self.user.pk,
            "role": ROLE_ADMIN,
        })

        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertTrue(user.is_staff)

        self.client.post(reverse("users"), {
            "action": "set_role",
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
            "action": "set_role",
            "user_id": self.admin.pk,
            "role": "student",
        })
        self.client.post(reverse("users"), {
            "action": "set_role",
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
            "action": "set_role",
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

    def test_teacher_cannot_be_in_two_places(self):
        other_group = Group.objects.create(
            group_name="Б-01",
            group_course=self.course,
            shift=self.shift,
        )
        other_pair = Pair.objects.create(
            group=other_group,
            teacher=self.teacher,
            pair_name="Физика",
        )
        Day.objects.create(
            group=other_group,
            pair=other_pair,
            pair_number=2,
            weekday=Weekday.objects.get(pk=2),
        )

        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {
            "cell_2_2": self.pair.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже ведёт занятие")
        self.assertFalse(
            Day.objects.filter(
                group=self.group, weekday_id=2, pair_number=2
            ).exists()
        )

    def test_foreign_pair_is_rejected(self):
        other_group = Group.objects.create(
            group_name="Б-01",
            group_course=self.course,
            shift=self.shift,
        )
        foreign = Pair.objects.create(
            group=other_group,
            teacher=Teacher.objects.create(teacher_name="Сидоров С. С."),
            pair_name="Химия",
        )

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

        self.assertRedirects(response, reverse("users"))

        student = self.get_user()
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

        self.assertRedirects(response, reverse("users"))

        user = self.get_user("petrov77@gmail.com")
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

    def test_page_number_is_kept_in_redirect(self):
        response = self.post_user(page="1")

        self.assertRedirects(response, reverse("users") + "?page=1")

    # --- список пользователей --------------------------------------------

    def test_users_table_shows_full_name(self):
        self.post_user()

        page = self.client.get(reverse("users"))

        self.assertContains(page, "Иванов Иван Иванович")
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
            "action": "set_group",
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
            "action": "set_role",
            "user_id": self.user.pk,
            "role": ROLE_TEACHER,
            "teacher": self.teacher.pk,
        })
        self.assertIsNone(self.fresh(self.user).profile.group_id)

        # Возврат в студенты не восстанавливает прежнюю группу
        self.client.post(reverse("users"), {
            "action": "set_role",
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
        })

        self.assertEqual(get_user_model().objects.count(), before)

    def test_add_teacher_action_does_nothing(self):
        before = Teacher.objects.count()

        self.client.post(reverse("workshop"), {
            "action": "add_teacher",
            "teacher-teacher_name": "Новый Н. Н.",
            "teacher-teacher_tag": "",
        })

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
        tags = html.index("<h2>Теги</h2>")
        tag_types = html.index("<h2>Типы тегов</h2>")

        self.assertLess(bells, data)
        self.assertLess(data, groups)
        self.assertLess(groups, pairs)
        self.assertLess(pairs, tags)
        self.assertLess(tags, tag_types)


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
            "group-group_name": "В-03",
            "group-group_course": 1,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.teacher.pk,
        })

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertFalse(Group.objects.filter(group_name="В-03").exists())

    def test_new_group_takes_free_curator(self):
        self.client.post(reverse("workshop"), {
            "action": "add_group",
            "group-group_name": "В-03",
            "group-group_course": 1,
            "group-shift": 1,
            "group-group_tag": "",
            "group-group_curator": self.petrov.pk,
        })

        self.assertEqual(
            Group.objects.get(group_name="В-03").group_curator_id,
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
        })

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
            "pair-group": self.group.pk,
            "pair-teacher": teacher.pk,
        })

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
        pair = Pair.objects.create(
            group=self.group, teacher=self.legacy, pair_name="История"
        )

        response = self.client.post(reverse("workshop"), {
            "action": "update_item",
            "edit": "pair",
            "item": pair.pk,
            "pair-pair_name": "Всеобщая история",
            "pair-group": self.group.pk,
            "pair-teacher": self.legacy.pk,
        })

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
        other_pair = Pair.objects.create(
            group=other_group,
            teacher=self.teacher2,
            pair_name="Физика",
        )
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
        other_pair = Pair.objects.create(
            group=other_group,
            teacher=self.teacher,
            pair_name="Алгебра",
        )
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

        return self.client.post(reverse("workshop"), data)

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
        other_pair = Pair.objects.create(
            group=other_group,
            teacher=other_teacher,
            pair_name="Физика",
        )
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

        for key in ("tag", "tag_type", "group", "pair"):
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

        for key in ("tag", "tag_type", "teacher", "pair"):
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

    def test_rename_tag_type_and_duplicate_is_rejected(self):
        first = TagType.objects.create(tag_type_name="Отделение")
        TagType.objects.create(tag_type_name="Профиль")

        response = self.post(
            "update_item", "tag_type", first.pk,
            **{"tag_type-tag_type_name": "Профиль"},
        )

        self.assertContains(response, "Проверьте заполненные поля.")
        self.assertEqual(
            TagType.objects.get(pk=first.pk).tag_type_name, "Отделение"
        )

        self.post(
            "update_item", "tag_type", first.pk,
            **{"tag_type-tag_type_name": "Факультет"},
        )
        self.assertEqual(
            TagType.objects.get(pk=first.pk).tag_type_name, "Факультет"
        )

    def test_pair_group_cannot_be_changed(self):
        _, other_group, _ = self.second_group_with_pair()

        self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Алгебра",
                "pair-group": other_group.pk,
                "pair-teacher": self.teacher.pk,
            },
        )

        pair = Pair.objects.get(pk=self.pair.pk)
        self.assertEqual(pair.pair_name, "Алгебра")
        self.assertEqual(pair.group_id, self.group.pk)

    def test_pair_teacher_change_checks_schedule_conflict(self):
        other_teacher, _, _ = self.second_group_with_pair()

        # Понедельник, пара 1: у Петрова уже стоит Физика у группы Б-02
        response = self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Математика",
                "pair-group": self.group.pk,
                "pair-teacher": other_teacher.pk,
            },
        )

        self.assertContains(response, "уже ведёт занятие у группы Б-02")
        self.assertEqual(
            Pair.objects.get(pk=self.pair.pk).teacher_id, self.teacher.pk
        )

    def test_pair_teacher_change_without_conflict(self):
        other_teacher = make_teacher("Сидоров С. С.", "teachersidorov@gmail.com")

        self.post(
            "update_item", "pair", self.pair.pk,
            **{
                "pair-pair_name": "Математика",
                "pair-group": self.group.pk,
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
        self.assertIn("пары — 1", card["confirm_text"])
        self.assertIn("занятия в расписании — 1", card["confirm_text"])
        self.assertIn("Студенты (1)", card["confirm_text"])
        self.assertContains(response, 'value="delete_confirmed"')
        self.assertNotContains(response, 'value="update_item"')

    def test_confirmed_delete_removes_group_with_its_schedule(self):
        set_group(self.user, self.group)

        response = self.post("delete_confirmed", "group", self.group.pk)

        self.assertContains(response, "Удалено: А-01.")
        self.assertFalse(Group.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(Pair.objects.filter(pk=self.pair.pk).exists())
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

    def test_tag_type_with_tags_cannot_be_deleted(self):
        tag_type = TagType.objects.create(tag_type_name="Отделение")
        Tag.objects.create(tag_type=tag_type, tag_name="Очное")

        response = self.post("delete_confirmed", "tag_type", tag_type.pk)

        self.assertContains(response, "у него есть теги")
        self.assertTrue(TagType.objects.filter(pk=tag_type.pk).exists())

    def test_tag_is_deleted_and_removed_from_teacher(self):
        tag_type = TagType.objects.create(tag_type_name="Отделение")
        tag = Tag.objects.create(tag_type=tag_type, tag_name="Очное")
        self.teacher.teacher_tag = tag
        self.teacher.save()

        response = self.post("delete_item", "tag", tag.pk)
        self.assertIn("преподаватели — 1", response.context["cards"]["tag"]["confirm_text"])

        self.post("delete_confirmed", "tag", tag.pk)

        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
        self.assertIsNone(Teacher.objects.get(pk=self.teacher.pk).teacher_tag_id)

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
            "action": "add_tag_type",
            "tag_type-tag_type_name": "Отделение",
        })

        self.assertContains(response, "Запись добавлена.")
        self.assertTrue(
            TagType.objects.filter(tag_type_name="Отделение").exists()
        )


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

        return self.client.post(reverse("workshop"), post)

    def report(self, response):
        return response.context["import_report"]

    def fill_extra_data(self):
        tag_type = TagType.objects.create(tag_type_name="Отделение")
        tag = Tag.objects.create(tag_type=tag_type, tag_name="Очное")

        self.teacher.teacher_tag = tag
        self.teacher.save()
        self.group.group_curator = self.teacher
        self.group.group_tag = tag
        self.group.save()

        BreakHours.objects.create(
            shift=self.shift,
            break_number=1,
            break_start_time=time(10, 0),
            break_end_time=time(10, 10),
        )

    def second_pair(self, name="Физика"):
        return Pair.objects.create(
            group=self.group, teacher=self.teacher, pair_name=name
        )

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
        files = {kind: self.text(kind) for kind in self.ORDER}

        Day.objects.all().delete()
        Pair.objects.all().delete()
        Group.objects.all().delete()
        Teacher.objects.all().delete()
        Tag.objects.all().delete()
        TagType.objects.all().delete()
        StudyHours.objects.all().delete()
        BreakHours.objects.all().delete()

        for kind in self.ORDER:
            report = self.report(self.upload(kind, files[kind]))

            self.assertEqual(report["fatal"], "", kind)
            self.assertEqual(report["errors"], [], kind)
            self.assertTrue(report["saved"], kind)

        teacher = Teacher.objects.get(teacher_name="Иванов И. И.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Очное")

        group = Group.objects.get(group_name="А-01")
        self.assertEqual(group.group_course_id, 1)
        self.assertEqual(group.shift_id, 1)
        self.assertEqual(group.group_curator_id, teacher.pk)
        self.assertEqual(group.group_tag.tag_type.tag_type_name, "Отделение")

        day = Day.objects.get()
        self.assertEqual(day.pair.pair_name, "Математика")
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
            "ФИО;Тег\r\nХороший Х. Х.;\r\nПлохой П. П.;без двоеточия\r\n",
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
            "teachers", "Тег;ФИО\r\nОтделение: Очное;Сидоров С. С.\r\n"
        )

        self.assertEqual(self.report(response)["errors"], [])
        teacher = Teacher.objects.get(teacher_name="Сидоров С. С.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Очное")

    # --- содержимое наборов ------------------------------------------------

    def test_tag_is_created_from_text(self):
        self.upload(
            "teachers", "ФИО;Тег\r\nНовый Н. Н.;Отделение: Заочное\r\n"
        )

        teacher = Teacher.objects.get(teacher_name="Новый Н. Н.")
        self.assertEqual(teacher.teacher_tag.tag_name, "Заочное")
        self.assertEqual(
            teacher.teacher_tag.tag_type.tag_type_name, "Отделение"
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
        self.assertEqual(Pair.objects.filter(group=self.group).count(), 2)

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
        self.assertIn("нет пары «Химия»", errors[0])
        self.assertIn("не учебный день", errors[1])
        self.assertIn("от 1 до 6", errors[2])

    def test_schedule_teacher_cannot_be_in_two_groups(self):
        other_group = Group.objects.create(
            group_name="Б-02", group_course=self.course, shift=self.shift
        )
        Pair.objects.create(
            group=other_group, teacher=self.teacher, pair_name="Физика"
        )

        # Понедельник, пара 1: у Иванова уже стоит Математика у А-01
        response = self.upload(
            "schedule", "Группа;День недели;Номер пары;Предмет;Преподаватель\r\n"
            "Б-02;Понедельник;1;Физика;Иванов И. И.\r\n"
        )
        report = self.report(response)

        self.assertIn("стоит сразу у нескольких групп", report["errors"][0])
        self.assertFalse(Day.objects.filter(group=other_group).exists())

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
