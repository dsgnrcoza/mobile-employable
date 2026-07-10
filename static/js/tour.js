/*
 * Minimal sequential coach-mark tour: one step visible at a time, each
 * with a short line stemming from the target element to its tooltip
 * bubble (so tooltips never overlap each other or the target), and a
 * "Got it" button to advance. Each tour runs once ever per browser,
 * tracked by its own localStorage key.
 */
(function () {
  "use strict";

  var LINE_LENGTH = 26;
  var active = null; // { line, tooltip } currently on screen, so runTour can clean up between steps

  function teardownActive() {
    if (!active) return;
    if (active.line && active.line.parentNode) active.line.parentNode.removeChild(active.line);
    if (active.tooltip && active.tooltip.parentNode) active.tooltip.parentNode.removeChild(active.tooltip);
    active = null;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function showStep(step, onDone) {
    var rect = step.target.getBoundingClientRect();
    var direction = step.direction || "down";

    var line = document.createElement("div");
    line.className = "tour-line tour-line-" + direction;

    var tooltip = document.createElement("div");
    tooltip.className = "tour-tooltip tour-tooltip-" + direction;
    tooltip.innerHTML =
      '<p class="tour-tooltip-text"></p>' +
      '<button type="button" class="btn btn-gold btn-sm tour-tooltip-got-it">Got it</button>';
    tooltip.querySelector(".tour-tooltip-text").textContent = step.text;

    document.body.appendChild(line);
    document.body.appendChild(tooltip);

    if (direction === "down") {
      var cx = rect.left + rect.width / 2;
      // tooltipTop lets a set of steps share one fixed landing spot
      // (e.g. below a whole list of rows) so the tooltip never lands
      // on top of the next row in the list -- only the line's length
      // varies per step in that case, not the tooltip's position.
      var tooltipTop = step.tooltipTop != null ? step.tooltipTop : rect.bottom + LINE_LENGTH;
      line.style.left = cx + "px";
      line.style.top = rect.bottom + "px";
      line.style.height = Math.max(4, tooltipTop - rect.bottom) + "px";

      // Measure the tooltip once it's in the DOM, then clamp it inside
      // the viewport horizontally without moving the line itself.
      var tw = tooltip.offsetWidth;
      var left = clamp(cx - tw / 2, 12, window.innerWidth - tw - 12);
      tooltip.style.left = left + "px";
      tooltip.style.top = tooltipTop + "px";
    } else if (direction === "up") {
      var cxUp = rect.left + rect.width / 2;
      line.style.left = cxUp + "px";
      line.style.top = (rect.top - LINE_LENGTH) + "px";
      line.style.height = LINE_LENGTH + "px";

      var twUp = tooltip.offsetWidth;
      var thUp = tooltip.offsetHeight;
      var leftUp = clamp(cxUp - twUp / 2, 12, window.innerWidth - twUp - 12);
      tooltip.style.left = leftUp + "px";
      tooltip.style.top = (rect.top - LINE_LENGTH - thUp) + "px";
    } else {
      var cy = rect.top + rect.height / 2;
      line.style.top = cy + "px";
      line.style.left = rect.right + "px";
      line.style.width = LINE_LENGTH + "px";

      var th = tooltip.offsetHeight;
      var top = clamp(cy - th / 2, 12, window.innerHeight - th - 12);
      tooltip.style.top = top + "px";
      tooltip.style.left = (rect.right + LINE_LENGTH) + "px";
    }

    // Let the CSS transition play instead of snapping straight to full opacity.
    requestAnimationFrame(function () {
      line.classList.add("is-visible");
      tooltip.classList.add("is-visible");
    });

    active = { line: line, tooltip: tooltip };
    tooltip.querySelector(".tour-tooltip-got-it").addEventListener("click", function () {
      teardownActive();
      onDone();
    });
  }

  window.runTour = function (storageKey, steps) {
    if (localStorage.getItem(storageKey)) return;
    if (!steps.length) return;

    var idx = 0;
    function advance() {
      if (idx >= steps.length) {
        localStorage.setItem(storageKey, "1");
        return;
      }
      var step = steps[idx];
      idx += 1;
      if (!step.target) { advance(); return; }
      showStep(step, advance);
    }
    advance();
  };
})();
