(function () {
  "use strict";

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

  function sendChatMessage() {
    var text = chatInput.value.trim();
    if (!text || chatSending) return;
    chatInput.value = "";
    showEmptyState(false);
    appendChatMessage("user", text);
    chatHistory.push({ role: "user", text: text });

    var typingEl = document.createElement("div");
    typingEl.className = "chat-msg-typing";
    typingEl.textContent = "Typing…";
    messagesEl.appendChild(typingEl);
    scrollChatToBottom();

    chatSending = true;
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory }),
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

  // The attach endpoint (/api/chat/upload) already exists server-side
  // from before this redesign -- this button is wired to the file
  // picker as a placeholder for tonight; actually uploading and
  // attaching the result to the next message is follow-up work, not
  // part of this navigation/shell pass.
  if (chatAttachBtn) {
    chatAttachBtn.addEventListener("click", function () {
      chatInput.focus();
    });
  }

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
    window.location.href = "/tool/notes";
  });
  document.getElementById("drawer-profile-btn").addEventListener("click", function () {
    window.location.href = "/tool/notes";
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
