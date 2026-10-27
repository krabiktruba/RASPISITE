from django.conf import settings
from django.db import models


class TagType(models.Model):
    tag_type_id = models.AutoField(primary_key=True)
    tag_type_name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = "TagType"

    def __str__(self):
        return self.tag_type_name


class Tag(models.Model):
    tag_id = models.AutoField(primary_key=True)
    tag_type = models.ForeignKey(
        TagType,
        on_delete=models.PROTECT,
        db_column="tag_type"
    )
    tag_name = models.CharField(max_length=100)

    class Meta:
        db_table = "Tag"
        constraints = [
            models.UniqueConstraint(
                fields=["tag_type", "tag_name"],
                name="unique_tag_type_name"
            )
        ]

    def __str__(self):
        return self.tag_name


class Teacher(models.Model):
    teacher_id = models.AutoField(primary_key=True)
    teacher_tag = models.ForeignKey(
        Tag,
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
        Tag,
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

    def __str__(self):
        return self.group_name


class Pair(models.Model):
    pair_id = models.AutoField(primary_key=True)
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        db_column="group"
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        db_column="teacher"
    )
    pair_name = models.CharField(max_length=150)

    class Meta:
        db_table = "Pairs"

    def __str__(self):
        return self.pair_name


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

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"
