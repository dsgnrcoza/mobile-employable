(function () {
  "use strict";

  var listEl = document.getElementById("trackers-list");
  var emptyEl = document.getElementById("trackers-empty");
  var newBtn = document.getElementById("tracker-new-btn");

  var editorOverlay = document.getElementById("tracker-editor-overlay");
  var editorCloseBtn = document.getElementById("tracker-editor-close-btn");
  var editorDeleteBtn = document.getElementById("tracker-editor-delete-btn");
  var jobTitleInput = document.getElementById("tracker-job-title-input");
  var companyInput = document.getElementById("tracker-company-input");
  var dateInput = document.getElementById("tracker-date-input");
  var notesInput = document.getElementById("tracker-notes-input");
  var statusChips = document.querySelectorAll(".tracker-status-chip");

  var trackers = [];
  var currentTrackerId = null; // null while editing a brand-new, not-yet-saved entry
  var currentStatus = "applied";

  var STATUS_LABELS = { applied: "Applied", interviewing: "Interviewing", offer: "Offer", rejected: "Rejected" };

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function formatDate(iso) {
    if (!iso) return "No date set";
    var parts = iso.split("-");
    if (parts.length !== 3) return iso;
    var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function renderTrackers() {
    listEl.innerHTML = "";
    emptyEl.hidden = trackers.length > 0;
    trackers.forEach(function (t) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "tracker-row";
      row.innerHTML =
        '<span class="tracker-row-main">' +
        '<span class="tracker-row-title">' + escapeHtml(t.job_title || "Untitled role") + '</span>' +
        '<span class="tracker-row-company">' + escapeHtml(t.company || "") + '</span>' +
        '</span>' +
        '<span class="tracker-row-meta">' +
        '<span class="tracker-status-badge tracker-status-' + (t.status || "applied") + '">' + (STATUS_LABELS[t.status] || "Applied") + '</span>' +
        '<span class="tracker-row-date">' + escapeHtml(formatDate(t.date_applied)) + '</span>' +
        '</span>';
      row.addEventListener("click", function () { openEditor(t); });
      listEl.appendChild(row);
    });
  }

  function loadTrackers() {
    fetch("/api/trackers")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          trackers = data.trackers;
          renderTrackers();
        }
      });
  }

  function setStatus(status) {
    currentStatus = status;
    statusChips.forEach(function (chip) {
      chip.classList.toggle("is-selected", chip.dataset.status === status);
    });
  }

  function openEditor(tracker) {
    currentTrackerId = tracker ? tracker.id : null;
    jobTitleInput.value = tracker ? tracker.job_title : "";
    companyInput.value = tracker ? tracker.company : "";
    dateInput.value = tracker ? tracker.date_applied : "";
    notesInput.value = tracker ? tracker.notes : "";
    setStatus(tracker ? tracker.status || "applied" : "applied");
    editorDeleteBtn.hidden = !tracker;
    editorOverlay.hidden = false;
    jobTitleInput.focus();
  }

  function closeEditorAndSave() {
    var jobTitle = jobTitleInput.value.trim();
    var company = companyInput.value.trim();
    var dateApplied = dateInput.value;
    var notes = notesInput.value.trim();
    editorOverlay.hidden = true;

    if (!jobTitle && !company) {
      // Nothing worth keeping -- discard a brand-new blank entry silently.
      return;
    }

    var payload = { job_title: jobTitle, company: company, date_applied: dateApplied, status: currentStatus, notes: notes };

    if (currentTrackerId) {
      fetch("/api/trackers/" + currentTrackerId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) { return r.json(); })
        .then(function () { loadTrackers(); });
    } else {
      fetch("/api/trackers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (r) { return r.json(); })
        .then(function () { loadTrackers(); });
    }
  }

  newBtn.addEventListener("click", function () { openEditor(null); });
  editorCloseBtn.addEventListener("click", closeEditorAndSave);

  statusChips.forEach(function (chip) {
    chip.addEventListener("click", function () { setStatus(chip.dataset.status); });
  });

  editorDeleteBtn.addEventListener("click", function () {
    if (!currentTrackerId) return;
    var id = currentTrackerId;
    currentTrackerId = null; // prevent closeEditorAndSave (never called here) from re-saving
    editorOverlay.hidden = true;
    fetch("/api/trackers/" + id, { method: "DELETE" })
      .then(function () { loadTrackers(); });
  });

  loadTrackers();
})();
