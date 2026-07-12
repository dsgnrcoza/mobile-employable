(function () {
  "use strict";

  var listEl = document.getElementById("convs-list");
  var emptyEl = document.getElementById("convs-empty");

  function formatRelativeTime(iso) {
    if (!iso) return "";
    var then = new Date(iso.replace(" ", "T") + "Z");
    var diffMin = Math.round((Date.now() - then.getTime()) / 60000);
    if (diffMin < 1) return "Just now";
    if (diffMin < 60) return diffMin + "m ago";
    var diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return diffHr + "h ago";
    return Math.round(diffHr / 24) + "d ago";
  }

  function subtitleFor(c) {
    if (c.kind === "job" && (c.job_title || c.company)) {
      return [c.job_title, c.company].filter(Boolean).join(" · ");
    }
    return formatRelativeTime(c.updated_at);
  }

  function load() {
    fetch("/api/chat/conversations")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        listEl.innerHTML = "";
        if (!data.conversations.length) {
          emptyEl.hidden = false;
          return;
        }
        emptyEl.hidden = true;
        data.conversations.forEach(function (c) {
          var row = document.createElement("div");
          row.className = "convs-item";

          var link = document.createElement("a");
          link.className = "convs-item-link";
          link.href = "/dashboard?conv=" + c.id;

          var titleEl = document.createElement("span");
          titleEl.className = "convs-item-title";
          titleEl.textContent = c.title || "Conversation";
          link.appendChild(titleEl);

          var sub = subtitleFor(c);
          if (sub) {
            var subEl = document.createElement("span");
            subEl.className = "convs-item-sub";
            subEl.textContent = sub;
            link.appendChild(subEl);
          }

          if (c.kind === "job" && c.status_label) {
            var badge = document.createElement("span");
            badge.className = "convs-item-badge mono";
            badge.textContent = c.status_label;
            link.appendChild(badge);
          }

          row.appendChild(link);

          var delBtn = document.createElement("button");
          delBtn.type = "button";
          delBtn.className = "convs-item-delete";
          delBtn.setAttribute("aria-label", "Delete conversation");
          delBtn.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
          delBtn.addEventListener("click", function () {
            fetch("/api/chat/conversations/" + c.id, { method: "DELETE" })
              .then(load);
          });
          row.appendChild(delBtn);

          listEl.appendChild(row);
        });
      });
  }

  load();
})();
