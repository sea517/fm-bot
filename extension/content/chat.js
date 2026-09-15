/* Freelancermap inbox chat — /app/pobox/main (Postfach UI selectors). */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const INBOX_PATH = /\/app\/pobox/i;
  const INBOX_URL = "https://www.freelancermap.com/app/pobox/main";

  // Verified Postfach markup (same as src/freelancermap/messages.py)
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
    } else {
      el.textContent = value;
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /** SPEC: reply delay 2–10 minutes; prefer send_after_sec from Control API. */
  let lastReplyDelayMs = 0;
  function replyDelayMs(text, sendAfterSec) {
    const minSec = 120;
    const maxSec = 600;
    if (
      typeof sendAfterSec === "number" &&
      Number.isFinite(sendAfterSec) &&
      sendAfterSec >= minSec &&
      sendAfterSec <= maxSec
    ) {
      lastReplyDelayMs = Math.round(sendAfterSec * 1000);
      return lastReplyDelayMs;
    }
    const raw = (text || "").trim();
    const words = raw ? raw.split(/\s+/).length : 0;
    const chars = raw.length;
    const lengthFactor = Math.min(1, words / 80 + chars / 1200);
    const span = maxSec - minSec;
    const center = minSec + span * (0.15 + lengthFactor * 0.7);
    const spread = span * 0.45;
    let sec = center + (Math.random() * 2 - 1) * spread;
    sec = Math.max(minSec, Math.min(maxSec, sec));
    let ms = Math.round(sec * 1000);
    if (Math.abs(ms - lastReplyDelayMs) < 15000) {
      const bump = 30000 + Math.floor(Math.random() * 90000);
      ms = Math.max(
        minSec * 1000,
        Math.min(maxSec * 1000, ms + (Math.random() < 0.5 ? bump : -bump))
      );
    }
    ms = Math.max(
      minSec * 1000,
      Math.min(maxSec * 1000, ms + Math.floor(Math.random() * 20000) - 10000)
    );
    lastReplyDelayMs = ms;
    return ms;
  }

  function listConversationRows() {
    const rows = qsa(ROW_SELECTOR);
    const out = [];
    const seen = new Set();
    for (const el of rows) {
      const id = el.getAttribute("data-conversation-id");
      if (!id || seen.has(id)) continue;
      // Prefer visible; still keep hidden unread IDs when list is partially scrolled
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
    // Unread first, then visible, then rest
    out.sort((a, b) => {
      if (a.unread !== b.unread) return a.unread ? -1 : 1;
      if (a.visible !== b.visible) return a.visible ? -1 : 1;
      return 0;
    });
    return out;
  }

  async function ensureInboxList() {
    if (!onInboxPage()) {
      location.assign(INBOX_URL);
      await sleep(2500);
    }
    // Opening a thread hides the list — reload if no visible rows.
    const visibleRows = listConversationRows().filter((r) => r.visible);
    if (visibleRows.length === 0) {
      console.info("[FM Chat] conversation list hidden — reloading inbox");
      location.assign(INBOX_URL);
      await sleep(2800);
    }
    for (let i = 0; i < 20; i++) {
      if (listConversationRows().some((r) => r.visible)) return true;
      await sleep(400);
    }
    return listConversationRows().length > 0;
  }

  async function openConversation(id) {
    await ensureInboxList();
    let row = document.querySelector(
      `${ROW_SELECTOR}[data-conversation-id='${CSS.escape(String(id))}']`
    );
    if (!row || !visible(row)) {
      location.assign(INBOX_URL);
      await sleep(2500);
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
    row.click();
    for (let i = 0; i < 25; i++) {
      if (document.querySelector(THREAD_CONTAINER)) return true;
      await sleep(200);
    }
    return Boolean(document.querySelector(THREAD_CONTAINER));
  }

  function readThreadMessages() {
    const container = document.querySelector(THREAD_CONTAINER);
    if (!container) return [];
    const text = (container.innerText || "").trim();
    if (!text) return [];

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

    const lines = [];
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line || line.length > 4000) continue;
      const low = line.toLowerCase();
      if (noiseContains.some((n) => low.includes(n))) continue;
      if (noiseExact.has(low)) continue;
      lines.push(line);
    }
    return lines.slice(-40);
  }

  /** Newest candidate-facing blob for the API (not just one line). */
  function latestMessageBlob(lines) {
    if (!lines.length) return "";
    // Prefer the last ~8 lines / 2500 chars — catches multi-line replies
    const chunk = lines.slice(-8).join("\n").trim();
    if (chunk.length <= 2500) return chunk;
    return chunk.slice(-2500);
  }

  async function focusReplyField() {
    let input = document.querySelector(REPLY_INPUT);
    if (!input) {
      // Wait briefly for footer
      for (let i = 0; i < 15 && !input; i++) {
        await sleep(200);
        input = document.querySelector(REPLY_INPUT);
      }
    }
    if (!input) return null;

    try {
      input.focus();
    } catch (_e) {
      try {
        input.click();
      } catch (_e2) {
        /* ignore */
      }
    }
    await sleep(900);

    const expanded =
      qsa(
        ".pobox-message-footer textarea, textarea#pobox-reply-footer-textarea, #pobox-reply-footer-textarea"
      ).find(visible) || null;
    return expanded || input;
  }

  async function clickSendButton() {
    const btn =
      document.querySelector(SEND_BUTTON) ||
      qsa("button, [role='button'], [data-id*='reply-send']").find((el) => {
        const t = (el.textContent || "").trim().toLowerCase();
        const id = (el.getAttribute("data-id") || "").toLowerCase();
        return (
          visible(el) &&
          (id.includes("reply-send") ||
            t === "send" ||
            t === "send message" ||
            t === "nachricht senden" ||
            t === "antworten")
        );
      });
    if (!btn) return false;
    try {
      btn.click();
      return true;
    } catch (_e) {
      try {
        btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        return true;
      } catch (_e2) {
        return false;
      }
    }
  }

  async function sendReply(text, sendAfterSec) {
    const delay = replyDelayMs(text, sendAfterSec);
    console.info("[FM Chat] waiting", Math.round(delay / 1000), "s before send");
    await sleep(delay);

    const composer = await focusReplyField();
    if (!composer) return { ok: false, reason: "no_composer" };

    if (composer.tagName === "TEXTAREA" || composer.tagName === "INPUT") {
      setNativeValue(composer, text);
    } else {
      composer.focus();
      composer.textContent = text;
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    }
    await sleep(500);

    const clicked = await clickSendButton();
    if (!clicked) return { ok: false, reason: "no_send_button" };
    await sleep(1500);
    return { ok: true, delayMs: delay };
  }

  async function processOpenThread(meta) {
    const lines = readThreadMessages();
    if (!lines.length) return { ok: false, reason: "empty_thread" };
    const last = lines[lines.length - 1];
    const messageBlob = latestMessageBlob(lines);
    // Skip only when the last bubble is one of our short fixed templates
    // (not candidate replies that happen to include "Best regards").
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
    ];
    const lastTrim = last.trim();
    if (
      lastTrim.length < 420 &&
      lastTrim.split(/\s+/).length < 70 &&
      botExactStarts.some(
        (m) => lastTrim === m || lastTrim.startsWith(m + "\n") || lastTrim.startsWith(m + " ")
      )
    ) {
      return { ok: true, reason: "last_is_bot" };
    }

    const fingerprint = `${meta.id}:${messageBlob.slice(0, 240)}`;
    const store = await chrome.storage.local.get({ chatFingerprints: {} });
    const fps = store.chatFingerprints || {};
    if (fps[meta.id] === fingerprint) {
      return { ok: true, reason: "already_processed" };
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
      if (res?.action === "duplicate" || res?.action === "coalesced") {
        fps[meta.id] = fingerprint;
        await chrome.storage.local.set({ chatFingerprints: fps });
      }
      console.info("[FM Chat] no_reply", res?.action || res?.error || res);
      return {
        ok: true,
        reason: "no_reply",
        stage: res?.stage,
        action: res?.action,
        error: res?.error || null,
      };
    }
    const sent = await sendReply(res.reply, res.send_after_sec);
    if (sent.ok) {
      fps[meta.id] = fingerprint;
      await chrome.storage.local.set({ chatFingerprints: fps });
      console.info("[FM Chat] sent reply to", meta.name || meta.id);
    } else {
      console.warn("[FM Chat] send failed", sent.reason);
    }
    return { ...sent, stage: res.stage, action: res.action };
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
      await ensureInboxList();
      // Snapshot IDs first (DOM nodes go stale after open/reload)
      const snapshot = listConversationRows().slice(0, 12);
      console.info(
        "[FM Chat] conversations",
        snapshot.length,
        snapshot.map((c) => `${c.unread ? "*" : ""}${c.id}:${c.name}`).join(", ")
      );

      for (const c of snapshot) {
        try {
          const opened = await openConversation(c.id);
          if (!opened) {
            results.push({ id: c.id, ok: false, reason: "open_failed" });
            continue;
          }
          await sleep(1000);
          const r = await processOpenThread(c);
          results.push({ id: c.id, name: c.name, ...r });
          // One successful live send per pass keeps the SW timeout healthy
          if (r.ok && r.delayMs) break;
          await sleep(600);
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
          const opened = await openConversation(String(msg.conversation_id));
          if (!opened) {
            sendResponse({ ok: false, reason: "open_failed" });
            return;
          }
          await sleep(1000);
          const sent = await sendReply(msg.text, msg.send_after_sec);
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
        running,
      });
      return true;
    }
    return false;
  });
})();
