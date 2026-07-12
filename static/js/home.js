(function () {
  "use strict";

  // ---------- Smooth keyboard open/close ----------
  // The on-screen keyboard shrinks the visual viewport without firing
  // any layout event the CSS box model reacts to on its own -- without
  // this, the input bar either stays hidden behind the keyboard or
  // snaps into place the instant the keyboard finishes animating.
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

  // ---------- Chat ----------

  var emptyEl = document.getElementById("chat-empty");
  var messagesEl = document.getElementById("chat-messages");
  var chatInput = document.getElementById("chat-input");
  var chatSendBtn = document.getElementById("chat-send-btn");
  var chatAttachBtn = document.getElementById("chat-attach-btn");
  var chatChipsEl = document.getElementById("chat-chips");

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
    chatChipsEl.hidden = !show;
  }

  function appendChatMessage(role, text) {
    var el = document.createElement("div");
    el.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-bot");
    el.innerHTML = formatChatText(text);
    messagesEl.appendChild(el);
    scrollChatToBottom();
    return el;
  }

  function appendChatMessageTyped(text, onDone) {
    var el = appendChatMessage("assistant", "");
    var i = 0;
    var step = Math.max(1, Math.ceil(text.length / 40));
    var timer = setInterval(function () {
      i += step;
      el.innerHTML = formatChatText(text.slice(0, i));
      scrollChatToBottom();
      if (i >= text.length) {
        clearInterval(timer);
        if (onDone) onDone();
      }
    }, 15);
  }

  // The model ends substantive replies with a "[[QUICK_REPLIES]] a | b"
  // line -- parse it out of the reply text to render as tappable
  // buttons instead of showing it as literal text.
  function parseQuickReplies(buffer) {
    var idx = buffer.indexOf("[[QUICK_REPLIES]]");
    if (idx === -1) return { text: buffer.trim(), replies: [] };
    var text = buffer.slice(0, idx).trim();
    var replies = buffer
      .slice(idx + "[[QUICK_REPLIES]]".length)
      .split("|")
      .map(function (s) { return s.trim(); })
      .filter(Boolean)
      .slice(0, 3);
    return { text: text, replies: replies };
  }

  function clearQuickReplies() {
    var existing = messagesEl.querySelectorAll(".chat-quick-replies");
    existing.forEach(function (el) { el.remove(); });
  }

  function appendQuickReplies(replies) {
    if (!replies.length) return;
    var wrap = document.createElement("div");
    wrap.className = "chat-quick-replies";
    replies.forEach(function (label) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-quick-reply-btn";
      btn.textContent = label;
      btn.addEventListener("click", function () { sendChatMessage(label); });
      wrap.appendChild(btn);
    });
    messagesEl.appendChild(wrap);
    scrollChatToBottom();
  }

  // ---------- Signature cards (Verdict / Document) ----------

  function fitTier(score) {
    if (score >= 75) return "good";
    if (score >= 50) return "mid";
    return "bad";
  }

  function diamondList(items, colorClass) {
    if (!items || !items.length) return "";
    return (
      '<ul class="card-diamond-list ' + colorClass + '">' +
      items.map(function (t) { return "<li><span class=\"diamond\">◆</span>" + escapeHtml(t) + "</li>"; }).join("") +
      "</ul>"
    );
  }

  function renderVerdictCard(card) {
    var wrap = document.createElement("div");
    wrap.className = "verdict-card";
    var tier = fitTier(card.fit_score);
    var subtitle = [card.company, card.location].filter(Boolean).join(" · ");
    wrap.innerHTML =
      '<div class="card-header mono">VERDICT</div>' +
      '<div class="verdict-job-title">' + escapeHtml(card.job_title || "This role") + "</div>" +
      (subtitle ? '<div class="verdict-job-sub">' + escapeHtml(subtitle) + "</div>" : "") +
      '<div class="verdict-score mono tier-' + tier + '">' + card.fit_score + '<span class="verdict-score-max">/100</span></div>' +
      diamondList(card.strengths, "diamond-good") +
      diamondList(card.gaps, "diamond-bad") +
      '<div class="card-breakdown" hidden></div>' +
      '<div class="card-actions">' +
      '<button type="button" class="btn btn-gold btn-sm card-fix-btn">Fix my CV for this job</button>' +
      '<button type="button" class="btn btn-ghost btn-sm card-breakdown-btn">Show full breakdown</button>' +
      "</div>";

    wrap.querySelector(".card-fix-btn").addEventListener("click", function () {
      requestDocumentCard({ job_title: card.job_title, company: card.company, job_ad: card.job_ad });
    });
    var breakdownEl = wrap.querySelector(".card-breakdown");
    var breakdownBtn = wrap.querySelector(".card-breakdown-btn");
    breakdownBtn.addEventListener("click", function () {
      var show = breakdownEl.hidden;
      breakdownEl.hidden = !show;
      breakdownEl.textContent = card.breakdown || "";
      breakdownBtn.textContent = show ? "Hide breakdown" : "Show full breakdown";
      scrollChatToBottom();
    });
    return wrap;
  }

  function renderDocumentCard(card) {
    var wrap = document.createElement("div");
    wrap.className = "document-card";
    var statusParts = [card.kind === "cv" ? "Tailored" : "Drafted", "ATS pass ✓"];
    if (card.fit_score != null) statusParts.push("Fit now " + card.fit_score + "/100");
    wrap.innerHTML =
      '<div class="card-header mono">DOCUMENT</div>' +
      '<div class="document-row">' +
      '<div class="document-thumb">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 4h13l3 3v13H4Z"/><path d="M17 4v6h6"/></svg>' +
      "</div>" +
      '<div class="document-meta">' +
      '<div class="document-ready">Ready to send</div>' +
      '<div class="document-filename mono">' + escapeHtml(card.filename) + "</div>" +
      '<div class="document-status mono">' + statusParts.join(" · ") + "</div>" +
      "</div>" +
      "</div>" +
      '<div class="card-actions">' +
      '<button type="button" class="btn btn-gold btn-sm document-download-btn">Download</button>' +
      '<button type="button" class="btn btn-ghost btn-sm document-edit-btn">Edit</button>' +
      (card.kind === "cv" ? '<button type="button" class="btn btn-ghost btn-sm document-letter-btn">Cover letter</button>' : "") +
      (card.kind === "cv" ? '<button type="button" class="btn btn-ghost btn-sm document-applied-btn">Mark as applied</button>' : "") +
      "</div>";

    wrap.querySelector(".document-download-btn").addEventListener("click", function () {
      window.location.href = "/api/document-download/" + card.document_id;
    });
    wrap.querySelector(".document-edit-btn").addEventListener("click", function () {
      window.location.href = "/builder?doc=" + card.document_id;
    });
    if (card.kind === "cv") {
      wrap.querySelector(".document-letter-btn").addEventListener("click", function () {
        requestLetterCard({ job_title: card.job_title, company: card.company });
      });
      var appliedBtn = wrap.querySelector(".document-applied-btn");
      appliedBtn.addEventListener("click", function () {
        appliedBtn.disabled = true;
        appliedBtn.textContent = "Applied ✓";
        markThreadApplied(card);
      });
    }
    return wrap;
  }

  function appendCardMessage(card) {
    clearQuickReplies();
    showEmptyState(false);
    var node = card.type === "verdict" ? renderVerdictCard(card) : renderDocumentCard(card);
    messagesEl.appendChild(node);
    scrollChatToBottom();
    var summary = card.type === "verdict"
      ? "Verdict: " + card.fit_score + "/100 fit for " + (card.job_title || "this role") + (card.company ? " at " + card.company : "") + "."
      : "Document ready: " + card.filename;
    chatHistory.push({ role: "assistant", text: summary, card: card });
    saveConversation().then(function () { promoteThreadForCard(card); });
  }

  function requestCard(url, payload) {
    var typingEl = document.createElement("div");
    typingEl.className = "chat-msg-typing";
    typingEl.textContent = "Thinking…";
    messagesEl.appendChild(typingEl);
    scrollChatToBottom();
    chatSending = true;
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        typingEl.remove();
        if (data.ok) {
          appendCardMessage(data);
        } else {
          appendChatMessage("assistant", data.error || "Something went wrong there. Try again?");
        }
      })
      .catch(function () {
        typingEl.remove();
        appendChatMessage("assistant", "Connection error — please try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  function requestVerdictCard(jobAd) {
    showEmptyState(false);
    clearQuickReplies();
    appendChatMessage("user", "Am I a fit for this job?");
    chatHistory.push({ role: "user", text: "Am I a fit for this job?" });
    requestCard("/api/verdict", { job_ad: jobAd });
  }

  function requestDocumentCard(context) {
    requestCard("/api/document", context);
  }

  function requestBuilderCard() {
    showEmptyState(false);
    clearQuickReplies();
    appendChatMessage("user", "Build my CV.");
    chatHistory.push({ role: "user", text: "Build my CV." });
    requestCard("/api/document", {});
  }

  function requestLetterCard(context) {
    requestCard("/api/letter-document", context);
  }

  function conversationTitleFromHistory(history) {
    var firstUser = history.filter(function (m) { return m.role === "user"; })[0];
    if (!firstUser) return "New conversation";
    var text = firstUser.text.trim().replace(/\s+/g, " ");
    return text.length > 60 ? text.slice(0, 60) + "…" : text;
  }

  function saveConversation() {
    return fetch("/api/chat/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: chatConversationId,
        title: conversationTitleFromHistory(chatHistory),
        messages: chatHistory.map(function (m) { return { role: m.role, text: m.text, card: m.card }; }),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) chatConversationId = data.conversation_id;
      })
      .catch(function () {});
  }

  // ---------- Job-thread promotion ----------
  // The moment a Verdict or Document card lands in a chat, that chat is
  // promoted out of the plain chat list into a tracked "job thread" --
  // this is the whole tracker, there's no separate screen for it (see
  // the sidebar rendering below and db.promote_conversation server-side).

  function promoteThreadForCard(card) {
    if (!chatConversationId) return;
    var statusLabel;
    if (card.type === "verdict") {
      statusLabel = "Fit " + card.fit_score;
    } else if (card.kind === "cv") {
      statusLabel = card.fit_score != null ? "Fit " + card.fit_score + " · CV ready" : "CV ready";
    } else {
      statusLabel = "Cover letter ready";
    }
    fetch("/api/chat/conversations/" + chatConversationId + "/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_title: card.job_title || "",
        company: card.company || "",
        fit_score: card.fit_score != null ? card.fit_score : null,
        status_label: statusLabel,
      }),
    }).catch(function () {});
  }

  function markThreadApplied(card) {
    if (!chatConversationId) return;
    var statusLabel = card.fit_score != null ? "Fit " + card.fit_score + " · Sent" : "Sent";
    fetch("/api/chat/conversations/" + chatConversationId + "/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status_label: statusLabel }),
    }).catch(function () {});
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

  function sendChatMessage(overrideText) {
    var text = (overrideText != null ? overrideText : chatInput.value).trim();
    if ((!text && pendingAttachments.length === 0) || chatSending) return;
    var attachmentIds = pendingAttachments.map(function (a) { return a.id; });
    var displayText = text;
    if (pendingAttachments.length) {
      displayText += pendingAttachments.map(function (a) { return " [Attached: " + a.name + "]"; }).join("");
    }
    chatInput.value = "";
    autoGrowChatInput();
    pendingAttachments = [];
    renderPendingAttachments();
    showEmptyState(false);
    clearQuickReplies();
    appendChatMessage("user", displayText);
    chatHistory.push({ role: "user", text: displayText, attachment_ids: attachmentIds });

    var typingEl = document.createElement("div");
    typingEl.className = "chat-msg-typing";
    typingEl.textContent = "Thinking…";
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
        if (!data.ok) {
          appendChatMessageTyped("Something went wrong there. Try again?");
          return;
        }
        if (data.kind === "card") {
          // Card-producing tool calls (fit check / CV build) render as
          // their own card component, whether the user reached this via
          // a chip or just typed the equivalent request -- never as a
          // wall of text pretending to be one.
          appendCardMessage(data.card);
          return;
        }
        var parsed = parseQuickReplies(data.reply || "");
        appendChatMessageTyped(parsed.text, function () {
          appendQuickReplies(parsed.replies);
        });
        chatHistory.push({ role: "assistant", text: parsed.text });
        saveConversation();
      })
      .catch(function () {
        typingEl.remove();
        appendChatMessageTyped("Connection error — please try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  chatSendBtn.addEventListener("click", function () { sendChatMessage(); });
  chatInput.addEventListener("keydown", function (e) {
    // Shift+Enter inserts a real newline (now that this is a textarea,
    // not a single-line input) -- only a plain Enter sends.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  // Grows the textarea from 1 up to 3 lines as the user types (capped
  // by max-height in CSS, which also takes over with an internal
  // scrollbar beyond that) instead of staying pinned to one line.
  function autoGrowChatInput() {
    chatInput.style.height = "auto";
    chatInput.style.height = chatInput.scrollHeight + "px";
  }
  chatInput.addEventListener("input", autoGrowChatInput);

  // ---------- The 3 numbered chips ----------

  var chipPasteWrap = document.getElementById("chip-paste-wrap");
  var chipPasteInput = document.getElementById("chip-paste-input");
  var chipPasteCancelBtn = document.getElementById("chip-paste-cancel-btn");
  var chipPasteSubmitBtn = document.getElementById("chip-paste-submit-btn");

  document.getElementById("chip-qualify").addEventListener("click", function () {
    chatChipsEl.hidden = true;
    chipPasteWrap.hidden = false;
    chipPasteInput.focus();
  });

  function closeChipPaste() {
    chipPasteWrap.hidden = true;
    chipPasteInput.value = "";
    chatChipsEl.hidden = false;
  }

  chipPasteCancelBtn.addEventListener("click", closeChipPaste);

  chipPasteSubmitBtn.addEventListener("click", function () {
    var jobAd = chipPasteInput.value.trim();
    if (!jobAd) {
      chipPasteInput.focus();
      return;
    }
    closeChipPaste();
    requestVerdictCard(jobAd);
  });

  document.getElementById("chip-builder").addEventListener("click", function () {
    requestBuilderCard();
  });

  document.getElementById("chip-gaps").addEventListener("click", function () {
    sendChatMessage("What's holding me back?");
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

  // ---------- "Use my documents" toggle ----------
  // Off by default and never sent to the server at all unless it's
  // explicitly on for that message -- documents stay private between
  // the app and the user unless this is switched on.

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
        chatHistory = data.messages.map(function (m) { return { role: m.role, text: m.text, card: m.card }; });
        messagesEl.innerHTML = "";
        if (chatHistory.length === 0) {
          showEmptyState(true);
          return;
        }
        showEmptyState(false);
        chatHistory.forEach(function (m) {
          if (m.card) {
            messagesEl.appendChild(m.card.type === "verdict" ? renderVerdictCard(m.card) : renderDocumentCard(m.card));
          } else {
            appendChatMessage(m.role, m.text);
          }
        });
        scrollChatToBottom();
      });
  }

  function startNewChat() {
    chatConversationId = null;
    chatHistory = [];
    messagesEl.innerHTML = "";
    showEmptyState(true);
    chatInput.value = "";
    autoGrowChatInput();
    chatInput.focus();
  }

  document.getElementById("chat-new-btn").addEventListener("click", startNewChat);

  // ---------- Sidebar (chat history) ----------

  var sidebarOpenBtn = document.getElementById("sidebar-open-btn");
  var sidebar = document.getElementById("sidebar");
  var sidebarBackdrop = document.getElementById("sidebar-backdrop");
  var sidebarChatsEl = document.getElementById("sidebar-chats");
  var sidebarNewChatBtn = document.getElementById("sidebar-new-chat-btn");

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

  function loadSidebarChats() {
    fetch("/api/chat/conversations")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        sidebarChatsEl.innerHTML = "";
        if (!data.conversations.length) {
          var empty = document.createElement("div");
          empty.className = "sidebar-chats-empty";
          empty.textContent = "No conversations yet.";
          sidebarChatsEl.appendChild(empty);
          return;
        }
        data.conversations.forEach(function (c) {
          var row = document.createElement("button");
          row.type = "button";
          row.className = "sidebar-chat-row" + (c.kind === "job" ? " sidebar-chat-row-job" : "");
          row.title = formatRelativeTime(c.updated_at);
          var titleEl = document.createElement("span");
          titleEl.className = "sidebar-chat-title";
          titleEl.textContent = c.title || "Conversation";
          row.appendChild(titleEl);
          if (c.kind === "job" && c.status_label) {
            var badge = document.createElement("span");
            badge.className = "sidebar-chat-badge mono";
            badge.textContent = c.status_label;
            row.appendChild(badge);
          }
          row.addEventListener("click", function () {
            loadConversation(c.id);
            closeSidebar();
          });
          sidebarChatsEl.appendChild(row);
        });
      })
      .catch(function () {});
  }

  function openSidebar() {
    sidebar.hidden = false;
    sidebarBackdrop.hidden = false;
    void sidebar.offsetHeight;
    sidebar.classList.add("is-open");
    sidebarBackdrop.classList.add("is-open");
    loadSidebarChats();
  }

  function closeSidebar() {
    sidebar.classList.remove("is-open");
    sidebarBackdrop.classList.remove("is-open");
  }

  sidebar.addEventListener("transitionend", function (e) {
    if (e.target !== sidebar) return;
    if (!sidebar.classList.contains("is-open")) sidebar.hidden = true;
  });

  // Without this, the backdrop's opacity fades to 0 on close but the
  // element itself stays in the DOM without [hidden] -- invisible, yet
  // still a full-viewport fixed-position layer that swallows every
  // click on the page underneath it.
  sidebarBackdrop.addEventListener("transitionend", function (e) {
    if (e.target !== sidebarBackdrop) return;
    if (!sidebarBackdrop.classList.contains("is-open")) sidebarBackdrop.hidden = true;
  });

  sidebarOpenBtn.addEventListener("click", openSidebar);
  sidebarBackdrop.addEventListener("click", closeSidebar);
  sidebarNewChatBtn.addEventListener("click", function () {
    startNewChat();
    closeSidebar();
  });

  // A swipe starting from the left 40% of the screen opens the
  // sidebar, matching the native "edge swipe" pattern most drawer UIs
  // support. Listens on the document itself (not a dedicated overlay
  // element) so it never intercepts taps on real UI -- only the
  // *starting point* of the gesture has to fall in that zone, and a
  // plain tap never travels far enough horizontally to trigger it.
  var swipeStartX = null, swipeStartY = null;

  document.addEventListener("touchstart", function (e) {
    if (sidebar.classList.contains("is-open")) return;
    var t = e.touches[0];
    if (t.clientX > window.innerWidth * 0.4) return;
    swipeStartX = t.clientX;
    swipeStartY = t.clientY;
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (swipeStartX == null) return;
    var t = e.touches[0];
    var dx = t.clientX - swipeStartX;
    var dy = t.clientY - swipeStartY;
    // Require a clearly horizontal rightward drag so a vertical scroll
    // starting near the edge doesn't accidentally trigger the drawer.
    if (dx > 40 && Math.abs(dy) < 30) {
      openSidebar();
      swipeStartX = null;
    }
  }, { passive: true });

  document.addEventListener("touchend", function () {
    swipeStartX = null;
    swipeStartY = null;
  });

  // ---------- Voice dictation ----------
  // The mic button just hides itself on browsers that don't support
  // speech recognition at all, rather than showing a control that
  // would only ever fail.

  var chatMicBtn = document.getElementById("chat-mic-btn");
  var micIcon = chatMicBtn.querySelector(".chat-mic-icon");
  var micStopIcon = chatMicBtn.querySelector(".chat-mic-stop-icon");
  var chatVoiceLive = document.getElementById("chat-voice-live");
  var chatVoiceCancelBtn = document.getElementById("chat-voice-cancel-btn");
  var chatVoiceConfirmBtn = document.getElementById("chat-voice-confirm-btn");
  var chatDictationPreview = document.getElementById("chat-dictation-preview");
  var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!SpeechRecognitionCtor) {
    chatMicBtn.hidden = true;
  } else {
    var recognition = new SpeechRecognitionCtor();
    // continuous=false, with this file restarting it on every "end"
    // (below) unless the user asked to stop, instead of continuous=true.
    // Android Chrome's continuous mode is known to both auto-stop after
    // a second or two of silence anyway AND duplicate the same
    // utterance across many silent internal restarts. Restarting a
    // fresh short session ourselves sidesteps both bugs: each session's
    // results are simple and clean, and the restart is invisible to the
    // user, who just experiences one continuous listening session.
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    var listening = false;
    var shouldRestart = false;
    var cancelled = false;
    var finalTranscript = "";
    var baseText = "";
    var silenceTimer = null;
    var SILENCE_TIMEOUT_MS = 10000;

    function setListening(on) {
      listening = on;
      chatMicBtn.classList.toggle("is-listening", on);
      micIcon.hidden = on;
      micStopIcon.hidden = !on;
      chatVoiceLive.hidden = !on;
      chatInput.hidden = on;
      if (!on) {
        chatDictationPreview.hidden = true;
        chatDictationPreview.textContent = "";
      }
    }

    function resetSilenceTimer() {
      clearTimeout(silenceTimer);
      // A safety net, not the primary way this ends -- the user can
      // dictate for as long as they keep talking (or pause briefly
      // between sentences); this only fires after real silence.
      silenceTimer = setTimeout(function () { stopListening(); }, SILENCE_TIMEOUT_MS);
    }

    function updatePreview(interim) {
      var combined = (finalTranscript + (interim ? " " + interim : "")).trim();
      chatDictationPreview.hidden = false;
      chatDictationPreview.textContent = combined || "Listening…";
    }

    function commitTranscript() {
      var combined = ((baseText ? baseText + " " : "") + finalTranscript).trim();
      chatInput.value = combined;
      autoGrowChatInput();
    }

    recognition.addEventListener("result", function (e) {
      var interim = "";
      for (var i = e.resultIndex; i < e.results.length; i++) {
        var res = e.results[i];
        if (res.isFinal) {
          finalTranscript = (finalTranscript + " " + res[0].transcript).trim();
        } else {
          interim += res[0].transcript;
        }
      }
      updatePreview(interim);
      resetSilenceTimer();
    });

    recognition.addEventListener("end", function () {
      if (shouldRestart) {
        try { recognition.start(); return; } catch (e) { /* fall through to a full stop below */ }
      }
      clearTimeout(silenceTimer);
      setListening(false);
      if (!cancelled) commitTranscript();
      cancelled = false;
    });

    recognition.addEventListener("error", function (e) {
      // "no-speech" fires constantly between the short restarted
      // sessions above (there's simply been no speech since the last
      // one ended) -- expected, not a real failure, so the restart
      // loop keeps going. Anything else really did fail; stop trying.
      if (e.error !== "no-speech" && e.error !== "aborted") {
        shouldRestart = false;
      }
    });

    function stopListening() {
      shouldRestart = false;
      clearTimeout(silenceTimer);
      try { recognition.stop(); } catch (e) {}
    }

    function cancelListening() {
      shouldRestart = false;
      cancelled = true;
      finalTranscript = "";
      clearTimeout(silenceTimer);
      try { recognition.stop(); } catch (e) {}
    }

    chatMicBtn.addEventListener("click", function () {
      if (listening) {
        stopListening();
        return;
      }
      baseText = chatInput.value.trim();
      finalTranscript = "";
      shouldRestart = true;
      try {
        recognition.start();
        setListening(true);
        resetSilenceTimer();
        updatePreview("");
      } catch (e) {
        shouldRestart = false;
        setListening(false);
      }
    });

    chatVoiceConfirmBtn.addEventListener("click", stopListening);
    chatVoiceCancelBtn.addEventListener("click", cancelListening);
  }

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
    .catch(function () { showEmptyState(true); });
})();
