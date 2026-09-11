/* Freelancermap inbox chat — only on /app/pobox/main. */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const INBOX_PATH = /\/app\/pobox/i;
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
        : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    desc?.set?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /** Per-reply delay: random 5–120s, longer answers bias toward the high end. */
  let lastReplyDelayMs = 0;
  function replyDelayMs(text) {
    const raw = (text || "").trim();
    const words = raw ? raw.split(/\s+/).length : 0;
    const chars = raw.length;
    // Length factor 0..1 → prefer longer waits for longer replies
    const lengthFactor = Math.min(1, words / 80 + chars / 1200);
    const minSec = 5;
    const maxSec = 120;
    const span = maxSec - minSec;
    // Random around a length-biased center, still covering most of 5–120
    const center = minSec + span * (0.15 + lengthFactor * 0.7);
    const spread = span * 0.45;
    let sec = center + (Math.random() * 2 - 1) * spread;
    sec = Math.max(minSec, Math.min(maxSec, sec));
    // Ensure this wait differs from the previous reply (at least ~3s apart)
    let ms = Math.round(sec * 1000);
    if (Math.abs(ms - lastReplyDelayMs) < 3000) {
      const bump = 5000 + Math.floor(Math.random() * 25000);
      ms = Math.max(minSec * 1000, Math.min(maxSec * 1000, ms + (Math.random() < 0.5 ? bump : -bump)));
    }
    // Final full-range jitter so every reply is unique
    ms = Math.max(
      minSec * 1000,
      Math.min(
        maxSec * 1000,
        ms + Math.floor(Math.random() * 7000) - 3500
      )
    );
    lastReplyDelayMs = ms;
    return ms;
  }

  function conversationIdFromHref(href) {
    try {
      const u = new URL(href, location.href);
      const m =
        u.pathname.match(/\/(?:app\/)?pobox\/(?:main\/)?(?:conversation\/|thread\/)?(\d+)/i) ||
        u.pathname.match(/\/(?:messages|conversation|chat)\/(\d+)/i) ||
        u.search.match(/[?&](?:id|conversationId|conversation|threadId)=(\d+)/i) ||
        u.hash.match(/(\d{4,})/);
      return m?.[1] || null;
    } catch {
      return null;
    }
  }

  function listConversationAnchors() {
    const anchors = qsa("a[href]").filter((a) => {
      if (!visible(a)) return false;
      const href = a.getAttribute("href") || "";
      return /pobox|messages|conversation|chat|thread/i.test(href) && /\d{3,}/.test(href);
    });
    // Also clickable rows that are not <a>
    const rows = qsa("[data-conversation-id], [data-id], [role='listitem'], li, tr").filter(
      (el) => {
        if (!visible(el)) return false;
        const id =
          el.getAttribute("data-conversation-id") ||
          el.getAttribute("data-id") ||
          "";
        return /^\d{3,}$/.test(id);
      }
    );

    const seen = new Set();
    const out = [];
    for (const a of anchors) {
      const id = conversationIdFromHref(a.href);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({
        id,
        name: (a.textContent || "").trim().split("\n")[0].trim().slice(0, 80),
        el: a,
      });
    }
    for (const row of rows) {
      const id =
        row.getAttribute("data-conversation-id") || row.getAttribute("data-id");
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({
        id: String(id),
        name: (row.textContent || "").trim().split("\n")[0].trim().slice(0, 80),
        el: row,
      });
    }
    return out.slice(0, 40);
  }

  function readThreadMessages() {
    const root =
      document.querySelector(
        "[class*='message'], [class*='Message'], [class*='thread'], [class*='Thread'], main, [role='main']"
      ) || document.body;
    const blocks = qsa("p, div, li, span", root).filter((el) => {
      if (!visible(el)) return false;
      const t = (el.textContent || "").trim();
      if (t.length < 2 || t.length > 4000) return false;
      if (el.querySelector("p, div, li")) return false;
      return true;
    });
    return blocks.slice(-16).map((el) => (el.textContent || "").trim());
  }

  function findComposer() {
    return (
      qsa("textarea").find(visible) ||
      qsa("[contenteditable='true']").find(visible) ||
      null
    );
  }

  async function sendReply(text) {
    const delay = replyDelayMs(text);
    console.info("[FM Chat] waiting", Math.round(delay / 1000), "s before send");
    await sleep(delay);

    const composer = findComposer();
    if (!composer) return { ok: false, reason: "no_composer" };
    if (composer.tagName === "TEXTAREA" || composer.tagName === "INPUT") {
      setNativeValue(composer, text);
    } else {
      composer.focus();
      composer.textContent = text;
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    }
    await sleep(400);
    const btn = qsa("button, [role='button']").find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return (
        visible(el) &&
        (t === "send" ||
          t === "send message" ||
          t === "nachricht senden" ||
          t === "antworten")
      );
    });
    if (!btn) return { ok: false, reason: "no_send_button" };
    btn.click();
    await sleep(1200);
    return { ok: true, delayMs: delay };
  }

  async function processOpenThread(meta) {
    const lines = readThreadMessages();
    if (!lines.length) return { ok: false, reason: "empty_thread" };
    const last = lines[lines.length - 1];
    const botMarkers = [
      "Okay. Our team members will review your answer.",
      "Invited you to the assignment.",
      "Thank you for your work. Let us check",
      "We were impressed with your work; however",
      "Please reply with your GitHub username",
    ];
    if (botMarkers.some((m) => last.includes(m))) {
      return { ok: true, reason: "last_is_bot" };
    }
    const fingerprint = `${meta.id}:${last.slice(0, 200)}`;
    const store = await chrome.storage.local.get({ chatFingerprints: {} });
    const fps = store.chatFingerprints || {};
    if (fps[meta.id] === fingerprint) {
      return { ok: true, reason: "already_processed" };
    }

    const res = await chrome.runtime.sendMessage({
      type: "FM_CHAT_TURN",
      conversation_id: String(meta.id),
      display_name: meta.name || null,
      profile_key: null,
      message: last,
    });
    if (!res?.reply) {
      if (res?.action === "duplicate") {
        fps[meta.id] = fingerprint;
        await chrome.storage.local.set({ chatFingerprints: fps });
      }
      return {
        ok: true,
        reason: "no_reply",
        stage: res?.stage,
        action: res?.action,
      };
    }
    const sent = await sendReply(res.reply);
    if (sent.ok) {
      fps[meta.id] = fingerprint;
      await chrome.storage.local.set({ chatFingerprints: fps });
    }
    return { ...sent, stage: res.stage, action: res.action };
  }

  async function runInboxPass() {
    if (!onInboxPage()) {
      return { ok: false, reason: "not_inbox", href: location.href };
    }
    if (running) return { ok: false, reason: "busy" };
    running = true;
    const results = [];
    try {
      try {
        const due = await chrome.runtime.sendMessage({ type: "FM_CHAT_DUE" });
        for (const item of due?.items || []) {
          if (item.conversation_id && item.reply) {
            const match = listConversationAnchors().find(
              (c) => c.id === String(item.conversation_id)
            );
            if (match) {
              match.el.click();
              await sleep(1500);
              await sendReply(item.reply);
              results.push({ id: item.conversation_id, action: "rejected" });
            }
          }
        }
      } catch (_e) {
        /* ignore */
      }

      const convos = listConversationAnchors();
      for (const c of convos.slice(0, 8)) {
        try {
          c.el.click();
          await sleep(1500);
          const r = await processOpenThread(c);
          results.push({ id: c.id, ...r });
          await sleep(800);
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
    return false;
  });
})();
