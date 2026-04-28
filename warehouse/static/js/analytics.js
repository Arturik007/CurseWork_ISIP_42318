(function () {
  function drawLineChart(canvas, labels, values) {
    var ctx = canvas.getContext("2d");
    var w = canvas.width;
    var h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!values.length) {
      ctx.fillStyle = "#5c6570";
      ctx.font = "14px system-ui,sans-serif";
      ctx.fillText("Нет данных за выбранный товар", 16, h / 2);
      return;
    }
    var pad = 36;
    var minV = Math.min.apply(null, values);
    var maxV = Math.max.apply(null, values);
    if (minV === maxV) {
      minV -= 1;
      maxV += 1;
    }
    var x0 = pad;
    var y0 = h - pad;
    var x1 = w - pad;
    var y1 = pad;
    ctx.strokeStyle = "#d8dee6";
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y0);
    ctx.stroke();
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (var i = 0; i < values.length; i++) {
      var x = x0 + ((x1 - x0) * i) / Math.max(values.length - 1, 1);
      var t = (values[i] - minV) / (maxV - minV);
      var y = y0 - t * (y0 - y1);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.fillStyle = "#5c6570";
    ctx.font = "11px system-ui,sans-serif";
    if (labels.length) {
      ctx.fillText(labels[0], x0, h - 10);
      var last = labels[labels.length - 1];
      ctx.fillText(last, x1 - ctx.measureText(last).width, h - 10);
    }
  }

  function loadChart(baseUrl, productId) {
    var canvas = document.getElementById("movChart");
    if (!canvas) return;
    canvas.width = canvas.parentElement.clientWidth || 600;
    canvas.height = 220;
    if (!productId) {
      drawLineChart(canvas, [], []);
      return;
    }
    fetch(baseUrl + "?product_id=" + encodeURIComponent(productId))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        drawLineChart(canvas, data.labels || [], data.values || []);
      })
      .catch(function () {
        drawLineChart(canvas, [], []);
      });
  }

  function renderAbcTable(items) {
    var tb = document.querySelector("#abc-body");
    if (!tb) return;
    tb.innerHTML = "";
    (items || []).forEach(function (row) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        row.sku +
        "</td><td>" +
        row.name +
        "</td><td>" +
        row.turnover +
        "</td><td>" +
        row.share +
        "%</td><td>" +
        row.cum_share +
        "%</td><td><strong>" +
        row.class +
        "</strong></td>";
      tb.appendChild(tr);
    });
    if (!items || !items.length) {
      var tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="6" class="empty">Нет движений за период.</td>';
      tb.appendChild(tr);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var chartBtn = document.getElementById("btn-chart");
    var sel = document.getElementById("chart-product");
    var base = document.body.getAttribute("data-movement-api");
    if (chartBtn && sel && base) {
      chartBtn.addEventListener("click", function () {
        loadChart(base, sel.value);
      });
      loadChart(base, sel.value);
    }

    var abcBtn = document.getElementById("btn-abc");
    var df = document.getElementById("abc-from");
    var dt = document.getElementById("abc-to");
    var abcBase = document.body.getAttribute("data-abc-api");
    if (abcBtn && df && dt && abcBase) {
      abcBtn.addEventListener("click", function () {
        var u = abcBase + "?date_from=" + encodeURIComponent(df.value) + "&date_to=" + encodeURIComponent(dt.value);
        fetch(u)
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            renderAbcTable(data.items);
          })
          .catch(function () {
            renderAbcTable([]);
          });
      });
    }
  });
})();
