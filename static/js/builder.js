(function () {
  "use strict";

  var typeBtns = { cv: document.getElementById("builder-type-cv"), letter: document.getElementById("builder-type-letter") };
  var galleries = { cv: document.getElementById("template-gallery-cv"), letter: document.getElementById("template-gallery-letter") };
  var instructionInput = document.getElementById("builder-instruction-input");
  var generateBtn = document.getElementById("builder-generate-btn");
  var generateLabel = document.getElementById("builder-generate-label");
  var statusLine = document.getElementById("builder-status-line");
  var previewEmpty = document.getElementById("builder-preview-empty");
  var previewWrap = document.getElementById("builder-preview-wrap");
  var previewEl = document.getElementById("builder-preview");
  var copyBtn = document.getElementById("builder-copy-btn");
  var downloadBtn = document.getElementById("builder-download-btn");
  var saveBtn = document.getElementById("builder-save-btn");
  var saveStateEl = document.getElementById("builder-save-state");

  var defaults = window.BUILDER_DEFAULT_TEMPLATES || { cv: "signal", letter: "direct" };
  var activeType = "cv";
  var selectedTemplate = { cv: defaults.cv, letter: defaults.letter };
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

  function templateLabel(type, key) {
    var card = galleries[type].querySelector('.template-card[data-template="' + key + '"]');
    var nameEl = card && card.querySelector(".template-card-name");
    return nameEl ? nameEl.textContent : key;
  }

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

  function applyPreviewTemplateClass() {
    previewEl.className = "builder-preview" + (activeType === "cv"
      ? " tmpl-" + selectedTemplate.cv
      : " tmpl-letter-" + selectedTemplate.letter);
  }

  function setActiveType(type) {
    activeType = type;
    typeBtns.cv.classList.toggle("is-selected", type === "cv");
    typeBtns.letter.classList.toggle("is-selected", type === "letter");
    galleries.cv.hidden = type !== "cv";
    galleries.letter.hidden = type !== "letter";
    statusLine.hidden = true;
    updateSaveBtnVisibility();
    renderSaveState();
    applyPreviewTemplateClass();
    if (currentHtml[type]) {
      previewEl.innerHTML = currentHtml[type];
      previewEmpty.hidden = true;
      previewWrap.hidden = false;
      generateLabel.textContent = "Update";
    } else {
      previewEmpty.hidden = false;
      previewWrap.hidden = true;
      generateLabel.textContent = "Generate";
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

  function selectTemplate(type, key) {
    selectedTemplate[type] = key;
    galleries[type].querySelectorAll(".template-card").forEach(function (card) {
      card.classList.toggle("is-selected", card.dataset.template === key);
    });
    if (type === activeType) applyPreviewTemplateClass();
  }

  function wireGallery(type) {
    galleries[type].querySelectorAll(".template-card").forEach(function (card) {
      var key = card.dataset.template;
      card.querySelector(".template-card-select").addEventListener("click", function () {
        selectTemplate(type, key);
      });
      card.querySelector(".template-preview-btn").addEventListener("click", function () {
        openTemplatePreview(type, key, card);
      });
    });
  }
  wireGallery("cv");
  wireGallery("letter");

  // ---------- Full-size template preview overlay ----------

  var previewOverlay = document.getElementById("template-preview-overlay");
  var previewOverlayTitle = document.getElementById("template-preview-title");
  var previewOverlayBody = document.getElementById("template-preview-body");
  var previewOverlayCloseBtn = document.getElementById("template-preview-close-btn");
  var previewOverlaySelectBtn = document.getElementById("template-preview-select-btn");
  var previewingType = null;
  var previewingKey = null;

  function openTemplatePreview(type, key, card) {
    previewingType = type;
    previewingKey = key;
    var thumb = card.querySelector(".template-thumb");
    previewOverlayTitle.textContent = templateLabel(type, key);
    previewOverlayBody.innerHTML = "";
    previewOverlayBody.appendChild(thumb.cloneNode(true));
    previewOverlay.hidden = false;
  }

  function closeTemplatePreview() {
    previewOverlay.hidden = true;
    previewOverlayBody.innerHTML = "";
    previewingType = null;
    previewingKey = null;
  }

  previewOverlayCloseBtn.addEventListener("click", closeTemplatePreview);
  previewOverlay.addEventListener("click", function (e) {
    if (e.target === previewOverlay) closeTemplatePreview();
  });
  previewOverlaySelectBtn.addEventListener("click", function () {
    if (previewingType && previewingKey) selectTemplate(previewingType, previewingKey);
    closeTemplatePreview();
  });

  function setStatus(text, isError) {
    statusLine.textContent = text;
    statusLine.hidden = !text;
    statusLine.classList.toggle("builder-status-error", !!isError);
  }

  function generate() {
    if (generating) return;
    var note = instructionInput.value.trim();
    var template = templateLabel(activeType, selectedTemplate[activeType]);
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
        applyPreviewTemplateClass();
        previewEmpty.hidden = true;
        previewWrap.hidden = false;
        generateLabel.textContent = "Update";
        setStatus(data.description || "Done.", false);
        markUnsaved(activeType);
      })
      .catch(function () {
        setStatus("Couldn't reach Avryn to draft that — check your connection and try again.", true);
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
    }).catch(function () {
      setStatus("Couldn't copy — check clipboard permissions and try again.", true);
    });
  });

  var downloading = false;
  downloadBtn.addEventListener("click", function () {
    var html = currentHtml[activeType];
    if (!html || downloading) return;
    downloading = true;
    downloadBtn.disabled = true;
    setStatus("Preparing your PDF…", false);
    fetch("/api/cv-download/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cv_html: html, template: activeType === "cv" ? selectedTemplate.cv : null }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("bad status");
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = (activeType === "cv" ? "my-cv" : "my-cover-letter") + ".pdf";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setStatus("Downloaded.", false);
      })
      .catch(function () {
        setStatus("Couldn't generate that PDF — check your connection and try again.", true);
      })
      .finally(function () {
        downloading = false;
        downloadBtn.disabled = false;
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
      body: JSON.stringify({ html: currentHtml[activeType], template: activeType === "cv" ? selectedTemplate.cv : null }),
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

  applyPreviewTemplateClass();

  var initial = window.BUILDER_INITIAL;
  if (initial && initial.html) {
    currentHtml[initial.kind] = initial.html;
    loadedDocumentId[initial.kind] = initial.document_id;
    saveState[initial.kind] = "saved";
    setActiveType(initial.kind);
  }
})();
