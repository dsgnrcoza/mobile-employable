(function () {
  "use strict";

  var gridEl = document.getElementById("notes-grid");
  var emptyEl = document.getElementById("notes-empty");
  var noResultsEl = document.getElementById("notes-no-results");
  var searchInput = document.getElementById("notes-search-input");
  var newBtn = document.getElementById("notes-new-btn");

  var editorOverlay = document.getElementById("note-editor-overlay");
  var editorCloseBtn = document.getElementById("note-editor-close-btn");
  var editorDeleteBtn = document.getElementById("note-editor-delete-btn");
  var titleInput = document.getElementById("note-title-input");
  var bodyInput = document.getElementById("note-body-input");
  var colorSwatches = document.querySelectorAll(".note-color-swatch");

  var notes = [];
  var currentNoteId = null; // null while editing a brand-new, not-yet-saved note
  var currentColor = "default";

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function bodyPreview(body) {
    var oneLine = (body || "").replace(/\s+/g, " ").trim();
    return oneLine.length > 90 ? oneLine.slice(0, 90) + "…" : oneLine;
  }

  function matchesSearch(note, query) {
    if (!query) return true;
    var haystack = ((note.title || "") + " " + (note.body || "")).toLowerCase();
    return haystack.indexOf(query) !== -1;
  }

  function renderNotes() {
    var query = (searchInput.value || "").trim().toLowerCase();
    var visible = notes.filter(function (note) { return matchesSearch(note, query); });

    gridEl.innerHTML = "";
    emptyEl.hidden = notes.length > 0;
    noResultsEl.hidden = notes.length === 0 || visible.length > 0;

    visible.forEach(function (note) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "note-card note-color-" + (note.color || "default");
      card.innerHTML =
        (note.source === "chat"
          ? '<span class="note-card-source" title="Saved from a chat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>From chat</span>'
          : "") +
        '<span class="note-card-title">' + escapeHtml(note.title || "Untitled") + '</span>' +
        '<span class="note-card-body">' + escapeHtml(bodyPreview(note.body)) + '</span>';
      card.addEventListener("click", function () { openEditor(note); });
      gridEl.appendChild(card);
    });
  }

  function loadNotes() {
    fetch("/api/notes")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          notes = data.notes;
          renderNotes();
        }
      });
  }

  function setColor(color) {
    currentColor = color;
    colorSwatches.forEach(function (sw) {
      sw.classList.toggle("is-selected", sw.dataset.color === color);
    });
  }

  function openEditor(note) {
    currentNoteId = note ? note.id : null;
    titleInput.value = note ? note.title : "";
    bodyInput.value = note ? note.body : "";
    setColor(note ? note.color || "default" : "default");
    editorDeleteBtn.hidden = !note;
    editorOverlay.hidden = false;
    titleInput.focus();
  }

  function closeEditorAndSave() {
    var title = titleInput.value.trim();
    var body = bodyInput.value.trim();
    editorOverlay.hidden = true;

    if (!title && !body) {
      // Nothing worth keeping -- if this was a brand-new note, just
      // discard it silently rather than saving an empty card.
      return;
    }

    if (currentNoteId) {
      fetch("/api/notes/" + currentNoteId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, body: body, color: currentColor }),
      })
        .then(function (r) { return r.json(); })
        .then(function () { loadNotes(); });
    } else {
      fetch("/api/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, body: body, color: currentColor }),
      })
        .then(function (r) { return r.json(); })
        .then(function () { loadNotes(); });
    }
  }

  searchInput.addEventListener("input", renderNotes);

  newBtn.addEventListener("click", function () { openEditor(null); });
  editorCloseBtn.addEventListener("click", closeEditorAndSave);

  colorSwatches.forEach(function (sw) {
    sw.addEventListener("click", function () { setColor(sw.dataset.color); });
  });

  editorDeleteBtn.addEventListener("click", function () {
    if (!currentNoteId) return;
    if (!window.confirm("Delete this note? This can't be undone.")) return;
    var id = currentNoteId;
    currentNoteId = null; // prevent closeEditorAndSave (never called here) from re-saving
    editorOverlay.hidden = true;
    fetch("/api/notes/" + id, { method: "DELETE" })
      .then(function () { loadNotes(); });
  });

  loadNotes();
})();
