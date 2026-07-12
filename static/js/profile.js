(function () {
  "use strict";

  function setStatus(el, text, isError) {
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("profile-status-error", !!isError);
  }

  // ---------- Profile photo (with a basic crop/zoom step before upload) ----------

  var photoBtn = document.getElementById("profile-photo-btn");
  var photoInput = document.getElementById("profile-photo-input");
  var photoStatus = document.getElementById("profile-photo-status");

  var cropOverlay = document.getElementById("photo-crop-overlay");
  var cropViewport = document.getElementById("photo-crop-viewport");
  var cropImg = document.getElementById("photo-crop-img");
  var cropZoom = document.getElementById("photo-crop-zoom");
  var cropCancelBtn = document.getElementById("photo-crop-cancel-btn");
  var cropUseBtn = document.getElementById("photo-crop-use-btn");

  var cropState = { naturalW: 0, naturalH: 0, baseScale: 1, zoom: 1, offsetX: 0, offsetY: 0 };
  var dragging = false, dragStartX = 0, dragStartY = 0, dragOffsetStartX = 0, dragOffsetStartY = 0;

  function clampOffsets() {
    var size = cropViewport.clientWidth;
    var scale = cropState.baseScale * cropState.zoom;
    var w = cropState.naturalW * scale;
    var h = cropState.naturalH * scale;
    var maxX = Math.max(0, (w - size) / 2);
    var maxY = Math.max(0, (h - size) / 2);
    cropState.offsetX = Math.max(-maxX, Math.min(maxX, cropState.offsetX));
    cropState.offsetY = Math.max(-maxY, Math.min(maxY, cropState.offsetY));
  }

  function renderCrop() {
    clampOffsets();
    var scale = cropState.baseScale * cropState.zoom;
    cropImg.style.width = (cropState.naturalW * scale) + "px";
    cropImg.style.height = (cropState.naturalH * scale) + "px";
    cropImg.style.transform =
      "translate(calc(-50% + " + cropState.offsetX + "px), calc(-50% + " + cropState.offsetY + "px))";
  }

  function startDrag(x, y) {
    dragging = true;
    dragStartX = x;
    dragStartY = y;
    dragOffsetStartX = cropState.offsetX;
    dragOffsetStartY = cropState.offsetY;
  }
  function moveDrag(x, y) {
    if (!dragging) return;
    cropState.offsetX = dragOffsetStartX + (x - dragStartX);
    cropState.offsetY = dragOffsetStartY + (y - dragStartY);
    renderCrop();
  }
  function endDrag() { dragging = false; }

  cropViewport.addEventListener("mousedown", function (e) { startDrag(e.clientX, e.clientY); });
  window.addEventListener("mousemove", function (e) { moveDrag(e.clientX, e.clientY); });
  window.addEventListener("mouseup", endDrag);
  cropViewport.addEventListener("touchstart", function (e) {
    var t = e.touches[0];
    startDrag(t.clientX, t.clientY);
  });
  cropViewport.addEventListener("touchmove", function (e) {
    var t = e.touches[0];
    moveDrag(t.clientX, t.clientY);
  });
  cropViewport.addEventListener("touchend", endDrag);

  cropZoom.addEventListener("input", function () {
    cropState.zoom = parseFloat(cropZoom.value);
    renderCrop();
  });

  function closeCropModal() {
    cropOverlay.hidden = true;
    if (cropImg.src) URL.revokeObjectURL(cropImg.src);
    cropImg.src = "";
  }

  cropCancelBtn.addEventListener("click", closeCropModal);

  function uploadCroppedPhoto() {
    var size = cropViewport.clientWidth;
    var scale = cropState.baseScale * cropState.zoom;
    var OUTPUT = 512;
    var outScale = OUTPUT / size;

    var canvas = document.createElement("canvas");
    canvas.width = OUTPUT;
    canvas.height = OUTPUT;
    var ctx = canvas.getContext("2d");
    var drawW = cropState.naturalW * scale * outScale;
    var drawH = cropState.naturalH * scale * outScale;
    var drawX = OUTPUT / 2 - drawW / 2 + cropState.offsetX * outScale;
    var drawY = OUTPUT / 2 - drawH / 2 + cropState.offsetY * outScale;
    ctx.drawImage(cropImg, drawX, drawY, drawW, drawH);

    canvas.toBlob(function (blob) {
      if (!blob) {
        setStatus(photoStatus, "Couldn't process that image.", true);
        return;
      }
      var formData = new FormData();
      formData.append("photo", blob, "avatar.jpg");
      setStatus(photoStatus, "Uploading…", false);
      closeCropModal();
      fetch("/api/profile/photo", { method: "POST", body: formData })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            setStatus(photoStatus, data.error || "Couldn't upload that photo.", true);
            return;
          }
          setStatus(photoStatus, "", false);
          var preview = document.getElementById("profile-photo-preview");
          var img = document.createElement("img");
          img.id = "profile-photo-preview";
          img.className = "profile-photo-preview";
          img.alt = "";
          img.src = data.avatar_url;
          preview.replaceWith(img);
        })
        .catch(function () {
          setStatus(photoStatus, "Connection error — please try again.", true);
        });
    }, "image/jpeg", 0.9);
  }

  cropUseBtn.addEventListener("click", uploadCroppedPhoto);

  photoBtn.addEventListener("click", function () { photoInput.click(); });

  photoInput.addEventListener("change", function () {
    var file = photoInput.files[0];
    photoInput.value = "";
    if (!file) return;

    var url = URL.createObjectURL(file);
    cropImg.onload = function () {
      cropState.naturalW = cropImg.naturalWidth;
      cropState.naturalH = cropImg.naturalHeight;
      cropState.zoom = 1;
      cropState.offsetX = 0;
      cropState.offsetY = 0;
      cropZoom.value = 1;
      cropOverlay.hidden = false;
      var size = cropViewport.clientWidth;
      cropState.baseScale = size / Math.min(cropState.naturalW, cropState.naturalH);
      renderCrop();
    };
    cropImg.src = url;
  });

  // ---------- Account ----------

  var fullNameInput = document.getElementById("profile-full-name");
  var emailInput = document.getElementById("profile-email");
  var accountSaveBtn = document.getElementById("profile-account-save-btn");
  var accountStatus = document.getElementById("profile-account-status");

  accountSaveBtn.addEventListener("click", function () {
    accountSaveBtn.disabled = true;
    fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullNameInput.value, email: emailInput.value }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(accountStatus, data.ok ? "Saved." : (data.error || "Couldn't save."), !data.ok);
      })
      .catch(function () {
        setStatus(accountStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        accountSaveBtn.disabled = false;
      });
  });

  // ---------- Personality (custom instructions) ----------

  var instructionsInput = document.getElementById("profile-instructions-input");
  var instructionsSaveBtn = document.getElementById("profile-instructions-save-btn");
  var instructionsStatus = document.getElementById("profile-instructions-status");

  instructionsSaveBtn.addEventListener("click", function () {
    instructionsSaveBtn.disabled = true;
    fetch("/api/custom-instructions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ custom_instructions: instructionsInput.value }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(instructionsStatus, data.ok ? "Saved." : (data.error || "Couldn't save."), !data.ok);
      })
      .catch(function () {
        setStatus(instructionsStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        instructionsSaveBtn.disabled = false;
      });
  });

  // ---------- Password ----------

  var currentPasswordInput = document.getElementById("profile-current-password");
  var newPasswordInput = document.getElementById("profile-new-password");
  var confirmPasswordInput = document.getElementById("profile-confirm-password");
  var passwordSaveBtn = document.getElementById("profile-password-save-btn");
  var passwordStatus = document.getElementById("profile-password-status");

  passwordSaveBtn.addEventListener("click", function () {
    passwordSaveBtn.disabled = true;
    fetch("/api/profile/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password_or_key: currentPasswordInput.value,
        new_password: newPasswordInput.value,
        confirm_password: confirmPasswordInput.value,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(passwordStatus, data.ok ? "Password changed." : (data.error || "Couldn't change password."), !data.ok);
        if (data.ok) {
          currentPasswordInput.value = "";
          newPasswordInput.value = "";
          confirmPasswordInput.value = "";
          // Non-null only when the security key (not the ordinary
          // password) was used as proof -- it's single-use, so a fresh
          // one was just issued and needs to be shown right now.
          if (data.new_security_key) revealSecurityKey(data.new_security_key);
        }
      })
      .catch(function () {
        setStatus(passwordStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        passwordSaveBtn.disabled = false;
      });
  });

  // ---------- Security key ----------

  var regenerateKeyBtn = document.getElementById("profile-regenerate-key-btn");
  var keyStatus = document.getElementById("profile-key-status");
  var keyRevealBox = document.getElementById("profile-key-reveal");
  var keyRevealValue = document.getElementById("profile-key-reveal-value");
  var keyRevealCopyBtn = document.getElementById("profile-key-reveal-copy-btn");
  var keyRevealWarning = document.getElementById("profile-key-reveal-warning");

  function revealSecurityKey(key) {
    keyRevealValue.textContent = key;
    keyRevealBox.hidden = false;
    keyRevealWarning.hidden = false;
  }

  keyRevealCopyBtn.addEventListener("click", function () {
    var text = keyRevealValue.textContent || "";
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () {
      keyRevealCopyBtn.classList.add("is-copied");
      setTimeout(function () { keyRevealCopyBtn.classList.remove("is-copied"); }, 1500);
    });
  });

  regenerateKeyBtn.addEventListener("click", function () {
    if (!window.confirm("This replaces your current security key and invalidates the old one immediately. Continue?")) return;
    regenerateKeyBtn.disabled = true;
    fetch("/api/profile/security-key/regenerate", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          setStatus(keyStatus, data.error || "Couldn't regenerate.", true);
          return;
        }
        setStatus(keyStatus, "", false);
        revealSecurityKey(data.security_key);
      })
      .catch(function () {
        setStatus(keyStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        regenerateKeyBtn.disabled = false;
      });
  });

  // ---------- Documents ----------

  var docListEl = document.getElementById("profile-doc-list");
  var uploadInput = document.getElementById("profile-upload-input");
  var uploadBtn = document.getElementById("profile-upload-btn");
  var docStatus = document.getElementById("profile-doc-status");

  function formatFileSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function loadDocuments() {
    fetch("/api/profile/documents")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        docListEl.innerHTML = "";
        if (!data.documents.length) {
          var empty = document.createElement("p");
          empty.className = "profile-doc-empty";
          empty.textContent = "No documents uploaded yet.";
          docListEl.appendChild(empty);
          return;
        }
        data.documents.forEach(function (d) {
          var row = document.createElement("div");
          row.className = "profile-doc-row";
          row.innerHTML =
            '<div class="profile-doc-meta">' +
            '<span class="profile-doc-filename">' + d.filename.replace(/[&<>"]/g, function (c) {
              return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
            }) + "</span>" +
            '<span class="profile-doc-sub mono">' + (d.category || "document") + (d.file_size ? " · " + formatFileSize(d.file_size) : "") + "</span>" +
            "</div>" +
            '<button type="button" class="profile-doc-delete-btn" aria-label="Delete">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            "</button>";
          row.querySelector(".profile-doc-delete-btn").addEventListener("click", function () {
            fetch("/api/onboarding/document/" + d.id, { method: "DELETE" })
              .then(function () { loadDocuments(); });
          });
          docListEl.appendChild(row);
        });
      });
  }

  uploadBtn.addEventListener("click", function () { uploadInput.click(); });

  uploadInput.addEventListener("change", function () {
    var file = uploadInput.files[0];
    uploadInput.value = "";
    if (!file) return;
    var formData = new FormData();
    formData.append("documents", file);
    formData.append("category", "cv");
    setStatus(docStatus, "Uploading…", false);
    fetch("/api/onboarding/upload", { method: "POST", body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(docStatus, data.ok ? "Uploaded." : (data.error || "Couldn't upload that file."), !data.ok);
        if (data.ok) loadDocuments();
      })
      .catch(function () {
        setStatus(docStatus, "Connection error — please try again.", true);
      });
  });

  loadDocuments();

  // ---------- Memory (conversations) ----------

  var memoryListEl = document.getElementById("profile-memory-list");
  var memoryClearBtn = document.getElementById("profile-memory-clear-btn");
  var memoryStatus = document.getElementById("profile-memory-status");

  function loadMemory() {
    fetch("/api/chat/conversations")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        memoryListEl.innerHTML = "";
        if (!data.conversations.length) {
          var empty = document.createElement("p");
          empty.className = "profile-memory-empty";
          empty.textContent = "Nothing remembered yet.";
          memoryListEl.appendChild(empty);
          memoryClearBtn.hidden = true;
          return;
        }
        memoryClearBtn.hidden = false;
        data.conversations.forEach(function (c) {
          var row = document.createElement("div");
          row.className = "profile-memory-item";
          row.innerHTML =
            '<span class="profile-memory-item-title"></span>' +
            '<button type="button" class="profile-memory-item-delete" aria-label="Delete">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            "</button>";
          row.querySelector(".profile-memory-item-title").textContent = c.title || "Conversation";
          row.querySelector(".profile-memory-item-delete").addEventListener("click", function () {
            fetch("/api/chat/conversations/" + c.id, { method: "DELETE" })
              .then(function () { loadMemory(); });
          });
          memoryListEl.appendChild(row);
        });
      });
  }

  memoryClearBtn.addEventListener("click", function () {
    if (!window.confirm("Delete every conversation Ploy remembers? This can't be undone.")) return;
    fetch("/api/chat/conversations", { method: "DELETE" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(memoryStatus, data.ok ? "Cleared." : (data.error || "Couldn't clear."), !data.ok);
        loadMemory();
      });
  });

  loadMemory();

  // ---------- Delete account ----------

  document.getElementById("profile-delete-account-btn").addEventListener("click", function () {
    if (!window.confirm("Delete your account? This permanently removes your documents, chats, and profile. This can't be undone.")) return;
    fetch("/api/account/delete", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) window.location.href = "/login";
      });
  });
})();
