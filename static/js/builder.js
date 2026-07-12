(function () {
  "use strict";

  var typeBtns = { cv: document.getElementById("builder-type-cv"), letter: document.getElementById("builder-type-letter") };
  var templateRows = { cv: document.getElementById("builder-template-row-cv"), letter: document.getElementById("builder-template-row-letter") };
  var instructionInput = document.getElementById("builder-instruction-input");
  var generateBtn = document.getElementById("builder-generate-btn");
  var statusLine = document.getElementById("builder-status-line");
  var previewEmpty = document.getElementById("builder-preview-empty");
  var previewWrap = document.getElementById("builder-preview-wrap");
  var previewEl = document.getElementById("builder-preview");
  var copyBtn = document.getElementById("builder-copy-btn");
  var downloadBtn = document.getElementById("builder-download-btn");
  var saveBtn = document.getElementById("builder-save-btn");
  var saveStateEl = document.getElementById("builder-save-state");

  var activeType = "cv";
  var selectedTemplate = { cv: "Modern", letter: "Formal" };
  var currentHtml = { cv: "", letter: "" };
  // Set only when this type's content came from (or has been saved to) an
  // existing generated document -- e.g. reached via a card's "Edit"
  // button -- so "Save" can write edits back to that same document and
  // a card's "Download" later reflects them, not just the original draft.
  var loadedDocumentId = { cv: null, letter: null };
  // Tracks whether each type's loaded document has edits since it was
  // last saved, so "Save" isn't the only way to tell -- an always-visible
  // "Unsaved changes" / "Saved" line makes it unambiguous whether an edit
  // actually persisted or would be lost by navigating away.
  var saveState = { cv: null, letter: null };
  var generating = false;

  function updateSaveBtnVisibility() {
    saveBtn.hidden = !loadedDocumentId[activeType];
  }

  function renderSaveState() {
    var state = saveState[activeType];
    if (!loadedDocumentId[activeType] || !state) {
      saveStateEl.hidden = true;
      return;
    }
    saveStateEl.hidden = false;
    saveStateEl.textContent = state === "saved" ? "Saved" : "Unsaved changes";
    saveStateEl.classList.toggle("is-unsaved", state === "unsaved");
  }

  function markUnsaved(type) {
    if (!loadedDocumentId[type]) return;
    saveState[type] = "unsaved";
    if (type === activeType) renderSaveState();
  }

  function setActiveType(type) {
    activeType = type;
    typeBtns.cv.classList.toggle("is-selected", type === "cv");
    typeBtns.letter.classList.toggle("is-selected", type === "letter");
    templateRows.cv.hidden = type !== "cv";
    templateRows.letter.hidden = type !== "letter";
    statusLine.hidden = true;
    updateSaveBtnVisibility();
    renderSaveState();
    if (currentHtml[type]) {
      previewEl.innerHTML = currentHtml[type];
      previewEmpty.hidden = true;
      previewWrap.hidden = false;
      generateBtn.textContent = "Update";
    } else {
      previewEmpty.hidden = false;
      previewWrap.hidden = true;
      generateBtn.textContent = "Generate";
    }
  }

  // The preview is directly editable -- keep currentHtml in sync with
  // whatever the user typed so "Update"/"Download"/"Save" all act on the
  // live edited content, not a stale AI-generated snapshot.
  previewEl.addEventListener("input", function () {
    currentHtml[activeType] = previewEl.innerHTML;
    markUnsaved(activeType);
  });

  typeBtns.cv.addEventListener("click", function () { setActiveType("cv"); });
  typeBtns.letter.addEventListener("click", function () { setActiveType("letter"); });

  function wireTemplateRow(type) {
    templateRows[type].querySelectorAll(".builder-template-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        selectedTemplate[type] = chip.dataset.template;
        templateRows[type].querySelectorAll(".builder-template-chip").forEach(function (c) {
          c.classList.toggle("is-selected", c === chip);
        });
      });
    });
  }
  wireTemplateRow("cv");
  wireTemplateRow("letter");

  function setStatus(text, isError) {
    statusLine.textContent = text;
    statusLine.hidden = !text;
    statusLine.classList.toggle("builder-status-error", !!isError);
  }

  function generate() {
    if (generating) return;
    var note = instructionInput.value.trim();
    var template = selectedTemplate[activeType];
    var docLabel = activeType === "cv" ? "CV" : "cover letter";
    var instruction = "Use a " + template + " style for this " + docLabel + "." + (note ? " " + note : "");

    generating = true;
    generateBtn.disabled = true;
    setStatus("Drafting…", false);

    var endpoint = activeType === "cv" ? "/api/cv-edit" : "/api/letter-edit";
    var payload = activeType === "cv"
      ? { instruction: instruction, cv_html: currentHtml.cv }
      : { instruction: instruction, letter_html: currentHtml.letter };

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          setStatus(data.error, true);
          return;
        }
        currentHtml[activeType] = data.updated_html || "";
        previewEl.innerHTML = currentHtml[activeType];
        previewEmpty.hidden = true;
        previewWrap.hidden = false;
        generateBtn.textContent = "Update";
        setStatus(data.description || "Done.", false);
        markUnsaved(activeType);
      })
      .catch(function () {
        setStatus("Couldn't reach Ploy to draft that — check your connection and try again.", true);
      })
      .finally(function () {
        generating = false;
        generateBtn.disabled = false;
      });
  }

  generateBtn.addEventListener("click", generate);

  copyBtn.addEventListener("click", function () {
    var text = previewEl.innerText || "";
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () {
      setStatus("Copied to clipboard.", false);
    });
  });

  downloadBtn.addEventListener("click", function () {
    var html = currentHtml[activeType];
    if (!html) return;
    fetch("/api/cv-download/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cv_html: html }),
    })
      .then(function (r) { return r.blob(); })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = (activeType === "cv" ? "my-cv" : "my-cover-letter") + ".pdf";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      });
  });

  var saving = false;
  saveBtn.addEventListener("click", function () {
    var docId = loadedDocumentId[activeType];
    if (!docId || saving) return;
    saving = true;
    saveBtn.disabled = true;
    fetch("/api/document/" + docId + "/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: currentHtml[activeType] }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(data.ok ? "Saved." : (data.error || "Couldn't save."), !data.ok);
        if (data.ok) {
          saveState[activeType] = "saved";
          renderSaveState();
        }
      })
      .catch(function () {
        setStatus("Couldn't save just now — check your connection and try again.", true);
      })
      .finally(function () {
        saving = false;
        saveBtn.disabled = false;
      });
  });

  // ---------- Load an existing document when opened via a card's "Edit" ----------

  var initial = window.BUILDER_INITIAL;
  if (initial && initial.html) {
    currentHtml[initial.kind] = initial.html;
    loadedDocumentId[initial.kind] = initial.document_id;
    saveState[initial.kind] = "saved";
    setActiveType(initial.kind);
  }
})();
