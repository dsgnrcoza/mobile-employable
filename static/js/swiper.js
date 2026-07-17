(function () {
  "use strict";

  var deckEl = document.getElementById("swiper-deck");
  var emptyEl = document.getElementById("swiper-empty");
  var fallbackActionsEl = document.getElementById("swiper-fallback-actions");
  var hideBtn = document.getElementById("swiper-hide-btn");
  var applyBtn = document.getElementById("swiper-apply-btn");
  var toastEl = document.getElementById("swiper-toast");
  var badgeEl = document.getElementById("swiper-outbox-badge");

  var queue = [];
  var toastTimer = null;
  var topCardEl = null;

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function showToast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 2200);
  }

  function relativeDate(iso) {
    if (!iso) return "";
    var posted = new Date(iso + "T00:00:00");
    if (isNaN(posted.getTime())) return "";
    var days = Math.round((Date.now() - posted.getTime()) / 86400000);
    if (days <= 0) return "Posted today";
    if (days === 1) return "Posted yesterday";
    return "Posted " + days + " days ago";
  }

  function updateBadge() {
    fetch("/api/applications")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        var count = data.applications.filter(function (a) { return a.status === "drafted"; }).length;
        badgeEl.textContent = count > 99 ? "99+" : String(count);
        badgeEl.hidden = count === 0;
      })
      .catch(function () {});
  }

  function refreshEmptyState() {
    var hasCards = deckEl.children.length > 0;
    emptyEl.hidden = hasCards;
    fallbackActionsEl.hidden = !hasCards;
  }

  function buildCard(job) {
    var card = document.createElement("div");
    card.className = "swiper-card";
    card.dataset.jobId = job.id;

    var salaryHtml = job.salary
      ? '<p class="swiper-card-salary">' + escapeHtml(job.salary) + "</p>"
      : "";

    card.innerHTML =
      '<div class="swiper-card-stamp swiper-card-stamp-apply">APPLY</div>' +
      '<div class="swiper-card-stamp swiper-card-stamp-hide">SKIP</div>' +
      '<div class="swiper-card-top">' +
      '<h2 class="swiper-card-title">' + escapeHtml(job.title) + "</h2>" +
      '<p class="swiper-card-company">' + escapeHtml(job.company) + "</p>" +
      "</div>" +
      '<p class="swiper-card-location">' + escapeHtml(job.location) + "</p>" +
      salaryHtml +
      '<p class="swiper-card-desc">' + escapeHtml(job.description) + "</p>" +
      '<span class="swiper-card-date">' + escapeHtml(relativeDate(job.posted_at)) + "</span>";
    return card;
  }

  function renderDeck() {
    deckEl.innerHTML = "";
    topCardEl = null;
    // Only the top 3 need to exist in the DOM -- the rest of the queue
    // is invisible behind them anyway, so building the whole stack up
    // front would just be wasted layout work on every swipe. Built
    // back-to-front (so the top card's DOM node paints last, above the
    // others) -- topCardEl is kept separately since that back-to-front
    // order means the top card is never simply deckEl's first child.
    var visible = queue.slice(0, 3);
    for (var i = visible.length - 1; i >= 0; i--) {
      var card = buildCard(visible[i]);
      card.style.zIndex = String(10 - i);
      card.style.setProperty("--stack-i", String(i));
      deckEl.appendChild(card);
      if (i === 0) {
        card.classList.add("is-top");
        wireDrag(card, visible[i]);
        topCardEl = card;
      }
    }
    refreshEmptyState();
  }

  function removeTopJob() {
    queue.shift();
    renderDeck();
  }

  function commitSwipe(direction, job) {
    // Optimistic UI: the card is already flying off-screen and the next
    // one is already showing before the network call below even
    // resolves. If the hide/apply call fails, the job just never gets
    // removed server-side and will reappear on the next full reload of
    // the queue -- an acceptable, simple failure mode that keeps the
    // swipe loop itself fast and uninterrupted, per spec.
    if (direction === "left") {
      fetch("/api/jobs/" + encodeURIComponent(job.id) + "/hide", { method: "POST" }).catch(function () {});
    } else {
      fetch("/api/jobs/" + encodeURIComponent(job.id) + "/apply", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            showToast(data.duplicate ? "Already in your Outbox" : "Drafted — check your Outbox");
            updateBadge();
          } else {
            showToast(data.error || "Couldn't draft that application.");
          }
        })
        .catch(function () {
          showToast("Couldn't reach Ploy — that one didn't save. Try again.");
        });
    }
    removeTopJob();
  }

  function flyCardAway(card, direction, onDone) {
    var travel = window.innerWidth * 1.4;
    card.style.transition = "transform 0.32s cubic-bezier(0.2, 0.8, 0.2, 1)";
    card.style.transform =
      "translate(" + (direction === "left" ? -travel : travel) + "px, -40px) rotate(" +
      (direction === "left" ? -24 : 24) + "deg)";
    setTimeout(onDone, 300);
  }

  function wireDrag(card, job) {
    var startX = 0, startY = 0, dx = 0, dy = 0, startTime = 0, dragging = false, decided = false;
    var stampApply = card.querySelector(".swiper-card-stamp-apply");
    var stampHide = card.querySelector(".swiper-card-stamp-hide");

    function onPointerMove(e) {
      dx = e.clientX - startX;
      dy = e.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) < 6) return;
      dragging = true;
      var rotation = dx / 18;
      card.style.transform = "translate(" + dx + "px, " + dy + "px) rotate(" + rotation + "deg)";
      var pull = Math.min(1, Math.abs(dx) / 100);
      stampApply.style.opacity = dx > 0 ? pull : 0;
      stampHide.style.opacity = dx < 0 ? pull : 0;
    }

    function onPointerUp() {
      card.removeEventListener("pointermove", onPointerMove);
      card.removeEventListener("pointerup", onPointerUp);
      card.removeEventListener("pointercancel", onPointerUp);
      if (decided) return;
      var elapsed = Date.now() - startTime;
      var isFastFlick = Math.abs(dx) > 30 && elapsed < 250;
      var committed = dragging && (Math.abs(dx) > 100 || isFastFlick);
      if (committed) {
        decided = true;
        var direction = dx < 0 ? "left" : "right";
        flyCardAway(card, direction, function () { commitSwipe(direction, job); });
      } else {
        card.style.transition = "transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1)";
        card.style.transform = "";
        stampApply.style.opacity = 0;
        stampHide.style.opacity = 0;
      }
      dragging = false;
    }

    card.addEventListener("pointerdown", function (e) {
      if (decided) return;
      startX = e.clientX;
      startY = e.clientY;
      dx = 0;
      dy = 0;
      startTime = Date.now();
      dragging = false;
      card.style.transition = "none";
      card.setPointerCapture(e.pointerId);
      card.addEventListener("pointermove", onPointerMove);
      card.addEventListener("pointerup", onPointerUp);
      card.addEventListener("pointercancel", onPointerUp);
    });

    // Fallback buttons act on whatever the current top card is.
    card.dataset.wired = "1";
  }

  function triggerFallback(direction) {
    if (!topCardEl || !queue.length) return;
    var job = queue[0];
    flyCardAway(topCardEl, direction, function () { commitSwipe(direction, job); });
  }

  hideBtn.addEventListener("click", function () { triggerFallback("left"); });
  applyBtn.addEventListener("click", function () { triggerFallback("right"); });

  fetch("/api/jobs")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      queue = data.ok ? data.jobs : [];
      renderDeck();
    })
    .catch(function () {
      queue = [];
      renderDeck();
      showToast("Couldn't load jobs — check your connection.");
    });

  updateBadge();
})();
