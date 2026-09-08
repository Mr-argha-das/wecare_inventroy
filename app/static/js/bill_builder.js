/* Dynamic bill / quotation item builder with live totals (vanilla JS).
   Server-side calculation remains the final authority. */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function initBuilder(rootId) {
    var root = document.getElementById(rootId);
    if (!root) return;
    var cfg = {};
    try { cfg = JSON.parse(document.getElementById(rootId + "-config").textContent); } catch (e) { cfg = {}; }
    var services = cfg.services || [];
    var equipment = cfg.equipment || [];
    var itemsWrap = $("#" + rootId + "-items", root);
    var hidden = $("#" + rootId + "-json");
    var form = root.closest("form");

    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }

    function serviceOptions(sel) {
      var h = '<option value="">-- Select service --</option>';
      services.forEach(function (s) {
        h += '<option value="' + esc(s.service_id) + '"' + (s.service_id === sel ? " selected" : "") + ">" +
          esc(s.service_name) + " (" + esc(s.billing_type) + " - Rs." + Number(s.default_rate).toFixed(2) + ")</option>";
      });
      return h;
    }
    function equipmentOptions(sel) {
      var h = '<option value="">-- Select equipment --</option>';
      equipment.forEach(function (e) {
        var label = e.equipment_name + " [" + (e.serial_number || e.equipment_id) + "] - " + e.status +
          " (D:" + Number(e.daily_rate).toFixed(0) + "/W:" + Number(e.weekly_rate).toFixed(0) +
          "/M:" + Number(e.monthly_rate).toFixed(0) + "/Sale:" + Number(e.sale_price).toFixed(0) + ")";
        h += '<option value="' + esc(e.equipment_id) + '"' + (e.equipment_id === sel ? " selected" : "") + ">" + esc(label) + "</option>";
      });
      return h;
    }

    function addRow(item) {
      item = item || {};
      var type = item.item_type || "service";
      var div = document.createElement("div");
      div.className = "builder-item";
      div.setAttribute("data-item", "1");
      var isService = type === "service";
      div.innerHTML =
        '<div class="form-grid">' +
        '<div class="field"><label>Type</label><select data-f="item_type" class="item-type">' +
        '<option value="service"' + (isService ? " selected" : "") + '>Service</option>' +
        '<option value="equipment"' + (!isService ? " selected" : "") + '>Equipment</option></select></div>' +
        '<div class="field svc-only"><label>Service</label><select data-f="ref_select" class="ref-select">' + serviceOptions(item.ref_id) + "</select></div>" +
        '<div class="field eq-only" style="display:none"><label>Equipment</label><select data-f="ref_select" class="ref-select">' + equipmentOptions(item.ref_id) + "</select></div>" +
        '<div class="field"><label>Description *</label><input data-f="description" value="' + esc(item.description || "") + '"></div>' +
        '<div class="field"><label>Billing</label><select data-f="billing_type">' +
        ["", "Per Day", "Per Visit", "Per Hour", "Daily", "Weekly", "Monthly", "Sale"].map(function (b) {
          return '<option value="' + b + '"' + (item.billing_type === b ? " selected" : "") + ">" + (b || "--") + "</option>";
        }).join("") + "</select></div>" +
        '<div class="field"><label>Start</label><input type="date" data-f="start_date" value="' + esc(item.start_date || "") + '"></div>' +
        '<div class="field"><label>End</label><input type="date" data-f="end_date" value="' + esc(item.end_date || "") + '"></div>' +
        '<div class="field"><label>Qty *</label><input type="number" min="0" step="0.01" data-f="quantity" value="' + esc(item.quantity != null ? item.quantity : 1) + '"></div>' +
        '<div class="field"><label>Rate *</label><input type="number" min="0" step="0.01" data-f="rate" value="' + esc(item.rate != null ? item.rate : 0) + '"></div>' +
        '<div class="field"><label>Discount</label><input type="number" min="0" step="0.01" data-f="discount" value="' + esc(item.discount != null ? item.discount : 0) + '"></div>' +
        '<div class="field"><label>&nbsp;</label><button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button></div>' +
        "</div>" +
        '<div class="small muted">Line total: <strong data-line-total>Rs. 0.00</strong></div>';
      itemsWrap.appendChild(div);
      syncRowType(div);
      return div;
    }

    function syncRowType(div) {
      var type = $(".item-type", div).value;
      $all(".svc-only", div).forEach(function (el) { el.style.display = type === "service" ? "" : "none"; });
      $all(".eq-only", div).forEach(function (el) { el.style.display = type === "equipment" ? "" : "none"; });
    }

    function daysBetween(a, b) {
      if (!a || !b) return 0;
      var d1 = new Date(a), d2 = new Date(b);
      var days = Math.round((d2 - d1) / 86400000) + 1;
      return days > 0 ? days : 0;
    }

    function collect() {
      var items = [];
      $all("[data-item]", itemsWrap).forEach(function (div) {
        var g = function (n) {
          var el = $("[data-f='" + n + "']", div);
          // ref_select has two selects (svc/eq); pick the visible one
          if (n === "ref_select") {
            var cands = $all("[data-f='ref_select']", div);
            for (var i = 0; i < cands.length; i++) {
              if (cands[i].closest(".field").style.display !== "none") return cands[i].value;
            }
            return "";
          }
          return el ? el.value : "";
        };
        var type = $(".item-type", div).value;
        var refId = g("ref_select");
        var billing = g("billing_type");
        var start = g("start_date"), end = g("end_date");
        var qty = parseFloat(g("quantity")) || 0;
        // auto qty for date-driven billing
        if (start && end) {
          if (type === "service" && billing === "Per Day") qty = daysBetween(start, end);
          if (type === "equipment") {
            var days = daysBetween(start, end);
            if (billing === "Daily") qty = days;
            else if (billing === "Weekly") qty = days ? Math.ceil(days / 7) : 0;
            else if (billing === "Monthly") qty = days ? Math.ceil(days / 30) : 0;
          }
          var qEl = $("[data-f='quantity']", div);
          if (qEl && qty) qEl.value = qty;
        }
        var serial = "";
        if (type === "equipment" && refId) {
          var found = equipment.filter(function (e) { return e.equipment_id === refId; })[0];
          if (found) serial = found.serial_number || "";
        }
        items.push({
          item_type: type, ref_id: refId, description: g("description"), serial_number: serial,
          start_date: start, end_date: end, billing_type: billing,
          quantity: qty, rate: parseFloat(g("rate")) || 0, discount: parseFloat(g("discount")) || 0
        });
      });
      return items;
    }

    function money(n) {
      return "Rs. " + (isNaN(n) ? 0 : n).toFixed(2);
    }

    function recalc() {
      var items = collect();
      var subtotal = 0;
      $all("[data-item]", itemsWrap).forEach(function (div, i) {
        var it = items[i];
        var line = Math.max(0, it.quantity * it.rate - it.discount);
        subtotal += line;
        $("[data-line-total]", div).textContent = money(line);
      });
      var billDisc = parseFloat($("#bill_discount") ? $("#bill_discount").value : (cfg.discount || 0)) || 0;
      var taxRate = parseFloat($("#tax_rate") ? $("#tax_rate").value : 0) || 0;
      var damage = parseFloat($("#damage_charges") ? $("#damage_charges").value : 0) || 0;
      var loss = parseFloat($("#loss_charges") ? $("#loss_charges").value : 0) || 0;
      var other = parseFloat($("#other_charges") ? $("#other_charges").value : 0) || 0;
      var deposit = parseFloat($("#deposit") ? $("#deposit").value : 0) || 0;
      if (billDisc > subtotal) billDisc = subtotal;
      var net = subtotal - billDisc;
      var tax = (net * taxRate) / 100;
      var grand = net + tax + damage + loss + other;
      function set(id, v) { var el = document.getElementById(id); if (el) el.textContent = money(v); }
      set("t-subtotal", subtotal); set("t-discount", billDisc); set("t-tax", tax);
      set("t-damage", damage); set("t-loss", loss); set("t-other", other);
      set("t-deposit", deposit); set("t-grand", grand);
      if (hidden) hidden.value = JSON.stringify(items);
      return { items: items, grand: grand };
    }

    // events
    itemsWrap.addEventListener("click", function (e) {
      if (e.target.closest("[data-remove]")) {
        e.target.closest("[data-item]").remove();
        recalc();
      }
    });
    itemsWrap.addEventListener("change", function (e) {
      var div = e.target.closest("[data-item]");
      if (!div) return;
      if (e.target.classList.contains("item-type")) {
        syncRowType(div);
        $("[data-f='description']", div).value = "";
        $("[data-f='rate']", div).value = 0;
      }
      if (e.target.classList.contains("ref-select")) {
        var id = e.target.value;
        var type = $(".item-type", div).value;
        if (type === "service") {
          var s = services.filter(function (x) { return x.service_id === id; })[0];
          if (s) {
            $("[data-f='description']", div).value = s.service_name;
            $("[data-f='rate']", div).value = s.default_rate;
            $("[data-f='billing_type']", div).value = s.billing_type;
          }
        } else {
          var q = equipment.filter(function (x) { return x.equipment_id === id; })[0];
          if (q) {
            $("[data-f='description']", div).value = q.equipment_name;
            var bt = $("[data-f='billing_type']", div).value;
            var rate = q.daily_rate;
            if (bt === "Weekly") rate = q.weekly_rate;
            else if (bt === "Monthly") rate = q.monthly_rate;
            else if (bt === "Sale") rate = q.sale_price;
            $("[data-f='rate']", div).value = rate;
            if (q.status !== "Available" && bt !== "Sale") {
              alert("Note: this equipment is currently '" + q.status + "'. Only Available equipment can be billed for rent.");
            }
          }
        }
      }
      if (e.target.getAttribute("data-f") === "billing_type") {
        // auto-fill equipment rate for chosen period
        var divType = $(".item-type", div).value;
        if (divType === "equipment") {
          var sel = $all("[data-f='ref_select']", div).filter(function (c) { return c.closest(".field").style.display !== "none"; })[0];
          var found = sel && equipment.filter(function (x) { return x.equipment_id === sel.value; })[0];
          if (found) {
            var bt2 = e.target.value, rate2 = found.daily_rate;
            if (bt2 === "Weekly") rate2 = found.weekly_rate;
            else if (bt2 === "Monthly") rate2 = found.monthly_rate;
            else if (bt2 === "Sale") rate2 = found.sale_price;
            $("[data-f='rate']", div).value = rate2;
          }
        }
      }
      recalc();
    });
    itemsWrap.addEventListener("input", recalc);
    ["bill_discount", "tax_rate", "damage_charges", "loss_charges", "other_charges", "deposit"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("input", recalc);
    });
    var addSvc = document.getElementById(rootId + "-add-service");
    if (addSvc) addSvc.addEventListener("click", function () { addRow({ item_type: "service" }); recalc(); });
    var addEq = document.getElementById(rootId + "-add-equipment");
    if (addEq) addEq.addEventListener("click", function () { addRow({ item_type: "equipment" }); recalc(); });
    var billType = document.getElementById("bill_type");
    if (billType) billType.addEventListener("change", recalc);

    if (form) {
      form.addEventListener("submit", function (e) {
        var res = recalc();
        if (!res.items.length) {
          e.preventDefault();
          alert("Add at least one service or equipment item.");
          return;
        }
        for (var i = 0; i < res.items.length; i++) {
          var it = res.items[i];
          if (!it.description) { e.preventDefault(); alert("Item " + (i + 1) + ": description is required."); return; }
          if (!(it.quantity > 0)) { e.preventDefault(); alert("Item " + (i + 1) + ": quantity must be greater than 0."); return; }
          if (!(it.rate >= 0)) { e.preventDefault(); alert("Item " + (i + 1) + ": rate cannot be negative."); return; }
          if (it.discount > it.quantity * it.rate) { e.preventDefault(); alert("Item " + (i + 1) + ": discount exceeds line amount."); return; }
          if (it.start_date && it.end_date && it.end_date < it.start_date) { e.preventDefault(); alert("Item " + (i + 1) + ": end date cannot be before start date."); return; }
        }
      });
    }

    // prefill
    var pre = cfg.prefill || [];
    if (pre.length) pre.forEach(addRow);
    else addRow({ item_type: "service" });
    recalc();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initBuilder("billBuilder");
      initBuilder("quotationBuilder");
    });
  } else {
    initBuilder("billBuilder");
    initBuilder("quotationBuilder");
  }
})();
