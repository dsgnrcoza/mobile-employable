(function () {
  "use strict";

  var stepEmail = document.getElementById("forgot-step-email");
  var stepCode = document.getElementById("forgot-step-code");
  var emailInput = document.getElementById("forgot-email");
  var emailError = document.getElementById("forgot-email-error");
  var sendBtn = document.getElementById("forgot-send-btn");
  var codeEmailLabel = document.getElementById("forgot-code-email");
  var codeBoxes = Array.prototype.slice.call(document.querySelectorAll(".code-box"));
  var newPasswordInput = document.getElementById("reset-new-password");
  var confirmPasswordInput = document.getElementById("reset-confirm-password");
  var codeError = document.getElementById("forgot-code-error");
  var submitBtn = document.getElementById("reset-submit-btn");
  var resendLink = document.getElementById("forgot-resend-link");

  var currentEmail = "";

  function setError(el, message) {
    el.textContent = message;
    el.hidden = !message;
  }

  function goToCodeStep(email) {
    currentEmail = email;
    codeEmailLabel.textContent = email;
    stepEmail.hidden = true;
    stepCode.hidden = false;
    codeBoxes[0].focus();
    maybeAskForNotifications();
  }

  // ---------- Segmented 6-digit code boxes ----------

  function currentCode() {
    return codeBoxes.map(function (b) { return b.value; }).join("");
  }

  codeBoxes.forEach(function (box, i) {
    box.addEventListener("input", function () {
      box.value = box.value.replace(/[^0-9]/g, "").slice(0, 1);
      if (box.value && i < codeBoxes.length - 1) codeBoxes[i + 1].focus();
    });
    box.addEventListener("keydown", function (e) {
      if (e.key === "Backspace" && !box.value && i > 0) {
        codeBoxes[i - 1].focus();
      }
    });
    box.addEventListener("paste", function (e) {
      var pasted = (e.clipboardData || window.clipboardData).getData("text").replace(/[^0-9]/g, "");
      if (!pasted) return;
      e.preventDefault();
      codeBoxes.forEach(function (b, j) { b.value = pasted[j] || ""; });
      var next = Math.min(pasted.length, codeBoxes.length) - 1;
      if (next >= 0) codeBoxes[next].focus();
    });
  });

  sendBtn.addEventListener("click", function () {
    var email = emailInput.value.trim();
    if (!email) {
      setError(emailError, "Enter your email first.");
      return;
    }
    setError(emailError, "");
    sendBtn.disabled = true;
    fetch("/api/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          setError(emailError, data.error || "Something went wrong. Please try again.");
          return;
        }
        goToCodeStep(email);
      })
      .catch(function () {
        setError(emailError, "Connection error — please try again.");
      })
      .finally(function () {
        sendBtn.disabled = false;
      });
  });

  resendLink.addEventListener("click", function (e) {
    e.preventDefault();
    codeBoxes.forEach(function (b) { b.value = ""; });
    stepCode.hidden = true;
    stepEmail.hidden = false;
    emailInput.focus();
  });

  // Same live checklist as signup.js, matching auth.validate_password()'s
  // rules exactly -- duplicated rather than shared since each page's
  // password field has its own id/markup and this is only a few lines.
  var reqItems = document.querySelectorAll("#password-reqs li");
  var RULES = {
    length: function (pw) { return pw.length >= 8; },
    upper: function (pw) { return /[A-Z]/.test(pw); },
    lower: function (pw) { return /[a-z]/.test(pw); },
    number: function (pw) { return /[0-9]/.test(pw); },
    special: function (pw) { return /[^A-Za-z0-9]/.test(pw); },
  };
  newPasswordInput.addEventListener("input", function () {
    var pw = newPasswordInput.value;
    reqItems.forEach(function (li) {
      var rule = RULES[li.dataset.rule];
      li.classList.toggle("is-met", !!(rule && rule(pw)));
    });
  });

  submitBtn.addEventListener("click", function () {
    setError(codeError, "");
    submitBtn.disabled = true;
    fetch("/api/reset-password-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: currentEmail,
        code: currentCode(),
        new_password: newPasswordInput.value,
        confirm_password: confirmPasswordInput.value,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          setError(codeError, data.error || "Something went wrong. Please try again.");
          submitBtn.disabled = false;
          return;
        }
        submitBtn.textContent = "Password reset — redirecting…";
        setTimeout(function () { window.location.href = "/login"; }, 1200);
      })
      .catch(function () {
        setError(codeError, "Connection error — please try again.");
        submitBtn.disabled = false;
      });
  });

  // ---------- One-time notification permission prompt ----------
  // Fires the first time any user reaches the code-entry screen on this
  // device, never again after -- tracked in localStorage rather than
  // per-account, since at this point in the flow the user isn't
  // authenticated yet and this is a device-level opt-in, not tied to a
  // specific account.

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
})();
