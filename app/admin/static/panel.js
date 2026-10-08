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

  // The same under a one-line field with data-count="<max>" (the WhatsApp list's limits of
  // "Tipos de problema"); red when it goes over (the server refuses it anyway).
  document.querySelectorAll("input[data-count]").forEach(function (input) {
    var max = parseInt(input.dataset.count, 10) || 0;
    var label = document.createElement("div");
    label.className = "form-hint char-count";
    input.insertAdjacentElement("afterend", label);
    function update() {
      var n = input.value.length;
      label.textContent = n + " de " + max + " caracteres";
      label.classList.toggle("char-count-over", max > 0 && n > max);
    }
    input.addEventListener("input", update);
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

  // --- "Nuevo reclamo" (claim_new.html) ---------------------------------------------------------
  // Only the fields of the chosen way of saying who reported, and only the people of the chosen
  // unit. A help: the server checks both again.
  var claimForm = document.getElementById("claim-form");
  if (claimForm) {
    var unitSelect = claimForm.querySelector("#unit_id");
    var personSelect = claimForm.querySelector("#person");
    var showReporter = function () {
      var checked = claimForm.querySelector("input[name=reporter]:checked");
      var mode = checked ? checked.value : "roster";
      claimForm.querySelectorAll("[data-reporter]").forEach(function (block) {
        block.hidden = block.dataset.reporter !== mode;
      });
    };
    var filterPeople = function () {
      if (!personSelect || !unitSelect) return;
      var unit = unitSelect.value;
      personSelect.querySelectorAll("optgroup").forEach(function (group) {
        var shown = !unit || group.dataset.unit === unit;
        group.hidden = !shown;
        group.disabled = !shown;
      });
      var chosen = personSelect.selectedOptions[0];
      if (chosen && chosen.parentElement.disabled) personSelect.value = "";
    };
    claimForm.querySelectorAll("input[name=reporter]").forEach(function (radio) {
      radio.addEventListener("change", showReporter);
    });
    if (unitSelect) unitSelect.addEventListener("change", filterPeople);
    showReporter();
    filterPeople();
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

  // --- The example of "Cómo usar" (app.admin.example) -----------------------------------------
  // Its buttons and links do nothing: a short note says what they would do in a real one.
  var note = null, noteTimer = null;
  function showNote(el, text) {
    if (!note) {
      note = document.createElement("div");
      note.className = "example-note";
      note.setAttribute("role", "status");
      document.body.appendChild(note);
    }
    note.textContent = text;
    note.hidden = false;
    var box = el.getBoundingClientRect();
    var width = Math.min(320, window.innerWidth - 24);
    note.style.left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12)) + "px";
    note.style.top = Math.min(box.bottom + 8, window.innerHeight - 90) + "px";
    window.clearTimeout(noteTimer);
    noteTimer = window.setTimeout(function () { note.hidden = true; }, 4500);
  }
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.dataset || !form.dataset.exampleNote) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    showNote(event.submitter || form, form.dataset.exampleNote);
  }, true);
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-example-note]");
    if (!link) return;
    event.preventDefault();
    showNote(link, link.dataset.exampleNote);
  });

  // --- The tour of "Conversaciones" (app.admin.help.TOUR), over the example -------------------
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

  // The last bubble of the example: done, and the way back to the real inbox.
  function done() {
    var last = document.createElement("div");
    last.className = "tour-bubble tour-centered tour-done";
    last.setAttribute("role", "dialog");
    last.innerHTML = '<h3 class="tour-title">Listo</h3><p class="tour-text"></p>' +
      '<div class="tour-buttons"><button type="button" class="btn btn-sm btn-secondary" data-tour-stay>Seguir mirando el ejemplo</button>' +
      '<a class="btn btn-sm btn-primary" data-example-back><i class="fa-solid fa-arrow-left me-1"></i>Volver a mis conversaciones</a></div>';
    last.querySelector(".tour-text").textContent = app.dataset.exampleDone || "";
    last.querySelector("[data-example-back]").href = app.dataset.exampleBack;
    last.querySelector("[data-tour-stay]").addEventListener("click", function () { last.remove(); });
    document.body.appendChild(last);
  }

  function finish() {
    window.clearInterval(keeper);
    document.querySelectorAll(".tour-target").forEach(function (el) { el.classList.remove("tour-target"); });
    if (shade) shade.remove();
    if (bubble) bubble.remove();
    shade = bubble = null;
    fetch(app.dataset.tourSeenUrl, { method: "POST", credentials: "same-origin" }).catch(function () {});
    if (app.dataset.example === "true") done();
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
    keeper = window.setInterval(mark, 700);
  }

  window.panelTour = start;
  if (app.dataset.tourAuto === "true") start();
})();
