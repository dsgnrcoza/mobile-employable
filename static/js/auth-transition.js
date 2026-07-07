(function () {
  "use strict";

  // Fades the page out before navigating to the other Login/Register
  // tab, instead of letting the browser hard-cut to blank while the
  // next page loads. This is a floor that works in every browser --
  // ones that also support the View Transitions API (see the
  // @view-transition rule in style.css) layer a native cross-fade on
  // top of it, but this still applies where that isn't supported.
  var shell = document.querySelector(".auth-shell");
  var tabs = document.querySelectorAll(".auth-tabs a.auth-tab");
  if (!shell || !tabs.length) return;

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  tabs.forEach(function (link) {
    link.addEventListener("click", function (e) {
      var href = link.getAttribute("href");
      if (!href || reduceMotion) return;
      e.preventDefault();
      shell.classList.add("is-leaving");
      setTimeout(function () {
        window.location.href = href;
      }, 150);
    });
  });
})();
