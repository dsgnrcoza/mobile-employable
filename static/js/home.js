(function () {
  "use strict";

  // ---------- Smooth keyboard open/close ----------
  // The on-screen keyboard shrinks the visual viewport without firing
  // any layout event the CSS box model reacts to on its own -- without
  // this, the input bar either stays hidden behind the keyboard or
  // snaps into place the instant the keyboard finishes animating.
  // Mirroring the visual viewport's shrinkage into a CSS custom
  // property (which .home-shell transitions smoothly) makes the whole
  // page glide up in sync with the keyboard instead.
  if (window.visualViewport) {
    var vv = window.visualViewport;
    var updateKeyboardOffset = function () {
      var offset = window.innerHeight - vv.height - vv.offsetTop;
      document.documentElement.style.setProperty("--kb-offset", Math.max(0, offset).toFixed(1) + "px");
    };
    vv.addEventListener("resize", updateKeyboardOffset);
    vv.addEventListener("scroll", updateKeyboardOffset);
    updateKeyboardOffset();
  }

  var state = window.HOME_STATE || {};
  var profile = state.profile || {};

  function firstName() {
    var raw = (profile.full_name || "").trim();
    if (!raw) return "there";
    return raw.split(/\s+/)[0];
  }

  function timeGreeting() {
    var hour = new Date().getHours();
    if (hour < 12) return "Morning";
    if (hour < 18) return "Afternoon";
    return "Evening";
  }

  document.getElementById("home-greeting").textContent = timeGreeting() + ", " + firstName();

  // ---------- Chat (same /api/chat + /api/chat/conversations contract ----------
  // the old dashboard.js chat used -- nothing changed server-side, this
  // is just a leaner client for the new single-screen home view.

  var emptyEl = document.getElementById("home-empty");
  var messagesEl = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatAttachBtn = document.getElementById("chat-attach-btn");

  var chatHistory = [];
  var chatConversationId = null;
  var chatSending = false;

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function formatChatText(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function scrollChatToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showEmptyState(show) {
    emptyEl.hidden = !show;
    messagesEl.hidden = show;
  }

  function appendChatMessage(role, text) {
    var el = document.createElement("div");
    el.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-bot");
    el.innerHTML = formatChatText(text);
    messagesEl.appendChild(el);
    scrollChatToBottom();
    return el;
  }

  function appendChatMessageTyped(text) {
    var el = appendChatMessage("assistant", "");
    var i = 0;
    var step = Math.max(1, Math.ceil(text.length / 40));
    var timer = setInterval(function () {
      i += step;
      el.innerHTML = formatChatText(text.slice(0, i));
      scrollChatToBottom();
      if (i >= text.length) clearInterval(timer);
    }, 15);
  }

  function conversationTitleFromHistory(history) {
    var firstUser = history.filter(function (m) { return m.role === "user"; })[0];
    if (!firstUser) return "New conversation";
    var text = firstUser.text.trim().replace(/\s+/g, " ");
    return text.length > 60 ? text.slice(0, 60) + "…" : text;
  }

  function saveConversation() {
    fetch("/api/chat/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: chatConversationId,
        title: conversationTitleFromHistory(chatHistory),
        messages: chatHistory.map(function (m) { return { role: m.role, text: m.text }; }),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          chatConversationId = data.conversation_id;
          loadRecents();
        }
      })
      .catch(function () {});
  }

  // ---------- Pending attachments (staged before the next send) ----------

  var pendingAttachmentsEl = document.getElementById("pending-attachments");
  var pendingAttachments = []; // { id, name, is_image }

  function renderPendingAttachments() {
    pendingAttachmentsEl.innerHTML = "";
    pendingAttachmentsEl.hidden = pendingAttachments.length === 0;
    pendingAttachments.forEach(function (att) {
      var chip = document.createElement("span");
      chip.className = "attachment-chip";
      chip.innerHTML =
        '<span class="attachment-chip-name"></span>' +
        '<button type="button" class="attachment-chip-remove" aria-label="Remove">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';
      chip.querySelector(".attachment-chip-name").textContent = att.name;
      chip.querySelector(".attachment-chip-remove").addEventListener("click", function () {
        pendingAttachments = pendingAttachments.filter(function (a) { return a.id !== att.id; });
        renderPendingAttachments();
      });
      pendingAttachmentsEl.appendChild(chip);
    });
  }

  function uploadFile(file) {
    if (!file) return;
    var formData = new FormData();
    formData.append("file", file);
    fetch("/api/chat/upload", { method: "POST", body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          pendingAttachments.push({ id: data.id, name: data.name, is_image: data.is_image });
          renderPendingAttachments();
        }
      });
  }

  ["attach-camera-input", "attach-photos-input", "attach-files-input"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", function (e) {
      if (e.target.files && e.target.files[0]) uploadFile(e.target.files[0]);
      e.target.value = "";
    });
  });

  // ---------- Send ----------

  function sendChatMessage() {
    var text = chatInput.value.trim();
    if ((!text && pendingAttachments.length === 0) || chatSending) return;
    var attachmentIds = pendingAttachments.map(function (a) { return a.id; });
    var displayText = text;
    if (pendingAttachments.length) {
      displayText += pendingAttachments.map(function (a) { return " [Attached: " + a.name + "]"; }).join("");
    }
    chatInput.value = "";
    pendingAttachments = [];
    renderPendingAttachments();
    showEmptyState(false);
    appendChatMessage("user", displayText);
    chatHistory.push({ role: "user", text: displayText, attachment_ids: attachmentIds });

    var typingEl = document.createElement("div");
    typingEl.className = "chat-msg-typing";
    typingEl.textContent = "Typing…";
    messagesEl.appendChild(typingEl);
    scrollChatToBottom();

    chatSending = true;
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory, use_notes: isUseNotesEnabled() }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        typingEl.remove();
        if (data.ok) {
          appendChatMessageTyped(data.reply);
          chatHistory.push({ role: "assistant", text: data.reply });
          saveConversation();
        } else {
          appendChatMessageTyped("Sorry, something went wrong there. Try again?");
        }
      })
      .catch(function () {
        typingEl.remove();
        appendChatMessageTyped("Connection error — please try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  chatSendBtn.addEventListener("click", sendChatMessage);
  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendChatMessage();
  });

  // ---------- Attach sheet ("Add context") ----------

  var attachSheetOverlay = document.getElementById("attach-sheet-overlay");

  function openAttachSheet() {
    attachSheetOverlay.hidden = false;
  }
  function closeAttachSheet() {
    attachSheetOverlay.hidden = true;
  }

  if (chatAttachBtn) {
    chatAttachBtn.addEventListener("click", openAttachSheet);
  }
  document.getElementById("attach-sheet-close-btn").addEventListener("click", closeAttachSheet);
  attachSheetOverlay.addEventListener("click", function (e) {
    if (e.target === attachSheetOverlay) closeAttachSheet();
  });

  document.getElementById("attach-camera-btn").addEventListener("click", function () {
    document.getElementById("attach-camera-input").click();
  });
  document.getElementById("attach-photos-btn").addEventListener("click", function () {
    document.getElementById("attach-photos-input").click();
  });
  document.getElementById("attach-files-btn").addEventListener("click", function () {
    document.getElementById("attach-files-input").click();
  });
  document.getElementById("attach-camera-btn").addEventListener("click", closeAttachSheet);
  document.getElementById("attach-photos-btn").addEventListener("click", closeAttachSheet);
  document.getElementById("attach-files-btn").addEventListener("click", closeAttachSheet);

  // ---------- "Use Notes" toggle ----------
  // Off by default and never sent to the server at all unless it's
  // explicitly on for that message (see the use_notes flag in
  // sendChatMessage above) -- notes stay private between the app and
  // the user unless this is switched on.

  var useNotesToggle = document.getElementById("use-notes-toggle");
  var useNotesTooltip = document.getElementById("use-notes-tooltip");
  var useNotesTooltipGotIt = document.getElementById("use-notes-tooltip-got-it");
  var USE_NOTES_TOOLTIP_MAX_SHOWS = 3;

  function isUseNotesEnabled() {
    return localStorage.getItem("useNotesEnabled") === "true";
  }

  function tooltipShowCount() {
    return parseInt(localStorage.getItem("useNotesTooltipCount") || "0", 10);
  }

  function applyUseNotesToggleUI() {
    var on = isUseNotesEnabled();
    useNotesToggle.classList.toggle("is-on", on);
    useNotesToggle.setAttribute("aria-checked", String(on));
  }
  applyUseNotesToggleUI();

  useNotesToggle.addEventListener("click", function () {
    var goingOn = !isUseNotesEnabled();
    localStorage.setItem("useNotesEnabled", goingOn ? "true" : "false");
    applyUseNotesToggleUI();

    if (goingOn && tooltipShowCount() < USE_NOTES_TOOLTIP_MAX_SHOWS) {
      useNotesTooltip.hidden = false;
    } else {
      useNotesTooltip.hidden = true;
    }
  });

  useNotesTooltipGotIt.addEventListener("click", function () {
    localStorage.setItem("useNotesTooltipCount", String(tooltipShowCount() + 1));
    useNotesTooltip.hidden = true;
  });

  function loadConversation(convId) {
    fetch("/api/chat/conversations/" + convId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        chatConversationId = convId;
        chatHistory = data.messages.map(function (m) { return { role: m.role, text: m.text }; });
        messagesEl.innerHTML = "";
        if (chatHistory.length === 0) {
          showEmptyState(true);
          return;
        }
        showEmptyState(false);
        chatHistory.forEach(function (m) { appendChatMessage(m.role, m.text); });
      });
  }

  function startNewChat() {
    chatConversationId = null;
    chatHistory = [];
    messagesEl.innerHTML = "";
    showEmptyState(true);
    chatInput.value = "";
    chatInput.focus();
  }

  document.getElementById("home-ghost-btn").addEventListener("click", startNewChat);

  // ---------- Recents (drawer) ----------

  var recentsListEl = document.getElementById("drawer-recents-list");

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

  function loadRecents() {
    fetch("/api/chat/conversations")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        recentsListEl.innerHTML = "";
        if (!data.conversations.length) {
          var empty = document.createElement("div");
          empty.className = "drawer-recents-empty";
          empty.textContent = "No conversations yet.";
          recentsListEl.appendChild(empty);
          return;
        }
        data.conversations.forEach(function (c) {
          var row = document.createElement("button");
          row.type = "button";
          row.className = "drawer-recent-row";
          row.textContent = c.title || "Conversation";
          row.title = formatRelativeTime(c.updated_at);
          row.addEventListener("click", function () {
            loadConversation(c.id);
            closeDrawer();
          });
          recentsListEl.appendChild(row);
        });
      })
      .catch(function () {});
  }

  // ---------- Drawer open/close ----------

  var drawerEl = document.getElementById("drawer");
  var drawerBackdropEl = document.getElementById("drawer-backdrop");

  function openDrawer() {
    drawerEl.hidden = false;
    drawerBackdropEl.hidden = false;
    // Forces layout before adding the class so the transform
    // transition actually plays instead of the drawer snapping open.
    void drawerEl.offsetHeight;
    drawerEl.classList.add("is-open");
    drawerBackdropEl.classList.add("is-open");
    loadRecents();
  }

  function closeDrawer() {
    drawerEl.classList.remove("is-open");
    drawerBackdropEl.classList.remove("is-open");
  }

  drawerEl.addEventListener("transitionend", function () {
    if (!drawerEl.classList.contains("is-open")) {
      drawerEl.hidden = true;
      drawerBackdropEl.hidden = true;
    }
  });

  document.getElementById("drawer-open-btn").addEventListener("click", openDrawer);
  drawerBackdropEl.addEventListener("click", closeDrawer);
  document.getElementById("drawer-new-chat-btn").addEventListener("click", function () {
    startNewChat();
    closeDrawer();
  });
  document.getElementById("home-notepad-btn").addEventListener("click", function () {
    window.location.href = "/notes";
  });
  document.getElementById("drawer-profile-btn").addEventListener("click", function () {
    window.location.href = "/notes";
  });

  // ---------- Initial load ----------

  fetch("/api/chat/conversations")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.ok && data.conversations.length) {
        loadConversation(data.conversations[0].id);
      } else {
        showEmptyState(true);
      }
    })
    .catch(function () { showEmptyState(true); })
    .finally(function () {
      // Best-effort: mobile browsers generally only honor .focus()
      // triggering the on-screen keyboard when it runs in direct
      // response to a user gesture, not on a plain page-load handler --
      // this still focuses the field itself (cursor + outline) even on
      // browsers that don't pop the keyboard for it automatically.
      chatInput.focus();
    });
})();
