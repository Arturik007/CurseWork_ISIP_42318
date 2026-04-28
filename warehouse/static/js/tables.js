(function () {
  function parseCell(th, td) {
    var t = (td && td.textContent) || "";
    t = t.trim();
    var num = t.replace(/\s/g, "").replace(",", ".");
    if (th.dataset.sort === "num" && !isNaN(parseFloat(num))) {
      return parseFloat(num);
    }
    return t.toLowerCase();
  }

  function initTable(table) {
    var filterInput = document.querySelector('[data-table-filter="' + table.id + '"]');
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var headers = table.tHead && table.tHead.rows[0] && table.tHead.rows[0].cells;
    if (!headers) return;

    for (var i = 0; i < headers.length; i++) {
      (function (colIndex) {
        var th = headers[colIndex];
        if (th.dataset.sort === "none") return;
        th.addEventListener("click", function () {
          var asc = th.classList.contains("sorted-asc");
          for (var j = 0; j < headers.length; j++) {
            headers[j].classList.remove("sorted-asc", "sorted-desc");
          }
          th.classList.add(asc ? "sorted-desc" : "sorted-asc");
          var dir = asc ? -1 : 1;
          var rows = Array.prototype.slice.call(tbody.rows);
          rows.sort(function (a, b) {
            var A = parseCell(th, a.cells[colIndex]);
            var B = parseCell(th, b.cells[colIndex]);
            if (A < B) return -1 * dir;
            if (A > B) return 1 * dir;
            return 0;
          });
          rows.forEach(function (r) {
            tbody.appendChild(r);
          });
        });
      })(i);
    }

    if (filterInput) {
      filterInput.addEventListener("input", function () {
        var q = filterInput.value.trim().toLowerCase();
        for (var r = 0; r < tbody.rows.length; r++) {
          var row = tbody.rows[r];
          var text = row.textContent.toLowerCase();
          row.style.display = !q || text.indexOf(q) !== -1 ? "" : "none";
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.data-table").forEach(initTable);
  });
})();
