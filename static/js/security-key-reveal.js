(function () {
  "use strict";

  var keyValueEl = document.getElementById("security-key-value");
  var copyBtn = document.getElementById("security-key-copy-btn");
  var confirmCheckbox = document.getElementById("security-key-confirm-checkbox");
  var continueBtn = document.getElementById("security-key-continue-btn");

  copyBtn.addEventListener("click", function () {
    var text = keyValueEl.textContent || "";
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () {
      copyBtn.classList.add("is-copied");
      setTimeout(function () { copyBtn.classList.remove("is-copied"); }, 1500);
    });
  });

  confirmCheckbox.addEventListener("change", function () {
    continueBtn.disabled = !confirmCheckbox.checked;
  });

  continueBtn.addEventListener("click", function () {
    continueBtn.disabled = true;
    fetch("/api/security-key/acknowledge", { method: "POST" })
      .catch(function () {})
      .then(function () {
        window.location.href = "/dashboard";
      });
  });

  // ---------- One-time notification permission prompt ----------
  // This page is a natural pause point -- the user already has to stop
  // and read/copy a critical secret -- so it's a better moment to ask
  // than mid-flow elsewhere. Tracked in localStorage (device-level, not
  // per-account) and only ever asked once per device.

  var NOTIF_ASKED_KEY = "ploy_notifications_asked";

  function maybeAskForNotifications() {
    if (!("Notification" in window)) return;
    if (localStorage.getItem(NOTIF_ASKED_KEY)) return;
    if (Notification.permission !== "default") {
      localStorage.setItem(NOTIF_ASKED_KEY, "1");
      return;
    }
    var row = document.getElementById("notif-permission-row");
    var btn = document.getElementById("notif-permission-btn");
    row.hidden = false;
    btn.addEventListener("click", function () {
      Notification.requestPermission().finally(function () {
        localStorage.setItem(NOTIF_ASKED_KEY, "1");
        row.hidden = true;
      });
    });
  }

  maybeAskForNotifications();
})();
