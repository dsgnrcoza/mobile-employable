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
    }).catch(function () {
      // Clipboard permission denied or unavailable (e.g. non-HTTPS
      // context) -- select the key text in place so the user still has
      // a visible, manually-copyable fallback instead of the button
      // silently doing nothing. The button itself is icon-only, so
      // feedback goes through its aria-label rather than replacing the
      // icon with text.
      var range = document.createRange();
      range.selectNodeContents(keyValueEl);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      copyBtn.setAttribute("aria-label", "Key selected — copy it manually");
      setTimeout(function () {
        copyBtn.setAttribute("aria-label", "Copy security key");
      }, 2500);
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
})();
