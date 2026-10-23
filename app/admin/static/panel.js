/* The panel's own script, loaded after SQLAdmin's (templates/sqladmin/base.html): the few
   texts SQLAdmin writes from its JavaScript, in Spanish, the folded filters, the panel's
   numbers and alerts on every page (app.admin.pending), the one confirmation dialog before
   what cannot be undone, sending a form when a building is picked from its list (Vecinos), the
   forms of «Agregar persona» and «Editar» inside Vecinos' list, and the tour of
   "Conversaciones".
   No libraries: everything here is plain DOM. */
(function () {
  "use strict";
  var root = typeof window !== "undefined" ? window : globalThis;

  // --- Two decisions without the page, so they can be tested alone (tests/admin/test_panel_js.py)

  // What to do with the answer of a poll: "ok" (use it), "retry" (a server error, an odd answer:
  // the next poll), "reload" (the session is gone: the login), "warn" (gone, but there is a
  // message being written: say so instead of losing it) or "denied" (a 403 with the panel's
  // JSON, app.admin.errors: no permission for that, the session is fine). A network failure
  // never gets here. url and loginUrl may be whole URLs or paths: only their paths are
  // compared. errorJson: the answer is JSON (whatever was expected).
  function pathOf(url) { return String(url || "").replace(/^[a-z]+:\/\/[^/]+/i, "").split(/[?#]/)[0]; }
  root.panelSessionAction = function (status, url, isJson, hasDraft, loginUrl, errorJson) {
    var login = pathOf(loginUrl);
    if (status === 403 && errorJson && !(login && pathOf(url) === login)) return "denied";
    var gone = status === 401 || status === 403 || (!!login && pathOf(url) === login);
    if (gone) return hasDraft ? "warn" : "reload";
    return status >= 200 && status < 300 && isJson ? "ok" : "retry";
  };

  // Which alerts are new, given what this browser already alerted (stored: {key: when}, null the
  // first time: then everything is taken as seen, without a sound). Keeps the current keys and
  // the ones of the last maxAge milliseconds; older ones that no longer wait are dropped.
  root.panelClaimAlerts = function (stored, keys, now, maxAge) {
    var first = !stored, next = {}, fresh = [];
    stored = stored || {};
    Object.keys(stored).forEach(function (key) {
      if (keys.indexOf(key) >= 0 || now - stored[key] < maxAge) next[key] = stored[key];
    });
    keys.forEach(function (key) {
      if (Object.prototype.hasOwnProperty.call(stored, key)) return;
      next[key] = now;
      if (!first) fresh.push(key);
    });
    return { fresh: fresh, stored: next };
  };

  // The list of «Reclamos» when the panel's numbers arrive: "none" (no claim changed), "refresh"
  // (redraw it) or "wait" (something changed, but she is typing in the search or has the
  // filters open: later).
  root.panelClaimsListAction = function (shownStamp, newStamp, busy) {
    if (!newStamp || newStamp === shownStamp) return "none";
    return busy ? "wait" : "refresh";
  };

  // fn, called only once the calls stop for `wait` ms (one request when she stops typing, not
  // one per key). Timers can be given (the tests).
  root.panelDebounce = function (fn, wait, setTimer, clearTimer) {
    setTimer = setTimer || function (f, ms) { return root.setTimeout(f, ms); };
    clearTimer = clearTimer || function (id) { root.clearTimeout(id); };
    var timer = null;
    return function () {
      var args = arguments, self = this;
      if (timer !== null) clearTimer(timer);
      timer = setTimer(function () { timer = null; fn.apply(self, args); }, wait);
    };
  };

  // A form's buttons while it goes: disabled, saying «Guardando…» (or the form's own text);
  // back as they were when it is done. Buttons are anything with disabled, textContent and a
  // dataset (the tests give plain objects).
  root.panelBusy = function (buttons, busy, text) {
    Array.prototype.forEach.call(buttons, function (b) {
      if (busy) {
        if (b.dataset.idleText === undefined) b.dataset.idleText = b.textContent;
        b.disabled = true;
        b.textContent = text || "Guardando…";
      } else {
        if (b.dataset.idleText !== undefined) { b.textContent = b.dataset.idleText; delete b.dataset.idleText; }
        b.disabled = false;
      }
    });
  };

  // How long a small notice stays: null (until closed) for an error, else 4 s.
  root.panelToastDelay = function (level) {
    return level === "error" ? null : 4000;
  };

  // What to do with the answer of an action sent without reloading: "apply" (the server's JSON),
  // "session" (the session is gone: 5e.1's way), "denied" (no permission: the server's message
  // as an error notice, nothing reloaded nor lost) or "unknown" (a network cut, a server error,
  // an odd answer). Never send it again: the server may have done it already.
  root.panelFetchOutcome = function (status, isJson, networkError, sessionAction) {
    if (networkError) return "unknown";
    if (sessionAction === "reload" || sessionAction === "warn") return "session";
    if (sessionAction === "denied") return "denied";
    return status >= 200 && status < 300 && isJson ? "apply" : "unknown";
  };

  if (typeof document === "undefined") return;

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

  // SQLAdmin's filters: open beside the list from 1280 px, folded above it under that. Opened
  // there by her, they stay open while she moves inside the same list (pages, sorting, a
  // search): sessionStorage, by the list's path. A browser without storage: folded again.
  var wide = window.matchMedia("(min-width: 1280px)").matches;
  var FILTERS_KEY = "panel-filters-open:" + window.location.pathname;
  document.querySelectorAll("details[data-open-wide]").forEach(function (box) {
    var kept = null;
    try { kept = window.sessionStorage.getItem(FILTERS_KEY); } catch (e) { kept = null; }
    box.open = wide || kept === "1";
    box.addEventListener("toggle", function () {
      if (wide) return;
      try { window.sessionStorage.setItem(FILTERS_KEY, box.open ? "1" : "0"); } catch (e) { /* not kept */ }
    });
  });

  // --- Alerts: a short beep (no sound file) and a browser notification -----------------------
  // Browsers only play sound after a click or a key on the page: the first one unlocks it,
  // without asking anything. The button of the bar asks for the notifications' permission.
  var audio = null;
  function unlockSound() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
    } catch (e) { /* no sound */ }
  }
  function beep() {
    try {
      unlockSound();
      var osc = audio.createOscillator(), gain = audio.createGain();
      osc.frequency.value = 880;
      osc.connect(gain); gain.connect(audio.destination);
      gain.gain.setValueAtTime(0.2, audio.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.5);
      osc.start(); osc.stop(audio.currentTime + 0.5);
    } catch (e) { /* no sound */ }
  }
  function notify(title, body) {
    beep();
    if ("Notification" in window && Notification.permission === "granted") {
      try { new Notification(title, { body: body }); } catch (e) { /* not allowed here */ }
    }
  }
  window.panelBeep = beep;
  window.panelNotify = notify;
  function firstGesture() {
    unlockSound();
    document.removeEventListener("pointerdown", firstGesture, true);
    document.removeEventListener("keydown", firstGesture, true);
  }
  document.addEventListener("pointerdown", firstGesture, true);
  document.addEventListener("keydown", firstGesture, true);

  var topbar = document.getElementById("panel-topbar");
  var alertsButton = document.getElementById("enable-alerts");
  var alertsOff = document.getElementById("alerts-off");
  function permission() { return "Notification" in window ? Notification.permission : "unsupported"; }
  function showAlertsState() {
    var state = permission();
    if (alertsButton) alertsButton.textContent = state === "granted" ? "🔔 Avisos activos" : "🔔 Activar avisos";
    // Phones: the bell in the top bar, crossed out while they are off.
    var bell = document.getElementById("alerts-bell");
    if (bell) {
      var on = state === "granted";
      bell.querySelector("i").className = "fa-solid " + (on ? "fa-bell" : "fa-bell-slash");
      bell.setAttribute("aria-label", on ? "Avisos activos" : "Los avisos con sonido están apagados. Activar");
      bell.classList.toggle("panel-bell-off", !on);
    }
    if (!alertsOff) return;
    alertsOff.hidden = state !== "default" && state !== "denied";
    // The notice has its own «Activar»: the bar's button, only once they are on.
    if (alertsButton) alertsButton.hidden = !alertsOff.hidden;
    alertsOff.querySelector('[data-alerts-text="off"]').hidden = state !== "default";
    alertsOff.querySelector('[data-alerts-text="blocked"]').hidden = state !== "denied";
    alertsOff.querySelector("[data-alerts-enable]").hidden = state !== "default";
  }
  showAlertsState();
  document.querySelectorAll("[data-alerts-enable]").forEach(function (button) {
    button.addEventListener("click", function () {
      beep();
      if (permission() === "default") Notification.requestPermission().then(showAlertsState);
      showAlertsState();
    });
  });

  // --- A lost session: reload to the login, unless a message is being written ----------------
  var sessionStopped = false;
  function hasDraft() {
    return Array.prototype.some.call(document.querySelectorAll("form textarea, form input"), function (el) {
      var form = el.form;
      if (!form || (form.getAttribute("method") || "get").toLowerCase() !== "post") return false;
      if (el.tagName === "INPUT" && ["text", "search", "email", "tel", "number"].indexOf(el.type) < 0) return false;
      return el.value !== el.defaultValue && el.value.trim() !== "";
    });
  }
  // For every request of the panel's pages (the polls, the redrawn parts): "ok" when the answer
  // can be used (JSON, or HTML with kind "html").
  window.panelSessionCheck = function (response, kind) {
    var login = topbar ? topbar.dataset.loginUrl : "";
    var type = response.headers.get("content-type") || "";
    var expected = type.indexOf(kind === "html" ? "html" : "json") >= 0;
    var action = window.panelSessionAction(response.status, response.url, expected, hasDraft(), login,
      type.indexOf("json") >= 0);
    if (action === "reload") window.location.reload();
    if (action === "warn" && !sessionStopped) {
      sessionStopped = true;
      var notice = document.getElementById("session-gone");
      if (notice) notice.hidden = false;
    }
    return action;
  };
  window.panelSessionStopped = function () { return sessionStopped; };

  // --- The panel's numbers (app.admin.pending): one request for every page --------------------
  // One tab asks (the one holding the "panel-poll" lock) and tells the others through a
  // BroadcastChannel; a visible tab that heard nothing for a while asks itself (the asking tab
  // may be in the background, where the browser slows it down). Old browsers: every tab asks.
  var pendingUrl = topbar && topbar.dataset.pendingUrl;
  var cardsBox = document.querySelector("[data-pending-cards]");
  var EVERY = 5000, STALE = 12000, KEEP = 3 * 24 * 3600 * 1000;
  // The tab: «(N) Pantalla · Panel · Estudio Diego Rufeil» (the server draws it the same way).
  var TITLE = (topbar && topbar.dataset.title) || "Panel · Estudio Diego Rufeil";
  var channel = null;
  try { channel = "BroadcastChannel" in window ? new BroadcastChannel("panel-numbers") : null; } catch (e) { channel = null; }
  var shared = !!(channel && navigator.locks);
  var leader = false, busy = false, heardAt = 0, cardsWantedAt = 0;

  function setTitle(n) { document.title = (n ? "(" + n + ") " : "") + TITLE; }
  if (topbar) setTitle(Number(topbar.dataset.titleCount) || 0);

  function showCounters(counts) {
    // «Más» of the bar at the bottom (phones): what waits in Teléfonos and Verificaciones.
    counts = Object.assign({ more: (counts.phones || 0) + (counts.verifications || 0) }, counts);
    document.querySelectorAll("[data-counter]").forEach(function (badge) {
      var key = badge.dataset.counter, n = counts[key] || 0, urgent = counts[key + "_urgent"] || 0;
      badge.textContent = String(n);
      badge.hidden = n === 0;
      badge.classList.toggle("count-urgent", urgent > 0);
      badge.classList.toggle("count-waiting", urgent === 0);
    });
  }

  // What was already alerted, shared by the tabs of this browser (localStorage), so an alert
  // sounds once: changing pages or with two tabs open. Read and written under a lock.
  var memory = null;
  function readAlerted() {
    try { var raw = window.localStorage.getItem("panel-alerted"); return raw ? JSON.parse(raw) : null; }
    catch (e) { return memory; }
  }
  function writeAlerted(value) {
    memory = value;
    try { window.localStorage.setItem("panel-alerted", JSON.stringify(value)); } catch (e) { /* memory only */ }
  }
  function watching(id) {
    var app = document.getElementById("inbox-app");
    return !!app && app.dataset.open === String(id) && document.visibilityState === "visible";
  }
  function announce(fresh, alerts) {
    if (!fresh.length) return;
    var isFresh = function (key) { return fresh.indexOf(key) >= 0; };
    var waiting = fresh.filter(function (key) { return key.indexOf("conv:") === 0; }).length;
    if (waiting) {
      notify("Esperando persona", waiting === 1
        ? "Entró una conversación que necesita a alguien del estudio."
        : "Entraron " + waiting + " conversaciones que necesitan a alguien del estudio.");
    }
    var mine = (alerts.mine || []).filter(function (m) { return isFresh(m.key) && !watching(m.id); });
    if (mine.length) notify("Mensaje nuevo", "Te escribieron en una conversación que tenés asignada.");
    (alerts.claims || []).forEach(function (c) {
      if (isFresh("claim:" + c.id)) notify(c.title || "Reclamo urgente #" + c.number, c.problem + " · " + c.building);
    });
  }
  function handleAlerts(alerts) {
    if (!alerts) return;
    var keys = (alerts.waiting || []).concat(
      (alerts.mine || []).map(function (m) { return m.key; }),
      (alerts.claims || []).map(function (c) { return "claim:" + c.id; }));
    var run = function () {
      var result = window.panelClaimAlerts(readAlerted(), keys, Date.now(), KEEP);
      writeAlerted(result.stored);
      announce(result.fresh, alerts);
    };
    // A hidden tab waits a moment: the alert is better given by the visible one (its sound is
    // more likely unlocked).
    window.setTimeout(function () {
      if (navigator.locks) navigator.locks.request("panel-alerts", run);
      else run();
    }, document.visibilityState === "visible" ? 0 : 1000);
  }

  function applyNumbers(data) {
    heardAt = Date.now();
    showCounters(data.counts || {});
    setTitle(data.title_count || 0);
    if (cardsBox && data.cards_html) cardsBox.innerHTML = data.cards_html;
    handleAlerts(data.alerts);
    // For the pages that follow them (the list of «Reclamos»).
    document.dispatchEvent(new CustomEvent("panel:numbers", { detail: data }));
  }

  function askNumbers() {
    if (!pendingUrl || busy || sessionStopped) return;
    busy = true;
    var cards = !!cardsBox || Date.now() - cardsWantedAt < STALE;
    fetch(pendingUrl + (cards ? "?cards=1" : ""), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) { return window.panelSessionCheck(response) === "ok" ? response.json() : null; })
      .then(function (data) {
        if (!data) return;
        applyNumbers(data);
        if (channel) channel.postMessage({ type: "numbers", data: data });
      })
      .catch(function () { /* offline for a moment: next time */ })
      .finally(function () { busy = false; });
  }

  // After an action without reloading: the menu's numbers at once, not at the next poll.
  window.panelRefreshNumbers = askNumbers;

  if (pendingUrl) {
    if (channel) {
      channel.onmessage = function (event) {
        var message = event.data || {};
        if (message.type === "numbers" && message.data) applyNumbers(message.data);
        if (message.type === "want-cards") cardsWantedAt = Date.now();
      };
    }
    if (shared) {
      // Held while the page is open: the browser hands it to another tab when this one goes.
      navigator.locks.request("panel-poll", function () {
        leader = true;
        askNumbers();
        return new Promise(function () {});
      });
    }
    window.setInterval(function () {
      if (cardsBox && channel) channel.postMessage({ type: "want-cards" });
      var stale = document.visibilityState === "visible" && Date.now() - heardAt > STALE;
      if (!shared || leader || stale) askNumbers();
    }, EVERY);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") askNumbers();
    });
    askNumbers();
  }

  // --- The list of «Reclamos» (claims.html): redrawn when a claim changed ----------------------
  // The panel's numbers carry claims_stamp (app.admin.pending): when it is not the one the list
  // was drawn with, the same URL is asked again (&partial=1, only the list) and replaced: the
  // tab, filters, search and page stay. Not while she types in the search, changed something
  // not applied yet, or has the filters open: then when she is done.
  var claimsList = document.querySelector("[data-claims-list]");
  if (claimsList) {
    var claimsSearch = document.getElementById("claim-search");
    var claimsFilters = document.getElementById("claim-filters");
    var claimsWaiting = null, claimsBusy = false;
    var claimsUsed = function () {
      if (claimsFilters && claimsFilters.open) return true;
      if (!claimsSearch) return false;
      if (claimsSearch.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return true;
      return Array.prototype.some.call(claimsSearch.querySelectorAll("input[type=search], select"), function (el) {
        if (el.tagName === "SELECT") {
          var initial = Array.prototype.filter.call(el.options, function (o) { return o.defaultSelected; })[0];
          return el.value !== (initial ? initial.value : "");
        }
        return el.value !== el.defaultValue;
      });
    };
    var redrawClaims = function (stamp) {
      if (claimsBusy) return;
      claimsBusy = true;
      var url = new URL(window.location.href);
      url.searchParams.set("partial", "1");
      fetch(url.toString(), { credentials: "same-origin" })
        .then(function (response) { return window.panelSessionCheck(response, "html") === "ok" ? response.text() : null; })
        .then(function (html) {
          if (html === null) return;
          claimsList.innerHTML = html;
          claimsList.dataset.claimsStamp = stamp;
          claimsWaiting = null;
        })
        .catch(function () { /* next time */ })
        .finally(function () { claimsBusy = false; });
    };
    var considerClaims = function (stamp) {
      var action = window.panelClaimsListAction(claimsList.dataset.claimsStamp, stamp, claimsUsed());
      if (action === "refresh") redrawClaims(stamp);
      if (action === "wait") claimsWaiting = stamp;
    };
    document.addEventListener("panel:numbers", function (event) {
      if (event.detail && event.detail.claims_stamp) considerClaims(event.detail.claims_stamp);
    });
    // Done with the search or the filters: what was waiting.
    var claimsDone = function () { window.setTimeout(function () { if (claimsWaiting) considerClaims(claimsWaiting); }, 0); };
    if (claimsFilters) claimsFilters.addEventListener("toggle", claimsDone);
    if (claimsSearch) claimsSearch.addEventListener("focusout", claimsDone);
  }

  // --- "Nuevo reclamo" (claim_new.html): who reports, then the claim, without reloading -------
  // «¿Quién reclama?» searches from 2 characters, once she stops typing; choosing a person (or a
  // building, for «No está en el padrón») brings the rest of the form. The links and forms work
  // without JavaScript too (they reload the page); the server checks everything again.
  var claimNew = document.getElementById("claim-new");
  if (claimNew) {
    var who = document.getElementById("claim-who");
    var other = document.getElementById("claim-other");
    var step2 = document.getElementById("claim-step2");
    var peopleBox = document.getElementById("claim-people");
    var peopleInput = document.getElementById("claim-people-q");
    var conversation = claimNew.dataset.conversation || "";
    var asked = 0;
    var fetchHtml = function (url) {
      return fetch(url, { credentials: "same-origin" }).then(function (response) {
        return window.panelSessionCheck(response, "html") === "ok" ? response.text() : null;
      });
    };
    var withConversation = function (params) {
      if (conversation) params.set("conversation", conversation);
      return params;
    };
    var searchPeople = function () {
      var q = peopleInput.value.trim();
      var mine = ++asked;
      if (q.length < 2) return;
      var params = withConversation(new URLSearchParams({ q: q }));
      fetchHtml(claimNew.dataset.peopleUrl + "?" + params.toString()).then(function (html) {
        if (html !== null && mine === asked) peopleBox.innerHTML = html;  // only the latest answer
      }).catch(function () { /* next key */ });
    };
    var showForm = function (params) {
      fetchHtml(claimNew.dataset.formUrl + "?" + withConversation(params).toString()).then(function (html) {
        if (html === null) return;
        step2.innerHTML = html;
        var first = step2.querySelector("select, input:not([type=hidden]), textarea");
        if (first) first.focus();
      }).catch(function () { /* stays as it is */ });
    };
    if (peopleInput) {
      peopleInput.addEventListener("input", window.panelDebounce(searchPeople, 350));
      document.getElementById("claim-people-form").addEventListener("submit", function (event) {
        event.preventDefault();
        searchPeople();
      });
    }
    claimNew.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[data-person-choice], a[data-claim-change], #claim-not-in-roster");
      if (!link) return;
      event.preventDefault();
      if (link.hasAttribute("data-person-choice")) {
        var url = new URL(link.href);
        who.hidden = true;
        showForm(new URLSearchParams({ person_id: url.searchParams.get("person_id"), unit_id: url.searchParams.get("unit_id") }));
      } else if (link.id === "claim-not-in-roster") {
        who.hidden = true;
        other.hidden = false;
        step2.innerHTML = "";
        document.getElementById("claim-other-building").focus();
      } else {
        step2.innerHTML = "";
        other.hidden = true;
        who.hidden = false;
        if (peopleInput) peopleInput.focus();
      }
    });
    var buildingSelect = document.getElementById("claim-other-building");
    if (buildingSelect) {
      var pickBuilding = function () {
        if (buildingSelect.value) showForm(new URLSearchParams({ reporter: "other", building_id: buildingSelect.value }));
      };
      buildingSelect.addEventListener("change", pickBuilding);
      document.getElementById("claim-other-form").addEventListener("submit", function (event) {
        event.preventDefault();
        pickBuilding();
      });
    }
  }

  // --- Confirmations (app.admin.help.CONFIRM) ----------------------------------------------
  // A form with data-confirm-* is sent only after "ok" in the dialog. Listened on the document,
  // before anything else: forms the page redraws (the conversation's header) are covered too.
  // Looked up when used: the dialog comes after this script in the page. The form's
  // [data-confirm-fields] (a reason, a provider) are shown inside the dialog while it is open
  // (still the form's fields: form="<its id>"); "ok" does nothing while a required one is empty:
  // it says so in the dialog (#confirm-error), never with the browser's own bubble (the forms
  // are novalidate: the server checks them too).
  function blank(el) { return el.required && !String(el.value || "").trim(); }
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.dataset || !form.dataset.confirmText) return;
    var dialog = document.getElementById("confirm-dialog");
    if (form.dataset.confirmed === "1") { delete form.dataset.confirmed; return; }
    var submitter = event.submitter || null;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(form.dataset.confirmTitle + "\n\n" + form.dataset.confirmText)) {
        form.dataset.confirmed = "1";
        form.requestSubmit(submitter);
      }
      return;
    }
    document.getElementById("confirm-title").textContent = form.dataset.confirmTitle || "";
    document.getElementById("confirm-text").textContent = form.dataset.confirmText;
    var ok = document.getElementById("confirm-ok");
    ok.textContent = form.dataset.confirmButton || "Confirmar";
    ok.className = "btn " + (form.dataset.confirmKind === "primary" ? "btn-primary" : "btn-danger");
    var box = document.getElementById("confirm-fields");
    var fields = form.querySelector("[data-confirm-fields]");
    var inputs = [];
    if (fields && box && form.id) {
      inputs = Array.prototype.slice.call(fields.querySelectorAll("input, select, textarea"));
      inputs.forEach(function (el) { el.setAttribute("form", form.id); el.removeAttribute("aria-invalid"); });
      fields.hidden = false;
      box.appendChild(fields);
    }
    var problem = document.getElementById("confirm-error");
    if (problem) { problem.hidden = true; problem.textContent = ""; }
    ok.onclick = function (click) {
      var empty = inputs.filter(blank)[0];
      if (!empty) return;
      click.preventDefault();
      if (problem) {
        problem.textContent = "Completá este campo.";
        problem.hidden = false;
        empty.setAttribute("aria-describedby", "confirm-error");
      }
      empty.setAttribute("aria-invalid", "true");
      empty.focus();
      empty.addEventListener("input", function () {
        empty.removeAttribute("aria-invalid");
        if (problem) problem.hidden = true;
      }, { once: true });
    };
    dialog.onclose = function () {
      if (fields && box && fields.parentNode === box) {
        fields.hidden = true;
        form.appendChild(fields);
      }
      if (dialog.returnValue !== "ok") return;
      form.dataset.confirmed = "1";
      form.requestSubmit(submitter);  // the button that sent it, with its name and value
    };
    dialog.returnValue = "";
    dialog.showModal();
    var first = inputs.filter(function (el) { return el.type !== "radio" && el.type !== "hidden"; })[0];
    if (first) first.focus();
  }, true);

  // The same dialog for a question that is not a form's (Vecinos' «Pasarlo a esta persona»).
  window.panelAsk = function (title, text, button, kind, onOk) {
    var dialog = document.getElementById("confirm-dialog");
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(title + "\n\n" + text)) onOk();
      return;
    }
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-text").textContent = text;
    var ok = document.getElementById("confirm-ok");
    ok.textContent = button;
    ok.className = "btn " + (kind === "primary" ? "btn-primary" : "btn-danger");
    ok.onclick = null;
    dialog.onclose = function () { if (dialog.returnValue === "ok") onOk(); };
    dialog.returnValue = "";
    dialog.showModal();
  };

  // --- Small notices at the bottom (#panel-toasts) ---------------------------------------------
  // The page's Flashes (layout.html) and what an action without reloading says. A success
  // goes away after 4 s; an error stays until «×» (role="alert").
  var toasts = document.getElementById("panel-toasts");
  function dismissLater(toast) {
    var delay = window.panelToastDelay(toast.dataset.toast);
    if (delay !== null) window.setTimeout(function () { toast.remove(); }, delay);
  }
  window.panelToast = function (text, level, action) {
    if (!toasts) return null;
    var toast = document.createElement("div");
    toast.className = "panel-toast panel-toast-" + level;
    toast.dataset.toast = level;
    if (level === "error") toast.setAttribute("role", "alert");
    var span = document.createElement("span");
    span.className = "panel-toast-text";
    span.textContent = text;
    toast.appendChild(span);
    if (action) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-primary";
      button.textContent = action.label;
      button.addEventListener("click", action.run);
      toast.appendChild(button);
    }
    var close = document.createElement("button");
    close.type = "button";
    close.className = "panel-toast-close";
    close.setAttribute("data-toast-close", "");
    close.setAttribute("aria-label", "Cerrar");
    close.textContent = "×";
    toast.appendChild(close);
    toasts.appendChild(toast);
    dismissLater(toast);
    return toast;
  };
  if (toasts) {
    toasts.querySelectorAll("[data-toast]").forEach(dismissLater);
    toasts.addEventListener("click", function (event) {
      var close = event.target.closest("[data-toast-close]");
      if (close) close.closest(".panel-toast").remove();
    });
  }

  // --- Actions: «Guardando…» while they go; some without reloading (data-fetch) ---------------
  // Every form that sends something (POST) disables its buttons while it goes, so a second tap
  // does nothing. After the event (setTimeout): a button's name and value still go, as in
  // SQLAdmin's «Guardar y seguir editando». A form with data-fetch is sent by fetch (header
  // X-Panel-Fetch: the server answers JSON, app.admin.fetch): rows go away, blocks are
  // redrawn, the menu's numbers are asked for at once. If the answer is unknown (no network,
  // a server error) it is never sent again: the server may have done it already.
  function formButtons(form) {
    var inside = Array.prototype.slice.call(form.querySelectorAll("button[type=submit], button:not([type]), input[type=submit]"));
    var outside = form.id ? Array.prototype.slice.call(document.querySelectorAll('[form="' + form.id + '"][type=submit]')) : [];
    return inside.concat(outside);
  }
  var UNKNOWN = "No se pudo confirmar si se guardó. Recargá la página para ver cómo quedó.";
  function applyReply(data) {
    (data.remove || []).forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) {
        el.classList.add("row-leaving");
        window.setTimeout(function () { el.remove(); }, 220);
      });
    });
    Object.keys(data.replace || {}).forEach(function (selector) {
      var el = document.querySelector(selector);
      if (el) el.outerHTML = data.replace[selector];
    });
    Object.keys(data.texts || {}).forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) { el.textContent = data.texts[selector]; });
    });
  }
  function sendByFetch(form, submitter) {
    var buttons = formButtons(form);
    var body = new FormData(form, submitter || undefined);
    window.panelBusy(buttons, true, form.dataset.busyText);
    var done = function () { window.panelBusy(buttons, false); };
    fetch(form.action, {
      method: "POST", body: body, credentials: "same-origin",
      headers: { "X-Panel-Fetch": "1", Accept: "application/json" }
    }).then(function (response) {
      var session = window.panelSessionCheck(response);  // "ok" for JSON; reload/warn: 5e.1
      var type = response.headers.get("content-type") || "";
      var outcome = window.panelFetchOutcome(response.status, type.indexOf("json") >= 0, false, session);
      if (outcome === "denied") {
        // No permission (app.admin.errors' JSON, ok: false): say so; the page stays as it is.
        return response.json().then(function (data) {
          if (data && data.ok === false && data.message) window.panelToast(data.message, "error");
          else unknown();
          return null;
        });
      }
      if (outcome !== "apply") { if (outcome === "unknown") unknown(); return null; }
      return response.json();
    }).then(function (data) {
      if (!data) return;
      applyReply(data);
      // Saved from a form opened inside the list (Vecinos): it closes, unless the answer drew it
      // again (then this one is no longer in the page).
      var editor = data.ok && form.closest ? form.closest("[data-editor]") : null;
      if (editor && document.body.contains(editor)) closeEditor(editor);
      window.panelToast(data.message, data.level);
      if (window.panelRefreshNumbers) window.panelRefreshNumbers();
    }).catch(function () {
      unknown();
    }).finally(done);
  }
  function unknown() {
    window.panelToast(UNKNOWN, "error", { label: "Recargar", run: function () { window.location.reload(); } });
  }
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form.getAttribute) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return;
    if (form.hasAttribute("data-fetch") && window.fetch && window.FormData) {
      event.preventDefault();
      sendByFetch(form, event.submitter);
      return;
    }
    var buttons = formButtons(form);
    window.setTimeout(function () { window.panelBusy(buttons, true, form.dataset.busyText); }, 0);
  });
  // Back to a page from the browser's cache: its buttons as they were.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form").forEach(function (form) { window.panelBusy(formButtons(form), false); });
  });

  // --- «Tipos de problema» (claim_category_list.html): the general order with «Subir» / «Bajar»
  // (table[data-reorder]). Without this the buttons stay hidden. Each row (tr[data-reorder-row],
  // data-title) has its place ([data-position-text]), its hidden position ([data-position])
  // and the two buttons ([data-move] up / down). Moving: the row swaps with its neighbor, every
  // row is numbered again, the first has no «Subir» and the last no «Bajar», and the new place
  // is said aloud (data-reorder-status: the live region's id). The focus stays on the button
  // pressed (or on the other one, at an end). «Guardar orden» sends the positions.
  document.querySelectorAll("table[data-reorder]").forEach(function (table) {
    var body = table.tBodies[0];
    var status = document.getElementById(table.dataset.reorderStatus || "");
    var rowsOf = function () {
      return Array.prototype.slice.call(body.querySelectorAll("tr[data-reorder-row]"));
    };
    var redraw = function () {
      var rows = rowsOf();
      rows.forEach(function (row, i) {
        row.querySelector("[data-position-text]").textContent = String(i + 1);
        row.querySelector("[data-position]").value = String(i + 1);
        row.querySelector('[data-move="up"]').disabled = i === 0;
        row.querySelector('[data-move="down"]').disabled = i === rows.length - 1;
      });
    };
    rowsOf().forEach(function (row) { row.querySelector("[data-move-buttons]").hidden = false; });
    table.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("button[data-move]") : null;
      if (!button || button.disabled) return;
      var rows = rowsOf();
      var row = button.closest("tr");
      var index = rows.indexOf(row);
      var up = button.dataset.move === "up";
      var other = rows[up ? index - 1 : index + 1];
      if (!other) return;
      if (up) body.insertBefore(row, other);
      else body.insertBefore(other, row);
      redraw();
      var now = rowsOf();
      if (button.disabled) row.querySelector('[data-move="' + (up ? "down" : "up") + '"]').focus();
      else button.focus();
      if (status) {
        status.textContent = row.dataset.title + ": lugar " + (now.indexOf(row) + 1) + " de " + now.length + ".";
      }
    });
  });

  // --- A field with a list to pick from (<input list data-submit-on-pick>): picking one of its
  // options sends its form at once (Vecinos' building). The value is the name of a field to
  // empty first (the search, so the building is what shows).
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!input.matches || !input.matches("input[list][data-submit-on-pick]") || !input.form) return;
    var options = input.list ? Array.prototype.map.call(input.list.options, function (o) { return o.value; }) : [];
    if (options.indexOf(input.value) < 0) return;
    var clear = input.dataset.submitOnPick && input.form.elements[input.dataset.submitOnPick];
    if (clear) clear.value = "";
    input.form.requestSubmit();
  });

  // --- Vecinos: «Agregar persona» and «Editar» inside the list (app.admin.neighbors) -----------
  // A link with data-open-editor="<unit id | top>" opens its form (the server's, ?fragment=1) in
  // [data-editor="<the same>"]; «Cancelar» closes it; one open at a time. While she types the
  // name: similar people of the roster, and «Sí, es la misma persona» links that one instead of
  // creating someone else. Each phone is checked when she leaves its field (how it is saved,
  // what is wrong, whose it is); «Pasarlo a esta persona» is asked first and done on saving.
  // The confirmation adds the owner's sentence while the role is Propietario/a.
  function closeEditor(editor) {
    editor.hidden = true;
    var body = editor.querySelector("[data-editor-body]");
    if (body) body.innerHTML = "";
  }
  function fetchFragment(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (response) {
      return window.panelSessionCheck(response, "html") === "ok" ? response.text() : null;
    });
  }
  function showMorePhones(root) {
    root.querySelectorAll("[data-add-phone]").forEach(function (button) { button.hidden = false; });
  }
  showMorePhones(document);
  // Vecinos opened for one person (from Teléfonos or a conversation): their rows in view.
  var marked = document.querySelector("[data-marked]");
  if (marked && marked.scrollIntoView) marked.scrollIntoView({ block: "center" });
  function ownerText(form) {
    if (!form || !form.dataset.confirmBase) return;
    var role = form.querySelector("[data-role-select]");
    // Verificaciones: «Es un propietario que ya está cargado» is an owner too.
    var mode = form.querySelector("input[name=mode]:checked");
    var owner = mode && mode.value === "link" ? true : Boolean(role && role.value === "owner");
    form.dataset.confirmText = form.dataset.confirmBase + (owner ? " " + form.dataset.ownerNote : "");
  }
  // Verificaciones: only the chosen option's fields show and are sent (no `required`: the
  // server checks them).
  function approveMode(form) {
    if (!form) return;
    var mode = form.querySelector("input[name=mode]:checked");
    form.querySelectorAll("[data-mode-fields]").forEach(function (block) {
      var on = Boolean(mode) && block.dataset.modeFields === mode.value;
      block.hidden = !on;
      block.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = !on; });
    });
  }
  // The browser may bring back another option (going back to the page).
  approveMode(document.getElementById("approve-registration"));
  function phoneField(el) { return el.closest("[data-phone-field]"); }
  function clearPhone(field) {
    var input = field.querySelector("input[data-phone-check]");
    if (input.dataset.name) { input.name = input.dataset.name; delete input.dataset.name; }
    input.value = "";
    field.querySelectorAll("input[name=move_phone_id]").forEach(function (el) { el.remove(); });
    field.querySelector("[data-phone-hint]").textContent = "";
    input.focus();
  }
  function checkPhone(input) {
    var field = phoneField(input);
    var hint = field.querySelector("[data-phone-hint]");
    if (!input.value.trim()) { hint.textContent = ""; return; }
    var form = input.form;
    var chosen = form ? form.querySelector("[data-person-id]") : null;
    var params = new URLSearchParams({ raw: input.value, mode: input.dataset.mode || "add" });
    var person = input.dataset.person || (chosen ? chosen.value : "");
    if (person) params.set("person_id", person);
    ["unit", "role", "back"].forEach(function (key) {
      if (input.dataset[key]) params.set(key === "unit" ? "unit_id" : key, input.dataset[key]);
    });
    var mine = String(Number(input.dataset.asked || 0) + 1);
    input.dataset.asked = mine;
    fetchFragment(input.dataset.phoneCheck + "?" + params.toString()).then(function (html) {
      if (html !== null && input.dataset.asked === mine) hint.innerHTML = html;
    }).catch(function () { /* the server checks it again on saving */ });
  }
  function similar(input) {
    var box = input.form.querySelector("[data-similar]");
    if (!box || input.readOnly) return;
    var mine = String(Number(input.dataset.asked || 0) + 1);
    input.dataset.asked = mine;
    var url = input.dataset.similarUrl + "?" + new URLSearchParams({ name: input.value }).toString();
    fetchFragment(url).then(function (html) {
      if (html !== null && input.dataset.asked === mine) box.innerHTML = html;
    }).catch(function () { /* next key */ });
  }
  var similarLater = window.panelDebounce(similar, 350);
  function pick(button) {
    var form = button.closest("form");
    var name = form.querySelector("input[name=full_name]");
    form.querySelector("[data-person-id]").value = button.dataset.pickPerson;
    name.value = button.dataset.personName;
    name.readOnly = true;
    var box = form.querySelector("[data-similar]");
    box.textContent = "";
    var note = document.createElement("p");
    note.className = "neighbor-hint mb-1";
    note.textContent = "Se va a vincular a " + button.dataset.personName + ", que ya está en el padrón.";
    var undo = document.createElement("button");
    undo.type = "button";
    undo.className = "btn btn-sm btn-link";
    undo.setAttribute("data-unpick", "");
    undo.textContent = "Es otra persona";
    box.appendChild(note);
    box.appendChild(undo);
  }
  function openEditor(link) {
    var editor = document.querySelector('[data-editor="' + link.dataset.openEditor + '"]');
    if (!editor) return false;
    document.querySelectorAll("[data-editor]").forEach(function (other) {
      if (other !== editor && !other.hidden) closeEditor(other);
    });
    var url = new URL(link.href);
    url.searchParams.set("fragment", "1");
    fetchFragment(url.toString()).then(function (html) {
      if (html === null) return;
      editor.querySelector("[data-editor-body]").innerHTML = html;
      editor.hidden = false;
      showMorePhones(editor);
      var first = editor.querySelector("input:not([type=hidden]), select");
      if (first) first.focus();
    }).catch(function () { window.location.href = link.href; });
    return true;
  }
  function addPhoneField(more) {
    var first = more.closest("[data-phones]").querySelector("[data-phone-field]");
    var copy = first.cloneNode(true);
    var input = copy.querySelector("input[data-phone-check]");
    input.value = "";
    input.name = "phone";
    delete input.dataset.name;
    delete input.dataset.asked;
    copy.querySelectorAll("input[name=move_phone_id]").forEach(function (el) { el.remove(); });
    copy.querySelector("[data-phone-hint]").textContent = "";
    more.parentNode.insertBefore(copy, more);
    input.focus();
  }
  function askMove(move) {
    var field = phoneField(move);
    window.panelAsk(move.dataset.askTitle, move.dataset.askText, move.dataset.askButton, "primary", function () {
      var phone = field.querySelector("input[data-phone-check]");
      phone.dataset.name = phone.name;
      phone.removeAttribute("name");  // not added again: moved instead (app.admin.neighbors)
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "move_phone_id";
      hidden.value = move.dataset.movePhone;
      field.appendChild(hidden);
      field.querySelector("[data-phone-hint]").textContent = "Se va a pasar a esta persona al guardar.";
    });
  }
  document.addEventListener("click", function (event) {
    var target = event.target.closest ? event.target : null;
    if (!target) return;
    var open = target.closest("a[data-open-editor]");
    if (open) { if (openEditor(open)) event.preventDefault(); return; }
    var close = target.closest("[data-close-editor]");
    if (close && close.closest("[data-editor]")) {
      event.preventDefault();
      closeEditor(close.closest("[data-editor]"));
      return;
    }
    var more = target.closest("[data-add-phone]");
    if (more) { addPhoneField(more); return; }
    var chosen = target.closest("[data-pick-person]");
    if (chosen) { pick(chosen); return; }
    var unpick = target.closest("[data-unpick]");
    if (unpick) {
      var form = unpick.closest("form");
      form.querySelector("[data-person-id]").value = "";
      var name = form.querySelector("input[name=full_name]");
      name.readOnly = false;
      form.querySelector("[data-similar]").textContent = "";
      name.focus();
      return;
    }
    var cancel = target.closest("[data-cancel-phone]");
    if (cancel) { clearPhone(phoneField(cancel)); return; }
    var move = target.closest("[data-move-phone]");
    if (move) askMove(move);
  });
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (input.matches && input.matches("input[data-similar-url]")) similarLater(input);
  });
  document.addEventListener("change", function (event) {
    var el = event.target;
    if (!el.matches) return;
    if (el.matches("input[data-phone-check]")) checkPhone(el);
    if (el.matches("input[name=mode]")) approveMode(el.form);
    if (el.matches("[data-role-select], input[name=mode]")) ownerText(el.form);
  });

  // --- The blocks of «Administración» (admins: the menu and «Más») ---------------------------
  // The block of the open page comes open from the server; the others stay as she left them
  // (localStorage, shared by the menu and «Más»). Some come after this script: when the page is
  // read. A browser without storage: they are just closed.
  var GROUPS_KEY = "panel-menu-groups";
  function readGroups() {
    try { return JSON.parse(window.localStorage.getItem(GROUPS_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function writeGroups(value) {
    try { window.localStorage.setItem(GROUPS_KEY, JSON.stringify(value)); } catch (e) { /* not kept */ }
  }
  document.addEventListener("DOMContentLoaded", function () {
    var saved = readGroups();
    document.querySelectorAll("details[data-menu-group]").forEach(function (box) {
      var group = box.dataset.menuGroup;
      if (box.hasAttribute("data-menu-current") || !(group in saved)) return;
      box.open = !!saved[group];
    });
  });
  // "toggle" does not bubble: listened while it goes down.
  document.addEventListener("toggle", function (event) {
    var box = event.target;
    // The block of the open page always comes open from the server: not a choice to keep
    // (the browser also says "toggle" when it draws a block already open).
    if (!box.dataset || !box.dataset.menuGroup || box.hasAttribute("data-menu-current")) return;
    var saved = readGroups();
    if (saved[box.dataset.menuGroup] === box.open) return;
    saved[box.dataset.menuGroup] = box.open;
    writeGroups(saved);
    // The same block in the menu and in «Más».
    document.querySelectorAll('details[data-menu-group="' + box.dataset.menuGroup + '"]').forEach(function (other) {
      if (other !== box && other.open !== box.open) other.open = box.open;
    });
  }, true);

  // --- «Más» of the bar at the bottom (phones) ---------------------------------------------------
  // The bar and its sheet come after this script in the page: looked up when used.
  document.addEventListener("click", function (event) {
    var sheet = document.getElementById("more-sheet");
    if (!sheet || typeof sheet.showModal !== "function" || !event.target.closest) return;
    if (event.target.closest("[data-more-open]")) { sheet.showModal(); return; }
    // The close button, or a tap on the shade around the sheet.
    if (sheet.open && (event.target === sheet || event.target.closest("[data-more-close]"))) sheet.close();
  });

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
    last.setAttribute("aria-labelledby", "tour-done-title");
    last.innerHTML = '<h2 class="tour-title" id="tour-done-title">Listo</h2><p class="tour-text"></p>' +
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
    bubble.setAttribute("aria-labelledby", "tour-title");
    bubble.innerHTML =
      '<div class="tour-count"></div><h2 class="tour-title" id="tour-title"></h2><p class="tour-text"></p>' +
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
