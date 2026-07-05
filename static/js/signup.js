(function () {
  "use strict";

  var form = document.getElementById("signup-form");
  if (!form) return;

  var flashArea = document.getElementById("signup-flash-area");
  var overlay = document.getElementById("celebrate-overlay");
  var continueBtn = document.getElementById("celebrate-continue-btn");
  var submitBtn = form.querySelector("button[type=submit]");

  var CONFETTI_COLORS = ["#8b5cf6", "#ec4899", "#1e3a8a", "#2dd4bf"];

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
})();
