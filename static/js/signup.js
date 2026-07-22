(function () {
  "use strict";

  // Exposed as a named function (rather than just running once at
  // parse time) so auth-transition.js can re-invoke it after swapping
  // a fresh signup form into the DOM client-side -- the elements this
  // closure originally captured no longer exist once that happens, so
  // without a re-run the new form's submit/password-checklist would
  // have no listeners at all.
  window.initSignupPage = function () {
  var form = document.getElementById("signup-form");
  if (!form) return;

  var flashArea = document.getElementById("signup-flash-area");
  var overlay = document.getElementById("celebrate-overlay");
  var continueBtn = document.getElementById("celebrate-continue-btn");
  var submitBtn = form.querySelector("button[type=submit]");

  // On-brand instead of a generic party-confetti palette -- the app's
  // own accent plus its semantic good/mid tones, so the very first
  // celebratory moment already looks like Avryn.
  var CONFETTI_COLORS = ["#C25F1C", "#4CC38A", "#E4B33C", "#D98A4C"];

  function launchConfetti() {
    var sides = ["left", "right"];
    sides.forEach(function (side) {
      for (var i = 0; i < 26; i++) {
        spawnPiece(side);
      }
    });
  }

  function spawnPiece(side) {
    var piece = document.createElement("div");
    piece.className = "confetti-piece";

    // Starts flush against its edge and travels across the screen
    // toward the opposite side, rather than falling top-to-bottom.
    var startY = Math.random() * window.innerHeight;
    piece.style.top = startY + "px";
    var travel = window.innerWidth * (0.7 + Math.random() * 0.4);
    var tx;
    if (side === "left") {
      piece.style.left = "-10px";
      tx = travel;
    } else {
      piece.style.right = "-10px";
      tx = -travel;
    }

    var dur = 1.6 + Math.random() * 1.1;
    var verticalDrift = (Math.random() - 0.5) * 140;
    var rot = 360 * (Math.random() > 0.5 ? 1 : -1) * (1 + Math.random());

    piece.style.setProperty("--dur", dur + "s");
    piece.style.setProperty("--tx", tx + "px");
    piece.style.setProperty("--ty", verticalDrift + "px");
    piece.style.setProperty("--rot", rot + "deg");
    piece.style.backgroundColor = CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)];
    piece.style.animationDelay = (Math.random() * 0.4) + "s";

    document.body.appendChild(piece);
    setTimeout(function () { piece.remove(); }, (dur + 0.4) * 1000);
  }

  function celebrate(nextUrl) {
    launchConfetti();
    overlay.hidden = false;
    continueBtn.onclick = function () {
      window.location.href = nextUrl;
    };
  }

  function showFailureHtml(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var freshFlash = doc.getElementById("signup-flash-area");
    if (freshFlash) flashArea.innerHTML = freshFlash.innerHTML;
    submitBtn.disabled = false;
  }

  // Live checklist matching the exact rules auth.validate_password()
  // enforces server-side, so a rejected password never comes as a
  // surprise -- every rule is visibly met before the user even submits.
  var passwordInput = document.getElementById("password");
  var reqItems = document.querySelectorAll("#password-reqs li");
  var RULES = {
    length: function (pw) { return pw.length >= 8; },
    upper: function (pw) { return /[A-Z]/.test(pw); },
    lower: function (pw) { return /[a-z]/.test(pw); },
    number: function (pw) { return /[0-9]/.test(pw); },
    special: function (pw) { return /[^A-Za-z0-9]/.test(pw); },
  };
  if (passwordInput && reqItems.length) {
    passwordInput.addEventListener("input", function () {
      var pw = passwordInput.value;
      reqItems.forEach(function (li) {
        var rule = RULES[li.dataset.rule];
        li.classList.toggle("is-met", !!(rule && rule(pw)));
      });
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    submitBtn.disabled = true;
    fetch(form.action, { method: "POST", body: new FormData(form) })
      .then(function (res) {
        if (res.redirected) {
          celebrate(res.url);
        } else {
          return res.text().then(showFailureHtml);
        }
      })
      .catch(function () {
        submitBtn.disabled = false;
        alert("Something went wrong. Please try again.");
      });
  });
  };

  // Login is a plain server-rendered POST (no client-side fetch/celebrate
  // flow to hook into like signup above), so this only adds visible
  // "working on it" feedback for the round-trip -- it never calls
  // preventDefault, the real submission proceeds exactly as before.
  window.initLoginPage = function () {
    var form = document.getElementById("login-form");
    if (!form) return;
    var submitBtn = form.querySelector("button[type=submit]");
    form.addEventListener("submit", function () {
      submitBtn.disabled = true;
      submitBtn.textContent = "Signing in…";
    });
  };

  window.initSignupPage();
  window.initLoginPage();
})();
