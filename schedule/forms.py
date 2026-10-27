import hashlib
import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Q

from .access import create_account, real_teachers
from .gmail import MAX_ADDRESS_LENGTH, normalize_gmail
from .models import (
    ROLE_STUDENT,
    ROLE_TEACHER,
    TagType,
    Tag,
    Teacher,
    Group,
    Pair,
    Course,
    Shift,
    BreakHours,
)

FIELD_LABELS = {
    "tag_type_name": "Название типа тега",
    "tag_type": "Тип тега",
    "tag_name": "Название тега",
    "teacher_tag": "Тег преподавателя",
    "teacher_name": "ФИО преподавателя",
    "group_tag": "Тег группы",
    "group_curator": "Куратор группы",
    "group_course": "Курс",
    "group_name": "Название группы",
    "shift": "Смена",
    "pair_name": "Название предмета",
    "group": "Группа",
    "teacher": "Преподаватель",
    "pair_number": "Номер пары",
    "break_number": "Номер перемены",
    "break_start_time": "Начало перемены",
    "break_end_time": "Конец перемены",
    "pair_start_time": "Начало пары",
    "pair_end_time": "Конец пары",
    "start_time": "Начало",
    "end_time": "Конец",
    "role": "Роль",
    "last_name": "Фамилия",
    "first_name": "Имя",
    "patronymic": "Отчество (необязательно)",
    "username": "Почта Gmail (логин)",
    "password": "Пароль",
}

class WorkshopFormMixin:
    """Общие подписи и оформление полей мастерской."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        ordering = {
            "TagType": "tag_type_name",
            "Tag": "tag_name",
            "Teacher": "teacher_name",
            "Group": "group_name",
            "Course": "course_id",
            "Shift": "shift_id",
        }

        for name, field in self.fields.items():
            field.label = FIELD_LABELS.get(
                name,
                name.replace("_", " ").capitalize(),
            )

            if isinstance(field, forms.ModelChoiceField):
                model = field.queryset.model
                ordering_field = ordering.get(model.__name__)

                if ordering_field:
                    field.queryset = field.queryset.order_by(
                        ordering_field
                    )

                field.label_from_instance = self.choice_label

                if field.required:
                    field.empty_label = "— Выберите —"
                else:
                    field.empty_label = "— Не выбрано —"

            if isinstance(field, forms.TimeField):
                field.widget = forms.TimeInput(
                    attrs={
                        "type": "time",
                    },
                    format="%H:%M",
                )

    @staticmethod
    def choice_label(obj):
        for attribute in (
                "teacher_name",
                "tag_name",
                "tag_type_name",
                "group_name",
                "pair_name",
        ):
            value = getattr(obj, attribute, None)

            if value:
                return value

        if hasattr(obj, "course_id"):
            return f"{obj.course_id} курс"

        if hasattr(obj, "shift_id"):
            return f"{obj.shift_id} смена"

        return str(obj)

class TagTypeForm(WorkshopFormMixin, forms.ModelForm):
    class Meta:
        model = TagType
        fields = ("tag_type_name",)

class TagForm(WorkshopFormMixin, forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("tag_type", "tag_name")

class GroupForm(WorkshopFormMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = (
            "group_name",
            "group_course",
            "shift",
            "group_tag",
            "group_curator",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Куратором может стать только преподаватель с учётной записью,
        # который ещё не курирует другую группу. Прежний куратор самой
        # редактируемой группы остаётся в списке (даже если у него нет
        # учётной записи), чтобы сохранение не сняло его молча.
        busy = Group.objects.filter(group_curator__isnull=False)

        if self.instance.pk:
            busy = busy.exclude(pk=self.instance.pk)

        free = Q(pk__in=real_teachers().values("pk")) & ~Q(
            pk__in=busy.values("group_curator_id")
        )

        if self.instance.group_curator_id:
            free |= Q(pk=self.instance.group_curator_id)

        self.fields["group_curator"].queryset = Teacher.objects.filter(
            free
        ).order_by("teacher_name", "teacher_id")


class PairForm(WorkshopFormMixin, forms.ModelForm):
    class Meta:
        model = Pair
        fields = ("pair_name", "group", "teacher")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Преподаватели — только реальные пользователи. Прежний преподаватель
        # существующей пары остаётся в списке, даже если у него нет записи.
        teachers = Q(pk__in=real_teachers().values("pk"))

        if self.instance.teacher_id:
            teachers |= Q(pk=self.instance.teacher_id)

        self.fields["teacher"].queryset = Teacher.objects.filter(
            teachers
        ).order_by("teacher_name", "teacher_id")

class BreakHoursForm(WorkshopFormMixin, forms.ModelForm):
    class Meta:
        model = BreakHours
        fields = "__all__"


NEW_USER_ROLES = [
    (ROLE_STUDENT, "Студент"),
    (ROLE_TEACHER, "Преподаватель"),
]

# Буквы любого алфавита; части разделяются пробелом, дефисом или апострофом
_NAME_RE = re.compile(r"[^\W\d_]+(?:[ '’-][^\W\d_]+)*")
MAX_NAME_PART = 100
MAX_FULL_NAME = 150


def _name_part(value, label, required):
    value = " ".join((value or "").split())

    if not value:
        if required:
            raise ValidationError(f"Введите поле «{label}».")

        return ""

    if len(value) > MAX_NAME_PART:
        raise ValidationError(
            f"«{label}» слишком длинное (не больше {MAX_NAME_PART} знаков)."
        )

    if not _NAME_RE.fullmatch(value):
        raise ValidationError(
            f"«{label}»: только буквы, пробел, дефис и апостроф."
        )

    return value


class NewUserForm(WorkshopFormMixin, forms.Form):
    """Новый пользователь: студент или преподаватель.

    ФИО (отчество не обязательно), почта Gmail — она же логин, пароль,
    роль и группа. Студент обязательно привязывается к группе, а группа
    преподавателя — та, которую он будет курировать (можно не выбирать).
    """

    role = forms.ChoiceField(choices=NEW_USER_ROLES, initial=ROLE_STUDENT)
    last_name = forms.CharField(
        max_length=MAX_NAME_PART,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    first_name = forms.CharField(
        max_length=MAX_NAME_PART,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    patronymic = forms.CharField(
        max_length=MAX_NAME_PART,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    username = forms.CharField(
        max_length=MAX_ADDRESS_LENGTH,
        widget=forms.TextInput(attrs={
            "autocomplete": "off",
            "autocapitalize": "none",
            "placeholder": "name@gmail.com",
        }),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"},
        ),
    )
    group = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        required=False,
    )

    field_order = (
        "role",
        "last_name",
        "first_name",
        "patronymic",
        "username",
        "password",
        "group",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        group = self.fields["group"]
        group.queryset = Group.objects.select_related(
            "group_curator"
        ).order_by("group_name", "group_id")
        group.label = "Группа (преподавателю — группа, которую он курирует)"
        group.empty_label = "— Не выбрана —"
        group.label_from_instance = self.group_label

    @staticmethod
    def group_label(group):
        if group.group_curator_id:
            return f"{group.group_name} · куратор: {group.group_curator}"

        return f"{group.group_name} · без куратора"

    def clean_last_name(self):
        return _name_part(self.cleaned_data["last_name"], "Фамилия", True)

    def clean_first_name(self):
        return _name_part(self.cleaned_data["first_name"], "Имя", True)

    def clean_patronymic(self):
        return _name_part(
            self.cleaned_data.get("patronymic"), "Отчество", False
        )

    def clean_username(self):
        # Пользователь входит по почте Gmail, поэтому логин — только она
        username = normalize_gmail(self.cleaned_data["username"])

        if get_user_model().objects.filter(
            username__iexact=username
        ).exists():
            raise ValidationError("Такой логин уже занят.")

        return username

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        group = cleaned.get("group")

        if role == ROLE_STUDENT and "group" not in self.errors and group is None:
            self.add_error("group", "Выберите группу студента.")

        if (
            role == ROLE_TEACHER
            and group is not None
            and group.group_curator_id
        ):
            self.add_error(
                "group",
                f"У группы {group.group_name} уже есть куратор: "
                f"{group.group_curator}. Выберите другую группу "
                f"или оставьте поле пустым.",
            )

        parts = [
            cleaned.get("last_name"),
            cleaned.get("first_name"),
            cleaned.get("patronymic"),
        ]

        if len(" ".join(part for part in parts if part)) > MAX_FULL_NAME:
            self.add_error(
                None,
                f"ФИО слишком длинное (не больше {MAX_FULL_NAME} знаков).",
            )

        password = cleaned.get("password")

        if password:
            candidate = get_user_model()(
                username=cleaned.get("username") or "",
                first_name=cleaned.get("first_name") or "",
                last_name=cleaned.get("last_name") or "",
            )

            try:
                validate_password(password, candidate)
            except ValidationError as exc:
                self.add_error("password", exc)

        return cleaned

    def save(self):
        data = self.cleaned_data

        return create_account(
            data["role"],
            data["last_name"],
            data["first_name"],
            data["patronymic"],
            data["username"],
            data["password"],
            data["group"],
        )


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------

MAX_FAILED_LOGINS = 5
LOGIN_LOCK_SECONDS = 300


def _client_ip(request):
    if request is None:
        return ""

    return request.META.get("REMOTE_ADDR", "")


def _failures_key(request, email):
    raw = f"{_client_ip(request)}|{email}".encode("utf-8")

    return "login-failures:" + hashlib.sha256(raw).hexdigest()


class GmailLoginForm(AuthenticationForm):
    """Вход по почте Gmail и паролю.

    Проверки: адрес — Gmail нужного вида (регистр и точки не важны);
    ответ при неверной почте и неверном пароле один и тот же, чтобы нельзя
    было узнать, есть ли такой пользователь; после нескольких неудачных
    попыток подряд вход с этого адреса и компьютера закрывается на время.
    """

    error_messages = {
        "invalid_login": "Неверная почта или пароль.",
        "inactive": "Этот аккаунт отключён.",
        "locked": (
            "Слишком много неудачных попыток. "
            "Подождите 5 минут и попробуйте снова."
        ),
    }

    username = forms.CharField(
        label="Почта Gmail",
        max_length=MAX_ADDRESS_LENGTH,
        widget=forms.EmailInput(attrs={
            "autofocus": True,
            "autocomplete": "username",
            "autocapitalize": "none",
            "spellcheck": "false",
            "placeholder": "name@gmail.com",
        }),
    )

    def clean_username(self):
        return normalize_gmail(self.cleaned_data["username"])

    def clean(self):
        email = self.cleaned_data.get("username")

        if not email:
            return self.cleaned_data

        key = _failures_key(self.request, email)

        if cache.get(key, 0) >= MAX_FAILED_LOGINS:
            raise ValidationError(
                self.error_messages["locked"], code="locked"
            )

        try:
            cleaned = super().clean()
        except ValidationError:
            cache.add(key, 0, LOGIN_LOCK_SECONDS)

            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, LOGIN_LOCK_SECONDS)

            raise

        # Пустой пароль вход не подтверждает и счётчик не сбрасывает
        if self.user_cache is not None:
            cache.delete(key)

        return cleaned
