/* ==========================================================================
   SY CSC CENTRE - small vanilla JS helpers (no framework, no CDN)
   ========================================================================== */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    /* ---------------------------------------------------------- sidebar */
    var burger = document.querySelector(".hamburger");
    var sidebar = document.querySelector(".sidebar");
    if (burger && sidebar) {
      burger.addEventListener("click", function () {
        sidebar.classList.toggle("open");
      });
      document.addEventListener("click", function (event) {
        if (window.innerWidth > 860) { return; }
        if (!sidebar.contains(event.target) && event.target !== burger) {
          sidebar.classList.remove("open");
        }
      });
    }

    /* ------------------------------------------------------- flash close */
    document.querySelectorAll(".alert .x").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.closest(".alert").remove();
      });
    });
    setTimeout(function () {
      document.querySelectorAll(".alert.auto-hide").forEach(function (el) {
        el.style.transition = "opacity .4s";
        el.style.opacity = "0";
        setTimeout(function () { el.remove(); }, 420);
      });
    }, 7000);

    /* -------------------------------------------------- loading overlay */
    var overlay = document.getElementById("loadingOverlay");
    document.querySelectorAll("form[data-loading]").forEach(function (form) {
      form.addEventListener("submit", function () {
        if (overlay) { overlay.classList.add("on"); }
        var btn = form.querySelector("button[type=submit]");
        if (btn && !btn.dataset.keepText) {
          btn.disabled = true;
          btn.innerHTML = '<span class="spinner"></span> ' + (btn.dataset.loadingText || "Saving...");
        }
      });
    });

    /* ------------------------------------------------ money total helper */
    var moneyInputs = document.querySelectorAll("[data-total-target]");
    if (moneyInputs.length) {
      var recalc = function () {
        var sum = 0;
        moneyInputs.forEach(function (input) {
          var value = parseFloat(input.value);
          sum += isNaN(value) ? 0 : value;
        });
        document.querySelectorAll("[data-total-target]").forEach(function (input) {
          var target = document.querySelector(input.dataset.totalTarget);
          if (target) { target.value = sum.toFixed(2); }
        });
        document.querySelectorAll("[data-total-display]").forEach(function (el) {
          el.textContent = sum.toFixed(2);
        });
      };
      moneyInputs.forEach(function (input) {
        input.addEventListener("input", recalc);
        input.addEventListener("change", recalc);
      });
      recalc();
    }

    /* --------------------------------------------------- total days calc */
    var dayFrom = document.querySelector("[data-days-from]");
    var dayTo = document.querySelector("[data-days-to]");
    if (dayFrom && dayTo) {
      var computeDays = function () {
        var out = document.querySelector("[data-days-output]");
        if (!out) { return; }
        if (!dayFrom.value || !dayTo.value) { out.value = ""; return; }
        var a = new Date(dayFrom.value + "T00:00:00");
        var b = new Date(dayTo.value + "T00:00:00");
        if (isNaN(a) || isNaN(b) || b < a) { out.value = ""; return; }
        out.value = Math.round((b - a) / 86400000) + 1;
      };
      dayFrom.addEventListener("change", computeDays);
      dayTo.addEventListener("change", computeDays);
      computeDays();
    }

    /* ----------------------------------------------- customer live search */
    var picker = document.getElementById("customerPicker");
    if (picker) {
      var box = document.getElementById("customerResults");
      var hidden = document.getElementById("customerIdField");
      var label = document.getElementById("customerChosen");
      var timer = null;
      picker.addEventListener("input", function () {
        clearTimeout(timer);
        var term = picker.value.trim();
        if (term.length < 2) { box.innerHTML = ""; return; }
        timer = setTimeout(function () {
          fetch(picker.dataset.url + "?q=" + encodeURIComponent(term))
            .then(function (r) { return r.json(); })
            .then(function (data) {
              box.innerHTML = "";
              if (!data.results.length) {
                box.innerHTML = '<div class="empty">No customer found</div>';
                return;
              }
              data.results.forEach(function (row) {
                var item = document.createElement("button");
                item.type = "button";
                item.className = "btn btn-outline btn-sm";
                item.style.margin = "2px";
                item.textContent = row.name + " - " + row.code + " (" + row.mobile + ")";
                item.addEventListener("click", function () {
                  hidden.value = row.id;
                  label.textContent = row.name + " | " + row.code;
                  label.className = "badge info";
                  box.innerHTML = "";
                  picker.value = "";
                });
                box.appendChild(item);
              });
            });
        }, 250);
      });
    }

    /* --------------------------------------------------- table row filter */
    var filter = document.getElementById("tableFilter");
    if (filter) {
      filter.addEventListener("input", function () {
        var term = filter.value.toLowerCase();
        document.querySelectorAll("[data-filter-row]").forEach(function (row) {
          row.style.display = row.textContent.toLowerCase().indexOf(term) > -1 ? "" : "none";
        });
      });
    }

    /* ------------------------------------------------------------- tabs */
    document.querySelectorAll(".tabs").forEach(function (tabs) {
      tabs.querySelectorAll("button").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var wrap = tabs.parentElement;
          tabs.querySelectorAll("button").forEach(function (b) { b.classList.remove("on"); });
          btn.classList.add("on");
          wrap.querySelectorAll(".tab-pane").forEach(function (pane) {
            pane.classList.toggle("on", pane.id === btn.dataset.tab);
          });
        });
      });
    });

    /* ---------------------------------------------- confirm on submit */
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.dataset.confirm)) { event.preventDefault(); }
      });
    });

    /* --------------------------------------------- print buttons */
    document.querySelectorAll("[data-print]").forEach(function (btn) {
      btn.addEventListener("click", function () { window.print(); });
    });

    /* --------------------------------------------- select-all checkbox */
    var selectAll = document.getElementById("selectAll");
    if (selectAll) {
      selectAll.addEventListener("change", function () {
        document.querySelectorAll("input[data-perm]").forEach(function (box) {
          box.checked = selectAll.checked;
        });
      });
    }
  });
})();
