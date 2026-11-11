from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from .tags import TAG_COLOR_DEFAULT, color_class


class TagBase(models.Model):
    """Общие поля тега: название и цвет метки.

    Теги групп и теги преподавателей — две отдельные таблицы (GroupTag и
    TeacherTag): у них свои названия и свои цвета, и один не мешает другому.
    """

    tag_id = models.AutoField(primary_key=True)
    tag_name = models.CharField(max_length=100, unique=True)
    # Цвет метки тега: #RRGGBB
    tag_color = models.CharField(
        max_length=7,
        blank=True,
        default=TAG_COLOR_DEFAULT,
        db_column="tag_color",
        validators=[
            RegexValidator(
                r"^#[0-9a-fA-F]{6}$",
                "Цвет нужно указать в виде #RRGGBB.",
            )
        ],
    )

    class Meta:
        abstract = True

    def __str__(self):
        return self.tag_name

    @property
    def full_name(self):
        """Имя тега для подписей и подсказок."""
        return self.tag_name

    @property
    def color_class(self):
        return color_class(self.tag_color)


class GroupTag(TagBase):
    """Тег группы."""

    class Meta:
        db_table = "GroupTag"


class TeacherTag(TagBase):
    """Тег преподавателя."""

    class Meta:
        db_table = "TeacherTag"


class Teacher(models.Model):
    teacher_id = models.AutoField(primary_key=True)
    teacher_tag = models.ForeignKey(
        TeacherTag,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="teacher_tag"
    )
    teacher_name = models.CharField(max_length=150)

    class Meta:
        db_table = "Teacher"

    def __str__(self):
        return self.teacher_name


class Course(models.Model):
    course_id = models.SmallIntegerField(
        primary_key=True
    )

    class Meta:
        db_table = "Course"

    def __str__(self):
        return f"{self.course_id} курс"


class Shift(models.Model):
    shift_id = models.AutoField(primary_key=True)

    class Meta:
        db_table = "Shift"

    def __str__(self):
        return f"{self.shift_id} смена"


class Group(models.Model):
    group_id = models.AutoField(primary_key=True)
    group_tag = models.ForeignKey(
        GroupTag,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="group_tag"
    )
    group_curator = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="group_curator_id"
    )
    group_course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        db_column="group_course"
    )
    shift = models.ForeignKey(
        Shift,
        on_delete=models.PROTECT,
        db_column="shift"
    )
    group_name = models.CharField(max_length=100)

    class Meta:
        db_table = "Group"
        indexes = [
            # «Кураторы» и группа преподавателя: поиск по куратору
            models.Index(fields=["group_curator"], name="group_curator_idx"),
        ]

    def __str__(self):
        return self.group_name


class Pair(models.Model):
    """Пара: предмет с преподавателем.

    Самостоятельный объект: одна и та же пара привязывается к нескольким
    группам (связь PairGroup). В расписание группы ставятся только пары,
    привязанные к ней.
    """

    pair_id = models.AutoField(primary_key=True)
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        db_column="teacher"
    )
    pair_name = models.CharField(max_length=150)
    groups = models.ManyToManyField(
        Group,
        through="PairGroup",
        related_name="pairs",
    )

    class Meta:
        db_table = "Pairs"

    def __str__(self):
        return self.pair_name


class PairGroup(models.Model):
    """Привязка пары к группе."""

    pair_group_id = models.AutoField(primary_key=True)
    pair = models.ForeignKey(
        Pair,
        on_delete=models.CASCADE,
        db_column="pair_id"
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        db_column="group_id"
    )

    class Meta:
        db_table = "PairGroup"
        constraints = [
            models.UniqueConstraint(
                fields=["pair", "group"],
                name="unique_pair_group"
            )
        ]

    def __str__(self):
        return f"{self.pair_id} → {self.group_id}"


# Семестры: по два на курс, номера 1–8 (курс N = семестры 2N-1 и 2N)
SEMESTER_NUMBERS = range(1, 9)


class Semester(models.Model):
    """Учебный семестр: номер и даты. Один интервал на номер."""

    semester_id = models.AutoField(primary_key=True)
    semester_number = models.SmallIntegerField(
        unique=True,
        choices=[(n, f"{n} семестр") for n in SEMESTER_NUMBERS],
        db_column="semester_number",
        error_messages={
            "unique": "Этот семестр уже задан: измените его через «Изменить».",
        },
    )
    start_date = models.DateField(db_column="start_date")
    end_date = models.DateField(db_column="end_date")

    class Meta:
        db_table = "Semester"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="semester_dates_order",
            ),
        ]

    @property
    def course(self):
        return (self.semester_number + 1) // 2

    def __str__(self):
        return f"{self.semester_number} семестр"


class Weekday(models.Model):
    weekday_id = models.SmallIntegerField(primary_key=True)
    weekday_name = models.CharField(max_length=20, unique=True)

    class Meta:
        db_table = "Weekday"

    def __str__(self):
        return self.weekday_name


class Day(models.Model):
    day_id = models.AutoField(primary_key=True)
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        db_column="group"
    )
    pair = models.ForeignKey(
        Pair,
        on_delete=models.CASCADE,
        db_column="pair_id"
    )
    pair_number = models.IntegerField()
    weekday = models.ForeignKey(
        Weekday,
        on_delete=models.PROTECT,
        db_column="weekday"
    )

    class Meta:
        db_table = "Day"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "weekday", "pair_number"],
                name="unique_group_weekday_pair"
            )
        ]


class Calendar(models.Model):
    calendar_id = models.AutoField(primary_key=True)
    date = models.DateField(unique=True)
    weekday = models.SmallIntegerField()
    is_holiday = models.BooleanField(default=False)
    holiday_name = models.CharField(
        max_length=150,
        null=True,
        blank=True
    )

    class Meta:
        db_table = "Calendar"

    def save(self, *args, **kwargs):
        # День недели всегда берётся из даты, чтобы не расходиться с календарём.
        if self.date:
            self.weekday = self.date.isoweekday()

        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.date)


class StudyHours(models.Model):
    study_hours_id = models.AutoField(primary_key=True)

    shift = models.ForeignKey(
        Shift,
        on_delete=models.CASCADE,
        db_column="shift"
    )

    pair_number = models.IntegerField()
    pair_start_time = models.TimeField()
    pair_end_time = models.TimeField()

    class Meta:
        db_table = "StudyHours"
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "pair_number"],
                name="unique_shift_pair"
            )
        ]

class BreakHours(models.Model):
    break_hours_id = models.AutoField(primary_key=True)
    shift = models.ForeignKey(
        Shift,
        on_delete=models.CASCADE,
        db_column="shift"
    )
    break_number = models.IntegerField()
    break_start_time = models.TimeField()
    break_end_time = models.TimeField()

    class Meta:
        db_table = "BreakHours"
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "break_number"],
                name="unique_shift_break"
            )
        ]


class ScheduleChange(models.Model):
    change_id = models.AutoField(primary_key=True)

    calendar = models.ForeignKey(
        Calendar,
        on_delete=models.CASCADE,
        db_column="calendar"
    )

    # Группа, у которой меняется занятие: одна пара ведётся у нескольких групп
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        db_column="group"
    )

    pair = models.ForeignKey(
        Pair,
        on_delete=models.CASCADE,
        db_column="pair"
    )

    # Номер пары в исходный день: у одной Pair бывает несколько слотов в день
    old_pair_number = models.IntegerField(
        null=True,
        blank=True,
        db_column="old_pair_number"
    )

    new_calendar = models.ForeignKey(
        Calendar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="new_calendar_id",
        related_name="schedule_changes"
    )

    new_teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="new_teacher"
    )

    new_pair_number = models.IntegerField(
        null=True,
        blank=True
    )

    new_pair_name = models.CharField(
        max_length=150,
        null=True,
        blank=True
    )

    study_hours = models.ForeignKey(
        StudyHours,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="study_hours_id"
    )

    change_type = models.CharField(max_length=20)

    class Meta:
        db_table = "ScheduleChange"

    def __str__(self):
        return f"{self.change_type}: {self.pair}"


ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"

ROLE_CHOICES = [
    (ROLE_STUDENT, "Студент"),
    (ROLE_TEACHER, "Преподаватель"),
    (ROLE_ADMIN, "Администратор"),
]


class Profile(models.Model):
    """Роль пользователя на сайте.

    Для преподавателя здесь же хранится связь с записью Teacher,
    а для студента — его учебная группа.
    """

    profile_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        db_column="user_id"
    )
    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default=ROLE_STUDENT
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="teacher"
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
        db_column="group"
    )
    # Имя и фамилия лежат в самом пользователе (first_name, last_name),
    # отчество необязательно и хранится здесь
    patronymic = models.CharField(
        max_length=150,
        blank=True,
        default="",
        db_column="patronymic"
    )

    class Meta:
        db_table = "Profile"
        indexes = [
            # Фильтры страницы «Пользователи»: роль, группа, преподаватель
            models.Index(fields=["role"], name="profile_role_idx"),
            models.Index(fields=["group"], name="profile_group_idx"),
            models.Index(fields=["teacher"], name="profile_teacher_idx"),
        ]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"
