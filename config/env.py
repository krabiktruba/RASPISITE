"""Чтение настроек из переменных окружения (без библиотек)."""

TRUE_WORDS = {"1", "true", "yes", "on", "да"}
FALSE_WORDS = {"0", "false", "no", "off", "нет", ""}


def env_bool(environ, name, default):
    """Переменная как «да/нет»; не задана — default. Непонятное значение — ошибка.

    Ошибка нужна, чтобы опечатка («DJANGO_DEBUG=flase») не включила режим
    отладки молча.
    """
    raw = environ.get(name)

    if raw is None:
        return default

    value = raw.strip().lower()

    if value in TRUE_WORDS:
        return True

    if value in FALSE_WORDS:
        return False

    raise ValueError(
        f"{name}={raw!r}: ожидалось одно из 1, 0, true, false, yes, no"
    )


def env_list(environ, name):
    """Список из значения через запятую; пустые элементы отбрасываются."""
    return [
        item.strip()
        for item in environ.get(name, "").split(",")
        if item.strip()
    ]
