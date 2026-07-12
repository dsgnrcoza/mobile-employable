(function () {
  "use strict";

  var emailInput = document.getElementById("forgot-email");
  var keyInput = document.getElementById("forgot-security-key");
  var newPasswordInput = document.getElementById("reset-new-password");
  var confirmPasswordInput = document.getElementById("reset-confirm-password");
  var errorEl = document.getElementById("forgot-error");
  var submitBtn = document.getElementById("reset-submit-btn");

  function setError(message) {
    errorEl.textContent = message;
    errorEl.hidden = !message;
  }

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
    var email = emailInput.value.trim();
    var securityKey = keyInput.value.trim();
    if (!email || !securityKey) {
      setError("Enter your email and security key.");
      return;
    }
    setError("");
    submitBtn.disabled = true;
    fetch("/api/reset-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: email,
        security_key: securityKey,
        new_password: newPasswordInput.value,
        confirm_password: confirmPasswordInput.value,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          setError(data.error || "Something went wrong. Please try again.");
          submitBtn.disabled = false;
          return;
        }
        submitBtn.textContent = "Password reset — redirecting…";
        window.location.href = data.redirect || "/dashboard";
      })
      .catch(function () {
        setError("Connection error — please try again.");
        submitBtn.disabled = false;
      });
  });
})();
