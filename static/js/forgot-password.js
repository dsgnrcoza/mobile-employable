(function () {
  "use strict";

  var emailInput = document.getElementById("forgot-email");
  var keyInput = document.getElementById("forgot-security-key");
  var newPasswordInput = document.getElementById("reset-new-password");
  var confirmPasswordInput = document.getElementById("reset-confirm-password");
  var errorEl = document.getElementById("forgot-error");
  var submitBtn = document.getElementById("reset-submit-btn");
  var keyFormatHint = document.getElementById("key-format-hint");

  // Matches auth._normalize_security_key/generate_security_key exactly:
  // 25 characters from an alphabet that excludes visually ambiguous ones
  // (no I, O, 0, 1), dashes are pure formatting and stripped either side.
  var KEY_LENGTH = 25;
  var KEY_ALPHABET_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]+$/;

  function normalizeKey(raw) {
    return (raw || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  }

  // Returns null when the key is fine (or still too short to judge),
  // otherwise a short, specific message about what's wrong -- catches a
  // malformed key before the round trip instead of only after a 401.
  function keyFormatProblem(stripped) {
    if (stripped.length < KEY_LENGTH) return null;
    if (stripped.length > KEY_LENGTH) {
      return "That's " + stripped.length + " characters — a security key is exactly 25.";
    }
    if (!KEY_ALPHABET_RE.test(stripped)) {
      return "Check for O, I, 0, or 1 — this key's letters/numbers never include those (easy to mistype).";
    }
    return null;
  }

  keyInput.addEventListener("input", function () {
    var stripped = normalizeKey(keyInput.value);
    if (!stripped.length) {
      keyFormatHint.hidden = true;
      return;
    }
    var problem = keyFormatProblem(stripped);
    keyFormatHint.hidden = false;
    keyFormatHint.classList.toggle("is-error", !!problem);
    if (problem) {
      keyFormatHint.textContent = problem;
    } else if (stripped.length === KEY_LENGTH) {
      keyFormatHint.textContent = "Looks right.";
    } else {
      keyFormatHint.textContent = stripped.length + " of 25 characters.";
    }
  });

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
    var strippedKey = normalizeKey(securityKey);
    if (strippedKey.length !== KEY_LENGTH) {
      setError("A security key is exactly 25 characters — check yours and try again.");
      return;
    }
    var keyProblem = keyFormatProblem(strippedKey);
    if (keyProblem) {
      setError(keyProblem);
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
        setError("Couldn't reach Avryn to reset your password — check your connection and try again.");
        submitBtn.disabled = false;
      });
  });
})();
