/*
 * Шапка сайта: меню-бургер на узких экранах и кнопка «Назад».
 * Без библиотек, без сборки.
 *
 * 1. Меню. Кнопка с data-nav-toggle открывает и закрывает блок data-nav
 *    (класс is-open). Escape и клик вне меню закрывают его.
 *
 * 2. «Назад». Ссылка с data-back ведёт на «родителя» страницы (адрес
 *    подставляет сервер). Если пользователь пришёл с другой страницы этого
 *    же сайта, вместо перехода выполняется настоящий возврат по истории
 *    браузера: так сохраняются прокрутка, выбранная неделя и фильтры.
 *
 * Если JavaScript выключен, меню просто переносится на несколько строк, а
 * «Назад» работает как обычная ссылка на родителя.
 */
(function () {
    "use strict";

    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.querySelector("[data-nav]");

    function setOpen(open) {
        if (!toggle || !nav) {
            return;
        }

        nav.classList.toggle("is-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            setOpen(!nav.classList.contains("is-open"));
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                setOpen(false);
            }
        });

        document.addEventListener("click", function (event) {
            if (
                nav.classList.contains("is-open") &&
                !nav.contains(event.target) &&
                !toggle.contains(event.target)
            ) {
                setOpen(false);
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
