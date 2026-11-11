/*
 * Таблица дневника группы: быстрый ввод оценок с клавиатуры.
 *
 *   Enter       — перейти в ячейку ниже в том же столбце;
 *                 в самой нижней ячейке столбца Enter сохраняет таблицу;
 *   стрелки ↓ ↑ — перейти вниз / вверх.
 *
 * Разметка: форма с data-diary-form, поля оценок с data-diary-col — все поля
 * одного занятия (столбца) имеют одинаковое значение, порядок в странице
 * совпадает с порядком строк.
 *
 * Без JavaScript Enter просто сохраняет форму, как обычная форма.
 */
(function () {
    "use strict";

    var form = document.querySelector("[data-diary-form]");

    if (!form) {
        return;
    }

    function focusCell(input) {
        input.focus();
        input.select();
    }

    form.addEventListener("keydown", function (event) {
        var input = event.target;

        if (event.isComposing || !input.matches("input[data-diary-col]")) {
            return;
        }

        var isEnter = event.key === "Enter";
        var isDown = event.key === "ArrowDown";
        var isUp = event.key === "ArrowUp";

        if (!isEnter && !isDown && !isUp) {
            return;
        }

        var column = form.querySelectorAll(
            'input[data-diary-col="' + input.getAttribute("data-diary-col") + '"]'
        );
        var index = Array.prototype.indexOf.call(column, input);
        var next = column[index + (isUp ? -1 : 1)];

        if (next) {
            event.preventDefault();
            focusCell(next);
        } else if (!isEnter) {
            event.preventDefault();
        }
        // Enter в нижней ячейке: событие не гасим, форма отправится сама.
    });

    // При входе в ячейку выделяем содержимое: новая оценка заменяет старую.
    form.addEventListener("focusin", function (event) {
        if (event.target.matches("input[data-diary-col]")) {
            event.target.select();
        }
    });
})();
