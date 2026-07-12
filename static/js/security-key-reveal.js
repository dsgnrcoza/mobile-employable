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
})();
