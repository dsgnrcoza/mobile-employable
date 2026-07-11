(function () {
  "use strict";

  function setStatus(el, text, isError) {
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle("profile-status-error", !!isError);
  }

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
        current_password: currentPasswordInput.value,
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
        }
      })
      .catch(function () {
        setStatus(passwordStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        passwordSaveBtn.disabled = false;
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

  // ---------- Target roles ----------

  var targetFieldInput = document.getElementById("profile-target-field");
  var targetSaveBtn = document.getElementById("profile-target-save-btn");
  var targetStatus = document.getElementById("profile-target-status");

  targetSaveBtn.addEventListener("click", function () {
    targetSaveBtn.disabled = true;
    fetch("/api/target-field", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_field: targetFieldInput.value }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setStatus(targetStatus, data.ok ? "Saved." : "Couldn't save.", !data.ok);
      })
      .catch(function () {
        setStatus(targetStatus, "Connection error — please try again.", true);
      })
      .finally(function () {
        targetSaveBtn.disabled = false;
      });
  });

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
