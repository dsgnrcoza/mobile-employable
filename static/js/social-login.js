(function () {
  "use strict";

  // Google/Apple sign-in aren't wired up to a real OAuth provider yet
  // (that needs real API credentials from each platform's developer
  // console, which only the account owner can provision) -- so these
  // say so plainly instead of pretending to work.
  var note = document.getElementById("social-login-note");
  if (!note) return;

  function showComingSoon(provider) {
    note.textContent = provider + " sign-in is coming soon.";
    note.hidden = false;
  }

  var googleBtn = document.getElementById("google-login-btn");
  var appleBtn = document.getElementById("apple-login-btn");
  if (googleBtn) googleBtn.addEventListener("click", function () { showComingSoon("Google"); });
  if (appleBtn) appleBtn.addEventListener("click", function () { showComingSoon("Apple"); });
})();
