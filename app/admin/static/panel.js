/* The panel's own script, loaded after SQLAdmin's (templates/sqladmin/base.html): the few
   texts SQLAdmin writes from its JavaScript, in Spanish, and the folded filters. */
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
})();
