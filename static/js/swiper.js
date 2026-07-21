(function () {
  "use strict";

  var deckEl = document.getElementById("swiper-deck");
  var emptyEl = document.getElementById("swiper-empty");
  var fallbackActionsEl = document.getElementById("swiper-fallback-actions");
  var hideBtn = document.getElementById("swiper-hide-btn");
  var applyBtn = document.getElementById("swiper-apply-btn");
  var toastEl = document.getElementById("swiper-toast");
  var badgeEl = document.getElementById("job-tab-badge");
  var hintEl = document.getElementById("swiper-hint");

  var queue = [];
  var stack = []; // {el, job} front-to-back, stack[0] is always the interactive top card
  var toastTimer = null;

  var SPRING_BACK = "transform 0.42s cubic-bezier(0.34, 1.56, 0.64, 1)";
  var STACK_SHIFT = "transform 0.38s cubic-bezier(0.22, 1, 0.36, 1)";
  var FLY_AWAY = "transform 0.36s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.36s ease";

  // A short, sharp tick on commit -- most phones support it, iOS Safari
  // silently doesn't, hence the feature check rather than a try/catch.
  function hapticTick() {
    if (navigator.vibrate) navigator.vibrate(12);
  }

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
    var hasCards = stack.length > 0;
    emptyEl.hidden = hasCards;
    fallbackActionsEl.hidden = !hasCards;
    if (!hasCards) hintEl.hidden = true;
  }

  // Shown once, ever, before this user's very first swipe -- neither
  // direction is explained anywhere else in the UI, so a first-time
  // visitor has no way to know left means skip and right means apply
  // until they've already committed to a gesture.
  var HINT_SEEN_KEY = "employable_swiper_hint_seen";
  var hintSeen = false;
  try { hintSeen = localStorage.getItem(HINT_SEEN_KEY) === "1"; } catch (e) {}
  if (!hintSeen) hintEl.hidden = false;

  function dismissHint() {
    if (hintEl.hidden) return;
    hintEl.hidden = true;
    try { localStorage.setItem(HINT_SEEN_KEY, "1"); } catch (e) {}
  }

  // Resting transform for a given position in the stack -- position 0
  // (front, interactive) sits at full size; each position behind it is
  // very slightly smaller and dropped, so the deck reads as a real
  // physical stack of cards rather than everything living in one spot.
  // Accepts fractional positions too, so a card mid-transition between
  // two slots can be driven frame-by-frame instead of only snapping
  // between whole positions.
  function restTransform(i) {
    return "scale(" + (1 - i * 0.04) + ") translateY(" + (i * 10) + "px)";
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

  function addCardAt(i) {
    var job = queue[i];
    var el = buildCard(job);
    el.style.transition = "none";
    el.style.transform = restTransform(i);
    el.style.zIndex = String(10 - i);
    if (i === 0) el.classList.add("is-top");
    deckEl.appendChild(el);
    var entry = { el: el, job: job };
    stack.push(entry);
    wireDrag(entry);
    return entry;
  }

  // First paint: build the whole visible stack from scratch. Every
  // later swipe uses advanceStack() instead, which shifts the existing
  // DOM cards forward rather than tearing the deck down and rebuilding
  // it -- that's what makes the reveal underneath feel like one
  // continuous motion instead of a jump cut.
  function renderInitialDeck() {
    deckEl.innerHTML = "";
    stack = [];
    var count = Math.min(3, queue.length);
    for (var i = 0; i < count; i++) addCardAt(i);
    refreshEmptyState();
  }

  function advanceStack() {
    queue.shift();
    stack.shift();

    stack.forEach(function (entry, i) {
      entry.el.style.transition = STACK_SHIFT;
      entry.el.style.zIndex = String(10 - i);
      entry.el.style.transform = restTransform(i);
      entry.el.classList.toggle("is-top", i === 0);
    });

    while (stack.length < 3 && stack.length < queue.length) {
      addCardAt(stack.length);
    }

    refreshEmptyState();
  }

  function commitSwipe(direction, job) {
    // Fired immediately, in parallel with the fly-away animation below
    // -- the swipe loop never waits on this network call. If it fails,
    // the job just never gets removed server-side and reappears on the
    // next full reload of the queue.
    if (direction === "left") {
      fetch("/api/jobs/" + encodeURIComponent(job.id) + "/hide", { method: "POST" }).catch(function () {});
    } else {
      fetch("/api/jobs/" + encodeURIComponent(job.id) + "/apply", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            // The Outbox tab's own badge count updating is enough
            // signal that this landed -- a toast on top of it for
            // every single successful swipe was just noise. Still
            // worth a toast specifically when nothing new happened,
            // though, since the badge not changing there could
            // otherwise look like this one silently failed.
            if (data.duplicate) showToast("Already in your Outbox");
            updateBadge();
          } else {
            showToast(data.error || "Couldn't draft that application.");
          }
        })
        .catch(function () {
          showToast("Couldn't reach Avryn — that one didn't save. Try again.");
        });
    }
  }

  function flyCardAway(entry, direction) {
    var card = entry.el;
    // Thrown well past the edge of the screen, with a bigger spin and a
    // harder shrink than a gentle slide-off -- reads as flung, not just
    // nudged away, which is most of what makes the gesture satisfying
    // to repeat.
    var travel = window.innerWidth * 2.1;
    var rise = -110 - Math.random() * 60;
    var spin = 34 + Math.random() * 14;
    // This card is no longer in the stack array by the time it flies
    // away (advanceStack already shifted it out) -- drop its is-top
    // marker explicitly so exactly one card ever carries it, even
    // during the brief overlap while this one is still departing.
    card.classList.remove("is-top");
    card.style.transition = FLY_AWAY;
    card.style.transform =
      "translate(" + (direction === "left" ? -travel : travel) + "px, " + rise + "px) rotate(" +
      (direction === "left" ? -spin : spin) + "deg) scale(0.8)";
    card.style.opacity = "0.15";
    setTimeout(function () { card.remove(); }, 370);
  }

  function swipeCommitted(entry, direction) {
    dismissHint();
    hapticTick();
    commitSwipe(direction, entry.job);
    flyCardAway(entry, direction);
    advanceStack();
  }

  function wireDrag(entry) {
    var card = entry.el;
    var startX = 0, startY = 0, dx = 0, dy = 0, startTime = 0, dragging = false, decided = false;
    var stampApply = card.querySelector(".swiper-card-stamp-apply");
    var stampHide = card.querySelector(".swiper-card-stamp-hide");
    var rafPending = false;
    var thresholdHit = false;

    function isTop() {
      return stack.length > 0 && stack[0].el === card;
    }

    function applyFrame() {
      rafPending = false;
      // Steeper tilt and a slight lift (scale) the further the card is
      // pulled -- reads as picking the card up and throwing it, not
      // just sliding it sideways.
      var rotation = dx / 10;
      var pull = Math.min(1, Math.abs(dx) / 120);
      var lift = 1 + pull * 0.04;
      card.style.transform = "translate(" + dx + "px, " + dy + "px) rotate(" + rotation + "deg) scale(" + lift + ")";
      stampApply.style.opacity = dx > 0 ? pull : 0;
      stampHide.style.opacity = dx < 0 ? pull : 0;
      // The instant a drag first crosses the commit threshold, the
      // stamp snaps with a little overshoot instead of just smoothly
      // reaching opacity 1 -- a distinct "locked in" moment the user
      // can feel is coming before they even release.
      if (pull >= 1 && !thresholdHit) {
        thresholdHit = true;
        var stamp = dx > 0 ? stampApply : stampHide;
        stamp.style.transition = "transform 0.18s cubic-bezier(0.34, 1.56, 0.64, 1)";
        stamp.style.transform = "scale(1.25)";
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { stamp.style.transform = "scale(1)"; });
        });
      } else if (pull < 1 && thresholdHit) {
        thresholdHit = false;
      }
      // The next card in the stack rises to meet the front position as
      // this one is dragged away, so committing mid-drag (a fast flick
      // released early) never has to "catch up" visually afterward --
      // by the time this card commits, the one underneath is already
      // sitting close to where it needs to be.
      var beneath = stack[1];
      if (beneath) {
        beneath.el.style.transition = "none";
        beneath.el.style.transform = restTransform(1 - pull);
      }
    }

    function onPointerMove(e) {
      dx = e.clientX - startX;
      dy = e.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) < 6) return;
      dragging = true;
      if (!rafPending) {
        rafPending = true;
        requestAnimationFrame(applyFrame);
      }
    }

    function onPointerUp() {
      card.removeEventListener("pointermove", onPointerMove);
      card.removeEventListener("pointerup", onPointerUp);
      card.removeEventListener("pointercancel", onPointerUp);
      if (decided) return;
      var elapsed = Date.now() - startTime;
      var isFastFlick = Math.abs(dx) > 28 && elapsed < 260;
      var committed = dragging && (Math.abs(dx) > 110 || isFastFlick);
      if (committed) {
        decided = true;
        swipeCommitted(entry, dx < 0 ? "left" : "right");
      } else {
        card.style.transition = SPRING_BACK;
        card.style.transform = restTransform(0);
        stampApply.style.opacity = 0;
        stampHide.style.opacity = 0;
        var beneath = stack[1];
        if (beneath) {
          beneath.el.style.transition = SPRING_BACK;
          beneath.el.style.transform = restTransform(1);
        }
      }
      dragging = false;
    }

    card.addEventListener("pointerdown", function (e) {
      if (decided || !isTop()) return;
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
  }

  function triggerFallback(direction) {
    if (!stack.length) return;
    // A drag gets its stamp feedback for free from applyFrame(), which
    // this button-triggered path never runs -- flash the matching stamp
    // in directly so a tap feels just as confirmed as a real swipe,
    // instead of the card silently flying off with no stamp at all.
    var card = stack[0].el;
    var stamp = card.querySelector(direction === "left" ? ".swiper-card-stamp-hide" : ".swiper-card-stamp-apply");
    if (stamp) stamp.style.opacity = "1";
    swipeCommitted(stack[0], direction);
  }

  hideBtn.addEventListener("click", function () { triggerFallback("left"); });
  applyBtn.addEventListener("click", function () { triggerFallback("right"); });

  fetch("/api/jobs")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      queue = data.ok ? data.jobs : [];
      renderInitialDeck();
    })
    .catch(function () {
      queue = [];
      renderInitialDeck();
      showToast("Couldn't load jobs — check your connection.");
    });

  updateBadge();
})();
