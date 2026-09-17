/* Freelancermap inbox chat — /app/pobox/main (Postfach UI selectors). */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const INBOX_PATH = /\/app\/pobox/i;
  const INBOX_URL = "https://www.freelancermap.com/app/pobox/main";

  const ROW_SELECTOR = "div.pobox-message-preview[data-conversation-id]";
  const REPLY_INPUT = "#pobox-reply-footer-textarea";
  const SEND_BUTTON = "[data-id='pobox-message-footer-reply-send']";
  const THREAD_CONTAINER = ".pobox-message-body .items-container";

  let running = false;

  function onInboxPage() {
    return INBOX_PATH.test(location.pathname + location.href);
  }

  function visible(el) {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    return (
      st.display !== "none" &&
      st.visibility !== "hidden" &&
      (el.offsetParent !== null || st.position === "fixed")
    );
  }

  function qsa(sel, root = document) {
    return Array.from(root.querySelectorAll(sel));
  }

  function setNativeValue(el, value) {
    const proto =
      el instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : el instanceof HTMLInputElement
          ? window.HTMLInputElement.prototype
          : null;
    if (proto) {
      const desc = Object.getOwnPropertyDescriptor(proto, "value");
      desc?.set?.call(el, value);
    } else if (el.isContentEditable) {
      el.focus();
      el.textContent = value;
    } else {
      el.textContent = value;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function listConversationRows() {
    const rows = qsa(ROW_SELECTOR);
    const out = [];
    const seen = new Set();
    for (const el of rows) {
      const id = el.getAttribute("data-conversation-id");
      if (!id || seen.has(id)) continue;
      const nameEl = el.querySelector(".image-row span");
      const name = (nameEl?.textContent || el.textContent || "")
        .trim()
        .split("\n")[0]
        .trim()
        .slice(0, 80);
      const unread = (el.className || "").toLowerCase().includes("unread");
      seen.add(id);
      out.push({
        id: String(id),
        name,
        unread,
        visible: visible(el),
        el,
      });
    }
    out.sort((a, b) => {
      if (a.unread !== b.unread) return a.unread ? -1 : 1;
      if (a.visible !== b.visible) return a.visible ? -1 : 1;
      return 0;
    });
    return out;
  }

  /** Prefer Back / list restore over full reload (reload kills this script). */
  async function restoreConversationList() {
    if (listConversationRows().some((r) => r.visible)) return true;

    const backBtn = qsa(
      "button, a, [role='button'], .pobox-back, [data-id*='back']"
    ).find((el) => {
      if (!visible(el)) return false;
      const t = (el.textContent || "").trim().toLowerCase();
      const aria = (el.getAttribute("aria-label") || "").toLowerCase();
      const id = (el.getAttribute("data-id") || "").toLowerCase();
      return (
        t === "back" ||
        t === "zurück" ||
        t === "zurueck" ||
        aria.includes("back") ||
        id.includes("back") ||
        id.includes("list")
      );
    });
    if (backBtn) {
      try {
        backBtn.click();
      } catch (_e) {
        /* ignore */
      }
      for (let i = 0; i < 15; i++) {
        if (listConversationRows().some((r) => r.visible)) return true;
        await sleep(300);
      }
    }
    return listConversationRows().some((r) => r.visible);
  }

  async function ensureInboxList({ allowReload = false } = {}) {
    if (!onInboxPage()) {
      // Background should navigate; content script must not hard-reload mid-handler.
      return false;
    }
    if (await restoreConversationList()) return true;
    for (let i = 0; i < 12; i++) {
      if (listConversationRows().some((r) => r.visible)) return true;
      await sleep(400);
    }
    if (allowReload && listConversationRows().filter((r) => r.visible).length === 0) {
      console.info("[FM Chat] requesting inbox reload via background");
      try {
        await chrome.runtime.sendMessage({ type: "FM_CHAT_RELOAD_INBOX" });
      } catch (_e) {
        /* ignore */
      }
      await sleep(3200);
    }
    return listConversationRows().length > 0;
  }

  async function openConversation(id, { allowReload = false } = {}) {
    await ensureInboxList({ allowReload });
    let row = document.querySelector(
      `${ROW_SELECTOR}[data-conversation-id='${CSS.escape(String(id))}']`
    );
    if (!row || !visible(row)) {
      await restoreConversationList();
      row = document.querySelector(
        `${ROW_SELECTOR}[data-conversation-id='${CSS.escape(String(id))}']`
      );
    }
    if (!row) return false;
    try {
      row.scrollIntoView({ block: "center", behavior: "instant" });
    } catch (_e) {
      /* ignore */
    }
    try {
      row.click();
    } catch (_e) {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
    for (let i = 0; i < 30; i++) {
      if (document.querySelector(THREAD_CONTAINER)) return true;
      await sleep(200);
    }
    return Boolean(document.querySelector(THREAD_CONTAINER));
  }

  function readDirectedMessages() {
    const container = document.querySelector(THREAD_CONTAINER);
    if (!container) return [];

    const noiseContains = [
      "möchten sie die konversation",
      "in den papierkorb",
      "gesendete anhänge",
      "keine anhänge vorhanden",
      "keine konversation ausgewählt",
      "wählen sie eine konversation",
    ];
    const noiseExact = new Set([
      "ablehnen",
      "antworten",
      "antwort",
      "weiterleiten",
      "zurück",
      "mehr anzeigen",
      "übersetzen",
      "add note",
      "contact",
      "senden",
      "send",
    ]);

    const parentRect = container.getBoundingClientRect();
    const midX = parentRect.left + parentRect.width / 2;

    // Prefer real message nodes over flat innerText (so we know left vs right).
    const candidates = qsa(
      "[class*='conversation-item'], [class*='message-item'], [class*='chat-item'], [data-message], .message, .bubble",
      container
    ).filter((el) => {
      const t = (el.innerText || "").trim();
      if (!t || t.length > 8000) return false;
      const low = t.toLowerCase();
      if (noiseContains.some((n) => low.includes(n))) return false;
      if (noiseExact.has(low)) return false;
      // Skip tiny chrome nodes
      if (t.length < 2) return false;
      return visible(el) || el.offsetHeight > 0;
    });

    const directed = [];
    if (candidates.length) {
      for (const el of candidates) {
        const text = (el.innerText || "").trim();
        if (!text) continue;
        const lowCls = String(el.className || "").toLowerCase();
        const rect = el.getBoundingClientRect();
        const fromUs =
          lowCls.includes("own") ||
          lowCls.includes("outgoing") ||
          lowCls.includes("sent") ||
          lowCls.includes("right") ||
          lowCls.includes("justify-end") ||
          lowCls.includes("self-end") ||
          rect.left + rect.width / 2 > midX + 12;
        directed.push({ text, fromUs, el });
      }
    }

    if (directed.length) return directed.slice(-40);

    // Fallback: flat lines — cannot know direction; mark unknown as fromUs=false
    // but callers must still skip likely outreach text.
    const text = (container.innerText || "").trim();
    if (!text) return [];
    const lines = [];
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line || line.length > 4000) continue;
      const low = line.toLowerCase();
      if (noiseContains.some((n) => low.includes(n))) continue;
      if (noiseExact.has(low)) continue;
      lines.push({ text: line, fromUs: false, el: null });
    }
    return lines.slice(-40);
  }

  function looksLikeOurOutreach(text) {
    const t = (text || "").trim();
    const low = t.toLowerCase();
    if (!t) return false;
    // Typical first DM patterns from this bot
    if (/^(hi|hello|hallo|hey)\b/i.test(t) && /kontrora|freelance|opportunity|project/i.test(t)) {
      return true;
    }
    if (low.includes("kontrora") && (low.includes("looking for") || low.includes("interested"))) {
      return true;
    }
    if (/best regards,?\s*\n?\s*(sabrina|oliver)/i.test(t)) return true;
    return false;
  }

  function latestFreelancerBlob(directed) {
    // Walk from the end; take contiguous messages not from us.
    const incoming = [];
    for (let i = directed.length - 1; i >= 0; i--) {
      const m = directed[i];
      if (m.fromUs || looksLikeOurOutreach(m.text)) break;
      incoming.unshift(m.text);
    }
    if (!incoming.length) return "";
    const chunk = incoming.slice(-8).join("\n").trim();
    if (chunk.length <= 2500) return chunk;
    return chunk.slice(-2500);
  }

  async function processOpenThread(meta) {
    const directed = readDirectedMessages();
    if (!directed.length) return { ok: false, reason: "empty_thread" };
    const last = directed[directed.length - 1];
    const messageBlob = latestFreelancerBlob(directed);

    // No inbound candidate text — e.g. only our outreach is visible.
    if (!messageBlob || last.fromUs || looksLikeOurOutreach(last.text)) {
      return { ok: true, reason: "awaiting_candidate_reply" };
    }

    const botExactStarts = [
      "Okay. Our team members will review your answer.",
      "Okay. Our team will review your answers.",
      "I invited you to the assignment",
      "I invited you to the assessment repository",
      "Invited you to the assignment",
      "Thank you for your work",
      "Thanks — we received that",
      "We will continue with another candidate",
      "Please send your GitHub username",
      "Please share your github username",
      "The next step is a short practical assessment",
      "I could not find that GitHub user",
      "That link points to an organisation account",
      "I am setting up the invite",
      "Let me get someone from the team to pick this up",
      "Just checking in — still interested",
      "Understood. I won't message you further",
    ];
    const lastTrim = last.text.trim();
    const storeEarly = await chrome.storage.local.get({
      chatFingerprints: {},
      chatLastOutbound: {},
    });
    const lastOut = (storeEarly.chatLastOutbound || {})[String(meta.id)] || "";
    if (
      lastOut &&
      (lastTrim === lastOut ||
        lastTrim.startsWith(lastOut.slice(0, Math.min(80, lastOut.length))) ||
        lastOut.startsWith(lastTrim.slice(0, Math.min(80, lastTrim.length))))
    ) {
      return { ok: true, reason: "last_is_bot" };
    }
    if (
      lastTrim.length < 420 &&
      lastTrim.split(/\s+/).length < 70 &&
      botExactStarts.some(
        (m) =>
          lastTrim === m || lastTrim.startsWith(m + "\n") || lastTrim.startsWith(m + " ")
      )
    ) {
      return { ok: true, reason: "last_is_bot" };
    }

    const fingerprint = `${meta.id}:${messageBlob.slice(0, 240)}`;
    const store = await chrome.storage.local.get({
      chatFingerprints: {},
      pendingChatReplies: {},
    });
    const fps = store.chatFingerprints || {};
    if (fps[meta.id] === fingerprint) {
      return { ok: true, reason: "already_processed" };
    }
    const pending = (store.pendingChatReplies || {})[String(meta.id)];
    if (pending && pending.dueAt && pending.dueAt > Date.now() - 30000) {
      return { ok: true, reason: "send_pending" };
    }

    console.info("[FM Chat] turn", meta.id, meta.name, messageBlob.slice(0, 80));
    const res = await chrome.runtime.sendMessage({
      type: "FM_CHAT_TURN",
      conversation_id: String(meta.id),
      display_name: meta.name || null,
      profile_key: null,
      message: messageBlob,
    });
    if (!res?.reply || res.authorised === false) {
      console.info("[FM Chat] no_reply", res?.action || res?.error || res);
      return {
        ok: true,
        reason: "no_reply",
        stage: res?.stage,
        action: res?.action,
        error: res?.error || null,
      };
    }

    const sendAfter =
      typeof res.send_after_sec === "number" && res.send_after_sec >= 120
        ? res.send_after_sec
        : 120 + Math.floor(Math.random() * 480);
    const scheduled = await chrome.runtime.sendMessage({
      type: "FM_SCHEDULE_CHAT_REPLY",
      conversation_id: String(meta.id),
      display_name: meta.name || null,
      text: res.reply,
      send_after_sec: sendAfter,
      fingerprint,
      applicant_id: res.applicant_id || null,
    });
    if (!scheduled?.ok) {
      console.warn("[FM Chat] schedule failed", scheduled);
      return { ok: false, reason: "schedule_failed", action: res.action };
    }
    console.info(
      "[FM Chat] scheduled reply in",
      sendAfter,
      "s for",
      meta.name || meta.id
    );
    return {
      ok: true,
      reason: "scheduled",
      delayMs: sendAfter * 1000,
      stage: res.stage,
      action: res.action,
    };
  }

  async function focusReplyField() {
    let input = document.querySelector(REPLY_INPUT);
    if (!input) {
      for (let i = 0; i < 20 && !input; i++) {
        await sleep(200);
        input =
          document.querySelector(REPLY_INPUT) ||
          document.querySelector(
            ".pobox-message-footer textarea, .pobox-message-footer [contenteditable='true']"
          );
      }
    }
    if (!input) return null;

    try {
      input.scrollIntoView({ block: "end", behavior: "instant" });
    } catch (_e) {
      /* ignore */
    }
    try {
      input.focus();
      input.click();
    } catch (_e) {
      /* ignore */
    }
    await sleep(700);

    const expanded =
      qsa(
        ".pobox-message-footer textarea, textarea#pobox-reply-footer-textarea, #pobox-reply-footer-textarea, .pobox-message-footer [contenteditable='true']"
      ).find(visible) || null;
    return expanded || input;
  }

  function findSendButton() {
    return (
      document.querySelector(SEND_BUTTON) ||
      qsa(
        "button, [role='button'], div.send-reply, [data-id*='reply-send'], [data-id*='send']"
      ).find((el) => {
        if (!visible(el)) return false;
        const t = (el.textContent || "").trim().toLowerCase();
        const id = (el.getAttribute("data-id") || "").toLowerCase();
        const cls = (el.className || "").toString().toLowerCase();
        return (
          id.includes("reply-send") ||
          id.includes("send") ||
          cls.includes("send-reply") ||
          t === "send" ||
          t === "senden" ||
          t === "send message" ||
          t === "nachricht senden" ||
          t === "antworten"
        );
      }) ||
      null
    );
  }

  async function clickSendButton() {
    const btn = findSendButton();
    if (!btn) return false;
    try {
      btn.scrollIntoView({ block: "end", behavior: "instant" });
    } catch (_e) {
      /* ignore */
    }
    // Overlay often blocks normal clicks — escalate like Playwright force.
    const methods = [
      () => btn.click(),
      () =>
        btn.dispatchEvent(
          new MouseEvent("click", { bubbles: true, cancelable: true, view: window })
        ),
      () => {
        const r = btn.getBoundingClientRect();
        const x = r.left + r.width / 2;
        const y = r.top + r.height / 2;
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
          btn.dispatchEvent(
            new MouseEvent(type, {
              bubbles: true,
              cancelable: true,
              clientX: x,
              clientY: y,
              view: window,
            })
          );
        }
      },
    ];
    for (const fn of methods) {
      try {
        fn();
        await sleep(400);
        return true;
      } catch (_e) {
        /* try next */
      }
    }
    return false;
  }

  async function submitViaKeyboard(composer) {
    try {
      composer.focus();
      const mods = [
        { key: "Enter", code: "Enter", ctrlKey: true },
        { key: "Enter", code: "Enter", metaKey: true },
        { key: "Enter", code: "Enter" },
      ];
      for (const mod of mods) {
        composer.dispatchEvent(
          new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...mod })
        );
        composer.dispatchEvent(
          new KeyboardEvent("keyup", { bubbles: true, cancelable: true, ...mod })
        );
        await sleep(300);
      }
      return true;
    } catch (_e) {
      return false;
    }
  }

  function composerValue(el) {
    if (!el) return "";
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return (el.value || "").trim();
    return (el.textContent || "").trim();
  }

  async function sendReply(text) {
    console.info("[FM Chat] sending immediately", String(text || "").slice(0, 60));
    const composer = await focusReplyField();
    if (!composer) return { ok: false, reason: "no_composer" };

    let payload = text;
    if (composer.tagName === "INPUT" && text.includes("\n")) {
      payload = text
        .split(/\n/)
        .map((l) => l.trim())
        .filter(Boolean)
        .join(" ");
    }

    if (composer.tagName === "TEXTAREA" || composer.tagName === "INPUT") {
      setNativeValue(composer, payload);
    } else {
      setNativeValue(composer, payload);
    }
    await sleep(600);

    if (!composerValue(composer)) {
      return { ok: false, reason: "compose_empty" };
    }

    let clicked = await clickSendButton();
    if (!clicked) {
      await submitViaKeyboard(composer);
      clicked = true;
    }
    await sleep(2000);

    // Footer clears on success; leftover text means send failed.
    const leftover = composerValue(composer);
    if (leftover && leftover.slice(0, 40) === String(payload).trim().slice(0, 40)) {
      console.warn("[FM Chat] composer still filled — retry send");
      await clickSendButton();
      await submitViaKeyboard(composer);
      await sleep(2000);
      const still = composerValue(composer);
      if (still && still.slice(0, 40) === String(payload).trim().slice(0, 40)) {
        return { ok: false, reason: "send_not_cleared" };
      }
    }
    return { ok: true, delayMs: 0 };
  }

  async function runInboxPass() {
    if (!onInboxPage()) {
      console.info("[FM Chat] not_inbox", location.href);
      return { ok: false, reason: "not_inbox", href: location.href };
    }
    if (running) return { ok: false, reason: "busy" };
    running = true;
    const results = [];
    try {
      const ready = await ensureInboxList({ allowReload: true });
      if (!ready) {
        return { ok: false, reason: "no_list", href: location.href };
      }
      const snapshot = listConversationRows().slice(0, 12);
      console.info(
        "[FM Chat] conversations",
        snapshot.length,
        snapshot.map((c) => `${c.unread ? "*" : ""}${c.id}:${c.name}`).join(", ")
      );

      for (const c of snapshot) {
        try {
          const opened = await openConversation(c.id, { allowReload: false });
          if (!opened) {
            results.push({ id: c.id, ok: false, reason: "open_failed" });
            continue;
          }
          await sleep(1000);
          const r = await processOpenThread(c);
          results.push({ id: c.id, name: c.name, ...r });
          if (r.ok && r.delayMs) break;
          await sleep(600);
          // Restore list without full reload so the next row is clickable
          await restoreConversationList();
        } catch (e) {
          results.push({ id: c.id, ok: false, reason: String(e) });
        }
      }
      return { ok: true, results, href: location.href };
    } finally {
      running = false;
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "FM_CHAT_PASS") {
      runInboxPass()
        .then((r) => sendResponse(r))
        .catch((e) => sendResponse({ ok: false, reason: String(e) }));
      return true;
    }
    if (msg?.type === "FM_CHAT_SEND") {
      (async () => {
        try {
          // Background already navigated to a fresh inbox list when possible.
          const opened = await openConversation(String(msg.conversation_id), {
            allowReload: false,
          });
          if (!opened) {
            sendResponse({ ok: false, reason: "open_failed" });
            return;
          }
          await sleep(1200);
          const sent = await sendReply(msg.text);
          sendResponse(sent);
        } catch (e) {
          sendResponse({ ok: false, reason: String(e) });
        }
      })();
      return true;
    }
    if (msg?.type === "FM_CHAT_PING") {
      sendResponse({
        ok: true,
        href: location.href,
        rows: listConversationRows().length,
        visibleRows: listConversationRows().filter((r) => r.visible).length,
        running,
      });
      return true;
    }
    return false;
  });
})();
