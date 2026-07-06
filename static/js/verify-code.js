(function () {
  "use strict";

  var row = document.getElementById("code-input-row");
  if (!row) return;

  var digits = Array.prototype.slice.call(row.querySelectorAll(".code-digit"));
  var hiddenInput = document.getElementById("code-hidden-input");
  var form = document.getElementById("verify-code-form");

  function currentCode() {
    return digits.map(function (d) { return d.value; }).join("");
  }

  digits.forEach(function (input, i) {
    input.addEventListener("input", function () {
      input.value = input.value.replace(/[^0-9]/g, "").slice(0, 1);
      if (input.value && i < digits.length - 1) {
        digits[i + 1].focus();
      }
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Backspace" && !input.value && i > 0) {
        digits[i - 1].focus();
      }
    });

    // Pasting a full code into any box distributes it across all six,
    // rather than only accepting one character at a time.
    input.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text").replace(/[^0-9]/g, "");
      if (!text) return;
      e.preventDefault();
      text.slice(0, digits.length).split("").forEach(function (ch, idx) {
        if (digits[idx]) digits[idx].value = ch;
      });
      var next = Math.min(text.length, digits.length - 1);
      digits[next].focus();
    });
  });

  form.addEventListener("submit", function () {
    hiddenInput.value = currentCode();
  });

  var resendBtn = document.getElementById("resend-code-btn");
  if (resendBtn) {
    resendBtn.addEventListener("click", function () {
      resendBtn.disabled = true;
      var originalText = resendBtn.textContent;
      resendBtn.textContent = "Sending…";
      fetch("/api/resend-code", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            resendBtn.textContent = "Code sent!";
            setTimeout(function () {
              resendBtn.textContent = originalText;
              resendBtn.disabled = false;
            }, 4000);
          } else {
            alert(data.error || "Couldn't resend the code. Please try again.");
            resendBtn.textContent = originalText;
            resendBtn.disabled = false;
          }
        })
        .catch(function () {
          alert("Something went wrong. Please try again.");
          resendBtn.textContent = originalText;
          resendBtn.disabled = false;
        });
    });
  }
})();
