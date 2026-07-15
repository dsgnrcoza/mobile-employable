(function () {
  "use strict";

  document.querySelectorAll(".plugin-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var goingOn = !btn.classList.contains("is-on");
      btn.classList.toggle("is-on", goingOn);
      btn.setAttribute("aria-checked", String(goingOn));
      fetch("/api/plugins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: btn.dataset.plugin, enabled: goingOn }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            // Roll back on failure -- the toggle already flipped optimistically above.
            btn.classList.toggle("is-on", !goingOn);
            btn.setAttribute("aria-checked", String(!goingOn));
          }
        })
        .catch(function () {
          btn.classList.toggle("is-on", !goingOn);
          btn.setAttribute("aria-checked", String(!goingOn));
        });
    });
  });
})();
