(function () {
  "use strict";

  var state = window.DASH_STATE || {};
  var profile = state.profile || {};
  var analysis = state.analysis || null;

  // Captured as early as possible so it's ready by the time the user
  // taps a Download button on the Store screen — Chrome fires this
  // once the PWA installability criteria (manifest + service worker +
  // icons) are met, and it can only be used once per capture.
  var deferredInstallPrompt = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredInstallPrompt = e;
  });

  // All 8 dimensions the backend scores, in the same canonical order
  // rubric.py's DIMENSIONS uses. Only 3 of these (PRIMARY_LABELS below)
  // are shown as the main cards and averaged into the headline
  // Employability Score (see pipeline.py's PRIMARY_SCORE_DIMENSIONS,
  // which this must match); the other 5 are still fully computed and
  // shown, just moved behind "Full Breakdown".
  var METRICS = [
    {
      label: "Documentation Strength",
      short: "Documentation",
      icon: '<path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/>',
    },
    {
      label: "Experience Strength",
      short: "Experience",
      icon: '<rect x="3" y="8" width="18" height="12" rx="1.5"/><path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    },
    {
      label: "Qualification Strength",
      short: "Qualifications",
      icon: '<path d="M22 10 12 5 2 10l10 5 10-5z"/><path d="M6 12v5c0 1.5 2.5 3 6 3s6-1.5 6-3v-5"/><path d="M22 10v6"/>',
    },
    {
      label: "Skill Strength",
      short: "Skills",
      icon: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/>',
    },
    {
      label: "Market Competitiveness",
      short: "Market Fit",
      icon: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 6h6v6"/>',
    },
    {
      label: "Evidence Credibility",
      short: "Credibility",
      icon: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>',
    },
    {
      label: "ATS Compatibility",
      short: "ATS Score",
      icon: '<path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3z"/><path d="m9 12 2 2 4-4"/>',
    },
    {
      label: "Career Progression",
      short: "Progression",
      icon: '<path d="M3 20h4v-4h4v-4h4v-4h4V4"/>',
    },
  ];

  var PRIMARY_LABELS = ["ATS Compatibility", "Skill Strength", "Experience Strength"];
  var PRIMARY_METRICS = PRIMARY_LABELS.map(function (label) {
    return METRICS.filter(function (m) { return m.label === label; })[0];
  });
  var SECONDARY_METRICS = METRICS.filter(function (m) { return PRIMARY_LABELS.indexOf(m.label) === -1; });

  function firstName() {
    var raw = (profile.full_name || "").trim();
    if (!raw) raw = (profile.username || "").trim();
    if (!raw) return "there";
    return raw.split(/\s+/)[0];
  }

  document.getElementById("dash-username").textContent = firstName();

  if (profile.avatar_url) {
    var img = document.getElementById("dash-avatar-img");
    img.src = profile.avatar_url;
    img.hidden = false;
    document.getElementById("dash-avatar-default").style.display = "none";
  }

  var gaugeFill = document.getElementById("dash-gauge-fill");
  var gaugeScore = document.getElementById("dash-gauge-score");
  var gaugeLabel = document.getElementById("dash-gauge-label");
  var primaryMetricsEl = document.getElementById("dash-primary-metrics");
  var metricsEl = document.getElementById("dash-metrics");
  var breakdownToggle = document.getElementById("dash-breakdown-toggle");
  var emptyEl = document.getElementById("dash-empty");

  breakdownToggle.addEventListener("click", function () {
    var willShow = metricsEl.hidden;
    metricsEl.hidden = !willShow;
    breakdownToggle.classList.toggle("is-expanded", willShow);
  });

  // Actually implements what the empty state's copy has always
  // promised — a real "Refresh Score" retry, for when the first
  // analysis attempt (during onboarding) timed out or failed instead
  // of completing, leaving the account confirmed but unscored.
  var refreshScoreBtn = document.getElementById("dash-refresh-score-btn");
  var refreshScoreError = document.getElementById("dash-refresh-score-error");
  if (refreshScoreBtn) {
    refreshScoreBtn.addEventListener("click", function () {
      refreshScoreError.hidden = true;
      refreshScoreBtn.disabled = true;
      refreshScoreBtn.textContent = "Scoring…";
      fetch("/api/reanalyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            window.location.reload();
          } else {
            refreshScoreBtn.disabled = false;
            refreshScoreBtn.textContent = "Refresh Score";
            refreshScoreError.textContent = data.error || "Couldn't score your documents yet. Please try again.";
            refreshScoreError.hidden = false;
          }
        })
        .catch(function () {
          refreshScoreBtn.disabled = false;
          refreshScoreBtn.textContent = "Refresh Score";
          refreshScoreError.textContent = "Something went wrong. Please try again.";
          refreshScoreError.hidden = false;
        });
    });
  }

  var CIRCUMFERENCE = 2 * Math.PI * 52;
  gaugeFill.style.strokeDasharray = CIRCUMFERENCE.toFixed(2);

  function setGauge(rating) {
    var pct = Math.max(0, Math.min(1, rating / 10));
    gaugeFill.style.strokeDashoffset = (CIRCUMFERENCE * (1 - pct)).toFixed(2);
  }

  // Same technique as the main gauge above, at the smaller radius used
  // by the 3 primary metric cards' rings.
  var PRIMARY_RING_RADIUS = 34;
  var PRIMARY_RING_CIRCUMFERENCE = 2 * Math.PI * PRIMARY_RING_RADIUS;

  var LABEL_EXPLANATIONS = {
    "Highly Employable": {
      clear: "Your profile is strong across the board — real experience, real skills, and real proof of both. Recruiters would see very few red flags here.",
      simple: "It's like your homework is done, checked, and gold-star good. You're ready to hand it in.",
    },
    "Job Ready": {
      clear: "You're close to a complete, competitive profile. A few gaps remain, but nothing that should stop you from applying now.",
      simple: "You're almost fully packed for the trip — just a couple of small things left in the drawer.",
    },
    "Competitive": {
      clear: "Your profile is workable, but there are real gaps that better-documented candidates won't have. Closing even one or two would help a lot.",
      simple: "You can play the game and you're not losing — but some other players have a few extra power-ups you don't have yet.",
    },
    "Needs Work": {
      clear: "There are significant gaps across your profile right now — thin documentation, limited evidence, or both. Worth addressing before applying broadly.",
      simple: "It's like a puzzle with quite a few pieces missing — you can still see the picture a little, but not the whole thing yet.",
    },
    "Highly Hindered": {
      clear: "Multiple core areas are weak or unverifiable right now. This is a good starting point, not a finished profile — the roadmap below is where to focus.",
      simple: "Think of this as the very start of building a Lego set — you've got the box open, but most of the pieces aren't clicked together yet.",
    },
    "Critical Gaps": {
      clear: "There isn't yet enough here — evidence, experience, or detail — for this profile to compete. Start with uploading a complete CV and any documents that back it up.",
      simple: "Right now it's like an empty backpack before a trip — we need to put some things in it before we can go anywhere.",
    },
  };

  var labelInfoBtn = document.getElementById("dash-label-info-btn");
  var labelInfoOverlay = document.getElementById("dash-label-info-overlay");
  var labelInfoTitle = document.getElementById("dash-label-info-title");
  var labelInfoDesc = document.getElementById("dash-label-info-desc");
  var labelInfoSimple = document.getElementById("dash-label-info-simple");
  var labelInfoCloseBtn = document.getElementById("dash-label-info-close-btn");

  labelInfoBtn.addEventListener("click", function () {
    var entry = LABEL_EXPLANATIONS[gaugeLabel.textContent] || { clear: "", simple: "" };
    labelInfoTitle.textContent = gaugeLabel.textContent;
    labelInfoDesc.textContent = entry.clear;
    labelInfoSimple.textContent = entry.simple;
    labelInfoOverlay.hidden = false;
  });
  labelInfoCloseBtn.addEventListener("click", function () { labelInfoOverlay.hidden = true; });
  labelInfoOverlay.addEventListener("click", function (e) {
    if (e.target === labelInfoOverlay) labelInfoOverlay.hidden = true;
  });

  var metricOverlay = document.getElementById("dash-metric-overlay");
  var metricTitle = document.getElementById("dash-metric-title");
  var metricScore = document.getElementById("dash-metric-score");
  var metricDesc = document.getElementById("dash-metric-desc");
  var metricSimple = document.getElementById("dash-metric-simple");
  var metricWhy = document.getElementById("dash-metric-why");
  var metricCloseBtn = document.getElementById("dash-metric-close-btn");

  // Generic, dimension-level "why employers actually care about this"
  // context — not tied to this user's specific score, just the honest
  // real-world reason each of these 5 metrics exists at all. Reused
  // as-is by the Full Breakdown rows below; the 3 primary cards show
  // this same text inline instead of via this modal.
  var WHY_EMPLOYERS_CARE = {
    "Documentation": "Employers care about this because anyone can write a great-sounding CV — what actually convinces a hiring manager is proof: certificates, references, transcripts. A CV backed by real documents tells them your claims are real, not just well-written.",
    "Experience": "Employers care about years of experience because it's the clearest sign you can do the job without heavy hand-holding. But raw years alone aren't enough — they also want to see what you actually achieved in that time, not just that you showed up.",
    "Skills": "Employers — and the automated systems that screen CVs before a human ever does — scan for specific, in-demand skills as an early filter. Skills you can prove you've actually used in a real role carry far more weight than a skill just listed at the bottom of a page.",
    "Market Fit": "Employers compare you against everyone else applying for the same role — 'good enough on its own' isn't the bar, 'better than the other applicants' is. This shows you honestly how you stack up against what the market currently expects for your target role.",
    "ATS Score": "Before a human ever reads your CV, an automated tracking system usually reads it first — and if it can't parse your layout, dates, or sections properly, a strong candidate can get filtered out before anyone sees a single word. This score is about surviving that first automated gate.",
  };

  function openMetricModal(label, score, description, simpleExplanation, shortLabel) {
    metricTitle.textContent = label;
    metricScore.textContent = score.toFixed(1) + " / 10";
    metricDesc.textContent = description || "";
    // Older cached analyses won't have simple_explanation yet — hide
    // that paragraph gracefully instead of showing it empty.
    if (simpleExplanation) {
      metricSimple.textContent = simpleExplanation;
      metricSimple.hidden = false;
    } else {
      metricSimple.hidden = true;
    }
    metricWhy.textContent = WHY_EMPLOYERS_CARE[shortLabel] || "";
    metricOverlay.hidden = false;
  }

  metricCloseBtn.addEventListener("click", function () { metricOverlay.hidden = true; });
  metricOverlay.addEventListener("click", function (e) {
    if (e.target === metricOverlay) metricOverlay.hidden = true;
  });

  // ---------- 3 primary cards: built once, updated (not rebuilt) on ----------
  // ---------- every re-render so the ring can animate smoothly       ----------

  var primaryCardRefs = {}; // label -> { ringFill, scoreEl, findingEl, detailDesc, detailSimple, detailWhy }

  // The one short "most relevant specific finding" line for a primary
  // card, built entirely from data the backend already computes: the
  // mechanical ATS findings for ATS Compatibility (most specific and
  // verifiable), otherwise the top roadmap item already targeting this
  // dimension, otherwise the dimension's own AI description.
  function findPrimaryFinding(label, dim, roadmap) {
    if (label === "ATS Compatibility" && dim && dim.ats_findings && dim.ats_findings.length) {
      return dim.ats_findings[0];
    }
    var topItem = (roadmap || []).filter(function (r) { return r.dimension === label; })[0];
    if (topItem && topItem.what) return topItem.what;
    if (dim && dim.description) return dim.description;
    return "No specific gaps flagged for this yet.";
  }

  function buildPrimaryCard(m) {
    var card = document.createElement("div");
    card.className = "dash-primary-card";

    var header = document.createElement("button");
    header.type = "button";
    header.className = "dash-primary-card-header";
    header.innerHTML =
      '<span class="dash-primary-ring-wrap">' +
        '<svg class="dash-primary-ring" viewBox="0 0 80 80">' +
          '<circle class="dash-primary-ring-track" cx="40" cy="40" r="' + PRIMARY_RING_RADIUS + '"></circle>' +
          '<circle class="dash-primary-ring-fill" cx="40" cy="40" r="' + PRIMARY_RING_RADIUS + '"></circle>' +
        '</svg>' +
        '<span class="dash-primary-ring-score">–</span>' +
      '</span>' +
      '<span class="dash-primary-info">' +
        '<span class="dash-primary-name">' + m.short + '</span>' +
        '<span class="dash-primary-finding"></span>' +
      '</span>' +
      '<svg class="dash-primary-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

    var detail = document.createElement("div");
    detail.className = "dash-primary-detail";
    detail.hidden = true;
    detail.innerHTML =
      '<p class="dash-primary-detail-desc"></p>' +
      '<p class="dash-primary-detail-simple"></p>' +
      '<p class="dash-primary-detail-why"></p>';

    header.addEventListener("click", function () {
      var willOpen = detail.hidden;
      detail.hidden = !willOpen;
      card.classList.toggle("is-expanded", willOpen);
    });

    card.appendChild(header);
    card.appendChild(detail);
    primaryMetricsEl.appendChild(card);

    var ringFill = header.querySelector(".dash-primary-ring-fill");
    ringFill.style.strokeDasharray = PRIMARY_RING_CIRCUMFERENCE.toFixed(2);
    ringFill.style.strokeDashoffset = PRIMARY_RING_CIRCUMFERENCE.toFixed(2);

    primaryCardRefs[m.label] = {
      ringFill: ringFill,
      scoreEl: header.querySelector(".dash-primary-ring-score"),
      findingEl: header.querySelector(".dash-primary-finding"),
      detailDesc: detail.querySelector(".dash-primary-detail-desc"),
      detailSimple: detail.querySelector(".dash-primary-detail-simple"),
      detailWhy: detail.querySelector(".dash-primary-detail-why"),
    };
  }

  PRIMARY_METRICS.forEach(buildPrimaryCard);

  function updatePrimaryCard(m, dim, roadmap) {
    var refs = primaryCardRefs[m.label];
    var score = dim ? dim.score : 0;
    var pct = Math.max(0, Math.min(1, score / 10));
    refs.ringFill.style.strokeDashoffset = (PRIMARY_RING_CIRCUMFERENCE * (1 - pct)).toFixed(2);
    refs.scoreEl.textContent = score.toFixed(1);
    refs.findingEl.textContent = findPrimaryFinding(m.label, dim, roadmap);
    refs.detailDesc.textContent = dim ? (dim.description || "") : "No details available yet.";
    if (dim && dim.simple_explanation) {
      refs.detailSimple.textContent = dim.simple_explanation;
      refs.detailSimple.hidden = false;
    } else {
      refs.detailSimple.hidden = true;
    }
    refs.detailWhy.textContent = WHY_EMPLOYERS_CARE[m.short] || "";
  }

  // ---------- Full Breakdown: the other 5 metrics, unchanged rows ----------
  // Rebuilt each time (not updated in place) -- it's a secondary,
  // usually-collapsed view, so it doesn't need its own animation.

  function renderSecondaryMetrics(dimByLabel) {
    metricsEl.innerHTML = "";
    SECONDARY_METRICS.forEach(function (m) {
      var dim = dimByLabel[m.label];
      var score = dim ? dim.score : 0;
      var pct = Math.max(0, Math.min(100, (score / 10) * 100));

      var row = document.createElement("button");
      row.type = "button";
      row.className = "dash-metric-row";

      row.innerHTML =
        '<span class="dash-metric-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + m.icon + '</svg></span>' +
        '<span class="dash-metric-main">' +
          '<span class="dash-metric-top-row">' +
            '<span class="dash-metric-name">' + m.short + '</span>' +
            '<span class="dash-metric-value">' + score.toFixed(1) + '/10</span>' +
          '</span>' +
          '<span class="dash-metric-bar-track"><span class="dash-metric-bar-fill" style="width:' + pct + '%"></span></span>' +
        '</span>';

      row.addEventListener("click", function () {
        openMetricModal(
          m.label,
          score,
          dim ? dim.description : "No details available yet.",
          dim ? dim.simple_explanation : "",
          m.short
        );
      });

      metricsEl.appendChild(row);
    });
  }

  // ---------- One entry point: gauge + 3 primary cards + Full Breakdown ----------
  // Called once at load and again after any upload/removal that comes
  // back with fresh state, so every part of the score display -- not
  // just the number -- stays in sync with what actually changed.

  function renderDashboardScore(analysisArg) {
    analysis = analysisArg;

    if (!analysis) {
      setGauge(0);
      gaugeScore.textContent = "–";
      gaugeLabel.textContent = "Awaiting analysis";
      labelInfoBtn.hidden = true;
      breakdownToggle.hidden = true;
      metricsEl.hidden = true;
      emptyEl.hidden = false;
      insightsEl.hidden = true;
      return;
    }

    emptyEl.hidden = true;
    breakdownToggle.hidden = false;

    var dimByLabel = {};
    (analysis.dimensions || []).forEach(function (d) { dimByLabel[d.label] = d; });
    var roadmap = analysis.improvement_roadmap || [];

    var overall = analysis.employability_score || 0;
    setGauge(overall);
    gaugeScore.textContent = overall.toFixed(1);
    gaugeLabel.textContent = analysis.employability_score_label || "Unrated";
    labelInfoBtn.hidden = false;

    PRIMARY_METRICS.forEach(function (m) {
      updatePrimaryCard(m, dimByLabel[m.label], roadmap);
    });

    renderSecondaryMetrics(dimByLabel);
    renderInsights(analysis);
  }

  // ---------- Dashboard insights: evidence, working well / hurting you, roadmap ----------
  // Every list rendered here comes straight from the analysis this user's
  // own documents already produced (evidence_summary, working_well,
  // critical_issues, improvement_roadmap) — nothing generic, nothing
  // invented client-side.

  var insightsEl = document.getElementById("dash-insights");
  var evidenceListEl = document.getElementById("dash-evidence-list");
  var workingListEl = document.getElementById("dash-working-list");
  var issuesListEl = document.getElementById("dash-issues-list");
  var roadmapListEl = document.getElementById("dash-roadmap-list");

  var completedItemLabels = {}; // item_label -> true, fetched from the server

  function fillList(el, items) {
    el.innerHTML = "";
    (items || []).forEach(function (text) {
      var li = document.createElement("li");
      li.textContent = text;
      el.appendChild(li);
    });
  }

  function renderRoadmap(roadmap) {
    roadmapListEl.innerHTML = "";
    (roadmap || []).forEach(function (item) {
      var done = !!completedItemLabels[item.what];
      var card = document.createElement("button");
      card.type = "button";
      card.className = "dash-roadmap-card" + (done ? " completed" : "");
      card.innerHTML =
        '<span class="dash-roadmap-main">' +
          '<span class="dash-roadmap-what">' + (done ? "✓ " : "") + item.what + '</span>' +
          '<span class="dash-roadmap-dim">' + (item.dimension || "") + '</span>' +
        '</span>' +
        '<span class="dash-roadmap-points">+' + (item.projected_score_gain || 0).toFixed(1) + ' pts</span>';
      card.addEventListener("click", function () { openRoadmapDetail(item, done); });
      roadmapListEl.appendChild(card);
    });
  }

  var currentRoadmapItem = null;

  function renderInsights(analysis) {
    fillList(evidenceListEl, analysis.evidence_summary);
    fillList(workingListEl, analysis.working_well);
    fillList(issuesListEl, analysis.critical_issues);

    fetch("/api/roadmap/completions")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        completedItemLabels = {};
        (data.completions || []).forEach(function (c) { completedItemLabels[c.item_label] = true; });
        renderRoadmap(analysis.improvement_roadmap);
      })
      .catch(function () { renderRoadmap(analysis.improvement_roadmap); });

    insightsEl.hidden = false;
  }

  // ---------- Roadmap item detail + "mark as done" flow ----------

  var roadmapDetailOverlay = document.getElementById("roadmap-detail-overlay");
  var roadmapDetailPoints = document.getElementById("roadmap-detail-points");
  var roadmapDetailTitle = document.getElementById("roadmap-detail-title");
  var roadmapDetailWhy = document.getElementById("roadmap-detail-why");
  var roadmapDetailHow = document.getElementById("roadmap-detail-how");
  var roadmapDetailDoneFlash = document.getElementById("roadmap-detail-done-flash");
  var roadmapDetailCompleteBtn = document.getElementById("roadmap-detail-complete-btn");
  var roadmapDetailCloseBtn = document.getElementById("roadmap-detail-close-btn");

  function openRoadmapDetail(item, done) {
    currentRoadmapItem = item;
    roadmapDetailPoints.textContent = "+" + (item.projected_score_gain || 0).toFixed(1) + " pts";
    roadmapDetailTitle.textContent = item.what;
    roadmapDetailWhy.textContent = item.why || "";
    roadmapDetailHow.textContent = item.how ? ("How: " + item.how) : "";
    roadmapDetailDoneFlash.hidden = !done;
    roadmapDetailCompleteBtn.hidden = done;
    roadmapDetailOverlay.hidden = false;
  }

  roadmapDetailCloseBtn.addEventListener("click", function () { roadmapDetailOverlay.hidden = true; });
  roadmapDetailOverlay.addEventListener("click", function (e) {
    if (e.target === roadmapDetailOverlay) roadmapDetailOverlay.hidden = true;
  });

  var roadmapDocPickerOverlay = document.getElementById("roadmap-doc-picker-overlay");
  var roadmapDocListEl = document.getElementById("roadmap-doc-list");
  var roadmapDocPickerCancelBtn = document.getElementById("roadmap-doc-picker-cancel-btn");

  roadmapDetailCompleteBtn.addEventListener("click", function () {
    var docs = state.documents || [];
    roadmapDocListEl.innerHTML = "";
    if (!docs.length) {
      var empty = document.createElement("div");
      empty.className = "uploads-list-empty";
      empty.textContent = "You don't have any documents uploaded yet.";
      roadmapDocListEl.appendChild(empty);
    } else {
      docs.forEach(function (d) {
        var row = document.createElement("button");
        row.type = "button";
        row.className = "profile-menu-row";
        row.innerHTML = '<span class="profile-menu-label">' + d.filename + '</span>';
        row.addEventListener("click", function () {
          roadmapDocPickerOverlay.hidden = true;
          submitRoadmapCompletion(currentRoadmapItem, d.id);
        });
        roadmapDocListEl.appendChild(row);
      });
    }
    roadmapDetailOverlay.hidden = true;
    roadmapDocPickerOverlay.hidden = false;
  });

  roadmapDocPickerCancelBtn.addEventListener("click", function () { roadmapDocPickerOverlay.hidden = true; });

  var roadmapResultOverlay = document.getElementById("roadmap-result-overlay");
  var roadmapResultTitle = document.getElementById("roadmap-result-title");
  var roadmapResultReason = document.getElementById("roadmap-result-reason");
  var roadmapResultSteps = document.getElementById("roadmap-result-steps");
  var roadmapResultCloseBtn = document.getElementById("roadmap-result-close-btn");

  function submitRoadmapCompletion(item, docId) {
    fetch("/api/roadmap/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_label: item.what,
        item_description: [item.why, item.how].filter(Boolean).join(" "),
        doc_id: docId,
        // The AI only judges fulfilled/not — the point value awarded is
        // always exactly what was already shown on this card, computed
        // server-side with the same formula as the visible gauge/bars.
        points: item.projected_score_gain,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error === "already_completed") {
          roadmapResultTitle.textContent = "Already done";
          roadmapResultReason.textContent = "You've already marked this one as complete.";
          roadmapResultSteps.innerHTML = "";
        } else if (data.error) {
          roadmapResultTitle.textContent = "Couldn't check that";
          roadmapResultReason.textContent = data.error;
          roadmapResultSteps.innerHTML = "";
        } else if (data.fulfilled) {
          completedItemLabels[item.what] = true;
          roadmapResultTitle.textContent = "Nice — that counts";
          roadmapResultReason.textContent = data.reason + " (+" + data.points + " pts)";
          roadmapResultSteps.innerHTML = "";
          renderRoadmap((analysis && analysis.improvement_roadmap) || []);
        } else {
          roadmapResultTitle.textContent = "Not quite there yet";
          roadmapResultReason.textContent = data.reason || "";
          fillList(roadmapResultSteps, data.steps);
        }
        roadmapResultOverlay.hidden = false;
      })
      .catch(function () {
        roadmapResultTitle.textContent = "Something went wrong";
        roadmapResultReason.textContent = "Please try again.";
        roadmapResultSteps.innerHTML = "";
        roadmapResultOverlay.hidden = false;
      });
  }

  roadmapResultCloseBtn.addEventListener("click", function () { roadmapResultOverlay.hidden = true; });
  roadmapResultOverlay.addEventListener("click", function (e) {
    if (e.target === roadmapResultOverlay) roadmapResultOverlay.hidden = true;
  });

  // Deferred until here (rather than inline where the primary cards are
  // built) so every var this depends on — evidenceListEl, roadmapListEl,
  // etc. — has actually been assigned by the time this runs.
  renderDashboardScore(analysis);

  var ratingInfoBtn = document.getElementById("dash-rating-info-btn");
  var ratingInfoOverlay = document.getElementById("dash-rating-info-overlay");
  var ratingInfoCloseBtn = document.getElementById("dash-rating-info-close-btn");
  ratingInfoBtn.addEventListener("click", function () { ratingInfoOverlay.hidden = false; });
  ratingInfoCloseBtn.addEventListener("click", function () { ratingInfoOverlay.hidden = true; });
  ratingInfoOverlay.addEventListener("click", function (e) {
    if (e.target === ratingInfoOverlay) ratingInfoOverlay.hidden = true;
  });

  // ---------- View switching (dashboard / AI chat / CV workshop) ----------
  // The 3 tab bar buttons only -- Profile, Notifications, Store, and
  // More live in the action sheet below now, not in this tab set.

  var avatarBtn = document.getElementById("dash-avatar-btn");
  var usernameEl = document.getElementById("dash-username");

  function switchView(name) {
    document.querySelectorAll(".dash-view").forEach(function (el) {
      el.hidden = el.id !== "dash-view-" + name;
    });
    document.querySelectorAll(".tabbar-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    if (name === "chat") scrollChatToBottom();
    if (name === "cv") loadCvContent();
  }

  document.querySelectorAll(".tabbar-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      closeActionSheet();
      switchView(btn.dataset.view);
    });
  });

  // ---------- Action sheet: Profile / Notifications / Store / More ----------
  // One shared sliding sheet, styled and animated like the dashboard's
  // own bottom sheet (same drag-to-expand, same tab bar fade), so
  // tapping any of the 4 header buttons feels like the same sheet
  // sliding up with different content -- not four different UI
  // patterns. Its resting position is higher than the dashboard
  // sheet's own, though: just below the header, covering the gauge
  // and "Employability Rating" entirely rather than sitting under it.

  var actionSheet = document.getElementById("action-sheet");
  var actionHandle = document.getElementById("action-sheet-handle");
  var actionBody = document.getElementById("action-sheet-body");
  var actionTabbar = document.getElementById("tabbar");
  var actionPanels = {
    profile: document.getElementById("action-panel-profile"),
    notifications: document.getElementById("action-panel-notifications"),
    store: document.getElementById("action-panel-store"),
    options: document.getElementById("action-panel-options"),
  };

  var actionReduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var ACTION_CLAIM_THRESHOLD = 6;
  var ACTION_SNAP_PX = 40;

  var actionState = "closed"; // 'closed' | 'collapsed' | 'expanded'
  var activePanel = null;
  var actionDragging = false;
  var actionPendingPull = false;
  var actionStartY = 0;
  var actionStartOffset = 0;
  var actionLastOffset = 0;

  // Rests just below the header itself (not the dashboard sheet's own
  // lower collapsed offset) -- rising past the gauge to cover
  // "Employability Rating" entirely, so only the header row (with its
  // back arrow/icons) stays visible above it.
  function getCollapsedOffset() {
    var header = document.querySelector(".dash-header");
    if (header) return header.getBoundingClientRect().bottom;
    return Math.round(window.innerHeight * 0.12);
  }

  function getClosedOffset() {
    return window.innerHeight + 40;
  }

  // Declared here (outer scope) and assigned inside the guard below --
  // strict mode block-scopes plain `function` declarations, and these
  // two are called from click handlers further down this same
  // function, outside that block.
  var openActionSheet = function () {};
  var closeActionSheet = function () {};

  if (actionSheet && actionHandle && actionBody) {
    function showActionPanel(name) {
      Object.keys(actionPanels).forEach(function (key) {
        if (actionPanels[key]) actionPanels[key].hidden = key !== name;
      });
      activePanel = name;
    }

    function applyActionOffset(offset) {
      var closed = getClosedOffset();
      offset = Math.max(0, Math.min(closed, offset));
      actionLastOffset = offset;
      actionSheet.style.transform = "translateY(" + offset + "px)";
      var collapsed = getCollapsedOffset() || 1;
      var progress = 1 - Math.min(offset, collapsed) / collapsed; // 0 collapsed/closed, 1 fully expanded
      if (actionTabbar) {
        actionTabbar.style.opacity = String(1 - progress);
        actionTabbar.style.pointerEvents = progress > 0.5 ? "none" : "";
      }
    }

    function setOffsetForState(s) {
      if (s === "expanded") {
        applyActionOffset(0);
        actionBody.classList.remove("is-scroll-locked");
      } else if (s === "collapsed") {
        applyActionOffset(getCollapsedOffset());
        actionBody.classList.add("is-scroll-locked");
      } else {
        applyActionOffset(getClosedOffset());
        actionBody.classList.add("is-scroll-locked");
      }
    }

    function snapToAction(newState) {
      actionState = newState;
      setOffsetForState(newState);
    }

    closeActionSheet = function () {
      if (actionState === "closed") return;
      snapToAction("closed");
    };

    openActionSheet = function (name) {
      if (actionState !== "closed" && activePanel === name) {
        closeActionSheet();
        return;
      }
      showActionPanel(name);
      if (name === "notifications") loadFriendRequests();
      snapToAction("collapsed");
    };

    // Placed instantly (no transition) off-screen on load -- only
    // snaps triggered by an actual open/drag afterward should animate.
    actionSheet.style.transition = "none";
    snapToAction("closed");
    void actionSheet.offsetHeight;
    actionSheet.style.transition = actionReduceMotion ? "none" : "";

    window.addEventListener("resize", function () {
      if (actionDragging || actionState === "closed") return;
      snapToAction(actionState);
    });

    function actionPointerY(e) {
      return e.touches ? e.touches[0].clientY : e.clientY;
    }

    function startActionDrag(y) {
      actionDragging = true;
      actionSheet.classList.add("is-dragging");
      actionStartY = y;
      actionStartOffset = actionLastOffset;
    }

    function moveActionDrag(y) {
      applyActionOffset(actionStartOffset + (y - actionStartY));
    }

    function finishActionDrag() {
      actionDragging = false;
      actionSheet.classList.remove("is-dragging");
      var moved = actionLastOffset - actionStartOffset; // negative = toward expanded
      var wasExpanded = actionState === "expanded";
      if (Math.abs(moved) < ACTION_CLAIM_THRESHOLD) {
        snapToAction(wasExpanded ? "collapsed" : "expanded");
        return;
      }
      if (moved <= -ACTION_SNAP_PX) {
        snapToAction("expanded");
        return;
      }
      if (moved >= ACTION_SNAP_PX) {
        if (wasExpanded) { snapToAction("collapsed"); } else { closeActionSheet(); }
        return;
      }
      if (wasExpanded) {
        var span = getCollapsedOffset() || 1;
        snapToAction(1 - actionLastOffset / span > 0.5 ? "expanded" : "collapsed");
      } else {
        var closeSpan = getClosedOffset() - getCollapsedOffset() || 1;
        var closeProgress = (actionLastOffset - getCollapsedOffset()) / closeSpan;
        if (closeProgress > 0.5) { closeActionSheet(); } else { snapToAction("collapsed"); }
      }
    }

    actionHandle.addEventListener("touchstart", function (e) {
      startActionDrag(actionPointerY(e));
      if (e.cancelable) e.preventDefault();
    }, { passive: false });

    actionHandle.addEventListener("touchmove", function (e) {
      if (!actionDragging) return;
      moveActionDrag(actionPointerY(e));
      if (e.cancelable) e.preventDefault();
    }, { passive: false });

    actionHandle.addEventListener("touchend", function () {
      if (actionDragging) finishActionDrag();
    });

    actionHandle.addEventListener("mousedown", function (e) {
      startActionDrag(e.clientY);
    });

    function actionShouldClaim(delta) {
      if (actionState === "expanded") {
        if (actionBody.scrollTop > 0) return false;
        return delta > ACTION_CLAIM_THRESHOLD;
      }
      return Math.abs(delta) > ACTION_CLAIM_THRESHOLD;
    }

    actionBody.addEventListener("touchstart", function (e) {
      if (actionDragging) return;
      actionPendingPull = true;
      actionStartY = actionPointerY(e);
    }, { passive: true });

    actionBody.addEventListener("touchmove", function (e) {
      if (actionDragging) {
        moveActionDrag(actionPointerY(e));
        if (e.cancelable) e.preventDefault();
        return;
      }
      if (!actionPendingPull) return;
      var y = actionPointerY(e);
      if (actionState === "expanded" && actionBody.scrollTop > 0) {
        actionPendingPull = false;
        return;
      }
      if (actionShouldClaim(y - actionStartY)) {
        actionPendingPull = false;
        startActionDrag(actionStartY);
        moveActionDrag(y);
        if (e.cancelable) e.preventDefault();
      }
    }, { passive: false });

    actionBody.addEventListener("touchend", function () {
      actionPendingPull = false;
      if (actionDragging) finishActionDrag();
    });

    actionBody.addEventListener("mousedown", function (e) {
      if (actionDragging) return;
      actionPendingPull = true;
      actionStartY = e.clientY;
    });

    window.addEventListener("mousemove", function (e) {
      if (actionDragging) {
        moveActionDrag(e.clientY);
        return;
      }
      if (!actionPendingPull) return;
      if (actionState === "expanded" && actionBody.scrollTop > 0) {
        actionPendingPull = false;
        return;
      }
      if (actionShouldClaim(e.clientY - actionStartY)) {
        actionPendingPull = false;
        startActionDrag(actionStartY);
        moveActionDrag(e.clientY);
      }
    });

    window.addEventListener("mouseup", function () {
      actionPendingPull = false;
      if (actionDragging) finishActionDrag();
    });
  }

  var shopBtn = document.getElementById("dash-shop-btn");
  if (shopBtn) shopBtn.addEventListener("click", function () { openActionSheet("store"); });
  if (avatarBtn) avatarBtn.addEventListener("click", function () { openActionSheet("profile"); });

  var notifBtn = document.getElementById("dash-notif-btn");
  var notifBadge = document.getElementById("dash-notif-badge");
  var menuBtn = document.getElementById("dash-menu-btn");
  var actionNotifList = document.getElementById("action-notif-list");

  function setNotifBadge(count) {
    if (notifBadge) notifBadge.hidden = !count;
  }

  setNotifBadge(state.pending_friend_request_count || 0);

  function renderFriendRequests(requests) {
    actionNotifList.innerHTML = "";
    if (!requests.length) {
      var empty = document.createElement("div");
      empty.className = "uploads-list-empty";
      empty.textContent = "No notifications yet.";
      actionNotifList.appendChild(empty);
      return;
    }
    requests.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "friend-request-row";

      var name = document.createElement("span");
      name.className = "friend-request-name";
      name.textContent = (r.from_full_name || r.from_username || "Someone") + " wants to be friends";
      row.appendChild(name);

      var actions = document.createElement("div");
      actions.className = "friend-request-actions";

      var acceptBtn = document.createElement("button");
      acceptBtn.type = "button";
      acceptBtn.className = "friend-request-btn friend-request-accept";
      acceptBtn.textContent = "Accept";
      acceptBtn.addEventListener("click", function () { respondToFriendRequest(r.id, true, row); });

      var denyBtn = document.createElement("button");
      denyBtn.type = "button";
      denyBtn.className = "friend-request-btn friend-request-deny";
      denyBtn.textContent = "Deny";
      denyBtn.addEventListener("click", function () { respondToFriendRequest(r.id, false, row); });

      actions.appendChild(acceptBtn);
      actions.appendChild(denyBtn);
      row.appendChild(actions);
      actionNotifList.appendChild(row);
    });
  }

  function loadFriendRequests() {
    fetch("/api/friends/requests")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var requests = data.requests || [];
        renderFriendRequests(requests);
        setNotifBadge(requests.length);
      })
      .catch(function () {});
  }

  function respondToFriendRequest(id, accept, row) {
    fetch("/api/friends/requests/" + id + "/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept: accept }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok || !row) return;
        row.remove();
        var remaining = actionNotifList.querySelectorAll(".friend-request-row").length;
        if (!remaining) renderFriendRequests([]);
        setNotifBadge(remaining);
      })
      .catch(function () {});
  }

  if (notifBtn) notifBtn.addEventListener("click", function () { openActionSheet("notifications"); });
  if (menuBtn) menuBtn.addEventListener("click", function () { openActionSheet("options"); });

  var menuUploadBtn = document.getElementById("dash-menu-upload");
  if (menuUploadBtn) {
    menuUploadBtn.addEventListener("click", function () {
      var uploadsBtn = document.getElementById("profile-uploads-btn");
      if (uploadsBtn) uploadsBtn.click();
    });
  }

  var menuSupportBtn = document.getElementById("dash-menu-support");
  if (menuSupportBtn) {
    menuSupportBtn.addEventListener("click", function () {
      window.location.href = "mailto:support@employable.app";
    });
  }

  // ---------- Friends: invite by username / view accepted friends ----------

  var friendsOverlay = document.getElementById("friends-overlay");
  var friendsTabButtons = document.querySelectorAll("#friends-tabs .auth-tab");
  var friendsPanelInvite = document.getElementById("friends-tab-invite");
  var friendsPanelList = document.getElementById("friends-tab-list");
  var friendUsernameInput = document.getElementById("friend-username-input");
  var friendInviteError = document.getElementById("friend-invite-error");
  var friendInviteSuccess = document.getElementById("friend-invite-success");
  var friendInviteSendBtn = document.getElementById("friend-invite-send-btn");
  var friendsListEl = document.getElementById("friends-list");

  function switchFriendsTab(tab) {
    friendsTabButtons.forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.friendsTab === tab);
    });
    friendsPanelInvite.hidden = tab !== "invite";
    friendsPanelList.hidden = tab !== "list";
    if (tab === "list") loadFriendsList();
  }

  friendsTabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () { switchFriendsTab(btn.dataset.friendsTab); });
  });

  function loadFriendsList() {
    friendsListEl.innerHTML = "";
    fetch("/api/friends")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var friends = data.friends || [];
        if (!friends.length) {
          var empty = document.createElement("div");
          empty.className = "uploads-list-empty";
          empty.textContent = "No friends yet — invite someone by username.";
          friendsListEl.appendChild(empty);
          return;
        }
        friends.forEach(function (f) {
          var row = document.createElement("div");
          row.className = "uploads-list-item";
          var name = document.createElement("span");
          name.className = "uploads-list-item-name";
          name.textContent = (f.full_name || "").trim() || f.username;
          row.appendChild(name);
          friendsListEl.appendChild(row);
        });
      })
      .catch(function () {});
  }

  var menuInviteBtn = document.getElementById("dash-menu-invite");
  if (menuInviteBtn) {
    menuInviteBtn.addEventListener("click", function () {
      friendInviteError.hidden = true;
      friendInviteSuccess.hidden = true;
      friendUsernameInput.value = "";
      switchFriendsTab("invite");
      friendsOverlay.hidden = false;
    });
  }

  if (friendInviteSendBtn) {
    friendInviteSendBtn.addEventListener("click", function () {
      var username = friendUsernameInput.value.trim();
      friendInviteError.hidden = true;
      friendInviteSuccess.hidden = true;
      if (!username) {
        friendInviteError.textContent = "Enter a username.";
        friendInviteError.hidden = false;
        return;
      }
      friendInviteSendBtn.disabled = true;
      friendInviteSendBtn.textContent = "Sending…";
      fetch("/api/friends/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          friendInviteSendBtn.disabled = false;
          friendInviteSendBtn.textContent = "Send Request";
          if (data.ok) {
            friendInviteSuccess.textContent = "Friend request sent.";
            friendInviteSuccess.hidden = false;
            friendUsernameInput.value = "";
          } else {
            friendInviteError.textContent = data.error || "Couldn't send that request.";
            friendInviteError.hidden = false;
          }
        })
        .catch(function () {
          friendInviteSendBtn.disabled = false;
          friendInviteSendBtn.textContent = "Send Request";
          friendInviteError.textContent = "Something went wrong. Please try again.";
          friendInviteError.hidden = false;
        });
    });
  }

  var friendsCloseBtn = document.getElementById("friends-close-btn");
  if (friendsCloseBtn) {
    friendsCloseBtn.addEventListener("click", function () { friendsOverlay.hidden = true; });
  }

  // ---------- Profile screen ----------

  var profileNameEl = document.getElementById("profile-name");
  var profileUsernameEl = document.getElementById("profile-username");
  var profilePhoneEl = document.getElementById("profile-phone");
  var profileEmailEl = document.getElementById("profile-email");
  var profileAvatarImg = document.getElementById("profile-avatar-img");
  var profileAvatarDefault = document.getElementById("profile-avatar-default");

  function renderProfileCard() {
    var name = (profile.full_name || "").trim() || (profile.username || "").trim() || "Your Name";
    profileNameEl.textContent = name;
    var username = (profile.username || "").trim();
    profileUsernameEl.textContent = username ? "@" + username : "";
    if (profile.phone) {
      profilePhoneEl.textContent = profile.phone;
      profilePhoneEl.hidden = false;
    } else {
      profilePhoneEl.hidden = true;
    }
    if (profile.email) {
      profileEmailEl.textContent = profile.email;
      profileEmailEl.hidden = false;
    } else {
      profileEmailEl.hidden = true;
    }
    if (profile.avatar_url) {
      profileAvatarImg.src = profile.avatar_url;
      profileAvatarImg.hidden = false;
      profileAvatarDefault.style.display = "none";
    } else {
      profileAvatarImg.hidden = true;
      profileAvatarDefault.style.display = "";
    }
  }

  renderProfileCard();

  // ---------- Edit Profile ----------

  var editOverlay = document.getElementById("edit-profile-overlay");
  var editNameInput = document.getElementById("edit-profile-name");
  var editEmailInput = document.getElementById("edit-profile-email");
  var editPhoneInput = document.getElementById("edit-profile-phone");
  var editLocationInput = document.getElementById("edit-profile-location");
  var editErrorEl = document.getElementById("edit-profile-error");

  function openEditProfile() {
    editNameInput.value = profile.full_name || "";
    editEmailInput.value = profile.email || "";
    editPhoneInput.value = profile.phone || "";
    editLocationInput.value = profile.location || "";
    editErrorEl.hidden = true;
    editOverlay.hidden = false;
  }

  document.getElementById("profile-edit-btn").addEventListener("click", openEditProfile);
  document.getElementById("edit-profile-cancel-btn").addEventListener("click", function () { editOverlay.hidden = true; });

  document.getElementById("edit-profile-save-btn").addEventListener("click", function () {
    fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_name: editNameInput.value.trim(),
        email: editEmailInput.value.trim(),
        phone: editPhoneInput.value.trim(),
        location: editLocationInput.value.trim(),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          editErrorEl.textContent = data.error || "Couldn't save your changes.";
          editErrorEl.hidden = false;
          return;
        }
        profile = data.state.profile;
        renderProfileCard();
        usernameEl.textContent = firstName();
        editOverlay.hidden = true;
      })
      .catch(function () {
        editErrorEl.textContent = "Something went wrong. Please try again.";
        editErrorEl.hidden = false;
      });
  });

  // ---------- Uploads (documents ever uploaded to this account) ----------

  var uploadsOverlay = document.getElementById("uploads-overlay");
  var uploadsListEl = document.getElementById("uploads-list");

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function renderUploadsList() {
    var docs = state.documents || [];
    uploadsListEl.innerHTML = "";
    if (!docs.length) {
      var empty = document.createElement("div");
      empty.className = "uploads-list-empty";
      empty.textContent = "You haven't uploaded any documents yet.";
      uploadsListEl.appendChild(empty);
    } else {
      docs.forEach(function (d) {
        var row = document.createElement("div");
        row.className = "uploads-list-item";
        var name = document.createElement("span");
        name.className = "uploads-list-item-name";
        name.textContent = d.filename;
        var meta = document.createElement("span");
        meta.className = "uploads-list-item-meta";
        meta.textContent = formatBytes(d.file_size);
        row.appendChild(name);
        row.appendChild(meta);
        uploadsListEl.appendChild(row);
      });
    }
  }

  document.getElementById("profile-uploads-btn").addEventListener("click", function () {
    renderUploadsList();
    uploadsOverlay.hidden = false;
  });
  document.getElementById("uploads-close-btn").addEventListener("click", function () { uploadsOverlay.hidden = true; });

  // ---------- Uploads: "Upload More" adds documents without leaving this modal ----------

  var uploadsAddBtn = document.getElementById("uploads-add-btn");
  var uploadsAddInput = document.getElementById("uploads-add-input");
  var uploadsAddError = document.getElementById("uploads-add-error");

  uploadsAddBtn.addEventListener("click", function () { uploadsAddInput.click(); });

  uploadsAddInput.addEventListener("change", function () {
    var files = uploadsAddInput.files;
    if (!files || !files.length) return;
    uploadsAddError.hidden = true;
    uploadsAddBtn.disabled = true;
    uploadsAddBtn.textContent = "Uploading…";
    var formData = new FormData();
    Array.from(files).forEach(function (f) { formData.append("documents", f); });
    fetch("/api/upload", { method: "POST", body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        uploadsAddBtn.disabled = false;
        uploadsAddBtn.textContent = "Upload More";
        uploadsAddInput.value = "";
        if (data.ok) {
          if (data.state) {
            state.documents = data.state.documents;
            state.analysis = data.state.analysis;
            // Same re-render used at page load, so the gauge, the 3
            // primary rings, and the Full Breakdown all reflect the
            // freshly recomputed scores instead of going stale until
            // the next full page reload.
            renderDashboardScore(state.analysis);
          }
          if (data.warning) {
            uploadsAddError.textContent = data.warning;
            uploadsAddError.hidden = false;
          }
          renderUploadsList();
        } else {
          uploadsAddError.textContent = data.error || "Couldn't upload that file. Please try again.";
          uploadsAddError.hidden = false;
        }
      })
      .catch(function () {
        uploadsAddBtn.disabled = false;
        uploadsAddBtn.textContent = "Upload More";
        uploadsAddError.textContent = "Something went wrong. Please try again.";
        uploadsAddError.hidden = false;
      });
  });

  // ---------- Language (visual placeholder, not functional yet) ----------

  var LANGUAGES = [
    { flag: "🇬🇧", name: "English" },
    { flag: "🇿🇦", name: "Afrikaans" },
    { flag: "🇿🇦", name: "isiZulu" },
    { flag: "🇿🇦", name: "isiXhosa" },
    { flag: "🇿🇦", name: "Sesotho" },
    { flag: "🇫🇷", name: "Français" },
    { flag: "🇵🇹", name: "Português" },
    { flag: "🇪🇸", name: "Español" },
  ];

  var languageOverlay = document.getElementById("language-overlay");
  document.getElementById("profile-language-btn").addEventListener("click", function () {
    var listEl = document.getElementById("language-list");
    listEl.innerHTML = "";
    LANGUAGES.forEach(function (lang) {
      var row = document.createElement("div");
      row.className = "language-list-item";
      row.innerHTML = '<span class="language-list-flag">' + lang.flag + '</span><span>' + lang.name + '</span>';
      listEl.appendChild(row);
    });
    languageOverlay.hidden = false;
  });
  document.getElementById("language-close-btn").addEventListener("click", function () { languageOverlay.hidden = true; });

  // ---------- Location (opens Edit Profile, since that's where it lives) ----------

  document.getElementById("profile-location-btn").addEventListener("click", openEditProfile);

  // ---------- Subscription ----------

  var subscriptionOverlay = document.getElementById("subscription-overlay");
  var subscriptionStatusBadge = document.getElementById("subscription-status-badge");
  var subscriptionPriceEl = document.getElementById("subscription-price");
  var subscriptionTrialLine = document.getElementById("subscription-trial-line");
  var subscriptionBillingLine = document.getElementById("subscription-billing-line");

  document.getElementById("profile-subscription-btn").addEventListener("click", function () {
    var sub = state.subscription;
    if (!sub) {
      subscriptionStatusBadge.textContent = "Unavailable";
      subscriptionPriceEl.textContent = "R149 / month";
      subscriptionTrialLine.textContent = "Couldn't load your subscription details right now.";
      subscriptionBillingLine.textContent = "";
    } else {
      var inTrial = sub.status === "trial";
      subscriptionStatusBadge.textContent = inTrial ? "Free Trial" : "Active";
      subscriptionPriceEl.textContent = "R" + sub.price_zar + " / month";
      subscriptionTrialLine.textContent = inTrial
        ? sub.plan_name + " — your " + sub.trial_days + "-day free trial ends " + sub.trial_end + ". You won't be charged before then."
        : sub.plan_name + " — you're past your free trial and billed monthly.";
      subscriptionBillingLine.textContent = "Next billing date: " + sub.next_billing_date + " (billed on the " + sub.billing_day + (sub.billing_day === 1 ? "st" : sub.billing_day === 2 ? "nd" : sub.billing_day === 3 ? "rd" : "th") + " of every month).";
    }
    subscriptionOverlay.hidden = false;
  });
  document.getElementById("subscription-close-btn").addEventListener("click", function () { subscriptionOverlay.hidden = true; });
  subscriptionOverlay.addEventListener("click", function (e) {
    if (e.target === subscriptionOverlay) subscriptionOverlay.hidden = true;
  });

  // ---------- Log out ----------

  document.getElementById("profile-logout-btn").addEventListener("click", function () {
    window.location.href = "/logout";
  });

  // ---------- Delete account ----------

  var deleteOverlay = document.getElementById("delete-account-overlay");
  var deleteErrorEl = document.getElementById("delete-account-error");

  document.getElementById("profile-delete-btn").addEventListener("click", function () {
    deleteErrorEl.hidden = true;
    deleteOverlay.hidden = false;
  });
  document.getElementById("delete-account-cancel-btn").addEventListener("click", function () { deleteOverlay.hidden = true; });

  document.getElementById("delete-account-confirm-btn").addEventListener("click", function () {
    var btn = document.getElementById("delete-account-confirm-btn");
    btn.disabled = true;
    fetch("/api/account/delete", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          window.location.href = "/";
        } else {
          deleteErrorEl.textContent = data.error || "Couldn't delete your account. Please try again.";
          deleteErrorEl.hidden = false;
          btn.disabled = false;
        }
      })
      .catch(function () {
        deleteErrorEl.textContent = "Something went wrong. Please try again.";
        deleteErrorEl.hidden = false;
        btn.disabled = false;
      });
  });

  // ---------- Avatar upload + basic pan/zoom crop ----------

  var avatarInput = document.getElementById("profile-avatar-input");
  var cropOverlay = document.getElementById("crop-overlay");
  var cropCanvas = document.getElementById("crop-canvas");
  var cropCtx = cropCanvas.getContext("2d");
  var cropZoomSlider = document.getElementById("crop-zoom");
  var CROP_SIZE = 320;

  var cropImg = null;
  var cropBaseScale = 1;
  var cropOffsetX = 0;
  var cropOffsetY = 0;
  var cropDragging = false;
  var cropDragStartX = 0;
  var cropDragStartY = 0;
  var cropDragOffsetStartX = 0;
  var cropDragOffsetStartY = 0;

  document.getElementById("profile-avatar-btn").addEventListener("click", function () {
    avatarInput.click();
  });

  avatarInput.addEventListener("change", function () {
    var file = avatarInput.files[0];
    if (!file) return;
    var img = new Image();
    img.onload = function () {
      cropImg = img;
      cropBaseScale = Math.max(CROP_SIZE / img.width, CROP_SIZE / img.height);
      cropZoomSlider.value = "1";
      centerCropImage();
      drawCrop();
      cropOverlay.hidden = false;
    };
    img.src = URL.createObjectURL(file);
  });

  function centerCropImage() {
    var scale = cropBaseScale * parseFloat(cropZoomSlider.value);
    var drawW = cropImg.width * scale;
    var drawH = cropImg.height * scale;
    cropOffsetX = (CROP_SIZE - drawW) / 2;
    cropOffsetY = (CROP_SIZE - drawH) / 2;
  }

  function clampCropOffsets() {
    var scale = cropBaseScale * parseFloat(cropZoomSlider.value);
    var drawW = cropImg.width * scale;
    var drawH = cropImg.height * scale;
    cropOffsetX = Math.min(0, Math.max(CROP_SIZE - drawW, cropOffsetX));
    cropOffsetY = Math.min(0, Math.max(CROP_SIZE - drawH, cropOffsetY));
  }

  function drawCrop() {
    if (!cropImg) return;
    var scale = cropBaseScale * parseFloat(cropZoomSlider.value);
    var drawW = cropImg.width * scale;
    var drawH = cropImg.height * scale;
    cropCtx.clearRect(0, 0, CROP_SIZE, CROP_SIZE);
    cropCtx.drawImage(cropImg, cropOffsetX, cropOffsetY, drawW, drawH);
  }

  cropZoomSlider.addEventListener("input", function () {
    clampCropOffsets();
    drawCrop();
  });

  cropCanvas.addEventListener("pointerdown", function (e) {
    cropDragging = true;
    cropDragStartX = e.clientX;
    cropDragStartY = e.clientY;
    cropDragOffsetStartX = cropOffsetX;
    cropDragOffsetStartY = cropOffsetY;
    try { cropCanvas.setPointerCapture(e.pointerId); } catch (err) {}
  });
  cropCanvas.addEventListener("pointermove", function (e) {
    if (!cropDragging) return;
    cropOffsetX = cropDragOffsetStartX + (e.clientX - cropDragStartX);
    cropOffsetY = cropDragOffsetStartY + (e.clientY - cropDragStartY);
    clampCropOffsets();
    drawCrop();
  });
  function endCropDrag() { cropDragging = false; }
  cropCanvas.addEventListener("pointerup", endCropDrag);
  cropCanvas.addEventListener("pointercancel", endCropDrag);

  document.getElementById("crop-cancel-btn").addEventListener("click", function () {
    cropOverlay.hidden = true;
    avatarInput.value = "";
    cropImg = null;
  });

  document.getElementById("crop-upload-btn").addEventListener("click", function () {
    var uploadBtn = document.getElementById("crop-upload-btn");
    uploadBtn.disabled = true;
    cropCanvas.toBlob(function (blob) {
      var formData = new FormData();
      formData.append("photo", blob, "avatar.jpg");
      fetch("/api/profile/photo", { method: "POST", body: formData })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          uploadBtn.disabled = false;
          if (data.ok) {
            profile = data.state.profile;
            renderProfileCard();
            if (profile.avatar_url) {
              var headerImg = document.getElementById("dash-avatar-img");
              headerImg.src = profile.avatar_url;
              headerImg.hidden = false;
              document.getElementById("dash-avatar-default").style.display = "none";
            }
            cropOverlay.hidden = true;
            avatarInput.value = "";
            cropImg = null;
          } else {
            alert(data.error || "Couldn't upload that photo. Please try again.");
          }
        })
        .catch(function () {
          uploadBtn.disabled = false;
          alert("Something went wrong. Please try again.");
        });
    }, "image/jpeg", 0.9);
  });

  // ---------- AI chat ----------

  var chatMessagesEl = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatHistory = []; // [{role, text}]
  var chatConversationId = null;
  var chatSending = false;

  function scrollChatToBottom() {
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // The AI is asked to mark bold text with **double asterisks** and
  // nothing else (see the FORMATTING section of its system prompt) --
  // turn that into real bold instead of showing the literal asterisks.
  // Escaped first so nothing in a reply (or a user's own message, if
  // this is ever reused for that) can inject markup.
  function formatChatText(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function appendChatMessage(role, text) {
    var el = document.createElement("div");
    el.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-bot");
    if (role === "user") {
      el.textContent = text;
    } else {
      el.innerHTML = formatChatText(text);
    }
    chatMessagesEl.appendChild(el);
    scrollChatToBottom();
    return el;
  }

  // Fast character-reveal effect for new AI replies only (restored
  // history and the user's own messages appear instantly) — bounded to
  // ~40 ticks total regardless of reply length, so a long reply still
  // finishes in under a second instead of dragging on.
  function appendChatMessageTyped(text) {
    var el = document.createElement("div");
    el.className = "chat-msg chat-msg-bot";
    chatMessagesEl.appendChild(el);
    var totalTicks = 40;
    var chunkSize = Math.max(1, Math.ceil(text.length / totalTicks));
    var i = 0;
    var interval = setInterval(function () {
      i = Math.min(text.length, i + chunkSize);
      if (i >= text.length) {
        // Swap to the fully-formatted version only once the reveal is
        // done — mid-reveal, a partially-typed "**word" would otherwise
        // show a dangling, unclosed tag for a moment.
        el.innerHTML = formatChatText(text);
        clearInterval(interval);
      } else {
        el.textContent = text.slice(0, i);
      }
      scrollChatToBottom();
    }, 15);
    return el;
  }

  // Real, distinguishable titles (from the first thing the user actually
  // said) instead of a single repeated generic string — otherwise every
  // entry in the history list would look identical.
  function conversationTitleFromHistory(history) {
    var firstUser = history.filter(function (m) { return m.role === "user"; })[0];
    if (!firstUser) return "New conversation";
    var text = firstUser.text.trim().replace(/\s+/g, " ");
    return text.length > 60 ? text.slice(0, 60) + "…" : text;
  }

  function saveConversation() {
    fetch("/api/chat/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: chatConversationId,
        title: conversationTitleFromHistory(chatHistory),
        messages: chatHistory.map(function (m) { return { role: m.role, text: m.text }; }),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) chatConversationId = data.conversation_id;
      })
      .catch(function () {});
  }

  function sendChatMessage() {
    var text = chatInput.value.trim();
    if (!text || chatSending) return;
    chatInput.value = "";
    appendChatMessage("user", text);
    chatHistory.push({ role: "user", text: text });

    var typingEl = document.createElement("div");
    typingEl.className = "chat-msg-typing";
    typingEl.textContent = "Typing…";
    chatMessagesEl.appendChild(typingEl);
    scrollChatToBottom();

    chatSending = true;
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        typingEl.remove();
        if (data.ok) {
          appendChatMessageTyped(data.reply);
          chatHistory.push({ role: "assistant", text: data.reply });
          saveConversation();
        } else {
          appendChatMessageTyped("Sorry, something went wrong there. Try again?");
        }
      })
      .catch(function () {
        typingEl.remove();
        appendChatMessageTyped("Connection error — please try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  chatSendBtn.addEventListener("click", sendChatMessage);
  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendChatMessage();
  });

  // Restore the most recent conversation on load, so signing out and
  // back in (or just reloading) doesn't lose chat history — same
  // "don't lose what was already there" principle as document caching.
  fetch("/api/chat/conversations")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.ok || !data.conversations || !data.conversations.length) return;
      var latest = data.conversations[0];
      chatConversationId = latest.id;
      return fetch("/api/chat/conversations/" + latest.id)
        .then(function (r) { return r.json(); })
        .then(function (msgData) {
          if (!msgData.ok || !msgData.messages || !msgData.messages.length) return;
          chatMessagesEl.innerHTML = "";
          chatHistory = [];
          msgData.messages.forEach(function (m) {
            appendChatMessage(m.role === "user" ? "user" : "assistant", m.text);
            chatHistory.push({ role: m.role, text: m.text });
          });
        });
    })
    .catch(function () {});

  // ---------- Chat history panel ----------

  function formatRelativeTime(isoString) {
    var then = new Date(isoString).getTime();
    if (isNaN(then)) return "";
    var diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (diffSec < 60) return "Just now";
    var diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return diffMin + (diffMin === 1 ? " minute ago" : " minutes ago");
    var diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return diffHr + (diffHr === 1 ? " hour ago" : " hours ago");
    var diffDay = Math.round(diffHr / 24);
    if (diffDay < 7) return diffDay + (diffDay === 1 ? " day ago" : " days ago");
    var diffWeek = Math.round(diffDay / 7);
    if (diffWeek < 5) return diffWeek + (diffWeek === 1 ? " week ago" : " weeks ago");
    var diffMonth = Math.round(diffDay / 30);
    return diffMonth + (diffMonth === 1 ? " month ago" : " months ago");
  }

  var chatHistoryOverlay = document.getElementById("chat-history-overlay");
  var chatHistoryBtn = document.getElementById("chat-history-btn");
  var chatHistoryCloseBtn = document.getElementById("chat-history-close-btn");
  var chatHistoryNewBtn = document.getElementById("chat-history-new-btn");
  var chatHistoryNewBtnFab = document.getElementById("chat-history-new-btn-fab");
  var chatHistoryListEl = document.getElementById("chat-history-list");
  var chatHistorySearchInput = document.getElementById("chat-history-search");
  var chatHistoryConversations = [];

  function renderChatHistoryList(filterText) {
    var q = (filterText || "").trim().toLowerCase();
    var items = chatHistoryConversations.filter(function (c) {
      return !q || (c.title || "").toLowerCase().indexOf(q) !== -1;
    });
    chatHistoryListEl.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "chat-history-empty";
      empty.textContent = q ? "No chats match that search." : "No conversations yet.";
      chatHistoryListEl.appendChild(empty);
      return;
    }
    items.forEach(function (c) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "chat-history-item" + (c.id === chatConversationId ? " active" : "");
      var title = document.createElement("span");
      title.className = "chat-history-item-title";
      title.textContent = c.title || "New conversation";
      var time = document.createElement("span");
      time.className = "chat-history-item-time";
      time.textContent = formatRelativeTime(c.updated_at || c.created_at);
      row.appendChild(title);
      row.appendChild(time);
      row.addEventListener("click", function () { loadConversation(c.id); });
      chatHistoryListEl.appendChild(row);
    });
  }

  function loadConversation(convId) {
    fetch("/api/chat/conversations/" + convId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        chatConversationId = convId;
        chatMessagesEl.innerHTML = "";
        chatHistory = [];
        (data.messages || []).forEach(function (m) {
          appendChatMessage(m.role === "user" ? "user" : "assistant", m.text);
          chatHistory.push({ role: m.role, text: m.text });
        });
        chatHistoryOverlay.hidden = true;
        scrollChatToBottom();
      })
      .catch(function () {});
  }

  function openChatHistory() {
    chatHistorySearchInput.value = "";
    fetch("/api/chat/conversations")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        chatHistoryConversations = (data.ok && data.conversations) || [];
        renderChatHistoryList("");
      })
      .catch(function () {
        chatHistoryConversations = [];
        renderChatHistoryList("");
      });
    chatHistoryOverlay.hidden = false;
  }

  chatHistoryBtn.addEventListener("click", openChatHistory);
  chatHistoryCloseBtn.addEventListener("click", function () { chatHistoryOverlay.hidden = true; });
  chatHistorySearchInput.addEventListener("input", function () { renderChatHistoryList(chatHistorySearchInput.value); });

  function startNewChat() {
    chatConversationId = null;
    chatHistory = [];
    chatMessagesEl.innerHTML = "";
    appendChatMessage("assistant", "Hey! I'm here to help with the app, your documents, or anything about getting hired. What's up?");
    chatHistoryOverlay.hidden = true;
  }

  chatHistoryNewBtn.addEventListener("click", startNewChat);
  if (chatHistoryNewBtnFab) chatHistoryNewBtnFab.addEventListener("click", startNewChat);

  // ---------- CV Workshop: Google-Docs-style editor ----------

  var cvPage = document.getElementById("cv-page");
  var CV_DRAFT_KEY = "employable_cv_draft";
  var CV_MARGINS_KEY = "employable_cv_margins";
  var cvLoaded = false;

  // ---------- CV Workshop: toolbar dropdowns (Styles / color / align / Insert / More) ----------

  // Menus get reparented to <body> when opened (see positionCvDropdown),
  // so their element references are captured up front here rather than
  // re-queried from `dd` each time -- once moved, `dd.querySelector(...)`
  // would no longer find them.
  var cvAllMenus = Array.prototype.slice.call(document.querySelectorAll("#cv-toolbar .cv-dd-menu"));

  function closeAllCvDropdowns() {
    cvAllMenus.forEach(function (menu) { menu.hidden = true; });
  }

  function positionCvDropdown(toggle, menu) {
    // Moved to <body> so its "position: fixed" isn't relative to some
    // clipping/positioned ancestor, then placed from the toggle's live
    // rect -- necessary since the toolbar scrolls horizontally and the
    // menu must still land directly under whichever button opened it.
    document.body.appendChild(menu);
    menu.hidden = false;
    var rect = toggle.getBoundingClientRect();
    var menuRect = menu.getBoundingClientRect();
    var left = rect.left;
    if (left + menuRect.width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menuRect.width - 8);
    }
    var top = rect.bottom + 6;
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuRect.height - 6);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  Array.prototype.slice.call(document.querySelectorAll("#cv-toolbar .cv-dd")).forEach(function (dd) {
    var toggle = dd.querySelector(".cv-dd-toggle");
    var menu = dd.querySelector(".cv-dd-menu");
    if (!toggle || !menu) return;
    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = menu.hidden;
      closeAllCvDropdowns();
      if (willOpen) positionCvDropdown(toggle, menu);
    });
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest("#cv-toolbar .cv-dd")) {
      closeAllCvDropdowns();
    }
  });

  // Toolbar buttons apply document.execCommand on the current
  // selection. mousedown on the button (not click) would steal focus
  // from the editor before the command runs, wiping out the user's
  // text selection — preventing default on mousedown keeps the
  // selection intact so bold/italic/etc. actually apply to it.
  document.querySelectorAll("#cv-toolbar [data-cmd]").forEach(function (btn) {
    btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
    btn.addEventListener("click", function () {
      cvPage.focus();
      document.execCommand(btn.dataset.cmd, false, btn.dataset.value || undefined);
      closeAllCvDropdowns();
    });
  });

  // Styles dropdown: paragraph vs heading level
  var cvStylesMenu = document.getElementById("cv-styles-menu");
  var cvStylesLabel = document.getElementById("cv-styles-label");
  var CV_STYLE_LABELS = { p: "Normal", h1: "Title", h2: "Heading 1", h3: "Heading 2" };

  cvStylesMenu.querySelectorAll("[data-style]").forEach(function (btn) {
    btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
    btn.addEventListener("click", function () {
      cvPage.focus();
      document.execCommand("formatBlock", false, "<" + btn.dataset.style + ">");
      cvStylesLabel.textContent = CV_STYLE_LABELS[btn.dataset.style] || "Normal";
      closeAllCvDropdowns();
    });
  });

  // Text color and highlight color swatch grids
  var CV_TEXT_COLORS = ["#000000", "#434343", "#666666", "#999999", "#b7b7b7",
    "#e03131", "#f08c00", "#2f9e44", "#1971c2", "#7048e8", "#e64980", "#0c8599"];
  var CV_HIGHLIGHT_COLORS = ["#ffd8a8", "#ffec99", "#b2f2bb", "#a5d8ff", "#eebefa", "#ffc9c9", "#d0bfff"];

  var cvTextColorMenu = document.getElementById("cv-textcolor-menu");
  var cvTextColorSwatch = document.getElementById("cv-textcolor-swatch");

  CV_TEXT_COLORS.forEach(function (color) {
    var b = document.createElement("button");
    b.type = "button";
    b.style.background = color;
    b.title = color;
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function () {
      cvPage.focus();
      document.execCommand("foreColor", false, color);
      cvTextColorSwatch.style.background = color;
      closeAllCvDropdowns();
    });
    cvTextColorMenu.appendChild(b);
  });

  var cvHighlightMenu = document.getElementById("cv-highlight-menu");

  function applyCvHighlight(color) {
    cvPage.focus();
    var ok = false;
    try { ok = document.execCommand("hiliteColor", false, color); } catch (e) { ok = false; }
    if (!ok) {
      try { document.execCommand("backColor", false, color); } catch (e) {}
    }
  }

  var cvHighlightNoneBtn = document.createElement("button");
  cvHighlightNoneBtn.type = "button";
  cvHighlightNoneBtn.className = "cv-color-none";
  cvHighlightNoneBtn.textContent = "✕";
  cvHighlightNoneBtn.title = "None";
  cvHighlightNoneBtn.addEventListener("mousedown", function (e) { e.preventDefault(); });
  cvHighlightNoneBtn.addEventListener("click", function () {
    applyCvHighlight("transparent");
    closeAllCvDropdowns();
  });
  cvHighlightMenu.appendChild(cvHighlightNoneBtn);

  CV_HIGHLIGHT_COLORS.forEach(function (color) {
    var b = document.createElement("button");
    b.type = "button";
    b.style.background = color;
    b.title = color;
    b.addEventListener("mousedown", function (e) { e.preventDefault(); });
    b.addEventListener("click", function () {
      applyCvHighlight(color);
      closeAllCvDropdowns();
    });
    cvHighlightMenu.appendChild(b);
  });

  // Insert menu: link / horizontal line / table
  document.getElementById("cv-insert-link-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    var url = prompt("Link URL:", "https://");
    if (!url) return;
    cvPage.focus();
    document.execCommand("createLink", false, url);
  });

  document.getElementById("cv-insert-hr-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    cvPage.focus();
    document.execCommand("insertHorizontalRule", false);
  });

  document.getElementById("cv-insert-table-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    cvPage.focus();
    var cell = 'style="border:1px solid #999;padding:6px;min-width:60px;"';
    var row = "<tr><td " + cell + ">&nbsp;</td><td " + cell + ">&nbsp;</td></tr>";
    document.execCommand("insertHTML", false,
      '<table style="width:100%;border-collapse:collapse;">' + row + row + "</table><p><br></p>");
  });

  // More menu: print preview / find & replace / word count / page setup / spell check
  document.getElementById("cv-print-preview-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    // Open the tab synchronously (before the fetch resolves) so mobile
    // popup blockers don't treat it as an unrequested popup; PDFs also
    // don't render reliably embedded in an iframe on mobile, so this
    // hands off to the device's native PDF viewer instead (same pattern
    // used for the Android onboarding flow).
    var win = window.open("", "_blank");
    var margins = null;
    try { margins = localStorage.getItem(CV_MARGINS_KEY); } catch (e) {}
    fetch("/api/cv-download/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cv_html: cvPage.innerHTML, margins: margins || "normal" }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Preview failed");
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        if (win) {
          win.location.href = url;
        } else {
          window.open(url, "_blank");
        }
      })
      .catch(function () {
        if (win) win.close();
        alert("Couldn't generate the preview. Please try again.");
      });
  });

  var cvFindReplaceBar = document.getElementById("cv-findreplace-bar");
  var cvFindInput = document.getElementById("cv-find-input");
  var cvReplaceInput = document.getElementById("cv-replace-input");
  var cvFindReplaceStatus = document.getElementById("cv-findreplace-status");

  // Built on Range/Selection (not window.find, which Chrome doesn't
  // support) so it works consistently in the Android WebView and on iOS.
  function cvTextNodes() {
    var walker = document.createTreeWalker(cvPage, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [];
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    return nodes;
  }

  function cvFindRanges(term) {
    if (!term) return [];
    var lower = term.toLowerCase();
    var ranges = [];
    cvTextNodes().forEach(function (node) {
      var textLower = node.textContent.toLowerCase();
      var idx = textLower.indexOf(lower);
      while (idx !== -1) {
        var range = document.createRange();
        range.setStart(node, idx);
        range.setEnd(node, idx + term.length);
        ranges.push(range);
        idx = textLower.indexOf(lower, idx + term.length);
      }
    });
    return ranges;
  }

  var cvFindIndex = -1;

  function cvUpdateFindStatus() {
    var term = cvFindInput.value;
    if (!term) { cvFindReplaceStatus.textContent = ""; return; }
    var count = cvFindRanges(term).length;
    cvFindReplaceStatus.textContent = count ? ((cvFindIndex + 1) + " of " + count) : "No matches";
  }

  function cvFindNext() {
    var ranges = cvFindRanges(cvFindInput.value);
    if (!ranges.length) { cvFindIndex = -1; cvUpdateFindStatus(); return; }
    cvFindIndex = (cvFindIndex + 1) % ranges.length;
    var range = ranges[cvFindIndex];
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    var el = range.startContainer.parentElement;
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
    cvUpdateFindStatus();
  }

  document.getElementById("cv-find-next-btn").addEventListener("click", cvFindNext);
  cvFindInput.addEventListener("input", function () { cvFindIndex = -1; cvUpdateFindStatus(); });
  cvFindInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") cvFindNext();
  });

  document.getElementById("cv-replace-btn").addEventListener("click", function () {
    var term = cvFindInput.value;
    var replace = cvReplaceInput.value;
    if (!term) return;
    var ranges = cvFindRanges(term);
    if (!ranges.length) { cvUpdateFindStatus(); return; }
    var range = ranges[cvFindIndex >= 0 ? cvFindIndex : 0];
    range.deleteContents();
    range.insertNode(document.createTextNode(replace));
    cvPage.normalize();
    try { localStorage.setItem(CV_DRAFT_KEY, cvPage.innerHTML); } catch (e) {}
    cvFindIndex = -1;
    cvUpdateFindStatus();
  });

  document.getElementById("cv-replace-all-btn").addEventListener("click", function () {
    var term = cvFindInput.value;
    var replace = cvReplaceInput.value;
    if (!term) return;
    var ranges = cvFindRanges(term);
    // Replace back-to-front so earlier ranges' offsets stay valid.
    for (var i = ranges.length - 1; i >= 0; i--) {
      ranges[i].deleteContents();
      ranges[i].insertNode(document.createTextNode(replace));
    }
    cvPage.normalize();
    cvFindReplaceStatus.textContent = ranges.length ? ("Replaced " + ranges.length) : "No matches";
    try { localStorage.setItem(CV_DRAFT_KEY, cvPage.innerHTML); } catch (e) {}
    cvFindIndex = -1;
  });

  document.getElementById("cv-findreplace-toggle-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    cvFindReplaceBar.hidden = !cvFindReplaceBar.hidden;
    if (!cvFindReplaceBar.hidden) cvFindInput.focus();
  });

  document.getElementById("cv-findreplace-close-btn").addEventListener("click", function () {
    cvFindReplaceBar.hidden = true;
  });

  document.getElementById("cv-wordcount-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    var text = (cvPage.innerText || "").trim();
    var words = text ? text.split(/\s+/).length : 0;
    var chars = text.length;
    var charsNoSpaces = text.replace(/\s/g, "").length;
    document.getElementById("cv-wordcount-body").textContent =
      words + " words, " + chars + " characters, " + charsNoSpaces + " characters (no spaces).";
    document.getElementById("cv-wordcount-overlay").hidden = false;
  });

  document.getElementById("cv-wordcount-close-btn").addEventListener("click", function () {
    document.getElementById("cv-wordcount-overlay").hidden = true;
  });

  var cvPageSetupOptions = document.querySelectorAll(".cv-pagesetup-option");

  function cvApplyMargin(margin) {
    cvPageSetupOptions.forEach(function (b) {
      b.classList.toggle("is-selected", b.dataset.margin === margin);
    });
    var pad = margin === "narrow" ? "28px 24px" : margin === "wide" ? "64px 60px" : "48px 44px";
    document.getElementById("cv-page").style.padding = pad;
  }

  (function () {
    var saved = null;
    try { saved = localStorage.getItem(CV_MARGINS_KEY); } catch (e) {}
    cvApplyMargin(saved || "normal");
  })();

  document.getElementById("cv-pagesetup-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    document.getElementById("cv-pagesetup-overlay").hidden = false;
  });

  document.getElementById("cv-pagesetup-close-btn").addEventListener("click", function () {
    document.getElementById("cv-pagesetup-overlay").hidden = true;
  });

  cvPageSetupOptions.forEach(function (btn) {
    btn.addEventListener("click", function () {
      cvApplyMargin(btn.dataset.margin);
      try { localStorage.setItem(CV_MARGINS_KEY, btn.dataset.margin); } catch (e) {}
      document.getElementById("cv-pagesetup-overlay").hidden = true;
    });
  });

  var cvSpellcheckCheck = document.getElementById("cv-spellcheck-check");
  var CV_SPELLCHECK_KEY = "employable_cv_spellcheck";

  function cvApplySpellcheck(on) {
    cvPage.spellcheck = on;
    // Re-mount so Chrome/Safari actually re-evaluate the spellcheck attribute.
    var parent = cvPage.parentNode;
    parent.removeChild(cvPage);
    parent.appendChild(cvPage);
    cvSpellcheckCheck.classList.toggle("is-on", on);
  }

  (function () {
    var saved = null;
    try { saved = localStorage.getItem(CV_SPELLCHECK_KEY); } catch (e) {}
    cvApplySpellcheck(saved === "on");
  })();

  document.getElementById("cv-spellcheck-toggle-btn").addEventListener("click", function () {
    closeAllCvDropdowns();
    var on = !cvSpellcheckCheck.classList.contains("is-on");
    cvApplySpellcheck(on);
    try { localStorage.setItem(CV_SPELLCHECK_KEY, on ? "on" : "off"); } catch (e) {}
  });

  function loadCvContent() {
    if (cvLoaded) return;
    cvLoaded = true;
    var saved = null;
    try { saved = localStorage.getItem(CV_DRAFT_KEY); } catch (e) {}
    if (saved) {
      cvPage.innerHTML = saved;
      return;
    }
    fetch("/api/cv-text")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        cvPage.innerHTML = data.html || "<p><br></p>";
      })
      .catch(function () {
        cvPage.innerHTML = "<p><br></p>";
      });
  }

  var cvSaveTimeout = null;
  cvPage.addEventListener("input", function () {
    clearTimeout(cvSaveTimeout);
    cvSaveTimeout = setTimeout(function () {
      try { localStorage.setItem(CV_DRAFT_KEY, cvPage.innerHTML); } catch (e) {}
    }, 400);
  });

  // ---------- CV Workshop: AI-assisted editing ----------

  var cvAiInput = document.getElementById("cv-ai-input");
  var cvAiSendBtn = document.getElementById("cv-ai-send-btn");
  var cvAiSending = false;

  function sendCvAiInstruction() {
    var instruction = cvAiInput.value.trim();
    if (!instruction || cvAiSending) return;
    cvAiSending = true;
    cvAiSendBtn.disabled = true;
    cvAiSendBtn.textContent = "…";
    fetch("/api/cv-edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction: instruction, cv_html: cvPage.innerHTML }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.updated_html) {
          cvPage.innerHTML = data.updated_html;
          try { localStorage.setItem(CV_DRAFT_KEY, cvPage.innerHTML); } catch (e) {}
          cvAiInput.value = "";
        } else {
          alert(data.error || "Couldn't apply that edit. Please try again.");
        }
      })
      .catch(function () {
        alert("Something went wrong. Please try again.");
      })
      .finally(function () {
        cvAiSending = false;
        cvAiSendBtn.disabled = false;
        cvAiSendBtn.textContent = "Send";
      });
  }

  cvAiSendBtn.addEventListener("click", sendCvAiInstruction);
  cvAiInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendCvAiInstruction();
  });

  // ---------- CV Workshop: download as PDF / Word ----------

  var cvDownloadBtn = document.getElementById("cv-download-btn");
  var cvDownloadMenu = document.getElementById("cv-download-menu");

  cvDownloadBtn.addEventListener("click", function () {
    cvDownloadMenu.hidden = !cvDownloadMenu.hidden;
  });

  document.addEventListener("click", function (e) {
    if (!cvDownloadMenu.hidden && e.target !== cvDownloadBtn && !cvDownloadMenu.contains(e.target) && !cvDownloadBtn.contains(e.target)) {
      cvDownloadMenu.hidden = true;
    }
  });

  function downloadCv(format) {
    var margins = null;
    try { margins = localStorage.getItem(CV_MARGINS_KEY); } catch (e) {}
    fetch("/api/cv-download/" + format, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cv_html: cvPage.innerHTML, margins: margins || "normal" }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Download failed");
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "my-cv." + (format === "docx" ? "docx" : "pdf");
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        cvDownloadMenu.hidden = true;
      })
      .catch(function () {
        alert("Couldn't download right now. Please try again.");
      });
  }

  document.getElementById("cv-download-pdf-btn").addEventListener("click", function () { downloadCv("pdf"); });
  document.getElementById("cv-download-docx-btn").addEventListener("click", function () { downloadCv("docx"); });

  // ---------- Store: Mobile = real Android APK, Desktop = PWA install ----------
  // Mobile gets an actual installable Android app — a Trusted Web
  // Activity (see android/) built and signed by
  // .github/workflows/build-android-apk.yml, published as a stable
  // GitHub Release download. Desktop still uses the real,
  // standards-based browser install flow (no desktop .exe build
  // pipeline exists, and the PWA install prompt is the correct native
  // equivalent there).
  //
  // Downloaded through our own domain (/download/android, which
  // proxies the GitHub release asset server-side) rather than linking
  // straight to github.com — a direct github.com link, clicked from
  // inside the installed app, counts as navigating outside the app's
  // verified origin, which hands the whole flow off to an external
  // browser tab instead of just downloading in place.
  var ANDROID_APK_URL = "/download/android";

  function isStandaloneApp() {
    return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  }

  function isIOSDevice() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  function handleMobileDownloadClick() {
    var note = document.getElementById("install-note");
    if (isIOSDevice()) {
      // No .ipa side-loading equivalent exists on iOS outside the App
      // Store/TestFlight — the real native-feeling install there is
      // still "Add to Home Screen," exactly like the old PWA flow.
      note.textContent = "On iPhone/iPad: tap the Share icon in Safari, then \"Add to Home Screen.\"";
      note.hidden = false;
      return;
    }
    // Triggered through a throwaway <a download> link rather than a
    // full page navigation, so the page itself never jumps while the
    // download starts.
    var a = document.createElement("a");
    a.href = ANDROID_APK_URL;
    a.download = "employable.apk";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Android shows an "Unknown apps"/Play Protect warning for any APK
    // that isn't from the Play Store, regardless of the app itself —
    // that's a real, unremovable OS security check, not a bug. Telling
    // people upfront that it's expected turns a scary-looking dead end
    // into an anticipated extra tap.
    note.textContent = "Downloading the Android app… Android will warn that it's from outside the Play Store — that's expected for any app installed this way. Open the file, then tap \"Install anyway\" / \"Install without scanning\" to continue.";
    note.hidden = false;
  }

  function handleDesktopInstallClick() {
    var note = document.getElementById("install-note");
    if (isStandaloneApp()) {
      note.textContent = "You're already using the installed app — nothing more to download.";
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
  }

  var installMobileBtn = document.getElementById("install-mobile-btn");
  var installDesktopBtn = document.getElementById("install-desktop-btn");
  if (installMobileBtn) installMobileBtn.addEventListener("click", handleMobileDownloadClick);
  if (installDesktopBtn) installDesktopBtn.addEventListener("click", handleDesktopInstallClick);

  // ---------- Shop: "coming soon" info popup ----------
  var shopComingSoonInfoBtn = document.getElementById("shop-coming-soon-info-btn");
  var shopComingSoonOverlay = document.getElementById("shop-coming-soon-overlay");
  var shopComingSoonCloseBtn = document.getElementById("shop-coming-soon-close-btn");
  if (shopComingSoonInfoBtn) {
    shopComingSoonInfoBtn.addEventListener("click", function () {
      shopComingSoonOverlay.hidden = false;
    });
  }
  if (shopComingSoonCloseBtn) {
    shopComingSoonCloseBtn.addEventListener("click", function () {
      shopComingSoonOverlay.hidden = true;
    });
  }

  // ---------- Draggable bottom sheet (dashboard home view) ----------
  // Collapsed rest position is measured from .dash-score-header's real
  // rendered height rather than a hardcoded number, so it stays correct
  // regardless of font size, safe-area insets, or content changes.
  //
  // Moved purely via `transform: translateY(...)` (compositor-only,
  // no layout/paint per frame) instead of animating `top`, so dragging
  // stays smooth even on slower devices.
  //
  // Two ways to drag:
  //  1. The handle -- always draggable, regardless of state.
  //  2. Anywhere in the sheet's body -- while collapsed, its scroll is
  //     disabled entirely (see .is-scroll-locked), so ANY clear drag
  //     there unambiguously means "move the sheet"; while expanded,
  //     normal scrolling takes over, and only pulling down once
  //     already scrolled to the very top collapses it again.
  //
  // The snap-to decision at release uses a deliberately low threshold
  // (SNAP_THRESHOLD) -- a small drag commits fully open or closed
  // rather than needing to cross halfway, so a tiny swipe is enough.
  (function () {
    var sheet = document.getElementById("dash-sheet");
    var handle = document.getElementById("dash-sheet-handle");
    var body = document.querySelector(".dash-sheet-body");
    var scoreHeader = document.querySelector(".dash-score-header");
    var tabbar = document.getElementById("tabbar");
    if (!sheet || !handle || !body || !scoreHeader) return;

    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var CLAIM_THRESHOLD = 6; // px of movement before a body touch commits to a drag (vs. a tap on a row)
    // Absolute px, not a fraction of the sheet's range -- a percentage
    // would need a bigger physical swipe on a taller screen to mean
    // the same thing, which isn't what "a tiny slide" should feel like.
    var SNAP_PX = 40;

    var collapsedOffset = 0;
    var expanded = false;
    var dragging = false; // a drag (handle- or body-driven) is actively moving the sheet
    var pendingPull = false; // touched down inside the body; still deciding if this becomes a drag
    var startY = 0;
    var startOffset = 0;
    var lastOffset = 0;

    function measure() {
      collapsedOffset = scoreHeader.getBoundingClientRect().bottom;
    }

    function applyOffset(offset) {
      offset = Math.max(0, Math.min(collapsedOffset, offset));
      lastOffset = offset;
      sheet.style.transform = "translateY(" + offset + "px)";
      var span = collapsedOffset || 1;
      var progress = 1 - offset / span; // 0 = collapsed, 1 = fully expanded
      if (tabbar) {
        tabbar.style.opacity = String(1 - progress);
        tabbar.style.pointerEvents = progress > 0.5 ? "none" : "";
      }
    }

    function snapTo(isExpanded) {
      expanded = isExpanded;
      applyOffset(isExpanded ? 0 : collapsedOffset);
      body.classList.toggle("is-scroll-locked", !isExpanded);
    }

    // Placed instantly (no transition) on first load -- only snaps
    // triggered by an actual drag afterward should animate.
    measure();
    sheet.style.transition = "none";
    snapTo(false);
    void sheet.offsetHeight; // force the "none" transition to commit first
    sheet.style.transition = reduceMotion ? "none" : "";

    window.addEventListener("resize", function () {
      if (dragging) return;
      measure();
      snapTo(expanded);
    });

    function pointerY(e) {
      return e.touches ? e.touches[0].clientY : e.clientY;
    }

    function startDrag(y) {
      dragging = true;
      sheet.classList.add("is-dragging");
      measure();
      startY = y;
      startOffset = lastOffset;
    }

    function moveDrag(y) {
      applyOffset(startOffset + (y - startY));
    }

    function finishDrag() {
      dragging = false;
      sheet.classList.remove("is-dragging");
      // Negative = moved toward expanded (offset shrinking toward 0);
      // positive = moved toward collapsed.
      var moved = lastOffset - startOffset;
      if (Math.abs(moved) < CLAIM_THRESHOLD) {
        // Barely moved -- treat as a tap, toggle instead.
        snapTo(!expanded);
        return;
      }
      if (moved <= -SNAP_PX) { snapTo(true); return; }
      if (moved >= SNAP_PX) { snapTo(false); return; }
      // Between the claim and snap thresholds -- not quite "tiny" but
      // not a full commit either; fall back to whichever side it's
      // closer to.
      var span = collapsedOffset || 1;
      snapTo(1 - lastOffset / span > 0.5);
    }

    // ---- The handle: a large (44px) touch target, always draggable ----
    handle.addEventListener("touchstart", function (e) {
      startDrag(pointerY(e));
      if (e.cancelable) e.preventDefault();
    }, { passive: false });

    handle.addEventListener("touchmove", function (e) {
      if (!dragging) return;
      moveDrag(pointerY(e));
      if (e.cancelable) e.preventDefault();
    }, { passive: false });

    handle.addEventListener("touchend", function () {
      if (dragging) finishDrag();
    });

    handle.addEventListener("mousedown", function (e) {
      startDrag(e.clientY);
    });

    // ---- The body ----
    // Collapsed: scrolling is locked (see .is-scroll-locked), so any
    // clear drag in either direction is unambiguous -- it moves the
    // sheet. Expanded: only a downward pull that's already at
    // scrollTop 0 claims the gesture; anything else is left alone for
    // native scrolling.
    function shouldClaim(delta) {
      if (!expanded) return Math.abs(delta) > CLAIM_THRESHOLD;
      if (body.scrollTop > 0) return false;
      return delta > CLAIM_THRESHOLD;
    }

    body.addEventListener("touchstart", function (e) {
      if (dragging) return;
      pendingPull = true;
      startY = pointerY(e);
    }, { passive: true });

    body.addEventListener("touchmove", function (e) {
      if (dragging) {
        moveDrag(pointerY(e));
        if (e.cancelable) e.preventDefault();
        return;
      }
      if (!pendingPull) return;
      var y = pointerY(e);
      if (expanded && body.scrollTop > 0) {
        pendingPull = false; // scrolled away from the top -- normal scroll
        return;
      }
      if (shouldClaim(y - startY)) {
        pendingPull = false;
        startDrag(startY);
        moveDrag(y);
        if (e.cancelable) e.preventDefault();
      }
    }, { passive: false });

    body.addEventListener("touchend", function () {
      pendingPull = false;
      if (dragging) finishDrag();
    });

    body.addEventListener("mousedown", function (e) {
      if (dragging) return;
      pendingPull = true;
      startY = e.clientY;
    });

    // Shared continuation for whichever gesture (handle- or
    // body-driven) is currently active via the mouse.
    window.addEventListener("mousemove", function (e) {
      if (dragging) {
        moveDrag(e.clientY);
        return;
      }
      if (!pendingPull) return;
      if (expanded && body.scrollTop > 0) {
        pendingPull = false;
        return;
      }
      if (shouldClaim(e.clientY - startY)) {
        pendingPull = false;
        startDrag(startY);
        moveDrag(e.clientY);
      }
    });

    window.addEventListener("mouseup", function () {
      pendingPull = false;
      if (dragging) finishDrag();
    });
  })();
})();
