"""Самостоятельная регистрация студента: черновик в сессии и код на почту.

Порядок такой:
 1. человек заполняет форму (ФИО, почта Gmail, пароль два раза, группа);
 2. данные и ХЕШ пароля ложатся в серверную сессию, на почту уходит
    шестизначный код (сам код нигде не хранится, только его подпись);
 3. код введён верно — создаётся аккаунт студента, человек сразу входит.

Код нужен один раз, при первой регистрации. Дальше вход идёт только по
почте и паролю. Аккаунт создаётся только после подтверждения почты, поэтому
чужой адрес занять нельзя.
"""

import secrets
import string
import time

from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac

SESSION_KEY = "registration"

CODE_LENGTH = 6
CODE_LIFETIME = 15 * 60          # секунд до устаревания кода
RESEND_COOLDOWN = 60             # секунд между отправками
MAX_SENDS = 4                    # писем на одну регистрацию
MAX_CODE_ATTEMPTS = 5            # неверных вводов кода

# Защита от рассылки: писем на один адрес и регистраций с одного компьютера
ADDRESS_LIMIT = (5, 60 * 60)
CLIENT_LIMIT = (20, 60 * 60)

OK = "ok"
WRONG = "wrong"
EXPIRED = "expired"
LOCKED = "locked"
MISSING = "missing"


def now():
    """Текущее время в секундах (отдельная функция, чтобы подменять в тестах)."""
    return time.time()


def make_code():
    return "".join(secrets.choice(string.digits) for _ in range(CODE_LENGTH))


def code_digest(email, code):
    return salted_hmac(
        "raspisite.registration", f"{email}|{code}"
    ).hexdigest()


def hit(name, key, limit):
    """Считает обращение; False, если за период их уже слишком много."""
    maximum, seconds = limit
    cache_key = f"registration-{name}:{key}"

    cache.add(cache_key, 0, seconds)

    try:
        return cache.incr(cache_key) <= maximum
    except ValueError:
        cache.set(cache_key, 1, seconds)

        return True


def get_draft(request):
    """Черновик регистрации из сессии или None."""
    draft = request.session.get(SESSION_KEY)

    return draft if isinstance(draft, dict) else None


def clear_draft(request):
    request.session.pop(SESSION_KEY, None)


def save_draft(request, data, password_hash, code, previous=None):
    """Кладёт черновик (новый или после повторной отправки) в сессию."""
    moment = now()

    request.session[SESSION_KEY] = {
        "data": data,
        "password_hash": password_hash,
        "digest": code_digest(data["username"], code),
        "expires": moment + CODE_LIFETIME,
        "sent_at": moment,
        "sends": (previous or {}).get("sends", 0) + 1,
        "attempts": 0,
    }


def seconds_to_resend(draft):
    wait = RESEND_COOLDOWN - (now() - draft["sent_at"])

    return max(0, int(wait) + (1 if wait % 1 else 0))


def can_resend(draft):
    return draft["sends"] < MAX_SENDS and seconds_to_resend(draft) == 0


def check_code(request, code):
    """Проверяет код; считает неверные попытки; возвращает статус."""
    draft = get_draft(request)

    if draft is None:
        return MISSING

    if now() > draft["expires"]:
        clear_draft(request)

        return EXPIRED

    code = "".join((code or "").split())

    if constant_time_compare(
        draft["digest"], code_digest(draft["data"]["username"], code)
    ):
        return OK

    draft["attempts"] += 1

    if draft["attempts"] >= MAX_CODE_ATTEMPTS:
        clear_draft(request)

        return LOCKED

    request.session[SESSION_KEY] = draft

    return WRONG
