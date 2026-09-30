/* Dynamic bill / quotation item builder with live totals (vanilla JS).
   Server-side calculation remains the final authority.

   CHARGE BY:
   - Har row me "Charge By" dropdown hai: Qty ya Days.
   - Dono fields OPTIONAL hain; sirf selected wala hi calculation
     drive karta hai, doosre ka effective value 1 maan liya jaata hai.
*/
(function () {
  "use strict";

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function $all(sel, root) {
    return Array.prototype.slice.call(
      (root || document).querySelectorAll(sel)
    );
  }

  function initBuilder(rootId) {
    var root = document.getElementById(rootId);
    if (!root) return;

    var cfg = {};
    try {
      cfg = JSON.parse(
        document.getElementById(rootId + "-config").textContent
      );
    } catch (e) {
      cfg = {};
    }

    var services = cfg.services || [];
    var equipment = cfg.equipment || [];
    var itemsWrap = $("#" + rootId + "-items", root);
    var hidden = $("#" + rootId + "-json");
    var form = root.closest("form");

    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
        return {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        }[c];
      });
    }

    function serviceOptions(sel) {
      var h = '<option value="">-- Select service --</option>';

      services.forEach(function (s) {
        h +=
          '<option value="' +
          esc(s.service_id) +
          '"' +
          (s.service_id === sel ? " selected" : "") +
          ">" +
          esc(s.service_name) +
          " (" +
          esc(s.billing_type) +
          " - Rs." +
          Number(s.default_rate).toFixed(2) +
          ")</option>";
      });

      return h;
    }

    function equipmentOptions(sel) {
      var h = '<option value="">-- Select equipment --</option>';

      equipment.forEach(function (e) {
        var label =
          e.equipment_name +
          " [" +
          (e.serial_number || e.equipment_id) +
          "] - " +
          e.status +
          " (D:" +
          Number(e.daily_rate).toFixed(0) +
          "/W:" +
          Number(e.weekly_rate).toFixed(0) +
          "/M:" +
          Number(e.monthly_rate).toFixed(0) +
          "/Sale:" +
          Number(e.sale_price).toFixed(0) +
          ")";

        h +=
          '<option value="' +
          esc(e.equipment_id) +
          '"' +
          (e.equipment_id === sel ? " selected" : "") +
          ">" +
          esc(label) +
          "</option>";
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

      // Charge mode: "qty" (default) or "days"
      // (prefill se charge_mode ya unit_type, dono me se jo mile)
      var savedMode = item.charge_mode || item.unit_type;
      var chargeMode = savedMode === "days" ? "days" : "qty";

      div.innerHTML =
        '<div class="form-grid">' +

        '<div class="field">' +
        '<label>Type</label>' +
        '<select data-f="item_type" class="item-type">' +
        '<option value="service"' +
        (isService ? " selected" : "") +
        '>Service</option>' +
        '<option value="equipment"' +
        (!isService ? " selected" : "") +
        '>Equipment</option>' +
        "</select>" +
        "</div>" +

        '<div class="field svc-only">' +
        "<label>Service</label>" +
        '<select data-f="ref_select" class="ref-select">' +
        serviceOptions(item.ref_id) +
        "</select>" +
        "</div>" +

        '<div class="field eq-only" style="display:none">' +
        "<label>Equipment</label>" +
        '<select data-f="ref_select" class="ref-select">' +
        equipmentOptions(item.ref_id) +
        "</select>" +
        "</div>" +

        '<div class="field">' +
        "<label>Description</label>" +
        '<input data-f="description" value="' +
        esc(item.description || "") +
        '">' +
        "</div>" +

        '<div class="field">' +
        "<label>Billing</label>" +
        '<select data-f="billing_type">' +
        ["", "Per Day", "Per Visit", "Per Hour", "Daily", "Weekly", "Monthly", "Sale"]
          .map(function (b) {
            return (
              '<option value="' +
              b +
              '"' +
              (item.billing_type === b ? " selected" : "") +
              ">" +
              (b || "--") +
              "</option>"
            );
          })
          .join("") +
        "</select>" +
        "</div>" +

        '<div class="field">' +
        "<label>Start</label>" +
        '<input type="date" data-f="start_date" value="' +
        esc(item.start_date || "") +
        '">' +
        "</div>" +

        '<div class="field">' +
        "<label>End</label>" +
        '<input type="date" data-f="end_date" value="' +
        esc(item.end_date || "") +
        '">' +
        "</div>" +

        /* Charge By dropdown - optional switch between Qty / Days */
        '<div class="field">' +
        "<label>Charge By</label>" +
        '<select data-f="charge_mode">' +
        '<option value="qty"' +
        (chargeMode === "qty" ? " selected" : "") +
        ">Qty</option>" +
        '<option value="days"' +
        (chargeMode === "days" ? " selected" : "") +
        ">Days</option>" +
        "</select>" +
        "</div>" +

        /* Qty aur Days dono OPTIONAL — star nahi hai.
           Jo "Charge By" me chuna hai, wahi visible rehta hai. */
        '<div class="field field-qty">' +
        "<label>Qty</label>" +
        '<input type="number" min="0" step="0.01" data-f="quantity" value="' +
        esc(item.quantity != null ? item.quantity : 1) +
        '">' +
        "</div>" +

        '<div class="field field-days">' +
        "<label>Days</label>" +
        '<input type="number" min="0" step="1" data-f="days" value="' +
        esc(item.days != null && Number(item.days) > 0 ? item.days : 1) +
        '">' +
        "</div>" +

        '<div class="field">' +
        "<label>Rate *</label>" +
        '<input type="number" min="0" step="0.01" data-f="rate" value="' +
        esc(item.rate != null ? item.rate : 0) +
        '">' +
        "</div>" +

        '<div class="field">' +
        "<label>Discount</label>" +
        '<input type="number" min="0" step="0.01" data-f="discount" value="' +
        esc(item.discount != null ? item.discount : 0) +
        '">' +
        "</div>" +

        '<div class="field">' +
        "<label>&nbsp;</label>" +
        '<button type="button" class="btn btn-danger btn-sm" data-remove>Remove</button>' +
        "</div>" +

        "</div>" +

        '<div class="small muted">' +
        'Line total: <strong data-line-total>Rs. 0.00</strong>' +
        "</div>";

      itemsWrap.appendChild(div);

      syncRowType(div);
      syncChargeMode(div);

      return div;
    }

    function syncRowType(div) {
      var type = $(".item-type", div).value;

      $all(".svc-only", div).forEach(function (el) {
        el.style.display = type === "service" ? "" : "none";
      });

      $all(".eq-only", div).forEach(function (el) {
        el.style.display = type === "equipment" ? "" : "none";
      });
    }

    /* Charge By dropdown ke hisaab se sirf ek field (Qty ya Days)
       enabled/visible rakhta hai; doosri disabled ho jaati hai aur
       calculation me uska effective value 1 maan liya jaata hai. */
    function syncChargeMode(div) {
      var modeEl = $("[data-f='charge_mode']", div);
      var mode = modeEl ? modeEl.value : "qty";

      var qtyField = $(".field-qty", div);
      var daysField = $(".field-days", div);
      var qtyInput = $("[data-f='quantity']", div);
      var daysInput = $("[data-f='days']", div);

      if (mode === "days") {
        if (qtyField) qtyField.style.display = "none";
        if (daysField) daysField.style.display = "";
        if (qtyInput) qtyInput.disabled = true;
        if (daysInput) daysInput.disabled = false;
      } else {
        if (qtyField) qtyField.style.display = "";
        if (daysField) daysField.style.display = "none";
        if (qtyInput) qtyInput.disabled = false;
        if (daysInput) daysInput.disabled = true;
      }
    }

    function daysBetween(a, b) {
      if (!a || !b) return 0;

      var d1 = new Date(a);
      var d2 = new Date(b);

      var days = Math.round((d2 - d1) / 86400000) + 1;

      return days > 0 ? days : 0;
    }

    /* Start/End choose hone par, agar Charge By = Days hai, to
       Days auto-fill ho jaata hai (Weekly -> weeks, Monthly -> months). */
    function autoFillDays(div) {
      var modeEl = $("[data-f='charge_mode']", div);
      if (modeEl && modeEl.value !== "days") return;

      var start = $("[data-f='start_date']", div).value;
      var end = $("[data-f='end_date']", div).value;
      var daysEl = $("[data-f='days']", div);

      if (!daysEl || !start || !end) return;

      var raw = daysBetween(start, end);
      if (!raw) return;

      var billing = $("[data-f='billing_type']", div).value;
      var units = raw;

      if (billing === "Weekly") {
        units = Math.ceil(raw / 7);
      } else if (billing === "Monthly") {
        units = Math.ceil(raw / 30);
      } else if (billing === "Sale") {
        units = 1;
      }

      daysEl.value = units > 0 ? units : 1;
    }

    function collect() {
      var items = [];

      $all("[data-item]", itemsWrap).forEach(function (div) {
        var g = function (n) {
          var el = $("[data-f='" + n + "']", div);

          // ref_select has two selects (svc/eq);
          // pick the visible one
          if (n === "ref_select") {
            var cands = $all("[data-f='ref_select']", div);

            for (var i = 0; i < cands.length; i++) {
              if (
                cands[i].closest(".field").style.display !== "none"
              ) {
                return cands[i].value;
              }
            }

            return "";
          }

          return el ? el.value : "";
        };

        var type = $(".item-type", div).value;
        var refId = g("ref_select");
        var billing = g("billing_type");
        var start = g("start_date");
        var end = g("end_date");
        var mode = g("charge_mode") || "qty";

        var qty, days;

        if (mode === "days") {
          // Days is the active driver; Qty treated as 1.
          days = parseFloat(g("days"));
          if (!(days > 0)) days = 0;
          qty = 1;
        } else {
          // Qty is the active driver; Days treated as 1.
          qty = parseFloat(g("quantity"));
          if (!(qty > 0)) qty = 0;
          days = 1;
        }

        var serial = "";

        if (type === "equipment" && refId) {
          var found = equipment.filter(function (e) {
            return e.equipment_id === refId;
          })[0];

          if (found) {
            serial = found.serial_number || "";
          }
        }

        items.push({
          item_type: type,
          ref_id: refId,
          description: g("description"),
          serial_number: serial,
          start_date: start,
          end_date: end,
          billing_type: billing,

          // Backend engine "unit_type" padhta hai;
          // "charge_mode" purane code ke liye rakha gaya hai.
          unit_type: mode,
          charge_mode: mode,

          quantity: qty,
          days: days,
          rate: parseFloat(g("rate")) || 0,
          discount: parseFloat(g("discount")) || 0
        });
      });

      return items;
    }

    function money(n) {
      return (
        "Rs. " +
        (isNaN(n) ? 0 : n).toFixed(2)
      );
    }

    /*
     * GST calculation
     */
    function calculateGST(taxable) {
      var applicableEl = document.getElementById("gst_applicable");
      var typeEl = document.getElementById("gst_type");
      var rateEl = document.getElementById("gst_rate");

      var applicable = false;

      if (applicableEl) {
        var value = String(applicableEl.value || "")
          .trim()
          .toLowerCase();

        applicable =
          value === "yes" ||
          value === "true" ||
          value === "1" ||
          value === "on";
      }

      var gstType = typeEl
        ? String(typeEl.value || "CGST_SGST").trim()
        : "CGST_SGST";

      var gstRate = rateEl
        ? parseFloat(rateEl.value) || 0
        : 0;

      if (!applicable || gstRate <= 0) {
        return {
          rate: 0,
          tax: 0,
          cgst: 0,
          sgst: 0,
          igst: 0
        };
      }

      var tax = (taxable * gstRate) / 100;

      var cgst = 0;
      var sgst = 0;
      var igst = 0;

      if (gstType === "IGST") {
        igst = tax;
      } else {
        cgst = tax / 2;
        sgst = tax / 2;
      }

      return {
        rate: gstRate,
        tax: tax,
        cgst: cgst,
        sgst: sgst,
        igst: igst
      };
    }

    function recalc() {
      var items = collect();

      var subtotal = 0;

      $all("[data-item]", itemsWrap).forEach(function (div, i) {
        var it = items[i];

        if (!it) return;

        var line = Math.max(
          0,
          it.quantity * it.days * it.rate - it.discount
        );

        subtotal += line;

        var lineTotalEl = $("[data-line-total]", div);

        if (lineTotalEl) {
          lineTotalEl.textContent = money(line);
        }
      });

      /*
       * Bill discount
       */
      var billDiscEl = document.getElementById("bill_discount");

      var billDisc = billDiscEl
        ? parseFloat(billDiscEl.value) || 0
        : (cfg.discount || 0);

      if (billDisc > subtotal) {
        billDisc = subtotal;
      }

      if (billDisc < 0) {
        billDisc = 0;
      }

      /*
       * Taxable amount
       */
      var taxable = subtotal - billDisc;

      if (taxable < 0) {
        taxable = 0;
      }

      /*
       * GST
       */
      var gst = calculateGST(taxable);

      /*
       * Keep old tax_rate field synchronized.
       */
      var taxRateEl = document.getElementById("tax_rate");

      if (taxRateEl) {
        taxRateEl.value = gst.rate;
      }

      /*
       * Other charges (Security Deposit removed)
       */
      var damageEl = document.getElementById("damage_charges");
      var lossEl = document.getElementById("loss_charges");
      var otherEl = document.getElementById("other_charges");

      var damage = damageEl ? parseFloat(damageEl.value) || 0 : 0;
      var loss = lossEl ? parseFloat(lossEl.value) || 0 : 0;
      var other = otherEl ? parseFloat(otherEl.value) || 0 : 0;

      /*
       * Grand total
       */
      var grand =
        taxable +
        gst.tax +
        damage +
        loss +
        other;

      function set(id, v) {
        var el = document.getElementById(id);

        if (el) {
          el.textContent = money(v);
        }
      }

      /*
       * Existing totals (always shown, like the Preview modal)
       */
      set("t-subtotal", subtotal);
      set("t-discount", billDisc);
      set("t-tax", gst.tax);
      set("t-damage", damage);
      set("t-loss", loss);
      set("t-other", other);
      set("t-grand", grand);

      /*
       * GST breakdown
       */
      set("t-taxable", taxable);
      set("t-cgst", gst.cgst);
      set("t-sgst", gst.sgst);
      set("t-igst", gst.igst);

      /*
       * Store items in hidden JSON field
       */
      if (hidden) {
        hidden.value = JSON.stringify(items);
      }

      return {
        items: items,
        subtotal: subtotal,
        discount: billDisc,
        taxable: taxable,
        gst_rate: gst.rate,
        tax: gst.tax,
        cgst: gst.cgst,
        sgst: gst.sgst,
        igst: gst.igst,
        damage: damage,
        loss: loss,
        other: other,
        grand: grand
      };
    }

    /*
     * Remove item
     */
    itemsWrap.addEventListener("click", function (e) {
      if (e.target.closest("[data-remove]")) {
        e.target.closest("[data-item]").remove();
        recalc();
      }
    });

    /*
     * Item changes
     */
    itemsWrap.addEventListener("change", function (e) {
      var div = e.target.closest("[data-item]");

      if (!div) return;

      /*
       * Change Service / Equipment
       */
      if (e.target.classList.contains("item-type")) {
        syncRowType(div);

        $("[data-f='description']", div).value = "";
        $("[data-f='rate']", div).value = 0;
      }

      /*
       * Charge By (Qty / Days) toggle
       */
      if (e.target.getAttribute("data-f") === "charge_mode") {
        syncChargeMode(div);
        autoFillDays(div);
      }

      /*
       * Service / Equipment selection
       */
      if (e.target.classList.contains("ref-select")) {
        var id = e.target.value;
        var type = $(".item-type", div).value;

        if (type === "service") {
          var s = services.filter(function (x) {
            return x.service_id === id;
          })[0];

          if (s) {
            $("[data-f='description']", div).value = s.service_name;
            $("[data-f='rate']", div).value = s.default_rate;
            $("[data-f='billing_type']", div).value = s.billing_type;
            autoFillDays(div);
          }
        } else {
          var q = equipment.filter(function (x) {
            return x.equipment_id === id;
          })[0];

          if (q) {
            $("[data-f='description']", div).value = q.equipment_name;

            var bt = $("[data-f='billing_type']", div).value;

            var rate = q.daily_rate;

            if (bt === "Weekly") {
              rate = q.weekly_rate;
            } else if (bt === "Monthly") {
              rate = q.monthly_rate;
            } else if (bt === "Sale") {
              rate = q.sale_price;
            }

            $("[data-f='rate']", div).value = rate;

            if (q.status !== "Available" && bt !== "Sale") {
              alert(
                "Note: this equipment is currently '" +
                q.status +
                "'. Only Available equipment can be billed for rent."
              );
            }
          }
        }
      }

      /*
       * Billing type change
       */
      if (e.target.getAttribute("data-f") === "billing_type") {
        var divType = $(".item-type", div).value;

        if (divType === "equipment") {
          var sel = $all("[data-f='ref_select']", div).filter(function (c) {
            return c.closest(".field").style.display !== "none";
          })[0];

          var found =
            sel &&
            equipment.filter(function (x) {
              return x.equipment_id === sel.value;
            })[0];

          if (found) {
            var bt2 = e.target.value;
            var rate2 = found.daily_rate;

            if (bt2 === "Weekly") {
              rate2 = found.weekly_rate;
            } else if (bt2 === "Monthly") {
              rate2 = found.monthly_rate;
            } else if (bt2 === "Sale") {
              rate2 = found.sale_price;
            }

            $("[data-f='rate']", div).value = rate2;
          }
        }

        autoFillDays(div);
      }

      /*
       * Start/End date change -> Days auto-fill (if charge mode = days)
       */
      if (
        e.target.getAttribute("data-f") === "start_date" ||
        e.target.getAttribute("data-f") === "end_date"
      ) {
        autoFillDays(div);
      }

      recalc();
    });

    /*
     * Item input changes
     */
    itemsWrap.addEventListener("input", function (e) {
      var div = e.target.closest("[data-item]");

      if (
        div &&
        (e.target.getAttribute("data-f") === "start_date" ||
          e.target.getAttribute("data-f") === "end_date")
      ) {
        autoFillDays(div);
      }

      recalc();
    });

    /*
     * Existing bill fields (deposit removed from this list)
     */
    [
      "bill_discount",
      "damage_charges",
      "loss_charges",
      "other_charges"
    ].forEach(function (id) {
      var el = document.getElementById(id);

      if (el) {
        el.addEventListener("input", recalc);
        el.addEventListener("change", recalc);
      }
    });

    /*
     * GST fields
     */
    ["gst_applicable", "gst_type", "gst_rate"].forEach(function (id) {
      var el = document.getElementById(id);

      if (el) {
        el.addEventListener("input", recalc);
        el.addEventListener("change", recalc);
      }
    });

    /*
     * Add Service
     */
    var addSvc = document.getElementById(rootId + "-add-service");

    if (addSvc) {
      addSvc.addEventListener("click", function () {
        addRow({ item_type: "service" });
        recalc();
      });
    }

    /*
     * Add Equipment
     */
    var addEq = document.getElementById(rootId + "-add-equipment");

    if (addEq) {
      addEq.addEventListener("click", function () {
        addRow({ item_type: "equipment" });
        recalc();
      });
    }

    /*
     * Bill type (OPTIONAL - koi selection na ho to bhi bill save hoga)
     */
    var billType = document.getElementById("bill_type");

    if (billType) {
      billType.addEventListener("change", function () {
        recalc();
      });
    }

    /*
     * Form submit validation
     */
    if (form) {
      form.addEventListener("submit", function (e) {
        var res = recalc();

        if (!res.items.length) {
          e.preventDefault();
          alert("Add at least one service or equipment item.");
          return;
        }

        /*
         * GST validation
         */
        var gstApplicableEl = document.getElementById("gst_applicable");
        var gstRateEl = document.getElementById("gst_rate");

        if (gstApplicableEl) {
          var gstApplicable = String(gstApplicableEl.value || "")
            .trim()
            .toLowerCase();

          if (
            gstApplicable === "yes" &&
            gstRateEl &&
            (parseFloat(gstRateEl.value) || 0) < 0
          ) {
            e.preventDefault();
            alert("GST rate cannot be negative.");
            return;
          }
        }

        /*
         * Item validation
         *
         * Qty aur Days dono OPTIONAL hain — sirf wahi field check
         * hota hai jo "Charge By" me select kiya gaya hai.
         */
        for (var i = 0; i < res.items.length; i++) {
          var it = res.items[i];

          var isDaysMode = it.charge_mode === "days";

          var driver = isDaysMode ? it.days : it.quantity;
          var driverName = isDaysMode ? "Days" : "Qty";

          if (!(driver > 0)) {
            e.preventDefault();
            alert(
              "Item " +
              (i + 1) +
              ": " +
              driverName +
              " must be greater than 0 (Charge By = " +
              driverName +
              ")."
            );
            return;
          }

          if (!(it.rate >= 0)) {
            e.preventDefault();
            alert("Item " + (i + 1) + ": rate cannot be negative.");
            return;
          }

          if (it.discount > it.quantity * it.days * it.rate) {
            e.preventDefault();
            alert("Item " + (i + 1) + ": discount exceeds line amount.");
            return;
          }

          if (
            it.start_date &&
            it.end_date &&
            it.end_date < it.start_date
          ) {
            e.preventDefault();
            alert(
              "Item " + (i + 1) + ": end date cannot be before start date."
            );
            return;
          }
        }
      });
    }

    /*
     * Prefill existing bill / quotation items
     */
    var pre = cfg.prefill || [];

    if (pre.length) {
      pre.forEach(addRow);
    } else {
      addRow({ item_type: "service" });
    }

    /*
     * Initial calculation
     */
    recalc();
  }

  /*
   * Initialize builders
   */
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