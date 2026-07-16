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

  // ---------- Message actions: copy / edit / reply ----------

  var COPY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
  var CHECK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 12 9 17 20 6"/></svg>';
  var EDIT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
  var REPLY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>';

  // A user message that's replying to a specific bot bubble is stored as
  // a single plain-text field (same as every other message, so it needs
  // no schema/db changes to round-trip) using a fixed, human-readable
  // prefix -- the model reads it as plain English context, and the UI
  // splits it back out into a quoted strip above the real text.
  function parseReplyQuote(text) {
    var m = /^Replying to: "([\s\S]*?)"\n([\s\S]*)$/.exec(text || "");
    if (!m) return { quote: null, text: text || "" };
    return { quote: m[1], text: m[2] };
  }

  function renderBubbleContent(bubble, text, edited) {
    bubble.innerHTML = "";
    var parsed = parseReplyQuote(text);
    if (parsed.quote) {
      var q = document.createElement("span");
      q.className = "chat-msg-quote";
      q.textContent = parsed.quote;
      bubble.appendChild(q);
    }
    var textEl = document.createElement("span");
    textEl.className = "chat-msg-text";
    textEl.innerHTML = formatChatText(parsed.text) + (edited ? ' <span class="chat-msg-edited-tag">(edited)</span>' : "");
    bubble.appendChild(textEl);
    return textEl;
  }

  function flashActionBtn(btn, cls) {
    var original = btn.innerHTML;
    btn.innerHTML = CHECK_ICON;
    btn.classList.add(cls);
    setTimeout(function () {
      btn.innerHTML = original;
      btn.classList.remove(cls);
    }, 1200);
  }

  function copyMessageText(text, btn) {
    var done = function () { flashActionBtn(btn, "is-copied"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) {}
      ta.remove();
      done();
    }
  }

  // ---------- Reply-to-a-message preview bar ----------

  var pendingReplyContext = null;
  var replyPreviewEl = document.getElementById("chat-reply-preview");
  var replyPreviewTextEl = document.getElementById("chat-reply-preview-text");
  var replyPreviewCloseBtn = document.getElementById("chat-reply-preview-close-btn");

  function setReplyContext(snippet) {
    pendingReplyContext = snippet;
    replyPreviewTextEl.textContent = snippet;
    replyPreviewEl.hidden = false;
    chatInput.focus();
  }

  function clearReplyContext() {
    pendingReplyContext = null;
    replyPreviewEl.hidden = true;
  }

  replyPreviewCloseBtn.addEventListener("click", clearReplyContext);

  // Swiping a bot bubble right is the gesture shortcut for the same
  // reply action the Reply icon triggers -- the bubble tracks the drag
  // finger 1:1 and either commits (past a distance/flick threshold) or
  // springs back, the standard swipe-to-dismiss feel.
  function wireSwipeToReply(bubble, onReply) {
    var startX = null, startY = null, dx = 0, startTime = 0, dragging = false;

    bubble.addEventListener("touchstart", function (e) {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      dx = 0;
      startTime = Date.now();
      dragging = false;
      bubble.style.transition = "none";
    }, { passive: true });

    bubble.addEventListener("touchmove", function (e) {
      if (startX == null) return;
      var t = e.touches[0];
      var moveX = t.clientX - startX;
      var moveY = t.clientY - startY;
      if (!dragging && Math.abs(moveX) < 10 && Math.abs(moveY) < 10) return;
      if (Math.abs(moveY) > Math.abs(moveX)) return; // vertical scroll, not a swipe
      dragging = true;
      dx = Math.max(0, moveX); // only rightward
      bubble.style.transform = "translateX(" + dx + "px)";
    }, { passive: true });

    bubble.addEventListener("touchend", function () {
      if (startX == null) return;
      var elapsed = Date.now() - startTime;
      var isFastFlick = dx > 30 && elapsed < 250;
      bubble.style.transition = "";
      bubble.style.transform = "";
      if (dragging && (dx > 60 || isFastFlick)) {
        onReply();
      }
      startX = null;
      dx = 0;
      dragging = false;
    });
  }

  var emptyHeadlineEl = document.getElementById("chat-empty-headline");
  var emptySubtextEl = document.getElementById("chat-empty-subtext");
  var firstName = ((window.HOME_STATE && window.HOME_STATE.profile.full_name) || "").trim().split(/\s+/)[0] || "";

  // A mix of name-personalized and plain openers -- picked fresh every
  // time a new chat starts, so the empty state reads like a person
  // asking "what are we doing today" instead of a static slogan. Named
  // ones are a minority by design (repeating someone's name every
  // single chat gets old fast).
  var EMPTY_HEADLINES = [
    ["What are we working on?", "Paste a job ad, or ask me anything."],
    ["Hey" + (firstName ? " " + firstName : "") + ", what's the move?", "A job ad, a CV tweak, whatever's next."],
    ["Did you apply anywhere new?", "Tell me about it, or paste the job ad."],
    ["What can we sort out today?", "Paste a job ad, or ask me anything."],
    ["Where do we start?", "Paste a job ad, or ask me anything."],
    ["Got a job in mind?", "Paste the ad and I'll check your fit."],
    [(firstName ? firstName + ", " : "") + "what's on your mind?", "Job hunting, CV, cover letter — just say it."],
    ["Let's get into it.", "Paste a job ad, or ask me anything."],
    ["What's next on the list?", "A job to check, a CV to fix, up to you."],
    ["Any new leads?", "Paste the job ad and I'll take a look."],
    ["What do you want to tackle?", "Paste a job ad, or ask me anything."],
    ["Ready when you are" + (firstName ? ", " + firstName : "") + ".", "Paste a job ad, or ask me anything."],
    ["Something new to look at?", "Paste a job ad, or ask me anything."],
    ["What's the plan today?", "A job ad, a CV question, anything."],
    ["Talk to me.", "Paste a job ad, or ask me anything."],
    ["Anything to report back on?", "Applications, interviews, whatever's up."],
    ["What are we chasing today?", "Paste a job ad, or ask me anything."],
    ["Back at it" + (firstName ? ", " + firstName : "") + "?", "Paste a job ad, or ask me anything."],
    ["What can I help with?", "Paste a job ad, or ask me anything."],
    ["Found something worth checking?", "Paste the job ad and I'll score your fit."],
  ];

  // A real proactive check-in, computed server-side from idle time and
  // what Ploy remembers about this person (see app.py's
  // _personalized_checkin) -- shown once, on the very first empty-state
  // render after a cold load, then it steps aside for the normal random
  // rotation so it doesn't repeat on every "New chat" tap this session.
  var pendingCheckin = (window.HOME_STATE && window.HOME_STATE.personalized_checkin) || null;

  function rollEmptyStateCopy() {
    if (!emptyHeadlineEl) return;
    if (pendingCheckin) {
      emptyHeadlineEl.textContent = "Hey" + (firstName ? " " + firstName : "") + ".";
      emptySubtextEl.textContent = pendingCheckin;
      pendingCheckin = null;
      return;
    }
    var pick = EMPTY_HEADLINES[Math.floor(Math.random() * EMPTY_HEADLINES.length)];
    emptyHeadlineEl.textContent = pick[0];
    emptySubtextEl.textContent = pick[1];
  }

  function showEmptyState(show) {
    emptyEl.hidden = !show;
    messagesEl.hidden = show;
    if (show) rollEmptyStateCopy();
  }

  // Named phases for the "thinking" indicator -- always opens on
  // "Thinking", then cycles through the rest every ~2.5s for as long as
  // the request is in flight, instead of a flat, meaningless "...".
  // There's no real progress signal from a single blocking request, so
  // this is a paced simulation, not literal status -- but naming each
  // phase (rather than just varying dot count) is what makes it read as
  // an actual line of reasoning instead of a generic spinner.
  var THINKING_PHASES = [
    "Thinking",
    "Reading your documents",
    "Weighing the details",
    "Connecting the dots",
    "Cross-checking the facts",
    "Sharpening the answer",
    "Considering what you've told me",
    "Formulating a response",
    "Double-checking the grounding",
    "Almost there",
  ];

  function createTypingIndicator() {
    var el = document.createElement("div");
    el.className = "chat-msg-typing";
    el.innerHTML =
      '<span class="chat-typing-phase"></span>' +
      '<span class="chat-typing-dots">' +
      '<span class="chat-typing-dot"></span>' +
      '<span class="chat-typing-dot"></span>' +
      '<span class="chat-typing-dot"></span>' +
      '</span>';
    var phaseEl = el.querySelector(".chat-typing-phase");
    var i = 0;
    phaseEl.textContent = THINKING_PHASES[0];
    var intervalId = setInterval(function () {
      i = (i + 1) % THINKING_PHASES.length;
      phaseEl.classList.remove("is-in");
      // Force a reflow so re-adding the class retriggers the fade-in
      // keyframe instead of the browser coalescing it into a no-op.
      void phaseEl.offsetWidth;
      phaseEl.textContent = THINKING_PHASES[i];
      phaseEl.classList.add("is-in");
    }, 2500);
    el._stopThinkingCycle = function () { clearInterval(intervalId); };
    return el;
  }

  function removeTypingIndicator(el) {
    if (el._stopThinkingCycle) el._stopThinkingCycle();
    el.remove();
  }

  // A "Checking your fit against this job…" style status line shown right
  // before a step's card lands, mid-work (animated dots) -- then flipped
  // to a done state (a checkmark, dots removed) and left in place
  // permanently once the card appears, instead of disappearing. That's
  // what actually builds a visible trail of everything a multi-part
  // reply did, the same way Claude Code's own collapsed tool-call
  // summaries stay in the transcript rather than vanishing once done.
  var STEP_CHECK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 12 9 17 20 6"/></svg>';
  var STEP_CHEVRON_ICON = '<svg class="chat-step-status-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';

  function createStepStatus(label) {
    var el = document.createElement("div");
    el.className = "chat-step-status";
    el.innerHTML =
      '<span class="chat-step-status-label"></span>' +
      '<span class="chat-step-status-dots">' +
      '<span class="chat-typing-dot"></span>' +
      '<span class="chat-typing-dot"></span>' +
      '<span class="chat-typing-dot"></span>' +
      '</span>';
    el.querySelector(".chat-step-status-label").textContent = label;
    return el;
  }

  // Once a step lands, its status line stays behind as a permanent,
  // clickable entry in the trail (see the module doc comment above) --
  // tapping it expands a small detail line underneath showing exactly
  // what that step actually did, the same way Claude Code's own
  // collapsed "Ran a command, used 2 tools" summaries expand on click.
  function markStepStatusDone(el, label, detail, toolName) {
    el.classList.add("is-done");
    el.innerHTML = STEP_CHECK_ICON + '<span class="chat-step-status-label"></span>';
    el.querySelector(".chat-step-status-label").textContent = label;
    if (!detail) return;

    el.insertAdjacentHTML("beforeend", STEP_CHEVRON_ICON);
    el.classList.add("is-expandable");

    var detailEl = document.createElement("div");
    detailEl.className = "chat-step-detail";
    detailEl.hidden = true;
    detailEl.innerHTML =
      (toolName ? '<span class="chat-step-detail-tool"></span>' : "") +
      '<span class="chat-step-detail-text"></span>';
    if (toolName) detailEl.querySelector(".chat-step-detail-tool").textContent = toolName;
    detailEl.querySelector(".chat-step-detail-text").textContent = detail;
    el.insertAdjacentElement("afterend", detailEl);

    el.addEventListener("click", function () {
      var expanded = el.classList.toggle("is-expanded");
      detailEl.hidden = !expanded;
    });
  }

  // A real reasoning pass, not decoration -- the backend runs a
  // genuinely separate, cheap completion to decide its plan before
  // touching any tool, and this renders that plan as its own quiet
  // aside in the trail: a single line, brain icon, no dots/checkmark
  // lifecycle like a tool step has, because it's already resolved by
  // the time it reaches the client.
  var THINKING_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a7 7 0 0 0-4 12.7V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.3A7 7 0 0 0 12 2Z"/><line x1="9.5" y1="22" x2="14.5" y2="22"/></svg>';

  function createThinkingStep(text) {
    var el = document.createElement("div");
    el.className = "chat-step-status chat-thinking-step is-done";
    el.innerHTML = THINKING_ICON + '<span class="chat-step-status-label"></span>';
    el.querySelector(".chat-step-status-label").textContent = text;
    return el;
  }

  // ---------- "Working…" elapsed-time indicator ----------
  // A small ticking clock pinned above the input for the full duration
  // of a turn (from send through the last step actually landing), so
  // the user can see real time passing on a slow multi-step reply
  // instead of wondering whether anything is happening at all.

  var workingBarEl = document.getElementById("chat-working-bar");
  var workingTimeEl = document.getElementById("chat-working-time");
  var workingTimerHandle = null;
  var workingStartedAt = 0;

  function formatElapsed(ms) {
    var totalSec = Math.max(0, Math.floor(ms / 1000));
    var m = Math.floor(totalSec / 60);
    var s = totalSec % 60;
    return m > 0 ? (m + "m " + s + "s") : (s + "s");
  }

  function startWorkingTimer() {
    workingStartedAt = Date.now();
    workingTimeEl.textContent = "0s";
    workingBarEl.hidden = false;
    if (workingTimerHandle) clearInterval(workingTimerHandle);
    workingTimerHandle = setInterval(function () {
      workingTimeEl.textContent = formatElapsed(Date.now() - workingStartedAt);
    }, 1000);
  }

  function stopWorkingTimer() {
    if (workingTimerHandle) {
      clearInterval(workingTimerHandle);
      workingTimerHandle = null;
    }
    workingBarEl.hidden = true;
  }

  // Every rendered message (text bubble or card, live-sent or reloaded)
  // is tagged with the chatHistory index it corresponds to -- this is
  // what lets editing a past message find and remove everything that
  // came after it, in both the DOM and the history array, without
  // needing a real per-message id from the server.
  function truncateHistoryFrom(keepCount) {
    chatHistory = chatHistory.slice(0, keepCount);
    var nodes = messagesEl.querySelectorAll("[data-history-index]");
    nodes.forEach(function (node) {
      if (Number(node.dataset.historyIndex) >= keepCount) node.remove();
    });
    clearQuickReplies();
  }

  function enterEditMode(group, bubble, idx) {
    var current = chatHistory[idx];
    if (!current) return;
    var parsed = parseReplyQuote(current.text);
    bubble.innerHTML = "";
    if (parsed.quote) {
      var q = document.createElement("span");
      q.className = "chat-msg-quote";
      q.textContent = parsed.quote;
      bubble.appendChild(q);
    }
    var textarea = document.createElement("textarea");
    textarea.className = "chat-msg-edit-textarea";
    textarea.value = parsed.text;
    bubble.appendChild(textarea);

    var editActions = document.createElement("div");
    editActions.className = "chat-msg-edit-actions";
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    var saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn btn-gold btn-sm";
    saveBtn.textContent = "Save & resend";
    editActions.appendChild(cancelBtn);
    editActions.appendChild(saveBtn);
    bubble.appendChild(editActions);

    function autoGrowTextarea() {
      textarea.style.height = "auto";
      textarea.style.height = textarea.scrollHeight + "px";
    }
    textarea.addEventListener("input", autoGrowTextarea);
    textarea.focus();
    autoGrowTextarea();

    cancelBtn.addEventListener("click", function () {
      group._textEl = renderBubbleContent(bubble, current.text, !!current.edited);
    });

    saveBtn.addEventListener("click", function () {
      if (chatSending) return;
      var newText = textarea.value.trim();
      if (!newText) return;
      var fullText = parsed.quote ? 'Replying to: "' + parsed.quote + '"\n' + newText : newText;
      chatHistory[idx].text = fullText;
      chatHistory[idx].edited = true;
      truncateHistoryFrom(idx + 1);
      group._textEl = renderBubbleContent(bubble, fullText, true);
      saveConversation();
      runAssistantTurn();
    });
  }

  function appendChatMessage(role, text, idxOverride) {
    var idx = idxOverride != null ? idxOverride : chatHistory.length;
    var group = document.createElement("div");
    group.className = "chat-msg-group " + (role === "user" ? "chat-msg-group-user" : "chat-msg-group-bot");
    group.dataset.historyIndex = String(idx);

    var bubble = document.createElement("div");
    bubble.className = "chat-msg " + (role === "user" ? "chat-msg-user" : "chat-msg-bot");
    var edited = !!(chatHistory[idx] && chatHistory[idx].edited);
    var textEl = renderBubbleContent(bubble, text, edited);
    group.appendChild(bubble);

    // Devices without hover (i.e. touch) have no way to reveal the
    // below-bubble action row otherwise -- tapping the bubble itself
    // toggles it, as long as the tap isn't on an action button already
    // or the tail end of a swipe-to-reply drag.
    bubble.addEventListener("click", function (e) {
      if (e.target.closest(".chat-msg-action-btn") || e.target.closest(".chat-msg-edit-textarea")) return;
      group.classList.toggle("is-actions-visible");
    });

    var actions = document.createElement("div");
    actions.className = "chat-msg-actions";

    var copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "chat-msg-action-btn chat-msg-copy-btn";
    copyBtn.setAttribute("aria-label", "Copy message");
    copyBtn.innerHTML = COPY_ICON;
    copyBtn.addEventListener("click", function () {
      var raw = chatHistory[group.dataset.historyIndex] ? chatHistory[group.dataset.historyIndex].text : text;
      copyMessageText(parseReplyQuote(raw).text, copyBtn);
    });
    actions.appendChild(copyBtn);

    if (role === "user") {
      var editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "chat-msg-action-btn chat-msg-edit-btn";
      editBtn.setAttribute("aria-label", "Edit message");
      editBtn.innerHTML = EDIT_ICON;
      editBtn.addEventListener("click", function () {
        if (chatSending) return;
        enterEditMode(group, bubble, Number(group.dataset.historyIndex));
      });
      actions.appendChild(editBtn);
    }

    // Reply applies to either role -- replying to your own earlier
    // message is just as valid a way to give the model context as
    // replying to something it said.
    var replyBtn = document.createElement("button");
    replyBtn.type = "button";
    replyBtn.className = "chat-msg-action-btn chat-msg-reply-btn";
    replyBtn.setAttribute("aria-label", "Reply to this message");
    replyBtn.innerHTML = REPLY_ICON;
    var triggerReply = function () { setReplyContext(bubble.querySelector(".chat-msg-text").innerText.trim().slice(0, 140)); };
    replyBtn.addEventListener("click", triggerReply);
    actions.appendChild(replyBtn);
    wireSwipeToReply(bubble, triggerReply);

    group.appendChild(actions);
    group._textEl = textEl;
    group._bubble = bubble;
    messagesEl.appendChild(group);
    scrollChatToBottom();
    return group;
  }

  function appendChatMessageTyped(text, onDone) {
    var el = appendChatMessage("assistant", "");
    var textEl = el._textEl;
    var i = 0;
    var step = Math.max(1, Math.ceil(text.length / 40));
    var timer = setInterval(function () {
      i += step;
      textEl.innerHTML = formatChatText(text.slice(0, i));
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

  // Quick-reply choices live in a fixed bar right above the input --
  // anchored where you're about to type, not scrolling away inline with
  // the message history -- so a clarifying question stays reachable
  // exactly where Claude/ChatGPT put theirs, and typing your own answer
  // instead of tapping one is always right there too.
  var quickReplyBar = document.getElementById("chat-quickreply-bar");

  function clearQuickReplies() {
    quickReplyBar.innerHTML = "";
    quickReplyBar.hidden = true;
  }

  function appendQuickReplies(replies) {
    if (!replies.length) return;
    quickReplyBar.innerHTML = "";
    replies.forEach(function (label) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-quick-reply-btn";
      btn.textContent = label;
      btn.addEventListener("click", function () { sendChatMessage(label); });
      quickReplyBar.appendChild(btn);
    });
    // None of these options might actually fit -- this just dismisses the
    // choices and focuses the input, so typing a real answer is always
    // one obvious tap away instead of something you have to already know
    // you can do.
    var otherBtn = document.createElement("button");
    otherBtn.type = "button";
    otherBtn.className = "chat-quick-reply-btn chat-quick-reply-other-btn";
    otherBtn.textContent = "Something else — let me type it";
    otherBtn.addEventListener("click", function () {
      clearQuickReplies();
      chatInput.focus();
    });
    quickReplyBar.appendChild(otherBtn);
    quickReplyBar.hidden = false;
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

  function scoreTrendBadge(score, previousScore) {
    if (previousScore == null) return "";
    var delta = score - previousScore;
    if (delta === 0) return '<span class="score-trend trend-flat">Same as last time</span>';
    var cls = delta > 0 ? "trend-up" : "trend-down";
    var arrow = delta > 0 ? "▲" : "▼";
    return '<span class="score-trend ' + cls + '">' + arrow + " " + Math.abs(delta) + " vs last time</span>";
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
      scoreTrendBadge(card.fit_score, card.previous_fit_score) +
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
      // Only nudge this card's own newly-revealed text into view (and
      // only when opening it) -- this used to force the whole chat to
      // jump to its very bottom on every toggle, which was jarring for
      // any card that wasn't already the last message on screen.
      if (show) breakdownEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    return wrap;
  }

  function renderGapCard(card) {
    var wrap = document.createElement("div");
    wrap.className = "verdict-card";
    var tier = fitTier(card.readiness_score);
    wrap.innerHTML =
      '<div class="card-header mono">GAP ANALYSIS</div>' +
      '<div class="verdict-job-title">' + escapeHtml(card.target_role || "This role") + "</div>" +
      '<div class="verdict-score mono tier-' + tier + '">' + card.readiness_score + '<span class="verdict-score-max">/100 ready</span></div>' +
      diamondList(card.strengths, "diamond-good") +
      diamondList(card.gaps, "diamond-bad") +
      '<div class="card-breakdown" hidden></div>' +
      '<div class="card-actions">' +
      '<button type="button" class="btn btn-gold btn-sm card-fix-btn">Build my CV</button>' +
      '<button type="button" class="btn btn-ghost btn-sm card-breakdown-btn">Show full breakdown</button>' +
      "</div>";

    wrap.querySelector(".card-fix-btn").addEventListener("click", function () {
      requestDocumentCard({});
    });
    var breakdownEl = wrap.querySelector(".card-breakdown");
    var breakdownBtn = wrap.querySelector(".card-breakdown-btn");
    breakdownBtn.addEventListener("click", function () {
      var show = breakdownEl.hidden;
      breakdownEl.hidden = !show;
      breakdownEl.textContent = card.breakdown || "";
      breakdownBtn.textContent = show ? "Hide breakdown" : "Show full breakdown";
      // Only nudge this card's own newly-revealed text into view (and
      // only when opening it) -- this used to force the whole chat to
      // jump to its very bottom on every toggle, which was jarring for
      // any card that wasn't already the last message on screen.
      if (show) breakdownEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    return wrap;
  }

  // Real, grounded values only (the user's actual scored dimensions,
  // 0-10 each) -- never invented. Bar chart is horizontal bars scaled
  // to the widest value; pie chart is an SVG donut built from
  // stroke-dasharray segments, since that's simpler and just as
  // correct as true wedge paths for this many slices.
  function renderChartCard(card) {
    var wrap = document.createElement("div");
    wrap.className = "chart-card";
    var colors = ["var(--accent)", "var(--good)", "var(--mid)", "var(--bad)", "#8b5cf6", "#2dd4bf", "#ec4899", "#f59e0b"];

    var bodyHtml;
    if (card.chart_type === "pie") {
      var total = card.values.reduce(function (a, b) { return a + b; }, 0) || 1;
      var circumference = 2 * Math.PI * 40;
      var offset = 0;
      var segments = card.labels.map(function (label, i) {
        var value = card.values[i];
        var frac = value / total;
        var dash = frac * circumference;
        var seg = '<circle cx="60" cy="60" r="40" fill="none" stroke="' + colors[i % colors.length] +
          '" stroke-width="18" stroke-dasharray="' + dash + " " + (circumference - dash) +
          '" stroke-dashoffset="' + (-offset) + '" transform="rotate(-90 60 60)"></circle>';
        offset += dash;
        return seg;
      }).join("");
      bodyHtml =
        '<svg viewBox="0 0 120 120" class="chart-pie-svg">' + segments + "</svg>" +
        '<div class="chart-legend">' + card.labels.map(function (label, i) {
          return '<span class="chart-legend-item"><span class="chart-legend-swatch" style="background:' + colors[i % colors.length] + '"></span>' +
            escapeHtml(label) + " — " + card.values[i] + "/10</span>";
        }).join("") + "</div>";
    } else {
      var maxVal = Math.max.apply(null, card.values.concat([10]));
      bodyHtml = '<div class="chart-bars">' + card.labels.map(function (label, i) {
        var value = card.values[i];
        var pct = Math.round((value / maxVal) * 100);
        var tier = value >= 7.5 ? "good" : value >= 5 ? "mid" : "bad";
        return '<div class="chart-bar-row">' +
          '<span class="chart-bar-label">' + escapeHtml(label) + "</span>" +
          '<span class="chart-bar-track"><span class="chart-bar-fill chart-bar-' + tier + '" style="width:' + pct + '%"></span></span>' +
          '<span class="chart-bar-value mono">' + value + "</span>" +
          "</div>";
      }).join("") + "</div>";
    }

    wrap.innerHTML =
      '<div class="card-header mono">CHART</div>' +
      '<div class="chart-body">' + bodyHtml + "</div>" +
      '<div class="card-actions">' +
      '<button type="button" class="btn btn-ghost btn-sm chart-improve-btn">What should I improve first?</button>' +
      "</div>";

    wrap.querySelector(".chart-improve-btn").addEventListener("click", function () {
      sendChatMessage("Looking at that chart, what should I improve first?");
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
      (card.grounding_note ? '<div class="document-grounding-note">⚠ ' + escapeHtml(card.grounding_note) + "</div>" : "") +
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

  function renderImageCard(card) {
    var wrap = document.createElement("div");
    wrap.className = "image-card";
    wrap.innerHTML =
      '<div class="card-header mono">IMAGE</div>' +
      '<img class="image-card-img" src="data:image/png;base64,' + card.image_b64 + '" alt="' + escapeHtml(card.prompt) + '">' +
      '<div class="card-actions">' +
      '<a class="btn btn-gold btn-sm image-card-download-btn" href="data:image/png;base64,' + card.image_b64 + '" download="ploy-image.png">Download</a>' +
      "</div>";
    return wrap;
  }

  function renderCardNode(card) {
    return card.type === "verdict" ? renderVerdictCard(card)
      : card.type === "gap" ? renderGapCard(card)
      : card.type === "chart" ? renderChartCard(card)
      : card.type === "image" ? renderImageCard(card)
      : renderDocumentCard(card);
  }

  function pick(options) {
    return options[Math.floor(Math.random() * options.length)];
  }

  // The phrasing here becomes the AI's own remembered utterance for this
  // turn (chatHistory is sent back as conversation context), so a single
  // fixed template would make its own "memory" of showing a card read
  // mechanically the same every time -- these vary the wording only,
  // never the underlying facts (score/title/company/etc. stay exact).
  function cardSummaryText(card) {
    if (card.type === "verdict") {
      var role = card.job_title || "this role";
      var at = card.company ? " at " + card.company : "";
      return pick([
        "Verdict: " + card.fit_score + "/100 fit for " + role + at + ".",
        "Scored " + role + at + " at " + card.fit_score + "/100.",
        "Ran the numbers on " + role + at + " — " + card.fit_score + "/100.",
      ]);
    }
    if (card.type === "gap") {
      var target = card.target_role || "this role";
      return pick([
        "Gap analysis: " + card.readiness_score + "/100 ready for " + target + ".",
        "Checked your readiness for " + target + " — " + card.readiness_score + "/100.",
        target + " readiness: " + card.readiness_score + "/100.",
      ]);
    }
    if (card.type === "chart") {
      var labels = card.labels.join(", ");
      return pick([
        "Here's your " + card.chart_type + " chart of " + labels + ".",
        "Charted " + labels + " as a " + card.chart_type + ".",
        "Your " + labels + " breakdown, as a " + card.chart_type + " chart.",
      ]);
    }
    if (card.type === "image") {
      return pick([
        "Generated: " + card.prompt,
        "Here's that image — " + card.prompt,
        "Image ready: " + card.prompt,
      ]);
    }
    var docLabel = card.kind === "cv" ? "CV" : "cover letter";
    return pick([
      "Document ready: " + card.filename,
      "Your " + docLabel + " is ready — " + card.filename,
      docLabel + " done: " + card.filename,
    ]);
  }

  function appendCardMessage(card) {
    clearQuickReplies();
    showEmptyState(false);
    var node = renderCardNode(card);
    node.dataset.historyIndex = String(chatHistory.length);
    messagesEl.appendChild(node);
    scrollChatToBottom();
    var summary = cardSummaryText(card);
    chatHistory.push({ role: "assistant", text: summary, card: card });
    saveConversation().then(function () {
      if (card.type !== "chart" && card.type !== "gap" && card.type !== "image") promoteThreadForCard(card);
    });
  }

  function requestCard(url, payload) {
    var typingEl = createTypingIndicator();
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
        removeTypingIndicator(typingEl);
        if (data.ok) {
          appendCardMessage(data);
        } else {
          appendChatMessage("assistant", data.error || "Something went wrong there. Try again?");
        }
      })
      .catch(function () {
        removeTypingIndicator(typingEl);
        appendChatMessage("assistant", "Couldn't reach Ploy just now — check your connection and try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  function requestDocumentCard(context) {
    requestCard("/api/document", context);
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

  // ---------- Send ----------

  // /api/chat can now do more than one thing per turn (e.g. "check my
  // fit AND build my CV" chains a verdict card, then a document card) --
  // this renders that ordered list of steps one at a time, each waiting
  // for the previous to finish, instead of dumping everything in at
  // once. This is what makes a multi-part request actually feel like
  // the assistant is working through it step by step rather than either
  // dropping everything but the first part or replying in one flat wall.
  var STEP_PAUSE_MS = 700;
  var STEP_STATUS_MS = 1400;

  function renderStepsSequentially(steps, i) {
    i = i || 0;
    if (i >= steps.length) {
      stopWorkingTimer();
      saveConversation();
      return;
    }
    var step = steps[i];
    var isLast = i === steps.length - 1;
    if (step.type === "thinking") {
      messagesEl.appendChild(createThinkingStep(step.text || "Thinking…"));
      scrollChatToBottom();
      setTimeout(function () { renderStepsSequentially(steps, i + 1); }, STEP_PAUSE_MS);
    } else if (step.type === "card") {
      var statusEl = createStepStatus(step.label || "Working on that");
      messagesEl.appendChild(statusEl);
      scrollChatToBottom();
      setTimeout(function () {
        markStepStatusDone(statusEl, step.label || "Done", step.detail, step.tool);
        appendCardMessage(step.card);
        setTimeout(function () { renderStepsSequentially(steps, i + 1); }, STEP_PAUSE_MS);
      }, STEP_STATUS_MS);
    } else {
      var parsed = parseQuickReplies(step.text || "");
      appendChatMessageTyped(parsed.text, function () {
        chatHistory.push({ role: "assistant", text: parsed.text });
        if (isLast) appendQuickReplies(parsed.replies);
        renderStepsSequentially(steps, i + 1);
      });
    }
  }

  // The network call + response rendering, shared by a normal send and
  // by "Save & resend" after editing a past message -- both cases just
  // need chatHistory to already end at the right point before this runs.
  function runAssistantTurn() {
    var typingEl = createTypingIndicator();
    messagesEl.appendChild(typingEl);
    scrollChatToBottom();
    startWorkingTimer();

    chatSending = true;
    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: chatHistory }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        removeTypingIndicator(typingEl);
        if (!data.ok) {
          stopWorkingTimer();
          appendChatMessageTyped("Something went wrong there. Try again?");
          return;
        }
        renderStepsSequentially(data.steps || []);
      })
      .catch(function () {
        removeTypingIndicator(typingEl);
        stopWorkingTimer();
        appendChatMessageTyped("Couldn't send that — check your connection and try again.");
      })
      .finally(function () {
        chatSending = false;
      });
  }

  function sendChatMessage(overrideText) {
    var raw = (overrideText != null ? overrideText : chatInput.value).trim();
    if (!raw || chatSending) return;
    var text = pendingReplyContext ? 'Replying to: "' + pendingReplyContext + '"\n' + raw : raw;
    clearReplyContext();
    chatInput.value = "";
    autoGrowChatInput();
    hideSlashMenu();
    showEmptyState(false);
    clearQuickReplies();
    appendChatMessage("user", text);
    chatHistory.push({ role: "user", text: text });
    runAssistantTurn();
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

  // Grows the textarea from 1 up to 10 lines as the user types (capped
  // by max-height in CSS, which also takes over with an internal
  // scrollbar beyond that) instead of staying pinned to one line.
  function autoGrowChatInput() {
    chatInput.style.height = "auto";
    chatInput.style.height = chatInput.scrollHeight + "px";
  }
  chatInput.addEventListener("input", autoGrowChatInput);

  // ---------- "/" slash commands (Discord-style) ----------
  // Typing "/" opens a small picker above the input -- right now just
  // /image, the one capability that used to live behind the removed
  // Plug-Ins toggle. Picking it (or just typing it out by hand) inserts
  // the command; the backend's generate_image tool description tells
  // the model to treat a leading "/image" as an explicit, unambiguous
  // command rather than plain text (see app.py's _CHAT_TOOLS).

  var SLASH_COMMANDS = [
    { cmd: "/image", label: "Image", desc: "Generate an image from a description" },
  ];

  var slashMenuEl = document.getElementById("chat-slash-menu");

  function renderSlashMenu(matches) {
    slashMenuEl.innerHTML = "";
    matches.forEach(function (c) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-slash-item";
      btn.innerHTML =
        '<span class="chat-slash-cmd mono"></span><span class="chat-slash-desc"></span>';
      btn.querySelector(".chat-slash-cmd").textContent = c.cmd;
      btn.querySelector(".chat-slash-desc").textContent = c.desc;
      btn.addEventListener("click", function () {
        chatInput.value = c.cmd + " ";
        autoGrowChatInput();
        hideSlashMenu();
        chatInput.focus();
      });
      slashMenuEl.appendChild(btn);
    });
    slashMenuEl.hidden = matches.length === 0;
  }

  function hideSlashMenu() {
    slashMenuEl.hidden = true;
  }

  function updateSlashMenu() {
    var value = chatInput.value;
    if (!/^\/\S*$/.test(value)) {
      hideSlashMenu();
      return;
    }
    var typed = value.toLowerCase();
    var matches = SLASH_COMMANDS.filter(function (c) { return c.cmd.indexOf(typed) === 0; });
    renderSlashMenu(matches);
  }

  chatInput.addEventListener("input", updateSlashMenu);

  function renderLoadedConversation(convId, messages) {
    chatConversationId = convId;
    chatHistory = messages.map(function (m) { return { role: m.role, text: m.text, card: m.card }; });
    messagesEl.innerHTML = "";
    if (chatHistory.length === 0) {
      showEmptyState(true);
      return;
    }
    showEmptyState(false);
    chatHistory.forEach(function (m, i) {
      if (m.card) {
        var node = renderCardNode(m.card);
        node.dataset.historyIndex = String(i);
        messagesEl.appendChild(node);
      } else {
        appendChatMessage(m.role, m.text, i);
      }
    });
    scrollChatToBottom();
  }

  function loadConversation(convId) {
    fetch("/api/chat/conversations/" + convId)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) return;
        renderLoadedConversation(convId, data.messages);
      });
  }

  function startNewChat() {
    chatConversationId = null;
    chatHistory = [];
    messagesEl.innerHTML = "";
    showEmptyState(true);
    chatInput.value = "";
    autoGrowChatInput();
    hideSlashMenu();
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
      if (on) hideSlashMenu();
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
      chatDictationPreview.scrollTop = chatDictationPreview.scrollHeight;
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

  // sessionStorage survives normal in-app navigation (e.g. Profile ->
  // back) but is cleared when the tab/PWA is actually closed and
  // reopened -- so a true fresh open always lands on a new chat,
  // while clicking around within the same open session still resumes
  // where you left off.
  var isFreshAppOpen = !sessionStorage.getItem("ploy_session_active");
  sessionStorage.setItem("ploy_session_active", "1");
  var requestedConvId = new URLSearchParams(window.location.search).get("conv");

  // The dashboard route already looked up this user's conversation list
  // (and, for the common case, the most recent one's full messages) to
  // render this page -- reusing that instead of firing a fresh fetch on
  // load means the resumed chat can paint immediately, with no visible
  // "loading" gap between the page appearing and the conversation
  // actually showing up.
  var initialState = window.HOME_STATE || {};
  var conversations = initialState.conversations || [];

  if (requestedConvId) {
    loadConversation(Number(requestedConvId));
    window.history.replaceState({}, "", window.location.pathname);
  } else if (isFreshAppOpen || !conversations.length) {
    showEmptyState(true);
  } else if (initialState.initial_conversation_id === conversations[0].id && initialState.initial_messages) {
    renderLoadedConversation(conversations[0].id, initialState.initial_messages);
  } else {
    loadConversation(conversations[0].id);
  }
})();
