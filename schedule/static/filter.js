/*
 * Небольшие помощники страниц RASPISITE. Без библиотек, без сборки.
 *
 * 1. Поиск по списку (мгновенно, в браузере).
 *    Разметка: контейнер с data-filter, внутри поле data-filter-input,
 *    элементы списка с data-filter-item (по умолчанию ищется по их тексту,
 *    можно задать data-filter-text) и необязательная надпись
 *    «ничего не найдено» с data-filter-empty.
 *    Слова из поля ищутся в любом порядке и в любом месте строки, регистр и
 *    буква «ё» не важны: «пет ив» найдёт «Иванов Пётр».
 *
 *    Необязательный элемент с data-filter-count показывает, сколько
 *    записей осталось.
 *
 * 2. Предпросмотр цвета тега в форме Кузни: блок с data-tag-preview.
 *
 * 3. Сортировка таблицы («Справочник»): table[data-sortable], у заголовков
 *    th data-sort="text" или "number". Значение ячейки — data-sort-value
 *    (если есть) или её текст. Повторный щелчок меняет порядок; пустые
 *    значения всегда внизу. Без JavaScript таблица остаётся как есть.
 *
 * Если JavaScript выключен, страницы работают как раньше: список показывается
 * целиком, а предпросмотр остаётся скрытым.
 */
(function () {
    "use strict";

    function normalize(text) {
        return (text || "")
            .toLowerCase()
            .replace(/ё/g, "е")
            .replace(/\s+/g, " ")
            .trim();
    }

    /* ---------- 1. Поиск по списку ---------- */

    function initFilter(box) {
        var input = box.querySelector("[data-filter-input]");

        if (!input) {
            return;
        }

        var items = Array.prototype.slice.call(
            box.querySelectorAll("[data-filter-item]")
        );
        var empty = box.querySelector("[data-filter-empty]");
        var count = box.querySelector("[data-filter-count]");

        var haystacks = items.map(function (item) {
            return normalize(
                item.getAttribute("data-filter-text") || item.textContent
            );
        });

        function visibleItems() {
            return items.filter(function (item) {
                return !item.hidden;
            });
        }

        function apply() {
            var words = normalize(input.value).split(" ").filter(Boolean);
            var shown = 0;

            items.forEach(function (item, index) {
                var match = words.every(function (word) {
                    return haystacks[index].indexOf(word) !== -1;
                });

                item.hidden = !match;

                if (match) {
                    shown += 1;
                }
            });

            if (empty) {
                empty.hidden = shown !== 0;
            }

            if (count) {
                count.textContent = String(shown);
            }
        }

        input.addEventListener("input", apply);

        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                // Enter открывает первый из найденных
                var first = visibleItems()[0];
                var link = first && first.querySelector("a[href]");

                event.preventDefault();

                if (link) {
                    window.location.href = link.href;
                }
            } else if (event.key === "Escape") {
                input.value = "";
                apply();
                input.blur();
            }
        });

        // Браузер мог вернуть текст в поле (кнопка «Назад»)
        apply();
    }

    /* ---------- 2. Предпросмотр цвета тега ---------- */

    // Та же формула, что в schedule/tags.py (text_color): яркость по W3C,
    // порог 140 — тёмный текст на светлом фоне, белый на тёмном.
    function textColor(hex) {
        var red = parseInt(hex.substr(1, 2), 16);
        var green = parseInt(hex.substr(3, 2), 16);
        var blue = parseInt(hex.substr(5, 2), 16);

        return (299 * red + 587 * green + 114 * blue) / 1000 > 140
            ? "#111827"
            : "#ffffff";
    }

    function initTagPreview(row) {
        var form = row.closest("form");
        var chip = row.querySelector("[data-tag-preview-chip]");

        if (!form || !chip) {
            return;
        }

        var color = form.querySelector('input[name$="tag_color"]');
        var name = form.querySelector('input[name$="tag_name"]');

        if (!color || !name) {
            return;
        }

        function update() {
            var value = /^#[0-9a-f]{6}$/i.test(color.value)
                ? color.value.toLowerCase()
                : "#6b7280";
            var label = name.value.replace(/\s+/g, " ").trim();

            chip.style.backgroundColor = value;
            chip.style.color = textColor(value);
            chip.textContent = label || "Название";
            chip.title = label;
        }

        [color, name].forEach(function (field) {
            field.addEventListener("input", update);
            field.addEventListener("change", update);
        });

        row.hidden = false;
        update();
    }

    /* ---------- 3. Сортировка таблицы ---------- */

    var collator = typeof Intl !== "undefined" && Intl.Collator
        ? new Intl.Collator("ru", {numeric: true, sensitivity: "base"})
        : null;

    function compareText(first, second) {
        return collator
            ? collator.compare(first, second)
            : first.localeCompare(second);
    }

    function cellValue(row, index) {
        var cell = row.cells[index];

        if (!cell) {
            return "";
        }

        var value = cell.getAttribute("data-sort-value");

        return (value !== null ? value : cell.textContent)
            .replace(/\s+/g, " ")
            .trim();
    }

    function initSort(table) {
        var body = table.tBodies[0];
        var heads = Array.prototype.slice.call(
            table.querySelectorAll("th[data-sort]")
        );

        if (!body || !heads.length) {
            return;
        }

        function sortBy(head, index) {
            var numeric = head.getAttribute("data-sort") === "number";
            var direction = head.getAttribute("aria-sort") === "ascending"
                ? "descending"
                : "ascending";
            var sign = direction === "ascending" ? 1 : -1;
            var rows = Array.prototype.slice.call(body.rows);

            // Индекс нужен, чтобы равные значения остались в прежнем порядке
            var keyed = rows.map(function (row, position) {
                return {
                    row: row,
                    position: position,
                    value: cellValue(row, index)
                };
            });

            keyed.sort(function (first, second) {
                var emptyFirst = first.value === "" || first.value === "—";
                var emptySecond = second.value === "" || second.value === "—";

                if (emptyFirst !== emptySecond) {
                    return emptyFirst ? 1 : -1;
                }

                var result = 0;

                if (numeric) {
                    result = (parseFloat(first.value) || 0)
                        - (parseFloat(second.value) || 0);
                } else {
                    result = compareText(first.value, second.value);
                }

                return result !== 0
                    ? result * sign
                    : first.position - second.position;
            });

            keyed.forEach(function (item) {
                body.appendChild(item.row);
            });

            heads.forEach(function (other) {
                other.setAttribute("aria-sort", "none");
            });

            head.setAttribute("aria-sort", direction);
        }

        heads.forEach(function (head) {
            var index = head.cellIndex;

            head.addEventListener("click", function () {
                sortBy(head, index);
            });

            head.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    sortBy(head, index);
                }
            });
        });
    }

    Array.prototype.forEach.call(
        document.querySelectorAll("[data-filter]"),
        initFilter
    );

    Array.prototype.forEach.call(
        document.querySelectorAll("table[data-sortable]"),
        initSort
    );

    Array.prototype.forEach.call(
        document.querySelectorAll("[data-tag-preview]"),
        initTagPreview
    );
})();
