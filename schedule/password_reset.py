"""Восстановление пароля по почте.

Другого способа нет: ни телефона, ни секретных вопросов, ни пароля «по
умолчанию». Человек вводит свою почту Gmail, ему приходит ссылка (действует
час, работает один раз), по ней он задаёт новый пароль и получает письмо о том,
что пароль изменён.

Ответ на странице всегда один и тот же, есть такой адрес на сайте или нет,
поэтому по нему нельзя узнать, кто зарегистрирован. Письма не отправляются
отключённым пользователям и тем, у кого нет адреса почты.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.urls import reverse_lazy

from . import registration as throttle
from .forms import _client_ip
from .gmail import MAX_ADDRESS_LENGTH, normalize_gmail
from .mail import send_password_changed_notice

# Писем со ссылкой: на один адрес и с одного компьютера в час
ADDRESS_LIMIT = (3, 60 * 60)
CLIENT_LIMIT = (20, 60 * 60)


class GmailPasswordResetForm(PasswordResetForm):
    """Почта Gmail в любом написании; письмо уходит только настоящему адресу."""

    email = forms.CharField(
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

    def clean_email(self):
        return normalize_gmail(self.cleaned_data["email"])

    def get_users(self, email):
        users = get_user_model().objects.filter(
            username__iexact=email, is_active=True
        )

        return (
            user for user in users
            if user.has_usable_password() and "@" in (user.email or "")
        )

    def save(self, *args, request=None, **kwargs):
        address = self.cleaned_data["email"]

        # Слишком частые запросы молча остаются без письма
        if not throttle.hit(
            "reset-client", _client_ip(request), CLIENT_LIMIT
        ) or not throttle.hit("reset-address", address, ADDRESS_LIMIT):
            return None

        return super().save(*args, request=request, **kwargs)


class ResetRequestView(auth_views.PasswordResetView):
    template_name = "password_reset_form.html"
    form_class = GmailPasswordResetForm
    email_template_name = "password_reset_email.txt"
    subject_template_name = "password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")


class ResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "password_reset_done.html"


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        user = form.user
        response = super().form_valid(form)
        send_password_changed_notice(user)

        return response


class ResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "password_reset_complete.html"
