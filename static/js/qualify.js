(function () {
  "use strict";

  var uploadBlock = document.getElementById("qualify-upload-block");
  var uploadBtn = document.getElementById("qualify-upload-btn");
  var uploadInput = document.getElementById("qualify-upload-input");
  var uploadStatus = document.getElementById("qualify-upload-status");
  var formBlock = document.getElementById("qualify-form-block");

  var jobTitleInput = document.getElementById("qualify-job-title-input");
  var salaryInput = document.getElementById("qualify-salary-input");
  var locationInput = document.getElementById("qualify-location-input");
  var checkBtn = document.getElementById("qualify-check-btn");

  var resultEl = document.getElementById("qualify-result");
  var verdictBadge = document.getElementById("qualify-verdict-badge");
  var headlineEl = document.getElementById("qualify-headline");
  var reasoningEl = document.getElementById("qualify-reasoning");
  var nextStepsTitle = document.getElementById("qualify-next-steps-title");
  var nextStepsEl = document.getElementById("qualify-next-steps");

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function formatText(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  uploadBtn.addEventListener("click", function () { uploadInput.click(); });

  uploadInput.addEventListener("change", function () {
    var files = uploadInput.files;
    if (!files || !files.length) return;
    var formData = new FormData();
    for (var i = 0; i < files.length; i++) formData.append("documents", files[i]);

    uploadStatus.hidden = false;
    uploadStatus.textContent = "Uploading…";
    uploadBtn.disabled = true;

    fetch("/api/upload", { method: "POST", body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          uploadBlock.hidden = true;
          formBlock.hidden = false;
        } else {
          uploadStatus.textContent = data.error || "Upload failed — try again.";
        }
      })
      .catch(function () {
        uploadStatus.textContent = "Connection error — please try again.";
      })
      .finally(function () {
        uploadBtn.disabled = false;
        uploadInput.value = "";
      });
  });

  checkBtn.addEventListener("click", function () {
    var jobTitle = jobTitleInput.value.trim();
    if (!jobTitle) {
      jobTitleInput.focus();
      return;
    }
    checkBtn.disabled = true;
    checkBtn.textContent = "Checking…";
    resultEl.hidden = true;

    fetch("/api/qualify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_title: jobTitle,
        salary_expectation: salaryInput.value.trim(),
        location: locationInput.value.trim(),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          headlineEl.innerHTML = formatText(data.error || "Something went wrong — try again.");
          verdictBadge.hidden = true;
          reasoningEl.parentElement.hidden = true;
          nextStepsEl.parentElement.hidden = true;
          resultEl.hidden = false;
          return;
        }
        verdictBadge.hidden = false;
        verdictBadge.textContent = data.qualifies ? "Qualifies" : "Not yet";
        verdictBadge.className = "qualify-verdict-badge " + (data.qualifies ? "qualify-verdict-yes" : "qualify-verdict-no");
        headlineEl.innerHTML = formatText(data.headline);
        reasoningEl.innerHTML = formatText(data.reasoning);
        reasoningEl.parentElement.hidden = false;
        nextStepsTitle.textContent = data.qualifies ? "How to double down" : "How to qualify";
        nextStepsEl.innerHTML = formatText(data.next_steps);
        nextStepsEl.parentElement.hidden = false;
        resultEl.hidden = false;
      })
      .catch(function () {
        headlineEl.textContent = "Connection error — please try again.";
        verdictBadge.hidden = true;
        reasoningEl.parentElement.hidden = true;
        nextStepsEl.parentElement.hidden = true;
        resultEl.hidden = false;
      })
      .finally(function () {
        checkBtn.disabled = false;
        checkBtn.textContent = "Check my fit";
      });
  });
})();
