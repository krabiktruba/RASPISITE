/*
 * Запоминание прокрутки страницы, как в Телеграме.
 *
 * Положение запоминается в sessionStorage (живёт, пока открыта вкладка) и
 * восстанавливается в двух случаях:
 *
 *   1. страницу обновили (F5, Ctrl+R, Ctrl+F5);
 *   2. форму этой же страницы отправили (например, сохранили оценки или
 *      изменили пользователя) и сайт вернул ту же страницу.
 *
 * Обычный переход по ссылке всегда начинается сверху. Кнопки «Назад» и
 * «Вперёд» браузера восстанавливают прокрутку сами, этот скрипт им не мешает.
 * Если в адресе есть #якорь, он важнее сохранённого положения.
 *
 * Запоминается вертикальная прокрутка страницы и горизонтальная прокрутка
 * широких таблиц: у них в разметке стоит атрибут data-scroll-x.
 *
 * После сохранения формы сообщение («Сохранено…») осталось бы вверху
 * страницы, за пределами экрана. Поэтому при восстановлении после формы
 * сообщения всплывают внизу экрана: успех исчезает через 6 секунд, ошибки и
 * предупреждения остаются, пока по ним не нажмут.
 *
 * Без JavaScript или при недоступном sessionStorage (приватный режим)
 * страница работает как обычно.
 */
(function () {
    "use strict";

    var STORE = "raspisite:scroll";
    var PENDING = "raspisite:scroll-pending";
    var MAX_PAGES = 20;
    var PENDING_LIFETIME = 60 * 1000;
    var SAVE_DELAY = 150;
    var TOAST_LIFETIME = 6000;

    var key = window.location.pathname + window.location.search;
    var boxes = Array.prototype.slice.call(
        document.querySelectorAll("[data-scroll-x]")
    );

    function read(name) {
        try {
            var raw = window.sessionStorage.getItem(name);

            return raw ? JSON.parse(raw) : null;
        } catch (error) {
            return null;
        }
    }

    function write(name, value) {
        try {
            window.sessionStorage.setItem(name, JSON.stringify(value));
        } catch (error) {
            // хранилище недоступно: просто не запоминаем
        }
    }

    function drop(name) {
        try {
            window.sessionStorage.removeItem(name);
        } catch (error) {
            // см. выше
        }
    }

    function position() {
        return {
            y: Math.round(window.pageYOffset),
            x: boxes.map(function (box) {
                return Math.round(box.scrollLeft);
            }),
            t: Date.now(),
        };
    }

    function apply(pos) {
        if (!pos) {
            return;
        }

        window.scrollTo(0, pos.y || 0);

        boxes.forEach(function (box, index) {
            if (pos.x && typeof pos.x[index] === "number") {
                box.scrollLeft = pos.x[index];
            }
        });
    }

    function wasReload() {
        var perf = window.performance;

        if (perf && perf.getEntriesByType) {
            var entries = perf.getEntriesByType("navigation");

            if (entries.length) {
                return entries[0].type === "reload";
            }
        }

        return Boolean(perf && perf.navigation && perf.navigation.type === 1);
    }

    function save() {
        var all = read(STORE) || {};
        var keys;

        all[key] = position();
        keys = Object.keys(all);

        if (keys.length > MAX_PAGES) {
            keys.sort(function (a, b) {
                return all[a].t - all[b].t;
            });

            keys.slice(0, keys.length - MAX_PAGES).forEach(function (old) {
                delete all[old];
            });
        }

        write(STORE, all);
    }

    function floatMessages() {
        var list = document.querySelector(".messages");

        if (!list) {
            return;
        }

        list.classList.add("is-floating");
        list.setAttribute("title", "Нажмите, чтобы закрыть");

        list.addEventListener("click", function () {
            list.classList.add("is-gone");
        });

        if (!list.querySelector(".message.error, .message.warning")) {
            window.setTimeout(function () {
                list.classList.add("is-gone");
            }, TOAST_LIFETIME);
        }
    }

    // ---- восстановление при открытии страницы ----

    var pending = read(PENDING);
    var fromForm = false;
    var restored = null;

    drop(PENDING);

    if (!window.location.hash) {
        fromForm = Boolean(
            pending &&
            pending.path === window.location.pathname &&
            Date.now() - pending.t < PENDING_LIFETIME
        );

        if (fromForm) {
            restored = pending.pos;
        } else if (wasReload()) {
            restored = (read(STORE) || {})[key] || null;
        }
    }

    // Пока положение не восстановлено, «верх страницы» запоминать нельзя
    var canSave = !restored;

    if (restored) {
        apply(restored);

        // Шрифты и картинки могут сдвинуть вёрстку: ставим положение ещё раз
        window.addEventListener("load", function () {
            apply(restored);
            canSave = true;
        });
    }

    if (fromForm && restored) {
        floatMessages();
    }

    // ---- запоминание ----

    var timer = null;

    function schedule() {
        if (!canSave || timer !== null) {
            return;
        }

        timer = window.setTimeout(function () {
            timer = null;
            save();
        }, SAVE_DELAY);
    }

    window.addEventListener("scroll", schedule, { passive: true });

    boxes.forEach(function (box) {
        box.addEventListener("scroll", schedule, { passive: true });
    });

    window.addEventListener("pagehide", function () {
        if (canSave) {
            save();
        }
    });

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "hidden" && canSave) {
            save();
        }
    });

    // Форма отправляется на эту же страницу: запоминаем положение «на лету»,
    // потому что после ответа сервера страница откроется заново
    document.addEventListener("submit", function (event) {
        var form = event.target;
        var target;

        if (!form || String(form.method).toLowerCase() !== "post") {
            return;
        }

        try {
            target = new URL(form.action, window.location.href);
        } catch (error) {
            return;
        }

        if (target.pathname !== window.location.pathname) {
            return;
        }

        write(PENDING, {
            path: window.location.pathname,
            pos: position(),
            t: Date.now(),
        });
    });
})();
