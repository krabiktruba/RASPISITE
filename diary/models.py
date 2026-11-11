"""Дневник: оценки и отметки студентов за занятия.

Приложение отдельное от расписания, но занятие определяется так же, как у
разовых правок расписания: дата + пара + номер пары в этот день (номер нужен,
потому что одна и та же пара может стоять в дне дважды).

Таблица создаётся SQL-скриптом database/add_diary_tables.py (как и таблицы
приложения schedule), миграций у приложения нет.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from schedule.models import Pair, Teacher

MARK_SCORE = "score"
MARK_ABSENT = "absent"
MARK_ILL = "ill"

MARK_CHOICES = [
    (MARK_SCORE, "Оценка"),
    (MARK_ABSENT, "Н (не был)"),
    (MARK_ILL, "Б (болеет)"),
]

# Короткие подписи для отметок без баллов
MARK_SHORT = {
    MARK_ABSENT: "Н",
    MARK_ILL: "Б",
}

MIN_SCORE = 0
MAX_SCORE = 100
FIRST_PAIR_NUMBER = 1
LAST_PAIR_NUMBER = 6


class Mark(models.Model):
    """Одна запись дневника: студент, занятие и оценка (или Н / Б).

    На одно занятие у студента одна запись. Оценка по 100-балльной шкале
    либо отметка «Н» (не был) или «Б» (болеет); для отметок баллов нет.
    """

    mark_id = models.AutoField(primary_key=True)
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="diary_marks",
        db_column="student_id",
    )
    pair = models.ForeignKey(
        Pair,
        on_delete=models.CASCADE,
        related_name="diary_marks",
        db_column="pair",
    )
    lesson_date = models.DateField()
    pair_number = models.SmallIntegerField()
    kind = models.CharField(
        max_length=6,
        choices=MARK_CHOICES,
        default=MARK_SCORE,
    )
    score = models.SmallIntegerField(null=True, blank=True)
    # Кто поставил (при замене это может быть не преподаватель пары)
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diary_marks",
        db_column="teacher",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "DiaryMark"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "pair", "lesson_date", "pair_number"],
                name="unique_diary_mark_slot",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind=MARK_SCORE,
                        score__isnull=False,
                        score__gte=MIN_SCORE,
                        score__lte=MAX_SCORE,
                    )
                    | (~Q(kind=MARK_SCORE) & Q(score__isnull=True))
                ),
                name="diary_mark_kind_score",
            ),
            models.CheckConstraint(
                condition=Q(
                    pair_number__gte=FIRST_PAIR_NUMBER,
                    pair_number__lte=LAST_PAIR_NUMBER,
                ),
                name="diary_mark_pair_number",
            ),
        ]
        indexes = [
            # «Дневник студента»: его записи по датам
            models.Index(
                fields=["student", "lesson_date"],
                name="diary_mark_student_date",
            ),
            # «Журнал занятия»: все оценки одного занятия
            models.Index(
                fields=["pair", "lesson_date", "pair_number"],
                name="diary_mark_slot",
            ),
        ]

    def __str__(self):
        return f"{self.student_id} · {self.lesson_date}: {self.label}"

    @property
    def label(self):
        """Что показывать в дневнике: число баллов, «Н» или «Б»."""
        if self.kind == MARK_SCORE:
            return "" if self.score is None else str(self.score)

        return MARK_SHORT.get(self.kind, "")

    def clean(self):
        if not FIRST_PAIR_NUMBER <= (self.pair_number or 0) <= LAST_PAIR_NUMBER:
            raise ValidationError(
                {"pair_number": "Номер пары должен быть от 1 до 6."}
            )

        if self.kind == MARK_SCORE:
            if self.score is None or not MIN_SCORE <= self.score <= MAX_SCORE:
                raise ValidationError(
                    {"score": "Оценка — число от 0 до 100."}
                )

        elif self.kind in MARK_SHORT:
            if self.score is not None:
                raise ValidationError(
                    {"score": "У отметки Н или Б баллов нет."}
                )

        else:
            raise ValidationError({"kind": "Неизвестный вид записи."})
