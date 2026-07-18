(function () {
  "use strict";

  var filterRowEl = document.getElementById("outbox-filter-row");
  var listEl = document.getElementById("outbox-list");
  var emptyEl = document.getElementById("outbox-empty");
  var toastEl = document.getElementById("outbox-toast");

  var reviewOverlay = document.getElementById("outbox-review-overlay");
  var reviewJobTitleEl = document.getElementById("outbox-review-job-title");
  var reviewCompanyEl = document.getElementById("outbox-review-company");
  var reviewCloseBtn = document.getElementById("outbox-review-close-btn");
  var reviewRecipientEl = document.getElementById("outbox-review-recipient");
  var reviewSubjectEl = document.getElementById("outbox-review-subject");
  var reviewMessageEl = document.getElementById("outbox-review-message");
  var reviewAutosaveEl = document.getElementById("outbox-review-autosave");
  var reviewDownloadCvBtn = document.getElementById("outbox-review-download-cv-btn");
  var reviewReminderEl = document.getElementById("outbox-review-reminder");
  var reviewNudgeEl = document.getElementById("outbox-review-nudge");
  var statusSelect = document.getElementById("outbox-status-select");
  var openGmailBtn = document.getElementById("outbox-open-gmail-btn");

  var confirmOverlay = document.getElementById("outbox-confirm-overlay");
  var confirmNotYetBtn = document.getElementById("outbox-confirm-not-yet-btn");
  var confirmYesBtn = document.getElementById("outbox-confirm-yes-btn");

  // Interview/Rejected are still real statuses (settable from the
  // review sheet's status picker below, and still counted under "All")
  // -- just not their own filter chips, so this row stays to the four
  // that matter for the common case of scanning where things stand.
  var FILTERS = [
    { key: "all", label: "All" },
    { key: "drafted", label: "Drafted" },
    { key: "sent", label: "Sent" },
    { key: "replied", label: "Replied" },
  ];
  var STATUS_LABELS = { drafted: "Drafted", sent: "Sent", replied: "Replied", interview: "Interview", rejected: "Rejected" };
  var GMAIL_BODY_LIMIT = 1800;

  var applications = [];
  var activeFilter = "all";
  var reviewId = null;
  var cvDocId = null;
  var autosaveTimer = null;
  var awaitingSendConfirmation = false;
  // Captured separately from reviewId at the moment Gmail is opened --
  // closeReview() clears reviewId as soon as the sheet is dismissed, but
  // the "Did you send it?" prompt can still land afterward (it only
  // fires once the tab regains focus), so it needs its own record of
  // which application that prompt is actually about.
  var pendingConfirmId = null;
  var toastTimer = null;

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function showToast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 2600);
  }

  function formatDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function daysSince(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return 0;
    return Math.floor((Date.now() - d.getTime()) / 86400000);
  }

  function nudgeFor(app) {
    if (app.status === "drafted" && daysSince(app.createdAt) >= 2) {
      return "Drafted " + daysSince(app.createdAt) + " days ago — jobs close fast.";
    }
    if (app.status === "sent" && app.sentAt && daysSince(app.sentAt) >= 5) {
      return "Heard back yet?";
    }
    return "";
  }

  function findApp(id) {
    return applications.filter(function (a) { return a.id === id; })[0] || null;
  }

  // ---------- List + filters ----------

  var jobTabBadge = document.getElementById("job-tab-badge");

  function renderFilters() {
    if (jobTabBadge) {
      var draftedCount = applications.filter(function (a) { return a.status === "drafted"; }).length;
      jobTabBadge.textContent = draftedCount > 99 ? "99+" : String(draftedCount);
      jobTabBadge.hidden = draftedCount === 0;
    }
    filterRowEl.innerHTML = "";
    FILTERS.forEach(function (f) {
      var count = f.key === "all" ? applications.length : applications.filter(function (a) { return a.status === f.key; }).length;
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "outbox-filter-chip" + (activeFilter === f.key ? " is-active" : "");
      chip.textContent = f.label + (count ? " (" + count + ")" : "");
      chip.addEventListener("click", function () {
        activeFilter = f.key;
        renderFilters();
        renderList();
      });
      filterRowEl.appendChild(chip);
    });
  }

  function renderList() {
    listEl.innerHTML = "";
    emptyEl.hidden = applications.length !== 0;
    filterRowEl.hidden = applications.length === 0;
    if (!applications.length) return;

    var visible = activeFilter === "all" ? applications : applications.filter(function (a) { return a.status === activeFilter; });

    if (!visible.length) {
      var none = document.createElement("p");
      none.className = "outbox-list-none";
      none.textContent = "Nothing in " + STATUS_LABELS[activeFilter] + " right now.";
      listEl.appendChild(none);
      return;
    }

    visible.forEach(function (app) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "outbox-item";
      var nudge = nudgeFor(app);
      item.innerHTML =
        '<div class="outbox-item-main">' +
        '<span class="outbox-item-title">' + escapeHtml(app.jobTitle) + "</span>" +
        '<span class="outbox-item-company">' + escapeHtml(app.company) + "</span>" +
        '<span class="outbox-item-recipient">' + (app.recipientEmail ? escapeHtml(app.recipientEmail) : "No email listed") + "</span>" +
        (nudge ? '<span class="outbox-item-nudge">' + escapeHtml(nudge) + "</span>" : "") +
        "</div>" +
        '<div class="outbox-item-side">' +
        '<span class="outbox-status-chip outbox-status-' + app.status + '">' + STATUS_LABELS[app.status] + "</span>" +
        '<span class="outbox-item-date">' + escapeHtml(formatDate(app.createdAt)) + "</span>" +
        "</div>";
      item.addEventListener("click", function () { openReview(app.id); });
      listEl.appendChild(item);
    });
  }

  function fetchApplications() {
    return fetch("/api/applications")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        applications = data.ok ? data.applications : [];
        renderFilters();
        renderList();
      })
      .catch(function () {
        showToast("Couldn't load your Outbox — check your connection.");
      });
  }

  // ---------- Review sheet ----------

  function ensureCvDoc() {
    if (cvDocId !== null) return Promise.resolve(cvDocId);
    return fetch("/api/profile/documents")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var docs = (data.ok && data.documents) || [];
        if (!docs.length) { cvDocId = 0; return 0; }
        var newest = docs.reduce(function (a, b) { return (a.uploaded_at || "") > (b.uploaded_at || "") ? a : b; });
        cvDocId = newest.id;
        return cvDocId;
      })
      .catch(function () { cvDocId = 0; return 0; });
  }

  function openReview(id) {
    var app = findApp(id);
    if (!app) return;
    reviewId = id;
    reviewJobTitleEl.textContent = app.jobTitle;
    reviewCompanyEl.textContent = app.company;
    reviewRecipientEl.textContent = app.recipientEmail ? "To: " + app.recipientEmail : "No email listed for this job";
    reviewSubjectEl.value = app.subject;
    reviewMessageEl.value = app.body;
    reviewAutosaveEl.textContent = "";
    statusSelect.value = app.status;

    var nudge = nudgeFor(app);
    reviewNudgeEl.hidden = !nudge;
    reviewNudgeEl.textContent = nudge;

    if (app.recipientEmail) {
      openGmailBtn.textContent = "Open in Gmail";
      reviewReminderEl.hidden = false;
    } else {
      openGmailBtn.textContent = app.jobUrl ? "Open job posting" : "No email listed";
      openGmailBtn.disabled = !app.jobUrl;
      // No Gmail compose happens on this path at all -- a reminder about
      // attaching a CV "in Gmail" would be actively confusing here.
      reviewReminderEl.hidden = true;
    }

    reviewDownloadCvBtn.hidden = true;
    ensureCvDoc().then(function (id) {
      reviewDownloadCvBtn.hidden = !id;
    });

    reviewOverlay.hidden = false;
  }

  function closeReview() {
    reviewOverlay.hidden = true;
    reviewId = null;
  }

  reviewCloseBtn.addEventListener("click", closeReview);
  reviewDownloadCvBtn.addEventListener("click", function () {
    if (cvDocId) window.open("/api/document-download/" + cvDocId, "_blank");
  });

  function scheduleAutosave() {
    reviewAutosaveEl.textContent = "Saving…";
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(function () {
      if (reviewId == null) return;
      var app = findApp(reviewId);
      var subject = reviewSubjectEl.value.trim();
      var body = reviewMessageEl.value;
      fetch("/api/applications/" + reviewId, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: subject, body: body }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok && app) {
            app.subject = data.application.subject;
            app.body = data.application.body;
            app.updatedAt = data.application.updatedAt;
          }
          reviewAutosaveEl.textContent = "Saved";
        })
        .catch(function () {
          reviewAutosaveEl.textContent = "Couldn't save — check your connection.";
        });
    }, 700);
  }

  reviewSubjectEl.addEventListener("input", scheduleAutosave);
  reviewMessageEl.addEventListener("input", scheduleAutosave);

  statusSelect.addEventListener("change", function () {
    if (reviewId == null) return;
    var status = statusSelect.value;
    fetch("/api/applications/" + reviewId, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: status }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        var app = findApp(reviewId);
        if (app) {
          app.status = data.application.status;
          app.sentAt = data.application.sentAt;
        }
        renderFilters();
        renderList();
      });
  });

  // ---------- Truncation + clipboard ----------

  function truncateAtSentence(text, limit) {
    if (text.length <= limit) return { truncated: text, wasCut: false };
    var slice = text.slice(0, limit);
    var lastBoundary = Math.max(slice.lastIndexOf(". "), slice.lastIndexOf("! "), slice.lastIndexOf("? "), slice.lastIndexOf("\n"));
    var cut = lastBoundary > limit * 0.4 ? slice.slice(0, lastBoundary + 1) : slice;
    return { truncated: cut.trim(), wasCut: true };
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    ta.remove();
    return Promise.resolve();
  }

  // ---------- Open in mail app / job posting ----------

  // No email URI scheme (mailto:, Gmail's own web compose link) can
  // carry a file attachment -- that's a hard platform limitation, not
  // something any web page can route around. The Web Share API is the
  // one exception: handing the OS a real file lets whatever app the
  // user picks from the native share sheet (Gmail included -- its
  // Android share target genuinely attaches shared files) receive it
  // as an actual attachment. Fetched fresh each time rather than cached
  // once, since the underlying CV document can change between reviews.
  function fetchCvFile() {
    if (!cvDocId) return Promise.resolve(null);
    return fetch("/api/document-download/" + cvDocId)
      .then(function (r) {
        if (!r.ok) throw new Error("bad status");
        var disposition = r.headers.get("content-disposition") || "";
        var match = disposition.match(/filename="?([^";]+)"?/);
        var filename = match ? match[1] : "CV.pdf";
        return r.blob().then(function (blob) {
          return new File([blob], filename, { type: blob.type || "application/pdf" });
        });
      })
      .catch(function () { return null; });
  }

  function markAwaitingConfirmation() {
    awaitingSendConfirmation = true;
    pendingConfirmId = reviewId;
  }

  // mailto: is what actually opens the device's real mail app (Gmail if
  // it's set as the default, otherwise a native chooser) -- unlike
  // Gmail's own "https://mail.google.com/..." compose link, which
  // always opens as a browser tab/in-app browser, never the installed
  // app itself. Used whenever the Share API (with a file) isn't
  // available, and reminds the user to attach the CV manually since
  // this path genuinely can't do it for them.
  function openViaMailto(app, subject, fullBody) {
    var result = truncateAtSentence(fullBody, GMAIL_BODY_LIMIT);
    if (result.wasCut) {
      copyToClipboard(fullBody).then(function () {
        showToast("Full message copied — paste it in your mail app.");
      });
    }
    window.location.href = "mailto:" + encodeURIComponent(app.recipientEmail) +
      "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(result.truncated);
    if (cvDocId) {
      showToast("Opening your mail app — attach the CV you downloaded before sending.");
    }
    markAwaitingConfirmation();
  }

  openGmailBtn.addEventListener("click", function () {
    var app = findApp(reviewId);
    if (!app) return;

    if (!app.recipientEmail) {
      if (app.jobUrl) window.open(app.jobUrl, "_blank");
      return;
    }

    var subject = reviewSubjectEl.value.trim();
    var fullBody = reviewMessageEl.value;

    if (navigator.share && navigator.canShare && cvDocId) {
      fetchCvFile().then(function (file) {
        if (file && navigator.canShare({ files: [file] })) {
          return navigator.share({ title: subject, text: fullBody, files: [file] }).then(function () {
            markAwaitingConfirmation();
            return true;
          });
        }
        return false;
      }).then(function (shared) {
        if (!shared) openViaMailto(app, subject, fullBody);
      }).catch(function (e) {
        // AbortError just means the user backed out of the share sheet
        // without picking anything -- not a failure worth falling back
        // from into a second, competing compose flow.
        if (e && e.name !== "AbortError") openViaMailto(app, subject, fullBody);
      });
      return;
    }

    openViaMailto(app, subject, fullBody);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && awaitingSendConfirmation) {
      awaitingSendConfirmation = false;
      confirmOverlay.hidden = false;
    }
  });

  // ---------- "Did you send it?" confirm ----------

  function nextDraftedIdAfter(id) {
    var drafted = applications.filter(function (a) { return a.status === "drafted"; });
    var idx = drafted.findIndex(function (a) { return a.id === id; });
    if (idx === -1) return drafted.length ? drafted[0].id : null;
    return idx + 1 < drafted.length ? drafted[idx + 1].id : null;
  }

  confirmNotYetBtn.addEventListener("click", function () {
    confirmOverlay.hidden = true;
    pendingConfirmId = null;
  });

  confirmYesBtn.addEventListener("click", function () {
    confirmOverlay.hidden = true;
    if (pendingConfirmId == null) return;
    var idForMarkSent = pendingConfirmId;
    pendingConfirmId = null;
    fetch("/api/applications/" + idForMarkSent + "/mark-sent", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        var app = findApp(idForMarkSent);
        if (app) {
          app.status = data.application.status;
          app.sentAt = data.application.sentAt;
        }
        renderFilters();
        renderList();
        var nextId = nextDraftedIdAfter(idForMarkSent);
        if (nextId) {
          openReview(nextId);
        } else {
          closeReview();
        }
      });
  });

  fetchApplications();
})();
