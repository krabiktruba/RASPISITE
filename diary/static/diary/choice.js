/*
 * Список групп в дневнике: выбор тега сразу применяет фильтр.
 * Без JavaScript работает кнопка-лупа у поля поиска (отправляет оба поля).
 */
(function () {
    "use strict";

    var form = document.querySelector("[data-diary-choice]");

    if (!form) {
        return;
    }

    var select = form.querySelector("select[name='tag']");

    if (select) {
        select.addEventListener("change", function () {
            form.submit();
        });
    }
})();
