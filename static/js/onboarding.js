(function () {
  "use strict";

  var initial = window.OB_INITIAL || { cvDocuments: [], supportingDocuments: [], hasCv: false, fullName: "" };

  var state = {
    cv: initial.cvDocuments.slice(),
    supporting: initial.supportingDocuments.slice(),
    hasCv: !!initial.hasCv,
    hasName: !!(initial.fullName || "").trim(),
    lastUploadedId: {},
  };

  var previews = new Map(); // doc id -> { url, mime, filename }, only for files uploaded this session
  var knownIds = new Set();
  state.cv.concat(state.supporting).forEach(function (d) { knownIds.add(d.id); });

  var stepName = document.getElementById("step-name");
  var stepCv = document.getElementById("step-cv");
  var stepSupporting = document.getElementById("step-supporting");
  var nameInput = document.getElementById("name-input");
  var nameNextBtn = document.getElementById("name-next-btn");
  var nameError = document.getElementById("name-error");
  var cvNextBtn = document.getElementById("cv-next-btn");
  var supportingNextBtn = document.getElementById("supporting-next-btn");
  var cvError = document.getElementById("cv-error");

  var previewOverlay = document.getElementById("ob-preview-overlay");
  var previewTitle = document.getElementById("ob-preview-title");
  var previewBody = document.getElementById("ob-preview-body");
  var previewClose = document.getElementById("ob-preview-close");

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function openPreview(doc) {
    var info = previews.get(doc.id);

    if (!info) {
      previewTitle.textContent = doc.filename;
      previewBody.innerHTML = "";
      var fallback = document.createElement("div");
      fallback.className = "ob-preview-fallback";
      fallback.textContent = "Preview is only available right after you upload a file in this session.";
      previewBody.appendChild(fallback);
      previewOverlay.hidden = false;
      return;
    }

    // PDFs render reliably in the browser's own dedicated viewer but not
    // when embedded in an iframe inside a page (blank on many mobile
    // browsers/webviews) — so hand PDFs straight to that native viewer
    // instead of trying to show them inside our modal.
    if (info.mime === "application/pdf") {
      window.open(info.url, "_blank");
      return;
    }

    previewTitle.textContent = doc.filename;
    previewBody.innerHTML = "";

    if (info.mime.indexOf("image/") === 0) {
      var img = document.createElement("img");
      img.src = info.url;
      previewBody.appendChild(img);
    } else {
      var note = document.createElement("div");
      note.className = "ob-preview-fallback";
      note.textContent = "This file type can't be previewed inline. It uploaded successfully — you can still remove it below if it's the wrong file.";
      previewBody.appendChild(note);
    }

    previewOverlay.hidden = false;
  }

  previewClose.addEventListener("click", function () {
    previewOverlay.hidden = true;
    previewBody.innerHTML = "";
  });
  previewOverlay.addEventListener("click", function (e) {
    if (e.target === previewOverlay) {
      previewOverlay.hidden = true;
      previewBody.innerHTML = "";
    }
  });

  function docListEl(target) {
    return document.getElementById(target + "-doc-list");
  }

  function renderDocList(target) {
    var container = docListEl(target);
    container.innerHTML = "";
    var docs = state[target];
    docs.forEach(function (doc) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "ob-doc-row";

      var info = document.createElement("span");
      info.className = "ob-doc-info";

      var name = document.createElement("span");
      name.className = "ob-doc-name";
      name.textContent = doc.filename;

      var size = document.createElement("span");
      size.className = "ob-doc-size";
      size.textContent = formatBytes(doc.file_size);

      info.appendChild(name);
      info.appendChild(size);

      var remove = document.createElement("span");
      remove.className = "ob-doc-remove";
      remove.textContent = "✕";
      remove.addEventListener("click", function (e) {
        e.stopPropagation();
        deleteDoc(target, doc.id);
      });

      row.appendChild(info);
      row.appendChild(remove);

      row.addEventListener("click", function () { openPreview(doc); });

      container.appendChild(row);
    });
  }

  function updateCvGate() {
    cvNextBtn.disabled = !state.hasCv;
    if (state.cv.length > 0 && !state.hasCv) {
      cvError.hidden = false;
      cvError.textContent = "This doesn't look like a CV yet — please upload your CV or resume.";
    } else {
      cvError.hidden = true;
    }
  }

  function setCardState(target, cardState) {
    document.getElementById(target + "-upload-card").dataset.state = cardState;
  }

  function uploadFile(target, file) {
    if (file.size > 25 * 1024 * 1024) {
      alert("That file is larger than 25MB. Please choose a smaller file.");
      return;
    }

    var card = document.getElementById(target + "-upload-card");
    var pctEl = card.querySelector(".ob-state-uploading .ob-upload-pct");
    var barEl = card.querySelector(".ob-state-uploading .ob-progress-bar");
    var statusEl = card.querySelector(".ob-state-uploading .ob-upload-status");
    var uploadingFilenameEl = card.querySelector(".ob-state-uploading .ob-upload-filename");
    var completeFilenameBtn = card.querySelector(".ob-state-complete .ob-preview-trigger");

    pctEl.textContent = "0%";
    barEl.style.width = "0%";
    statusEl.textContent = "Uploading document…";
    uploadingFilenameEl.textContent = file.name;
    setCardState(target, "uploading");

    var formData = new FormData();
    formData.append("documents", file);
    formData.append("category", target);

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/onboarding/upload");

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round((e.loaded / e.total) * 100);
      pctEl.textContent = pct + "%";
      barEl.style.width = pct + "%";
      if (pct >= 100) statusEl.textContent = "Finishing up…";
    };

    xhr.onload = function () {
      var data;
      try {
        data = JSON.parse(xhr.responseText);
      } catch (e) {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
        var newDoc = data.documents.filter(function (d) { return !knownIds.has(d.id); })[0];
        if (newDoc) {
          knownIds.add(newDoc.id);
          previews.set(newDoc.id, { url: URL.createObjectURL(file), mime: file.type, filename: file.name });
          state[target].push(newDoc);
          state.lastUploadedId[target] = newDoc.id;
          renderDocList(target);
        }
        if (target === "cv") {
          state.hasCv = !!data.has_cv;
          updateCvGate();
        }
        completeFilenameBtn.textContent = file.name;
        setCardState(target, "complete");
      } else {
        alert((data && data.error) || "Upload failed. Please try again.");
        setCardState(target, "empty");
      }
    };

    xhr.onerror = function () {
      alert("Upload failed — check your connection and try again.");
      setCardState(target, "empty");
    };

    xhr.send(formData);
  }

  function deleteDoc(target, id) {
    fetch("/api/onboarding/document/" + id, { method: "DELETE" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state[target] = state[target].filter(function (d) { return d.id !== id; });
        knownIds.delete(id);
        var info = previews.get(id);
        if (info) {
          URL.revokeObjectURL(info.url);
          previews.delete(id);
        }
        renderDocList(target);
        if (target === "cv") {
          state.hasCv = !!(data && data.has_cv);
          updateCvGate();
        }
      })
      .catch(function () {
        alert("Couldn't remove that document. Please try again.");
      });
  }

  function wireUploadCard(target) {
    var card = document.getElementById(target + "-upload-card");
    var fileInput = card.querySelector(".ob-file-input");
    var filesBtn = card.querySelector(".ob-files-btn");
    var clearBtn = card.querySelector(".ob-clear-btn");
    var previewTrigger = card.querySelector(".ob-preview-trigger");
    var addMoreBtn = card.querySelector(".ob-add-more-btn");

    // The file input is nested inside the card so a plain click on it
    // would bubble straight back up to the card's own listener below,
    // re-triggering fileInput.click() and reopening the picker the
    // moment a file was chosen. Stop it right at the source.
    fileInput.addEventListener("click", function (e) { e.stopPropagation(); });

    card.addEventListener("click", function (e) {
      if (card.dataset.state === "uploading") return;
      if (e.target.closest(".ob-files-btn") || e.target.closest(".ob-clear-btn") || e.target.closest(".ob-preview-trigger") || e.target.closest(".ob-add-more-btn")) return;
      fileInput.click();
    });

    filesBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      fileInput.click();
    });

    // Once a card shows its "complete" state, the idle state's own
    // "Open Files" button is hidden -- this is the only remaining,
    // clearly-labeled way to add a further document instead of the
    // card's completed filename/checkmark display, which otherwise
    // gives no visible cue that uploading again is even possible.
    addMoreBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      fileInput.click();
    });

    fileInput.addEventListener("change", function () {
      if (fileInput.files[0]) uploadFile(target, fileInput.files[0]);
      fileInput.value = "";
    });

    clearBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var id = state.lastUploadedId[target];
      setCardState(target, "empty");
      if (id) deleteDoc(target, id);
    });

    previewTrigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var id = state.lastUploadedId[target];
      var doc = state[target].filter(function (d) { return d.id === id; })[0];
      if (doc) openPreview(doc);
    });
  }

  function goToStep(step) {
    stepName.hidden = step !== "name";
    stepCv.hidden = step !== "cv";
    stepSupporting.hidden = step !== "supporting";
    document.querySelectorAll('[data-dot="name"]').forEach(function (d) { d.classList.toggle("active", step === "name"); });
    document.querySelectorAll('[data-dot="cv"]').forEach(function (d) { d.classList.toggle("active", step === "cv"); });
    document.querySelectorAll('[data-dot="supporting"]').forEach(function (d) { d.classList.toggle("active", step === "supporting"); });
  }

  nameNextBtn.addEventListener("click", function () {
    var name = nameInput.value.trim();
    if (!name) {
      nameError.hidden = false;
      nameError.textContent = "Please enter your name.";
      nameInput.focus();
      return;
    }
    nameError.hidden = true;
    nameNextBtn.disabled = true;
    fetch("/api/onboarding/name", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        nameNextBtn.disabled = false;
        if (data.ok) {
          goToStep("cv");
        } else {
          nameError.hidden = false;
          nameError.textContent = data.error || "Something went wrong. Please try again.";
        }
      })
      .catch(function () {
        nameNextBtn.disabled = false;
        nameError.hidden = false;
        nameError.textContent = "Connection error — please try again.";
      });
  });

  var reviewOverlay = document.getElementById("ob-review-overlay");
  var reviewList = document.getElementById("ob-review-list");
  var reviewContinueBtn = document.getElementById("ob-review-continue-btn");
  var reviewAddMoreBtn = document.getElementById("ob-review-add-more-btn");
  var analyzingOverlay = document.getElementById("ob-analyzing-overlay");

  function openReviewModal() {
    var allDocs = state.cv.concat(state.supporting);
    reviewList.innerHTML = "";
    allDocs.forEach(function (doc) {
      var item = document.createElement("div");
      item.className = "ob-review-item";
      item.textContent = doc.filename;
      reviewList.appendChild(item);
    });
    reviewOverlay.hidden = false;
  }

  reviewAddMoreBtn.addEventListener("click", function () {
    reviewOverlay.hidden = true;
  });

  reviewContinueBtn.addEventListener("click", function () {
    reviewOverlay.hidden = true;
    finalizeOnboarding();
  });

  var analyzingSubtext = document.getElementById("ob-analyzing-subtext");
  var ANALYZING_MESSAGES = [
    "This may take a while…",
    "Almost there…",
    "Just a moment…",
    "Hang tight…",
  ];
  var analyzingMessageTimer = null;

  function randomInterval() {
    return 5000 + Math.random() * 5000; // 5-10s
  }

  function startAnalyzingMessages() {
    var i = 0;
    analyzingSubtext.textContent = ANALYZING_MESSAGES[0];
    function scheduleNext() {
      analyzingMessageTimer = setTimeout(function () {
        i = (i + 1) % ANALYZING_MESSAGES.length;
        analyzingSubtext.textContent = ANALYZING_MESSAGES[i];
        scheduleNext();
      }, randomInterval());
    }
    scheduleNext();
  }

  function stopAnalyzingMessages() {
    if (analyzingMessageTimer) {
      clearTimeout(analyzingMessageTimer);
      analyzingMessageTimer = null;
    }
  }

  function finalizeOnboarding() {
    analyzingOverlay.hidden = false;
    startAnalyzingMessages();
    var allIds = state.cv.concat(state.supporting).map(function (d) { return d.id; });

    // The server marks documents_confirmed BEFORE it ever starts the
    // slow scoring call (see api_onboarding_confirm in app.py) — so by
    // the time this timeout could ever fire, onboarding itself has
    // already gone through server-side. That means the right thing to
    // do on a timeout/dead-connection isn't to show an error and leave
    // the user stuck here — it's to just go to the dashboard like a
    // normal completion would. If scoring genuinely didn't finish in
    // time, the dashboard's own "Refresh Score" button picks it up
    // from there instead of a failed request stranding the user on
    // this screen.
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, 28000);

    fetch("/api/onboarding/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "", keep_document_ids: allIds }),
      signal: controller.signal,
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        clearTimeout(timeoutId);
        if (data.ok) {
          window.location.href = "/dashboard";
        } else {
          // A real validation failure (e.g. no documents) — nothing
          // was confirmed server-side, so this is the one case where
          // staying put and telling the user is actually correct.
          stopAnalyzingMessages();
          analyzingOverlay.hidden = true;
          alert(data.error || "Something went wrong. Please try again.");
        }
      })
      .catch(function () {
        clearTimeout(timeoutId);
        window.location.href = "/dashboard";
      });
  }

  wireUploadCard("cv");
  wireUploadCard("supporting");
  renderDocList("cv");
  renderDocList("supporting");
  updateCvGate();

  // Documents that already existed on page load (e.g. a refresh mid-onboarding)
  // start each card in "complete" state so the flow doesn't look reset.
  if (state.cv.length > 0) {
    var lastCv = state.cv[state.cv.length - 1];
    state.lastUploadedId.cv = lastCv.id;
    document.querySelector("#cv-upload-card .ob-state-complete .ob-preview-trigger").textContent = lastCv.filename;
    setCardState("cv", "complete");
  }
  if (state.supporting.length > 0) {
    var lastSupporting = state.supporting[state.supporting.length - 1];
    state.lastUploadedId.supporting = lastSupporting.id;
    document.querySelector("#supporting-upload-card .ob-state-complete .ob-preview-trigger").textContent = lastSupporting.filename;
    setCardState("supporting", "complete");
  }

  cvNextBtn.addEventListener("click", function () {
    if (!state.hasCv) return;
    goToStep("supporting");
  });

  supportingNextBtn.addEventListener("click", openReviewModal);

  goToStep(!state.hasName ? "name" : (state.hasCv ? "supporting" : "cv"));
})();
