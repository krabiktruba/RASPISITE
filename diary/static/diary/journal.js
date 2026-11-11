/*
 * Фильтры журнала: курс и семестр взаимно исключают друг друга.
 * Отметили курс — семестры снимаются, отметили семестр — снимаются курсы.
 * Без JavaScript то же правило применяет сервер (при обоих берётся курс).
 */
(function () {
    "use strict";

    var form = document.querySelector("[data-journal-filters]");

    if (!form) {
        return;
    }

    form.addEventListener("change", function (event) {
        var target = event.target;

        if (!target.checked) {
            return;
        }

        var other = null;

        if (target.hasAttribute("data-journal-course")) {
            other = "[data-journal-semester]";
        } else if (target.hasAttribute("data-journal-semester")) {
            other = "[data-journal-course]";
        }

        if (!other) {
            return;
        }

        form.querySelectorAll(other).forEach(function (box) {
            box.checked = false;
        });
    });
})();
