"""Страницы самостоятельной регистрации студента."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import registration as reg
from .access import ROLE_STUDENT, create_account
from .context import home_url
from .forms import RegistrationForm, _client_ip
from .mail import send_registration_code
from .models import Group

BACKEND = "django.contrib.auth.backends.ModelBackend"
MINUTES = reg.CODE_LIFETIME // 60


def _too_many(request, form, text):
    form.add_error(None, text)

    return render(request, "register.html", {"form": form}, status=429)


@require_http_methods(["GET", "POST"])
def register(request):
    """Шаг 1: анкета. Ошибки показываются на этой же странице."""
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    if request.method != "POST":
        return render(request, "register.html", {
            "form": RegistrationForm(),
            "draft": reg.get_draft(request),
        })

    form = RegistrationForm(request.POST)

    if not form.is_valid():
        return render(request, "register.html", {"form": form})

    data = form.cleaned_data
    address = data["username"]

    if not reg.hit("client", _client_ip(request), reg.CLIENT_LIMIT) or (
        not reg.hit("address", address, reg.ADDRESS_LIMIT)
    ):
        return _too_many(
            request, form,
            "Слишком много попыток регистрации. Попробуйте через час.",
        )

    code = reg.make_code()

    if not send_registration_code(address, code, MINUTES):
        form.add_error(
            None,
            "Не удалось отправить письмо. Проверьте адрес и попробуйте "
            "позже.",
        )

        return render(request, "register.html", {"form": form})

    reg.save_draft(
        request,
        {
            "last_name": data["last_name"],
            "first_name": data["first_name"],
            "patronymic": data["patronymic"],
            "username": address,
            "group_id": data["group"].pk,
        },
        make_password(data["password"]),
        code,
    )

    return redirect(reverse("register_confirm"))


@require_http_methods(["GET", "POST"])
def register_confirm(request):
    """Шаг 2: код из письма (один раз, при первой регистрации)."""
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    draft = reg.get_draft(request)

    if draft is None:
        messages.info(request, "Начните регистрацию заново.")

        return redirect(reverse("register"))

    error = ""

    if request.method == "POST":
        action = request.POST.get("action", "verify")

        if action == "cancel":
            reg.clear_draft(request)

            return redirect(reverse("register"))

        if action == "resend":
            error = _resend(request, draft)
            draft = reg.get_draft(request)

            if not error:
                messages.success(request, "Новый код отправлен на почту.")

                return redirect(reverse("register_confirm"))

        else:
            status = reg.check_code(request, request.POST.get("code", ""))

            if status == reg.OK:
                return _finish(request, draft)

            if status in (reg.EXPIRED, reg.LOCKED, reg.MISSING):
                messages.error(
                    request,
                    {
                        reg.EXPIRED: "Код устарел. Заполните анкету заново.",
                        reg.LOCKED: (
                            "Слишком много неверных кодов. "
                            "Заполните анкету заново."
                        ),
                        reg.MISSING: "Начните регистрацию заново.",
                    }[status],
                )

                return redirect(reverse("register"))

            error = "Неверный код."
            draft = reg.get_draft(request)

    return render(request, "register_confirm.html", {
        "email": draft["data"]["username"],
        "error": error,
        "minutes": MINUTES,
        "wait": reg.seconds_to_resend(draft),
        "can_resend": reg.can_resend(draft),
        "sends_left": reg.MAX_SENDS - draft["sends"],
    })


def _resend(request, draft):
    """Повторная отправка кода; возвращает текст ошибки или пустую строку."""
    if draft["sends"] >= reg.MAX_SENDS:
        return "Лимит писем исчерпан. Заполните анкету заново."

    if reg.seconds_to_resend(draft):
        return "Повторное письмо можно запросить чуть позже."

    address = draft["data"]["username"]

    if not reg.hit("address", address, reg.ADDRESS_LIMIT):
        return "Слишком много писем на этот адрес. Попробуйте через час."

    code = reg.make_code()

    if not send_registration_code(address, code, MINUTES):
        return "Не удалось отправить письмо. Попробуйте позже."

    reg.save_draft(
        request, draft["data"], draft["password_hash"], code, previous=draft
    )

    return ""


def _finish(request, draft):
    """Код верный: создаёт студента, входит и открывает его расписание."""
    data = draft["data"]
    group = Group.objects.filter(pk=data["group_id"]).first()

    if group is None:
        reg.clear_draft(request)
        messages.error(
            request, "Эта группа больше не существует. Выберите заново."
        )

        return redirect(reverse("register"))

    try:
        user = create_account(
            ROLE_STUDENT,
            data["last_name"],
            data["first_name"],
            data["patronymic"],
            data["username"],
            None,
            group,
            password_hash=draft["password_hash"],
        )
    except (IntegrityError, DatabaseError):
        reg.clear_draft(request)
        messages.error(
            request, "Эта почта уже зарегистрирована. Попробуйте войти."
        )

        return redirect(reverse("login"))

    reg.clear_draft(request)
    login(request, user, backend=BACKEND)
    messages.success(request, "Регистрация завершена. Добро пожаловать!")

    return redirect(home_url(user))
