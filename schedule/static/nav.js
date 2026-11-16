/*
 * Шапка сайта: меню «⋮» и кнопка «Назад».
 * Без библиотек, без сборки.
 *
 * 1. Меню. Кнопка «⋮» (data-nav-toggle) открывает панель data-nav
 *    (класс is-open). На широком экране панель выезжает справа, на телефоне
 *    занимает всю ширину. Закрывают её крестик (data-nav-close), клик по
 *    затемнению (data-nav-scrim) и клавиша Escape.
 *    Пока панель открыта, страница под ней не прокручивается (класс
 *    nav-open у <html>). После закрытия фокус возвращается на «⋮».
 *
 * 2. «Назад». Ссылка с data-back ведёт на «родителя» страницы (адрес
 *    подставляет сервер). Если пользователь пришёл с другой страницы этого
 *    же сайта, вместо перехода выполняется настоящий возврат по истории
 *    браузера: так сохраняются прокрутка, выбранная неделя и фильтры.
 *
 * Если JavaScript выключен, меню остаётся обычным блоком под шапкой, а
 * «Назад» работает как обычная ссылка на родителя.
 */
(function () {
    "use strict";

    var root = document.documentElement;
    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-nav]");
    var scrim = document.querySelector("[data-nav-scrim]");
    var close = document.querySelector("[data-nav-close]");

    function isOpen() {
        return Boolean(nav) && nav.classList.contains("is-open");
    }

    function setOpen(open) {
        if (!toggle || !nav) {
            return;
        }

        nav.classList.toggle("is-open", open);
        root.classList.toggle("nav-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");

        if (scrim) {
            scrim.classList.toggle("is-open", open);
        }

        if (open && close) {
            close.focus();
        } else if (!open) {
            toggle.focus({ preventScroll: true });
        }
    }

    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            setOpen(!isOpen());
        });

        if (close) {
            close.addEventListener("click", function () {
                setOpen(false);
            });
        }

        if (scrim) {
            scrim.addEventListener("click", function () {
                setOpen(false);
            });
        }

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && isOpen()) {
                setOpen(false);
            }
        });

        // Страница из кэша «Назад/Вперёд» не должна открываться с меню
        window.addEventListener("pageshow", function () {
            nav.classList.remove("is-open");
            root.classList.remove("nav-open");
            toggle.setAttribute("aria-expanded", "false");

            if (scrim) {
                scrim.classList.remove("is-open");
            }
        });
    }

    // Адрес, с которого пришли, принадлежит этому же сайту и это не эта же страница
    function cameFromThisSite() {
        if (!document.referrer || window.history.length < 2) {
            return false;
        }

        try {
            var from = new URL(document.referrer);

            return (
                from.origin === window.location.origin &&
                from.href !== window.location.href
            );
        } catch (error) {
            return false;
        }
    }

    var back = document.querySelector("[data-back]");

    if (back) {
        back.addEventListener("click", function (event) {
            if (cameFromThisSite()) {
                event.preventDefault();
                window.history.back();
            }
        });
    }
})();
