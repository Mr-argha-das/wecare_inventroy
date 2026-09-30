/* We Care - global UI helpers (vanilla JS). */
(function () {
  "use strict";

  // Sidebar toggle (mobile)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-sidebar-toggle]");
    if (btn) {
      document.getElementById("sidebar").classList.toggle("open");
    }
    var sb = document.getElementById("sidebar");
    if (sb && sb.classList.contains("open") && !e.target.closest("#sidebar") && !e.target.closest("[data-sidebar-toggle]")) {
      sb.classList.remove("open");
    }
  });

  // Auto-hide success flashes
  setTimeout(function () {
    document.querySelectorAll(".flash-success").forEach(function (el) {
      el.style.transition = "opacity .5s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 550);
    });
  }, 4500);

  // Modals: [data-modal-open="#id"], [data-modal-close]
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) {
      var m = document.querySelector(opener.getAttribute("data-modal-open"));
      if (m) m.classList.add("open");
      return;
    }
    if (e.target.closest("[data-modal-close]") || e.target.classList.contains("modal-backdrop")) {
      var open = e.target.closest(".modal-backdrop");
      if (open) open.classList.remove("open");
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll(".modal-backdrop.open").forEach(function (m) { m.classList.remove("open"); });
  });

  // Confirm dialogs for dangerous forms
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.hasAttribute("data-confirm")) {
      var msg = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(msg)) e.preventDefault();
    }
  });

  // Client-side table sort
  document.addEventListener("click", function (e) {
    var th = e.target.closest("th.sortable");
    if (!th) return;
    var table = th.closest("table");
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    var tbody = table.querySelector("tbody");
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    var dir = th.getAttribute("data-dir") === "asc" ? "desc" : "asc";
    th.setAttribute("data-dir", dir);
    rows.sort(function (a, b) {
      var x = (a.cells[idx] ? a.cells[idx].innerText.trim() : "");
      var y = (b.cells[idx] ? b.cells[idx].innerText.trim() : "");
      var nx = parseFloat(x.replace(/[^0-9.\-]/g, ""));
      var ny = parseFloat(y.replace(/[^0-9.\-]/g, ""));
      var cmp;
      if (!isNaN(nx) && !isNaN(ny) && x.match(/[0-9]/)) cmp = nx - ny;
      else cmp = x.localeCompare(y);
      return dir === "asc" ? cmp : -cmp;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  });

  // Client-side table quick filter
  document.querySelectorAll("[data-table-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var table = document.querySelector(input.getAttribute("data-table-filter"));
      if (!table) return;
      var q = input.value.toLowerCase();
      table.querySelectorAll("tbody tr").forEach(function (tr) {
        tr.style.display = tr.innerText.toLowerCase().indexOf(q) >= 0 ? "" : "none";
      });
    });
  });

  // Tabs
  document.querySelectorAll("[data-tabs]").forEach(function (wrap) {
    var buttons = wrap.querySelectorAll("[data-tab]");
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        buttons.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        var scope = btn.closest("[data-tabs-scope]") || document;
        scope.querySelectorAll("[data-tab-panel]").forEach(function (p) { p.style.display = "none"; });
        var panel = scope.querySelector('[data-tab-panel="' + btn.getAttribute("data-tab") + '"]');
        if (panel) panel.style.display = "";
      });
    });
  });

  // Dashboard chart (canvas, vanilla)
  function drawChart() {
    var cv = document.getElementById("collectionChart");
    if (!cv) return;
    var raw = document.getElementById("chartData");
    if (!raw) return;
    var series;
    try { series = JSON.parse(raw.textContent); } catch (e) { return; }
    if (!series.length) return;
    var dpr = window.devicePixelRatio || 1;
    var W = cv.clientWidth || 600, H = 260;
    cv.width = W * dpr; cv.height = H * dpr;
    var ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);
    var max = Math.max.apply(null, series.map(function (d) { return Math.max(d.collection, d.billing); }).concat([1]));
    var padL = 56, padB = 30, padT = 14, padR = 10;
    var cw = W - padL - padR, ch = H - padB - padT;
    ctx.strokeStyle = "#dbe3ec"; ctx.fillStyle = "#6b7a90"; ctx.font = "11px sans-serif";
    for (var g = 0; g <= 4; g++) {
      var y = padT + (ch * g) / 4;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      var val = max * (1 - g / 4);
      ctx.fillText(val >= 1000 ? (val / 1000).toFixed(1) + "k" : Math.round(val), 6, y + 4);
    }
    function line(key, color) {
      ctx.beginPath();
      series.forEach(function (d, i) {
        var x = padL + (cw * (series.length === 1 ? 0.5 : i / (series.length - 1)));
        var y = padT + ch - (ch * d[key]) / max;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.stroke();
      series.forEach(function (d, i) {
        var x = padL + (cw * (series.length === 1 ? 0.5 : i / (series.length - 1)));
        var y = padT + ch - (ch * d[key]) / max;
        ctx.beginPath(); ctx.arc(x, y, 3, 0, 7); ctx.fillStyle = color; ctx.fill();
      });
    }
    line("billing", "#174478");
    line("collection", "#0e9f8a");
    ctx.fillStyle = "#6b7a90";
    var step = Math.ceil(series.length / 10);
    series.forEach(function (d, i) {
      if (i % step === 0 || i === series.length - 1) {
        var x = padL + (cw * (series.length === 1 ? 0.5 : i / (series.length - 1)));
        ctx.fillText(d.label, x - 14, H - 10);
      }
    });
    // legend
    ctx.fillStyle = "#174478"; ctx.fillRect(padL, H - 2, 10, 10);
    ctx.fillStyle = "#243447"; ctx.fillText("Billed", padL + 14, H + 6 - 8 + 10 - 2);
    ctx.fillStyle = "#0e9f8a"; ctx.fillRect(padL + 80, H - 2, 10, 10);
    ctx.fillStyle = "#243447"; ctx.fillText("Collected", padL + 94, H - 2 + 8);
  }
  window.addEventListener("resize", drawChart);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", drawChart);
  else drawChart();
})();
