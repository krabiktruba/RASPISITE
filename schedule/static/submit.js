/*
 * Защита от двойной отправки форм.
 *
 * Двойной щелчок по «Сохранить» или нетерпеливое повторное нажатие отправляло
 * форму дважды: второй запрос мог создать дубль или упасть с ошибкой базы.
 * Теперь после отправки формы (method=post) повторная отправка этой же формы
 * игнорируется несколько секунд, а кнопки отправки на это время отключены.
 *
 * Если ответ не пришёл (скачивание файла, обрыв связи), форма снова
 * разблокируется сама. Кнопка отключается уже после того, как браузер собрал
 * данные формы, поэтому имя нажатой кнопки (action=…) уходит на сервер как
 * обычно. Форму можно исключить атрибутом data-allow-resubmit.
 *
 * Без JavaScript формы работают как раньше.
 */
(function () {
    "use strict";

    var LOCK_MS = 4000;

    function buttons(form) {
        return Array.prototype.slice.call(
            form.querySelectorAll(
                'button:not([type]), button[type="submit"], input[type="submit"]'
            )
        );
    }

    function unlock(form) {
        form.removeAttribute("data-submitting");

        buttons(form).forEach(function (button) {
            if (button.hasAttribute("data-was-enabled")) {
                button.disabled = false;
                button.removeAttribute("data-was-enabled");
            }
        });
    }

    document.addEventListener("submit", function (event) {
        var form = event.target;

        if (
            !form.matches ||
            !form.matches('form[method="post" i]') ||
            form.hasAttribute("data-allow-resubmit")
        ) {
            return;
        }

        if (form.hasAttribute("data-submitting")) {
            event.preventDefault();
            return;
        }

        // Если другой скрипт отменил отправку, блокировать нечего
        window.setTimeout(function () {
            if (event.defaultPrevented) {
                return;
            }

            form.setAttribute("data-submitting", "1");

            buttons(form).forEach(function (button) {
                if (!button.disabled) {
                    button.setAttribute("data-was-enabled", "1");
                    button.disabled = true;
                }
            });

            window.setTimeout(function () {
                unlock(form);
            }, LOCK_MS);
        }, 0);
    });

    // Возврат кнопкой «Назад»: страница пришла из кэша с отключёнными кнопками
    window.addEventListener("pageshow", function () {
        Array.prototype.forEach.call(
            document.querySelectorAll("form[data-submitting]"),
            unlock
        );
    });
})();
