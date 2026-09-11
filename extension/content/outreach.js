/* Content script: search + Contact form campaign on freelancermap.com
   global FMOutreach */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  let campaignRunning = false;

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

  function firstVisible(selectors, root = document) {
    for (const sel of selectors) {
      for (const n of qsa(sel, root)) {
        if (visible(n)) return n;
      }
    }
    return null;
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

  let activeJobId = null;

  async function emitLog(text, level = "info") {
    console.info("[FM Outreach]", text);
    try {
      await chrome.runtime.sendMessage({ type: "FM_CAMPAIGN_LOG", text });
    } catch (_e) {
      /* popup closed */
    }
    if (activeJobId) {
      try {
        await chrome.runtime.sendMessage({
          type: "FM_JOB_EVENT",
          jobId: activeJobId,
          text,
          level,
        });
      } catch (_e) {
        /* background unavailable */
      }
    }
    try {
      await chrome.storage.local.set({
        campaignLastLog: `${new Date().toISOString()} ${text}`,
      });
    } catch (_e) {
      /* ignore */
    }
  }

  async function finishJob(status, stats, error = null) {
    if (!activeJobId) return;
    const jobId = activeJobId;
    try {
      await chrome.runtime.sendMessage({
        type: "FM_JOB_FINISHED",
        jobId,
        status,
        stats: stats || {},
        error,
      });
    } catch (_e) {
      /* ignore */
    }
  }

  async function shouldStop() {
    const { campaignStop } = await chrome.storage.local.get({ campaignStop: false });
    return Boolean(campaignStop) || !campaignRunning;
  }

  function randomIntervalMs(minSec, maxSec) {
    const a = Math.max(1, Number(minSec) || 240);
    const b = Math.max(a, Number(maxSec) || a);
    const sec = a + Math.floor(Math.random() * (b - a + 1));
    return sec * 1000;
  }

  async function runSearch(keyword) {
    const kw = (keyword || "").trim();
    if (!kw) throw new Error("empty keyword");

    // Prefer staying on /freelancer search.
    if (!/\/freelancer/i.test(location.pathname + location.href)) {
      location.href = "https://www.freelancermap.com/freelancer";
      await sleep(2500);
    }

    const input =
      firstVisible([
        "input[placeholder*='Keyword' i]",
        "input[name*='keyword' i]",
        "input[id*='keyword' i]",
        "input[placeholder*='Search' i]",
        "input[type='search']",
        "aside input[type='text']",
        "form input[type='text']",
      ]) ||
      qsa("input[type='text']").find(
        (el) =>
          visible(el) &&
          /keyword|search|skill/i.test(
            `${el.placeholder} ${el.name} ${el.id} ${el.getAttribute("aria-label") || ""}`
          )
      );

    if (!input) {
      await emitLog("Could not find keyword search input.");
      return false;
    }

    input.focus();
    setNativeValue(input, kw);
    await sleep(300);

    const findBtn = qsa("button, [role='button'], a").find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return (
        visible(el) &&
        (t === "find freelancers" ||
          t === "freelancer finden" ||
          t === "search" ||
          t === "suchen")
      );
    });

    if (findBtn) {
      findBtn.click();
    } else {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true })
      );
      input.form?.requestSubmit?.();
    }

    await emitLog(`Searching for “${kw}”…`);
    await sleep(2500);
    return true;
  }

  function collectProfileCards() {
    const anchors = qsa("a[href*='/freelancer/']").filter((a) => {
      if (!visible(a)) return false;
      const href = a.getAttribute("href") || "";
      if (/\/freelancer\/?(\?|$)/i.test(href)) return false; // search root
      if (/\/freelancer\/(search|list)/i.test(href)) return false;
      return /\/freelancer\/[^/?#]+/i.test(href);
    });

    // Deduplicate by href
    const seen = new Set();
    const cards = [];
    for (const a of anchors) {
      const abs = new URL(a.getAttribute("href"), location.href).pathname;
      if (seen.has(abs)) continue;
      seen.add(abs);
      cards.push(a);
    }
    return cards;
  }

  function readOpenProfile() {
    const root =
      firstVisible(["[role='dialog']", ".modal", "[class*='Modal']"]) ||
      document.body;
    let name = "";
    for (const sel of ["h1", "h2", "[data-testid='freelancer-name']"]) {
      const el = root.querySelector(sel);
      if (el && el.textContent.trim().length > 1) {
        name = el.textContent.trim().split("\n")[0].trim();
        break;
      }
    }

    const lines = (root.innerText || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    let title = "";
    let location = "";
    if (name) {
      const first = name.split(/\s+/)[0].toLowerCase();
      const last = name.split(/\s+/).slice(-1)[0].toLowerCase();
      const idx = lines.findIndex(
        (l) => l.toLowerCase().includes(first) && l.toLowerCase().includes(last)
      );
      const after = idx >= 0 ? lines.slice(idx + 1, idx + 10) : lines.slice(0, 10);
      for (const cand of after) {
        const low = cand.toLowerCase();
        if (
          [
            "verified",
            "premium member",
            "contact",
            "show contact details",
            "watchlist",
          ].includes(low)
        )
          continue;
        if (/€\s*\d|\/\s*h|%\s*available|updated/i.test(cand)) continue;
        if (!title && cand.length > 8) {
          title = cand;
          continue;
        }
        if (
          title &&
          !location &&
          (/,/.test(cand) ||
            /germany|hungary|spain|india|france|italy|remote|united|uk|cyprus|poland/i.test(
              cand
            ))
        ) {
          location = cand;
          break;
        }
      }
    }

    const href = window.location.href;
    const keyMatch = href.match(/\/freelancer\/([^/?#]+)/);
    return {
      name,
      title,
      location,
      profileKey: keyMatch?.[1] || name || href,
    };
  }

  async function clickContact() {
    const candidates = qsa("button, a, [role='button']").filter((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return t === "contact" || t === "kontaktieren" || t === "kontakt";
    });
    const target = candidates.find(visible) || null;
    if (!target) return false;
    target.click();
    await sleep(1200);
    return true;
  }

  async function fillForm({ subject, body }) {
    const subjectInput = firstVisible([
      "input[placeholder*='Subject' i]",
      "input[name*='subject' i]",
      "form input[type='text']",
    ]);
    const textarea = firstVisible([
      "textarea",
      "form textarea",
      "[role='dialog'] textarea",
    ]);
    if (!subjectInput || !textarea) return false;
    setNativeValue(subjectInput, subject);
    setNativeValue(textarea, body);
    return true;
  }

  async function clickSend() {
    const btn = qsa("button, [role='button']").find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return (
        visible(el) &&
        (t === "send message" || t === "nachricht senden" || t === "send")
      );
    });
    if (!btn) return false;
    btn.click();
    await sleep(1200);
    return true;
  }

  async function closeModal() {
    const btn = qsa("button, [aria-label]").find((el) => {
      const label = (el.getAttribute("aria-label") || "").toLowerCase();
      const t = (el.textContent || "").trim();
      return (
        visible(el) &&
        (label === "close" || t === "×" || t === "x" || label.includes("close"))
      );
    });
    if (btn) btn.click();
    else
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
      );
    await sleep(600);
  }

  async function runOne(settings) {
    const T = globalThis.FMOutreach;
    const info = readOpenProfile();
    if (!info.name) {
      return { ok: false, reason: "no_profile_name", info };
    }

    if (
      (await T.wasContactedByKey?.(settings, info.profileKey)) ||
      (await T.wasContactedByName(settings, info.name))
    ) {
      return { ok: false, reason: "already_contacted", info };
    }

    let hasForm = Boolean(firstVisible(["textarea"]));
    if (!hasForm) {
      const clicked = await clickContact();
      if (!clicked) return { ok: false, reason: "contact_button_missing", info };
      await sleep(800);
      hasForm = Boolean(firstVisible(["textarea"]));
      if (!hasForm) return { ok: false, reason: "contact_form_missing", info };
    }

    const detail = T.pickDetail(info.title, info.location);
    const subject = T.OUTREACH_SUBJECT;
    const body = T.renderBody(info.name, detail);
    const filled = await fillForm({ subject, body });
    if (!filled) return { ok: false, reason: "fill_failed", info };

    if (settings.dryRun) {
      return { ok: true, reason: "dry_run", info, subject, detail };
    }

    const sent = await clickSend();
    if (!sent) return { ok: false, reason: "send_failed", info };

    await T.markContacted(settings, {
      name: info.name,
      profileKey: info.profileKey,
      projectName: settings.projectName || null,
    });
    return { ok: true, reason: "sent", info, subject, detail };
  }

  async function runCampaign(settings) {
    if (campaignRunning) {
      return { ok: false, reason: "already_running" };
    }
    campaignRunning = true;
    activeJobId = settings.jobId || null;
    await chrome.storage.local.set({ campaignStop: false });

    const stats = { attempted: 0, sent: 0, skipped: 0, failed: 0 };
    let finishStatus = "completed";
    let finishError = null;
    try {
      const okSearch = await runSearch(settings.keyword);
      if (!okSearch) {
        finishStatus = "failed";
        finishError = "search_failed";
        await finishJob(finishStatus, stats, finishError);
        return { ok: false, reason: "search_failed", stats };
      }

      let cards = collectProfileCards();
      await emitLog(`Found ${cards.length} profile link(s) on page.`);
      if (!cards.length) {
        finishStatus = "failed";
        finishError = "no_results";
        await finishJob(finishStatus, stats, finishError);
        return { ok: false, reason: "no_results", stats };
      }

      const limit = Math.max(1, Number(settings.limit) || 10);
      let i = 0;
      while (stats.sent + stats.failed < limit && i < cards.length) {
        if (await shouldStop()) {
          await emitLog("Campaign stopped.");
          finishStatus = "stopped";
          break;
        }

        // Refresh card list in case DOM recycled
        cards = collectProfileCards();
        if (i >= cards.length) break;
        const card = cards[i];
        i += 1;
        stats.attempted += 1;

        try {
          card.scrollIntoView({ block: "center", behavior: "instant" });
          await sleep(400);
          card.click();
          await sleep(1500);

          const result = await runOne(settings);
          if (result.ok) {
            stats.sent += 1;
            await emitLog(
              `${settings.dryRun ? "Dry-run" : "Sent"} → ${result.info?.name || "?"} (${result.reason})`
            );
          } else if (result.reason === "already_contacted") {
            stats.skipped += 1;
            await emitLog(`Skip already contacted → ${result.info?.name || "?"}`);
          } else {
            stats.failed += 1;
            await emitLog(`Failed → ${result.info?.name || "?"} (${result.reason})`);
          }

          await closeModal();
          await sleep(500);

          // Back on search list — if we navigated away, go back
          if (!collectProfileCards().length && /\/freelancer\/[^/?#]+/i.test(location.pathname)) {
            history.back();
            await sleep(1500);
          }

          if (stats.sent + stats.failed >= limit) break;
          if (await shouldStop()) {
            finishStatus = "stopped";
            break;
          }

          const waitMs = randomIntervalMs(
            settings.minIntervalSec,
            settings.maxIntervalSec
          );
          await emitLog(`Waiting ${Math.round(waitMs / 1000)}s before next DM…`);
          const end = Date.now() + waitMs;
          while (Date.now() < end) {
            if (await shouldStop()) {
              finishStatus = "stopped";
              break;
            }
            await sleep(Math.min(1000, end - Date.now()));
          }
        } catch (err) {
          stats.failed += 1;
          await emitLog(`Error on card ${i}: ${err}`, "error");
          await closeModal();
        }
      }

      await emitLog(
        `Done. attempted=${stats.attempted} sent=${stats.sent} skipped=${stats.skipped} failed=${stats.failed}`
      );
      await finishJob(finishStatus, stats, finishError);
      return { ok: true, stats };
    } catch (err) {
      finishStatus = "failed";
      finishError = String(err);
      await emitLog(`Campaign crashed: ${err}`, "error");
      await finishJob(finishStatus, stats, finishError);
      return { ok: false, reason: String(err), stats };
    } finally {
      campaignRunning = false;
      activeJobId = null;
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "FM_PING") {
      sendResponse({ ok: true, href: location.href, campaignRunning });
      return true;
    }
    if (msg?.type === "FM_STOP_CAMPAIGN") {
      campaignRunning = false;
      chrome.storage.local.set({ campaignStop: true });
      sendResponse({ ok: true });
      return true;
    }
    if (msg?.type === "FM_START_CAMPAIGN") {
      // Respond immediately — campaign can run many minutes (channel would time out).
      sendResponse({ ok: true, reason: "started" });
      runCampaign(msg.settings || {});
      return false;
    }
    if (msg?.type === "FM_RUN_ONE") {
      runOne(msg.settings || {})
        .then((result) => sendResponse(result))
        .catch((err) => sendResponse({ ok: false, reason: String(err) }));
      return true;
    }
    if (msg?.type === "FM_READ_PROFILE") {
      sendResponse({ ok: true, info: readOpenProfile() });
      return true;
    }
    if (msg?.type === "FM_CLOSE_MODAL") {
      closeModal().then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  });
})();
