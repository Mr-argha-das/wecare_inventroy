(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {

        const gstApplicable = document.getElementById("gst_applicable");
        const gstType = document.getElementById("gst_type");
        const gstRate = document.getElementById("gst_rate");
        const taxRate = document.getElementById("tax_rate");

        const gstTypeField = document.getElementById("gst-type-field");
        const gstRateField = document.getElementById("gst-rate-field");

        const taxableElement = document.getElementById("t-taxable");
        const cgstElement = document.getElementById("t-cgst");
        const sgstElement = document.getElementById("t-sgst");
        const igstElement = document.getElementById("t-igst");


        /* =====================================================
           NUMBER HELPERS
           ===================================================== */

        function numberValue(value) {
            const number = parseFloat(value);

            if (isNaN(number) || number < 0) {
                return 0;
            }

            return number;
        }


        function money(value) {
            return numberValue(value).toFixed(2);
        }


        function setAmount(element, value) {
            if (element) {
                element.textContent = "Rs. " + money(value);
            }
        }


        /* =====================================================
           GST FIELD VISIBILITY
           ===================================================== */

        function updateGSTVisibility() {

            if (!gstApplicable) {
                return;
            }

            const enabled = gstApplicable.value === "yes";

            if (gstTypeField) {
                gstTypeField.style.display = enabled ? "" : "none";
            }

            if (gstRateField) {
                gstRateField.style.display = enabled ? "" : "none";
            }


            if (!enabled) {

                /*
                 * NOTE: gstRate.value ko yahan 0 NAHI kiya jaata,
                 * taaki user "No" -> "Yes" kare to purani rate wapas mile.
                 * Server "No" hone par rate khud zero kar deta hai.
                 */

                if (taxRate) {
                    taxRate.value = "0";
                }

                setAmount(cgstElement, 0);
                setAmount(sgstElement, 0);
                setAmount(igstElement, 0);

            } else {

                syncTaxRate();

            }
        }


        /* =====================================================
           TAX RATE COMPATIBILITY
           ===================================================== */

        function syncTaxRate() {

            if (!taxRate || !gstRate) {
                return;
            }

            taxRate.value = gstApplicable &&
                gstApplicable.value === "yes"
                ? (gstRate.value || "0")
                : "0";
        }


        /* =====================================================
           GST CALCULATION
           ===================================================== */

        function calculateGST() {

            if (!gstApplicable || gstApplicable.value !== "yes") {

                setAmount(cgstElement, 0);
                setAmount(sgstElement, 0);
                setAmount(igstElement, 0);

                return {
                    taxable: 0,
                    cgst: 0,
                    sgst: 0,
                    igst: 0,
                    totalTax: 0
                };
            }


            const taxableElementFromPage =
                document.getElementById("t-taxable");

            let taxable = 0;


            /*
             * bill_builder.js existing calculation se
             * taxable amount lene ki koshish.
             *
             * Agar existing builder t-taxable update nahi karta,
             * subtotal - discount se calculate hoga.
             */

            if (taxableElementFromPage) {

                const text =
                    taxableElementFromPage.textContent
                        .replace("Rs.", "")
                        .replace(/,/g, "")
                        .trim();

                taxable = numberValue(text);
            }


            if (taxable === 0) {

                const subtotalElement =
                    document.getElementById("t-subtotal");

                const discountElement =
                    document.getElementById("t-discount");

                const subtotal = subtotalElement
                    ? numberValue(
                        subtotalElement.textContent
                            .replace("Rs.", "")
                            .replace(/,/g, "")
                    )
                    : 0;

                const discount = discountElement
                    ? numberValue(
                        discountElement.textContent
                            .replace("Rs.", "")
                            .replace(/,/g, "")
                    )
                    : 0;

                taxable = Math.max(0, subtotal - discount);
            }


            const rate = numberValue(
                gstRate ? gstRate.value : 0
            );

            const totalTax =
                taxable * rate / 100;


            let cgst = 0;
            let sgst = 0;
            let igst = 0;


            if (gstType && gstType.value === "IGST") {

                igst = totalTax;

            } else {

                cgst = totalTax / 2;
                sgst = totalTax / 2;

            }


            setAmount(cgstElement, cgst);
            setAmount(sgstElement, sgst);
            setAmount(igstElement, igst);


            return {
                taxable: taxable,
                cgst: cgst,
                sgst: sgst,
                igst: igst,
                totalTax: totalTax
            };
        }


        /* =====================================================
           TAXABLE AMOUNT
           ===================================================== */

        function updateTaxableAmount() {

            if (!taxableElement) {
                return;
            }

            const subtotalElement =
                document.getElementById("t-subtotal");

            const discountElement =
                document.getElementById("t-discount");

            if (!subtotalElement) {
                return;
            }


            const subtotal = numberValue(
                subtotalElement.textContent
                    .replace("Rs.", "")
                    .replace(/,/g, "")
            );


            const discount = discountElement
                ? numberValue(
                    discountElement.textContent
                        .replace("Rs.", "")
                        .replace(/,/g, "")
                )
                : 0;


            const taxable =
                Math.max(0, subtotal - discount);


            setAmount(taxableElement, taxable);
        }


        /* =====================================================
           REFRESH GST
           ===================================================== */

        function refreshGST() {

            updateTaxableAmount();

            syncTaxRate();

            calculateGST();
        }


        /* =====================================================
           EVENTS
           ===================================================== */

        if (gstApplicable) {

            gstApplicable.addEventListener(
                "change",
                function () {

                    updateGSTVisibility();
                    refreshGST();

                }
            );

        }


        if (gstType) {

            gstType.addEventListener(
                "change",
                function () {

                    refreshGST();

                }
            );

        }


        if (gstRate) {

            gstRate.addEventListener(
                "input",
                function () {

                    syncTaxRate();
                    refreshGST();

                }
            );

        }


        /* =====================================================
           WATCH EXISTING BILL BUILDER TOTALS
           ===================================================== */

        const totalElements = [
            document.getElementById("t-subtotal"),
            document.getElementById("t-discount")
        ];


        totalElements.forEach(function (element) {

            if (!element) {
                return;
            }


            const observer = new MutationObserver(function () {

                updateTaxableAmount();
                calculateGST();

            });


            observer.observe(element, {
                childList: true,
                subtree: true,
                characterData: true
            });

        });


        /* =====================================================
           FORM SUBMIT
           ===================================================== */

        const form = document.getElementById("billForm");


        if (form) {

            form.addEventListener(
                "submit",
                function () {

                    syncTaxRate();

                    updateTaxableAmount();

                    calculateGST();

                }
            );

        }


        /* =====================================================
           INITIAL LOAD
           ===================================================== */

        updateGSTVisibility();

        updateTaxableAmount();

        syncTaxRate();

        calculateGST();

    });

})();