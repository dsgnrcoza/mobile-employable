(function () {
  "use strict";

  // First-visit choice, shown once per device on the very first screen
  // an unauthenticated visitor lands on (the login page, since that's
  // where "/" redirects anyone not already signed in): get the real
  // mobile app, or just continue in the browser. Skipped entirely for
  // anyone already using the installed app (nothing to offer them) and
  // never shown again once dismissed either way.
  var overlay = document.getElementById("install-choice-overlay");
  if (!overlay) return;

  var DISMISS_KEY = "employable_install_choice_seen";

  function isStandaloneApp() {
    return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  }

  function isIOSDevice() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  function isMobileDevice() {
    return isIOSDevice() || /android/i.test(navigator.userAgent);
  }

  var alreadySeen = false;
  try { alreadySeen = localStorage.getItem(DISMISS_KEY) === "1"; } catch (e) {}

  if (!alreadySeen && !isStandaloneApp() && isMobileDevice()) {
    overlay.hidden = false;
  }

  function markSeen() {
    try { localStorage.setItem(DISMISS_KEY, "1"); } catch (e) {}
  }

  document.getElementById("install-choice-web-btn").addEventListener("click", function () {
    overlay.hidden = true;
    markSeen();
  });

  document.getElementById("install-choice-download-btn").addEventListener("click", function () {
    var note = document.getElementById("install-choice-note");
    // Marked seen either way (don't hide the overlay itself yet, so the
    // note below stays visible) — the choice has been made either way,
    // no need to ask again on a future visit.
    markSeen();
    if (isIOSDevice()) {
      // No .ipa side-loading equivalent exists on iOS outside the App
      // Store/TestFlight — the real native-feeling install there is
      // "Add to Home Screen."
      note.textContent = "On iPhone/iPad: tap the Share icon in Safari, then \"Add to Home Screen.\"";
      note.hidden = false;
      return;
    }
    // Same-origin proxy (see /download/android in app.py), triggered
    // through a throwaway <a download> link rather than a full page
    // navigation — the download starts immediately instead of
    // navigating out to github.com, and the page itself never jumps.
    var a = document.createElement("a");
    a.href = "/download/android";
    a.download = "employable.apk";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Android shows an "Unknown apps"/Play Protect warning for any APK
    // from outside the Play Store — that's a real, unremovable OS
    // security check, not a bug in this app. Saying so upfront turns a
    // scary-looking dead end into an expected extra tap.
    note.textContent = "Downloading the Android app… Android will warn that it's from outside the Play Store — that's expected for any app installed this way. Open the file, then tap \"Install anyway\" / \"Install without scanning\" to continue.";
    note.hidden = false;
  });
})();
