/* Freelancermap inbox chat worker — assessment replies via Control API. */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let running = false;

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

  function conversationIdFromHref(href) {
    try {
      const u = new URL(href, location.href);
      const m =
        u.pathname.match(/\/(?:pobox|messages|conversation|chat)\/(\d+)/i) ||
        u.search.match(/[?&](?:id|conversationId|conversation)=(\d+)/i);
      return m?.[1] || null;
    } catch {
      return null;
    }
  }

  function listConversationAnchors() {
    const anchors = qsa("a[href]").filter((a) => {
      if (!visible(a)) return false;
      const href = a.getAttribute("href") || "";
      return /pobox|messages|conversation|chat/i.test(href) && /\d{4,}/.test(href);
    });
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
    return out.slice(0, 40);
  }

  function readThreadMessages() {
    const root =
      document.querySelector("[class*='message'], [class*='Message'], main, [role='main']") ||
      document.body;
    const blocks = qsa("p, div, li", root).filter((el) => {
      if (!visible(el)) return false;
      const t = (el.textContent || "").trim();
      if (t.length < 2 || t.length > 4000) return false;
      if (el.querySelector("p, div")) return false; // leaf-ish
      return true;
    });
    // Prefer last substantial text blocks in the thread pane
    return blocks.slice(-12).map((el) => (el.textContent || "").trim());
  }

  function findComposer() {
    return (
      qsa("textarea").find(visible) ||
      qsa("[contenteditable='true']").find(visible) ||
      null
    );
  }

  async function sendReply(text) {
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
    return { ok: true };
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
      return { ok: true, reason: "no_reply", stage: res?.stage, action: res?.action };
    }
    const sent = await sendReply(res.reply);
    if (sent.ok) {
      fps[meta.id] = fingerprint;
      await chrome.storage.local.set({ chatFingerprints: fps });
    }
    return { ...sent, stage: res.stage, action: res.action };
  }

  async function runInboxPass() {
    if (running) return { ok: false, reason: "busy" };
    running = true;
    const results = [];
    try {
      // Due rejections first
      try {
        const due = await chrome.runtime.sendMessage({ type: "FM_CHAT_DUE" });
        for (const item of due?.items || []) {
          if (item.conversation_id && item.reply) {
            // Open matching thread if present
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
      return { ok: true, results };
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
