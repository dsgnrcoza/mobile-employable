(function () {
  "use strict";

  // Floating download button on the login/signup screens — lets a
  // visitor grab the app without waiting for the first-visit prompt
  // (or after they've already dismissed it). Mirrors the same download
  // paths used on the dashboard's Store page.
  var fabBtn = document.getElementById("download-fab-btn");
  var menu = document.getElementById("download-fab-menu");
  if (!fabBtn || !menu) return;

  function isStandaloneApp() {
    return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  }

  function isIOSDevice() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  // Captured as early as possible, same as dashboard.js — Chrome fires
  // this once per page load and only if the PWA installability
  // criteria are already met, so it has to be listened for up front
  // rather than at click time.
  var deferredInstallPrompt = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredInstallPrompt = e;
  });

  fabBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });

  document.addEventListener("click", function (e) {
    if (!menu.hidden && e.target !== fabBtn && !menu.contains(e.target) && !fabBtn.contains(e.target)) {
      menu.hidden = true;
    }
  });

  var note = document.getElementById("download-fab-note");

  document.getElementById("download-fab-mobile-btn").addEventListener("click", function () {
    if (isStandaloneApp()) {
      // Already using the installed app — say so instead of silently
      // doing nothing, which (from inside the app) looks exactly like
      // a broken button.
      note.textContent = "You're already using the installed app — nothing more to download here.";
      note.hidden = false;
      return;
    }
    if (isIOSDevice()) {
      // No .ipa side-loading equivalent exists on iOS outside the App
      // Store/TestFlight — the real native-feeling install there is
      // "Add to Home Screen."
      note.textContent = "On iPhone/iPad: tap the Share icon in Safari, then \"Add to Home Screen.\"";
      note.hidden = false;
      return;
    }
    // Same-origin proxy (see /download/android in app.py) so the
    // download starts immediately instead of navigating out to
    // github.com first.
    window.location.href = "/download/android";
    // Android shows an "Unknown apps"/Play Protect warning for any APK
    // from outside the Play Store — a real, unremovable OS security
    // check, not a bug here. Naming it upfront turns a scary-looking
    // dead end into an expected extra tap.
    note.textContent = "Downloading the Android app… Android will warn that it's from outside the Play Store — that's expected for any app installed this way. Open the file, then tap \"Install anyway\" / \"Install without scanning\" to continue.";
    note.hidden = false;
  });

  document.getElementById("download-fab-desktop-btn").addEventListener("click", function () {
    if (isStandaloneApp()) {
      note.textContent = "You're already using the installed app — nothing more to download here.";
      note.hidden = false;
      return;
    }
    if (deferredInstallPrompt) {
      var promptEvent = deferredInstallPrompt;
      deferredInstallPrompt = null;
      promptEvent.prompt();
      note.hidden = true;
      return;
    }
    note.textContent = "Your browser hasn't offered an install prompt yet — look for an install icon in the address bar, or open this site in Chrome/Edge for a one-tap install.";
    note.hidden = false;
  });
})();
