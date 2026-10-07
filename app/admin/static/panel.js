/* The panel's own script, loaded after SQLAdmin's (templates/sqladmin/base.html): the few
   texts SQLAdmin writes from its JavaScript, in Spanish, the folded filters, the one
   confirmation dialog before what cannot be undone, and the tour of "Conversaciones".
   No libraries: everything here is plain DOM. */
(function () {
  "use strict";

  // "Number of characters: N" under each text area (SQLAdmin's main.js): ours runs after its own.
  document.querySelectorAll(".chars-count-label").forEach(function (label) {
    var area = label.previousElementSibling;
    if (!area || area.tagName !== "TEXTAREA") return;
    var max = parseInt(area.getAttribute("maxlength"), 10) || 0;
    function update() {
      var n = area.value.length;
      label.textContent = (max ? n + " de " + max : n) + " caracteres";
    }
    area.addEventListener("input", update);
    update();
  });

  // The delete confirmation ("This will permanently delete ..."), after SQLAdmin fills it.
  if (window.jQuery) {
    window.jQuery(document).on("shown.bs.modal", "#modal-delete", function () {
      var text = document.getElementById("modal-delete-text");
      if (text) text.textContent = "Se va a borrar y no se puede deshacer.";
    });
  }

  // SQLAdmin's filters: open beside the list on wide screens, folded above it on phones.
  if (window.matchMedia("(min-width: 992px)").matches) {
    document.querySelectorAll("details[data-open-wide]").forEach(function (box) {
      box.open = true;
    });
  }

  // --- Confirmations (app.admin.help.CONFIRM) ----------------------------------------------
  // A form with data-confirm-* is sent only after "ok" in the dialog. Listened on the document,
  // before anything else: forms the page redraws (the conversation's header) are covered too.
  // Looked up when used: the dialog comes after this script in the page.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.dataset || !form.dataset.confirmText) return;
    var dialog = document.getElementById("confirm-dialog");
    if (form.dataset.confirmed === "1") { delete form.dataset.confirmed; return; }
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(form.dataset.confirmTitle + "\n\n" + form.dataset.confirmText)) {
        form.dataset.confirmed = "1";
        form.requestSubmit();
      }
      return;
    }
    document.getElementById("confirm-title").textContent = form.dataset.confirmTitle || "";
    document.getElementById("confirm-text").textContent = form.dataset.confirmText;
    var ok = document.getElementById("confirm-ok");
    ok.textContent = form.dataset.confirmButton || "Confirmar";
    ok.className = "btn " + (form.dataset.confirmKind === "primary" ? "btn-primary" : "btn-danger");
    dialog.onclose = function () {
      if (dialog.returnValue !== "ok") return;
      form.dataset.confirmed = "1";
      form.requestSubmit();
    };
    dialog.returnValue = "";
    dialog.showModal();
  }, true);

  // --- The tour of "Conversaciones" (app.admin.help.TOUR) ------------------------------------
  var app = document.getElementById("inbox-app");
  var stepsData = document.getElementById("tour-steps");
  if (!app || !stepsData) return;
  var tour = JSON.parse(stepsData.textContent);
  var steps = tour.steps, index = 0, shade = null, bubble = null, keeper = null;

  function target(step) {
    var el = document.querySelector('[data-tour="' + step.target + '"]');
    return el && el.offsetParent !== null ? el : null;
  }

  function mark() {
    document.querySelectorAll(".tour-target").forEach(function (el) { el.classList.remove("tour-target"); });
    var el = target(steps[index]);
    if (el) el.classList.add("tour-target");
    return el;
  }

  function place() {
    var el = mark();
    var step = steps[index];
    bubble.querySelector(".tour-count").textContent = (index + 1) + " de " + steps.length;
    bubble.querySelector(".tour-title").textContent = step.title;
    bubble.querySelector(".tour-text").textContent = step.text + (el ? "" : " " + (step.missing || tour.missing));
    bubble.querySelector("[data-tour-prev]").hidden = index === 0;
    bubble.querySelector("[data-tour-next]").textContent = index === steps.length - 1 ? "Terminar" : "Siguiente";
    bubble.classList.toggle("tour-centered", !el);
    if (!el) { bubble.style.top = ""; bubble.style.left = ""; return; }
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    var box = el.getBoundingClientRect();
    var width = Math.min(340, window.innerWidth - 24);
    var left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12));
    var below = box.bottom + 12;
    var top = below + 180 < window.innerHeight ? below : Math.max(12, box.top - 192);
    bubble.style.left = left + "px";
    bubble.style.top = top + "px";
  }

  function finish() {
    window.clearInterval(keeper);
    document.querySelectorAll(".tour-target").forEach(function (el) { el.classList.remove("tour-target"); });
    if (shade) shade.remove();
    if (bubble) bubble.remove();
    shade = bubble = null;
    fetch(app.dataset.tourSeenUrl, { method: "POST", credentials: "same-origin" }).catch(function () {});
  }

  function start() {
    if (bubble || !steps.length) return;
    index = 0;
    shade = document.createElement("div");
    shade.className = "tour-shade";
    bubble = document.createElement("div");
    bubble.className = "tour-bubble";
    bubble.setAttribute("role", "dialog");
    bubble.setAttribute("aria-live", "polite");
    bubble.innerHTML =
      '<div class="tour-count"></div><h3 class="tour-title"></h3><p class="tour-text"></p>' +
      '<div class="tour-buttons">' +
      '<button type="button" class="btn btn-sm btn-secondary" data-tour-skip>Saltar</button>' +
      '<button type="button" class="btn btn-sm btn-secondary" data-tour-prev>Anterior</button>' +
      '<button type="button" class="btn btn-sm btn-primary" data-tour-next>Siguiente</button></div>';
    document.body.appendChild(shade);
    document.body.appendChild(bubble);
    bubble.querySelector("[data-tour-skip]").addEventListener("click", finish);
    bubble.querySelector("[data-tour-prev]").addEventListener("click", function () { index -= 1; place(); });
    bubble.querySelector("[data-tour-next]").addEventListener("click", function () {
      if (index === steps.length - 1) { finish(); return; }
      index += 1; place();
    });
    place();
    // The conversation's header is redrawn every few seconds: keep its button marked.
    keeper = window.setInterval(mark, 700);
  }

  window.panelTour = start;
  if (app.dataset.tourAuto === "true") start();
})();
