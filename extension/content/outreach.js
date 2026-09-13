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

  /** Random pause between outreach UI steps — disabled; only DM interval remains. */
  function stepDelayMs() {
    return 0;
  }

  async function pauseStep(label) {
    await touchCampaignAlive();
    if (await shouldStop()) return false;
    // No per-action wait; anti-spam delay is only waitBetweenDms (240–300s).
    if (label) {
      /* kept for call-site clarity; intentionally not logged as a wait */
    }
    return true;
  }

  async function clickResetAll() {
    const btn = qsa("a, button, [role='button'], span").find((el) => {
      if (!visible(el)) return false;
      const t = (el.textContent || "").trim().toLowerCase();
      return t === "reset all" || t === "alle zurücksetzen";
    });
    if (!btn) {
      await emitLog("No “Reset all” control found — continuing.");
      return false;
    }
    btn.click();
    await emitLog("Clicked “Reset all”.");
    await sleep(1200);
    return true;
  }

  /** Remove skill chips like "JavaScript" (keep Worldwide if present). */
  async function clearSkillChips() {
    const removable = qsa("button, a, span, [role='button']").filter((el) => {
      if (!visible(el)) return false;
      const t = (el.textContent || "").trim();
      if (!t || t.length > 80) return false;
      const low = t.toLowerCase();
      if (low === "worldwide" || low === "reset all" || low === "recently updated") {
        return false;
      }
      // Chip with an X / close control
      const hasX =
        /×|x$/i.test(t) ||
        el.querySelector("svg, [class*='close'], [class*='Close'], [aria-label*='remove' i]");
      if (!hasX && !/exclude freelancers/i.test(t)) return false;
      return true;
    });
    let removed = 0;
    for (const chip of removable.slice(0, 12)) {
      const close =
        chip.querySelector(
          "button, [aria-label*='remove' i], [aria-label*='close' i], svg, [class*='close']"
        ) || chip;
      try {
        close.click();
        removed += 1;
        await sleep(350);
      } catch (_e) {
        /* ignore */
      }
    }
    if (removed) await emitLog(`Cleared ${removed} filter chip(s).`);
    return removed;
  }

  function searchUrlForKeyword(kw) {
    const u = new URL("https://www.freelancermap.com/freelancer");
    u.searchParams.set("query", kw);
    return u.toString();
  }

  function activeFilterLabels() {
    return qsa("button, a, span, [class*='chip'], [class*='Chip'], [class*='tag'], [class*='Tag']")
      .filter(visible)
      .map((el) => (el.textContent || "").trim().replace(/\s*×\s*$/, "").trim())
      .filter((t) => t.length > 1 && t.length < 80);
  }

  function keywordAppearsInFilters(kw) {
    const parts = String(kw)
      .toLowerCase()
      .split(/[\s,/|]+/)
      .filter((p) => p.length > 2);
    const labels = activeFilterLabels().map((t) => t.toLowerCase());
    const joined = labels.join(" | ");
    if (joined.includes(String(kw).toLowerCase())) return true;
    // At least one meaningful token from the keyword should appear (e.g. FastAPI)
    return parts.some((p) => labels.some((l) => l.includes(p)));
  }

  async function markAwaitResults() {
    try {
      const { pendingCampaign } = await chrome.storage.local.get({
        pendingCampaign: null,
      });
      if (pendingCampaign?.settings) {
        await chrome.storage.local.set({
          pendingCampaign: {
            ...pendingCampaign,
            phase: "await_results",
            startedAt: pendingCampaign.startedAt || Date.now(),
          },
        });
      }
    } catch (_e) {
      /* ignore */
    }
  }

  /**
   * Apply keyword via URL ?query=… (reliable). UI autocomplete often turns
   * "FastAPI Next.js" into a wrong skill chip like "JavaScript".
   */
  async function ensureSearchApplied(kw) {
    const target = searchUrlForKeyword(kw);
    const currentQ = (new URLSearchParams(location.search).get("query") || "").trim();
    if (
      !/\/freelancer/i.test(location.pathname) ||
      currentQ.toLowerCase() !== kw.toLowerCase()
    ) {
      await emitLog(`Navigating to “${kw}” search…`);
      await markAwaitResults();
      location.href = target;
      await sleep(3500);
      return true;
    }

    // Query already correct — do not clear chips or reload (that restarts search mid-DM).
    const labels = activeFilterLabels().map((t) => t.toLowerCase());
    const hasWrongSkill =
      labels.some((l) => l === "javascript" || l === "java") &&
      !keywordAppearsInFilters(kw);
    if (hasWrongSkill) {
      await emitLog("Wrong skill chip detected — reloading clean query URL…");
      await markAwaitResults();
      location.href = target;
      await sleep(3500);
    }
    return true;
  }

  async function runSearch(keyword) {
    const kw = (keyword || "").trim();
    if (!kw) throw new Error("empty keyword");

    if (/\/app\/pobox/i.test(location.href)) {
      await markAwaitResults();
      location.href = searchUrlForKeyword(kw);
      await sleep(3000);
      return true;
    }

    await clickResetAll();
    await clearSkillChips();
    await sleep(500);

    await ensureSearchApplied(kw);

    const q = (new URLSearchParams(location.search).get("query") || "").trim();
    if (q.toLowerCase() !== kw.toLowerCase()) {
      await emitLog(`Re-applying query: “${kw}”`);
      await markAwaitResults();
      location.href = searchUrlForKeyword(kw);
      await sleep(3500);
    }

    if (
      !keywordAppearsInFilters(kw) &&
      !(new URLSearchParams(location.search).get("query") || "")
        .toLowerCase()
        .includes(kw.split(/\s+/)[0].toLowerCase())
    ) {
      await emitLog("Typing keyword without autocomplete as fallback…");
      const ok = await typeKeywordWithoutAutocomplete(kw);
      if (!ok) return false;
    }

    await emitLog(
      `Search ready for “${kw}”. URL query=${new URLSearchParams(location.search).get("query") || "(none)"}; filters: ${activeFilterLabels().slice(0, 8).join(", ") || "(none)"}`
    );
    return true;
  }

  async function typeKeywordWithoutAutocomplete(kw) {
    const input =
      firstVisible([
        "input[placeholder*='Search by skills' i]",
        "input[placeholder*='Keyword' i]",
        "input[placeholder*='Search' i]",
        "input[type='search']",
        "input[name*='keyword' i]",
        "input[name*='query' i]",
      ]) ||
      qsa("input[type='text'], input[type='search']").find(
        (el) =>
          visible(el) &&
          /keyword|search|skill|query/i.test(
            `${el.placeholder} ${el.name} ${el.id} ${el.getAttribute("aria-label") || ""}`
          )
      );

    if (!input) {
      await emitLog("Could not find keyword search input.");
      return false;
    }

    input.focus();
    setNativeValue(input, "");
    await sleep(200);
    // Commit free-text with Enter; do NOT click autocomplete suggestions.
    setNativeValue(input, kw);
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true })
    );
    await sleep(200);
    input.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
      })
    );
    await sleep(600);

    const findBtn = qsa("button, [role='button'], a").find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return (
        visible(el) &&
        (t === "find freelancers" || t === "freelancer finden")
      );
    });
    if (findBtn) findBtn.click();
    await sleep(2500);
    return true;
  }

  function parseFreelancerCountToken(raw) {
    if (!raw) return null;
    const n = Number.parseInt(String(raw).replace(/[.,\u00a0\s]/g, ""), 10);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }

  /**
   * Total from the results heading, e.g. "79,097 freelancers".
   * Prefer dedicated headings; take the largest match so sidebar crumbs don't win.
   */
  function readSearchResultCount() {
    const re = /([\d]{1,3}(?:[.,\u00a0\s]\d{3})+|\d+)\s*freelancers?\b/gi;
    const candidates = [];

    for (const el of qsa("h1, h2, h3")) {
      if (!visible(el)) continue;
      const t = (el.textContent || "").trim();
      // Exact-ish heading: "79,097 freelancers"
      const only = t.match(
        /^([\d]{1,3}(?:[.,\u00a0\s]\d{3})+|\d+)\s*freelancers?\s*$/i
      );
      if (only) {
        const n = parseFreelancerCountToken(only[1]);
        if (n != null) candidates.push({ n, score: 100 + Math.min(n, 1e6) / 1e6 });
      }
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(t))) {
        const n = parseFreelancerCountToken(m[1]);
        if (n != null) candidates.push({ n, score: 50 + n / 1e9 });
      }
    }

    // Also scan a short window of main content for the big total
    const main =
      document.querySelector("main, [role='main']") || document.body;
    const text = (main?.innerText || "").slice(0, 12000);
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      const n = parseFreelancerCountToken(m[1]);
      // Ignore tiny counts that are unlikely to be the results total
      if (n != null && n >= 10) candidates.push({ n, score: 10 + n / 1e9 });
    }

    if (!candidates.length) return null;
    candidates.sort((a, b) => b.score - a.score || b.n - a.n);
    return candidates[0].n;
  }

  async function waitForSearchResults(timeoutMs = 45000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const total = readSearchResultCount();
      const cards = collectTitleLinks();
      // Wait until real card titles exist (skeletons have no data-id title).
      if (cards.length > 0) {
        return { total: total != null ? total : null, cards };
      }
      await sleep(800);
    }
    return {
      total: readSearchResultCount(),
      cards: collectTitleLinks(),
    };
  }

  function elText(el) {
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  }

  function isPageChromeText(t) {
    const s = (t || "").trim().toLowerCase();
    return (
      s === "find freelancers" ||
      s === "find the ideal freelancer" ||
      s === "freelancer finden" ||
      s.startsWith("find the ideal") ||
      s === "reset all" ||
      s === "saved searches" ||
      s === "add to watchlist" ||
      s === "show contact details" ||
      /^€?\d/.test(s)
    );
  }

  function isSkillOrFilterText(t) {
    const s = (t || "").trim();
    if (!s) return true;
    if (isPageChromeText(s)) return true;
    if (/\(programming language\)|\(software\)|\(framework\)|\(library\)|\(tool\)/i.test(s)) {
      return true;
    }
    if (/^(python|java|javascript|typescript|react|angular|vue|node\.?js|sql|html|css|php|ruby|go|rust|c\+\+|c#)$/i.test(s)) {
      return true;
    }
    return false;
  }

  /** FM card headlines use | or ▪ (U+25AA) between role segments. */
  function hasTitleSeparator(s) {
    return /[|■▪▫●•]/.test(s || "");
  }

  function isJobTitleText(t) {
    const s = (t || "").trim();
    if (s.length < 12) return false;
    if (isSkillOrFilterText(s)) return false;
    if (isPageChromeText(s)) return false;
    if (/^(only remote|available|watchlist|contact|add to watchlist)$/i.test(s)) {
      return false;
    }
    if (/^[A-Za-z.\- ]+,\s*[A-Za-z.\- ]+$/.test(s) && s.length < 45 && !hasTitleSeparator(s)) {
      return false;
    }
    if (hasTitleSeparator(s) && s.length >= 16) return true;
    if (
      /\b(senior|lead|engineer|developer|architect|consultant|manager|designer|devops|backend|frontend|scientist|analyst|mlops|full[\s-]?stack|software)\b/i.test(
        s
      )
    ) {
      return true;
    }
    if (s.length >= 36 && /\s/.test(s) && !/^find\b/i.test(s)) return true;
    return false;
  }

  function isSkillOrFilterLink(a) {
    if (!a || a.tagName !== "A") return false;
    const href = (a.getAttribute("href") || "").toLowerCase();
    const t = elText(a);
    if (isSkillOrFilterText(t) || isPageChromeText(t)) return true;
    if (/\/(skill|keyword|tag|technologie|technolog)/i.test(href)) return true;
    if (/[?&](query|keywords|skills)=/.test(href) && !/[?&]id=\d+/i.test(href) && !hasTitleSeparator(t)) {
      return true;
    }
    // City links like /freelancer/arges
    if (/data-id=["']freelancer-card-city["']/.test(a.outerHTML || "")) return true;
    if (a.getAttribute("data-id") === "freelancer-card-city") return true;
    return false;
  }

  function profileIdFromEl(el) {
    if (!el) return null;
    const scope =
      el.closest?.(
        ".freelancer-container, .freelancer-card, article, li, [class*='card'], [class*='Card'], section"
      ) || el;
    const hrefs = [
      el.getAttribute?.("href"),
      ...qsa("a[href]", scope).map((a) => a.getAttribute("href")),
    ].filter(Boolean);
    for (const href of hrefs) {
      try {
        const u = new URL(href, location.href);
        const id = u.searchParams.get("id");
        if (id && /^\d+$/.test(id)) return id;
      } catch (_e) {
        /* ignore */
      }
      const m = String(href).match(/[?&]id=(\d+)/i);
      if (m) return m[1];
    }
    for (const node of [scope, el, ...qsa("[data-profile-id], [data-id]", scope)]) {
      for (const attr of ["data-profile-id", "data-freelancer-id", "data-id"]) {
        const v = node.getAttribute?.(attr);
        if (v && /^\d+$/.test(v)) return v;
      }
    }
    return null;
  }

  /**
   * Official FM SERP card titles:
   * <a role="button" data-id="freelancer-card-title" class="title" href="/profile/...">
   */
  function collectTitleEntries() {
    const out = [];
    const seen = new Set();
    const titles = qsa(
      'a[data-id="freelancer-card-title"], [data-id="freelancer-card-title"]'
    );
    for (const el of titles) {
      if (!visible(el)) continue;
      const text = elText(el);
      if (!text || isPageChromeText(text)) continue;
      const card = el.closest(
        ".freelancer-container, .freelancer-card, .card, article, li"
      );
      const profileId = profileIdFromEl(el) || (card ? profileIdFromEl(card) : null);
      const key = (profileId || el.getAttribute("href") || text.slice(0, 120)).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ el, text, profileId, card: card || null });
    }
    if (out.length) return out;

    // Fallback if FM renames data-id: .freelancer-container .title
    for (const el of qsa(".freelancer-container a.title, .freelancer-card a.title, a.title")) {
      if (!visible(el)) continue;
      if (el.getAttribute("data-id") === "freelancer-card-city") continue;
      const text = elText(el);
      if (!isJobTitleText(text)) continue;
      if (isSkillOrFilterLink(el)) continue;
      const card = el.closest(".freelancer-container, .freelancer-card, .card");
      const profileId = profileIdFromEl(el);
      const key = (profileId || el.getAttribute("href") || text.slice(0, 120)).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ el, text, profileId, card: card || null });
    }
    return out;
  }

  function collectTitleLinks() {
    return collectTitleEntries().map((e) => e.el);
  }

  function collectProfileCards() {
    return collectTitleLinks();
  }

  /** Visible profile modal / panel root when present. */
  function profileModalRoot() {
    return (
      firstVisible(["[role='dialog']", ".modal", "[class*='Modal']"]) || null
    );
  }

  function isContactLabel(text) {
    const t = (text || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!t) return false;
    if (t === "contact" || t === "kontaktieren" || t === "kontakt") return true;
    // Short labels only — avoid "show contact details" / long sentences.
    if (t.length <= 22 && /^(contact|kontaktieren|kontakt)\b/.test(t)) {
      return true;
    }
    return false;
  }

  /** Prefer Contact/Kontaktieren inside the open modal. */
  function findContactButton(root = null) {
    const scope = root || profileModalRoot() || document;
    const nodes = qsa(
      "button, a, [role='button'], [data-id*='contact'], [data-testid*='contact']",
      scope
    );
    const matches = nodes.filter((el) => {
      if (!visible(el)) return false;
      const label = `${el.getAttribute("aria-label") || ""} ${
        el.getAttribute("title") || ""
      }`;
      return isContactLabel(elText(el)) || isContactLabel(label);
    });
    // Prefer controls inside a dialog when searching the whole document.
    if (!root && matches.length > 1) {
      const inModal = matches.find((el) =>
        el.closest?.("[role='dialog'], .modal, [class*='Modal']")
      );
      if (inModal) return inModal;
    }
    return matches[0] || null;
  }

  /** Modal open: URL has &id=… (FM pattern) or dialog with Contact. */
  function profilePanelOpen() {
    if (/[?&]id=\d+/i.test(location.search) && findContactButton()) return true;
    if (/[?&]id=\d+/i.test(location.search) && profileModalRoot()) return true;
    const root = profileModalRoot();
    if (!root) return false;
    return Boolean(findContactButton(root));
  }

  async function waitForContactButton(timeoutMs = 12000) {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) {
      const btn = findContactButton();
      if (btn) return btn;
      await sleep(300);
    }
    return null;
  }

  async function openCardProfile(entry, keyword) {
    const before = location.href;
    // Prefer clicking the official card title — FM opens the SERP modal.
    entry.el.scrollIntoView({ block: "center", behavior: "instant" });
    entry.el.click();
    for (let i = 0; i < 25; i++) {
      await sleep(400);
      if (profilePanelOpen() || findContactButton()) break;
    }
    if (await waitForContactButton(8000)) return true;
    if (profilePanelOpen()) return true;

    // Fallback: navigate with &id= when we know the numeric id
    if (entry.profileId) {
      const u = new URL(location.href);
      if (keyword) u.searchParams.set("query", keyword);
      u.searchParams.set("id", String(entry.profileId));
      location.assign(u.toString());
      await sleep(2500);
      if (await waitForContactButton(10000)) return true;
      if (profilePanelOpen()) return true;
    }
    if (location.href !== before && !/[?&]id=\d+/i.test(location.search)) {
      history.back();
      await sleep(1500);
    }
    return Boolean(await waitForContactButton(4000)) || profilePanelOpen();
  }

  async function touchCampaignAlive() {
    try {
      await chrome.storage.local.set({ campaignAliveAt: Date.now() });
    } catch (_e) {
      /* ignore */
    }
  }

  function isUiChromeName(text) {
    const s = (text || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!s) return true;
    if (
      /only\s+enterprise|enterprise\s+members?|only\s+remote|premium\s+member|show\s+contact|add\s+to\s+watchlist|add\s+note|find\s+freelancers|^verified$|^watchlist$|^contact$|^kontakt|^add\s+note$|^note$/.test(
        s
      )
    ) {
      return true;
    }
    // Action / chrome labels (buttons, tabs)
    if (
      /^(add|edit|show|hide|save|cancel|close|next|back|share|copy|download|upload|delete|remove|open|view|send|reply|message|note|notes|watchlist|contact|kontakt)(\s|$)/.test(
        s
      )
    ) {
      return true;
    }
    // Single chrome tokens / short UI labels
    if (
      /^(only|remote|available|verified|premium|contact|watchlist|full|senior|lead|find|the|freelancer|profile|members?|enterprise|note|notes)(\s|$)/.test(
        s
      ) &&
      s.split(/\s+/).length <= 4
    ) {
      const parts = s.split(/\s+/);
      if (
        parts.every((p) =>
          /^(only|remote|available|verified|premium|contact|watchlist|full|senior|lead|find|the|freelancer|profile|members?|enterprise|note|notes|add)$/i.test(
            p
          )
        )
      ) {
        return true;
      }
      if (/^only\b/.test(s) || /^enterprise\b/.test(s) || /\bmembers?\b/.test(s)) {
        return true;
      }
    }
    return false;
  }

  function isEnterprisePaywallVisible(root = null) {
    const scope = root || profileModalRoot() || document.body;
    const text = (scope.innerText || "").toLowerCase();
    return (
      /only\s+enterprise\s+members/.test(text) ||
      /upgrade\s+to\s+enterprise/.test(text) ||
      /enterprise\s+members?\s+can\s+contact/.test(text)
    );
  }

  function profileIdFromUrl() {
    const m = String(location.search || "").match(/[?&]id=(\d+)/i);
    return m ? m[1] : null;
  }

  function looksLikePersonName(text) {
    const s = (text || "").replace(/\s+/g, " ").trim();
    if (!s || s.length > 60) return false;
    if (hasTitleSeparator(s)) return false;
    if (isUiChromeName(s)) return false;
    if (
      /\b(senior|lead|engineer|developer|architect|consultant|manager|designer|devops|backend|frontend|full[\s-]?stack|scientist|analyst|software|python|java|react|fastapi|django|next\.?js|enterprise|members?|note|notes)\b/i.test(
        s
      )
    ) {
      return false;
    }
    if (
      /only\s+remote|^only\b|^remote\b|available|verified|premium|watchlist|contact|^add\b/i.test(
        s
      )
    ) {
      return false;
    }
    if (/\d|[/\\|@]/.test(s)) return false;
    const parts = s.split(/\s+/);
    if (parts.length < 2 || parts.length > 4) return false;
    if (
      parts.some((p) =>
        /^(only|remote|available|full|senior|lead|the|find|enterprise|members?|add|note|notes|edit|show|hide)$/i.test(
          p
        )
      )
    ) {
      return false;
    }
    // Every token must look like a name part (capitalized), not "Add note"
    if (!parts.every((p) => /^[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ'’-]+$/.test(p) || /^[A-ZÀ-ÖØ-Ý]\.$/.test(p))) {
      return false;
    }
    return true;
  }

  function looksLikeJobTitle(text) {
    const s = (text || "").trim();
    if (!s || s.length < 8 || looksLikePersonName(s)) return false;
    if (hasTitleSeparator(s) && s.length >= 12) return true;
    return /\b(senior|lead|engineer|developer|architect|consultant|manager|designer|devops|backend|frontend|full[\s-]?stack|scientist|analyst|software|python|java|react|fastapi|django|next\.?js|node|cloud|data|mobile)\b/i.test(
      s
    );
  }

  function looksLikeLocation(text) {
    const s = (text || "").trim();
    if (!s || looksLikeJobTitle(s) || looksLikePersonName(s)) return false;
    if (
      /\b(pakistan|india|germany|hungary|spain|france|italy|remote|united|uk|cyprus|poland|lahore|berlin|london|munich|karachi|islamabad)\b/i.test(
        s
      )
    ) {
      return true;
    }
    return /^[A-Za-z.\- ]+,\s*[A-Za-z.\- ]+$/.test(s) && s.length < 50;
  }

  function readOpenProfile() {
    const root = profileModalRoot() || document.body;
    const lines = (root.innerText || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    // Prefer a real person name over a job-title heading / paywall chrome.
    let name = "";
    const nameCandidateOk = (t) => {
      if (!t || t.length < 2) return false;
      if (isUiChromeName(t) || looksLikeJobTitle(t) || looksLikeLocation(t)) return false;
      // Strict: real person names only (no loose title-case fallback for UI labels)
      return looksLikePersonName(t);
    };
    for (const sel of [
      "[data-testid='freelancer-name']",
      "h1",
      "h2",
    ]) {
      for (const el of qsa(sel, root)) {
        const t = elText(el).split("\n")[0].trim();
        if (!nameCandidateOk(t)) continue;
        name = t;
        break;
      }
      if (name) break;
    }
    if (!name) {
      for (const line of lines.slice(0, 12)) {
        if (nameCandidateOk(line)) {
          name = line;
          break;
        }
      }
    }
    // Last resort: first heading that looks like a real person name only
    if (!name) {
      for (const sel of ["h1", "h2"]) {
        const el = root.querySelector(sel);
        if (!el) continue;
        const t = elText(el).split("\n")[0].trim();
        if (nameCandidateOk(t) && t.length < 50) {
          name = t;
          break;
        }
      }
    }
    if (name && isUiChromeName(name)) name = "";


    let title = "";
    let location = "";
    const after = [];
    if (name) {
      const idx = lines.findIndex((l) => l.toLowerCase() === name.toLowerCase());
      after.push(...(idx >= 0 ? lines.slice(idx + 1, idx + 16) : lines.slice(0, 16)));
    } else {
      after.push(...lines.slice(0, 16));
    }
    // Also consider heading text that looks like a role
    for (const sel of ["h1", "h2", "h3", "[class*='title']", "[class*='headline']"]) {
      for (const el of qsa(sel, root)) {
        const t = elText(el).split("\n")[0].trim();
        if (t && looksLikeJobTitle(t) && t.toLowerCase() !== name.toLowerCase()) {
          after.unshift(t);
        }
      }
    }

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
      ) {
        continue;
      }
      if (/€\s*\d|\/\s*h|%\s*available|updated/i.test(cand)) continue;
      if (name && cand.toLowerCase() === name.toLowerCase()) continue;
      if (looksLikePersonName(cand)) continue;
      if (!title && looksLikeJobTitle(cand)) {
        title = cand;
        continue;
      }
      if (!title && cand.length > 12 && !looksLikeLocation(cand)) {
        // Accept long headlines with separators even if role hint is weak
        if (hasTitleSeparator(cand)) {
          title = cand;
          continue;
        }
      }
      if (title && !location && looksLikeLocation(cand)) {
        location = cand;
        break;
      }
    }

    // Skills / tags from the open profile modal
    const skillBits = [];
    const skillNodes = qsa(
      "[class*='skill'], [class*='Skill'], [class*='tag'], [class*='Tag'], [data-id*='skill'], a[href*='skill']",
      root
    );
    for (const el of skillNodes) {
      const t = elText(el);
      if (!t || t.length < 2 || t.length > 48) continue;
      if (isPageChromeText(t) || /contact|watchlist|verified|premium/i.test(t)) continue;
      if (looksLikePersonName(t) || looksLikeLocation(t)) continue;
      if (!skillBits.includes(t)) skillBits.push(t);
      if (skillBits.length >= 16) break;
    }
    // Also pull tech tokens from lines if DOM tags are empty
    if (!skillBits.length) {
      const techRe =
        /\b(FastAPI|Django|Flask|Next\.?js|React|Angular|Vue|Node\.?js|TypeScript|Python|PostgreSQL|Stripe|AWS|Docker|Kubernetes|GraphQL|MongoDB|Redis|Spring|Laravel|Rails|Java|Go|Rust)\b/gi;
      for (const line of lines) {
        let m;
        const re = new RegExp(techRe.source, "gi");
        while ((m = re.exec(line))) {
          if (!skillBits.includes(m[0])) skillBits.push(m[0]);
          if (skillBits.length >= 8) break;
        }
        if (skillBits.length >= 8) break;
      }
    }
    const skills = skillBits.join(", ");

    const skipLine = (l) => {
      const low = (l || "").toLowerCase();
      return (
        !l ||
        low === "contact" ||
        low === "watchlist" ||
        low === "verified" ||
        low === "premium member" ||
        low === "show contact details" ||
        /^€\s*\d/.test(l) ||
        /%\s*available|updated\s+\d/i.test(l)
      );
    };
    const experienceLines = lines.filter((l) => !skipLine(l)).slice(0, 40);
    let experience = experienceLines.join("\n").slice(0, 1500);
    const aboutIdx = lines.findIndex((l) =>
      /^(about|experience|profile|skills|overview|projects|zusammenfassung|erfahrung)\b/i.test(
        l
      )
    );
    if (aboutIdx >= 0) {
      experience = lines
        .slice(aboutIdx, aboutIdx + 30)
        .filter((l) => !skipLine(l))
        .join("\n")
        .slice(0, 1500);
    }

    const href = window.location.href;
    const keyMatch = href.match(/\/freelancer\/([^/?#]+)/);
    const urlId = profileIdFromUrl();
    const profileKey =
      (urlId ? `id-${urlId}` : null) ||
      keyMatch?.[1] ||
      (name && !isUiChromeName(name) ? name : null) ||
      href;
    return {
      name: name && !isUiChromeName(name) ? name : "",
      title,
      location,
      skills,
      experience,
      profileKey,
      enterpriseLocked: isEnterprisePaywallVisible(root),
    };
  }

  async function clickContact() {
    // Always target the Contact button on the displayed profile modal.
    let target = findContactButton(profileModalRoot()) || findContactButton();
    if (!target) {
      target = await waitForContactButton(10000);
    }
    if (!target) return false;
    target.scrollIntoView({ block: "center", behavior: "instant" });
    await sleep(200);
    target.click();
    return true;
  }

  async function fillForm({ subject, body }) {
    const root =
      firstVisible(["[role='dialog']", ".modal", "[class*='Modal']"]) || document;
    const subjectInput = firstVisible(
      [
        "input[placeholder*='Subject' i]",
        "input[name*='subject' i]",
        "input[type='text']",
      ],
      root
    );
    const textarea = firstVisible(["textarea"], root);
    if (!subjectInput || !textarea) return false;
    setNativeValue(subjectInput, subject);
    await sleep(400);
    setNativeValue(textarea, body);
    return true;
  }

  async function clickSend() {
    const root =
      firstVisible(["[role='dialog']", ".modal", "[class*='Modal']"]) || document;
    const btn = qsa("button, [role='button'], [type='submit']", root).find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return (
        visible(el) &&
        (t === "send message" || t === "nachricht senden" || t === "send")
      );
    });
    if (!btn) {
      await emitLog("Send message button not found.", "warn");
      return false;
    }
    // Still try click even if aria-disabled — FM may allow send without project
    btn.click();
    await sleep(1500);
    return true;
  }

  function isDryRun(settings) {
    return settings?.dryRun === true || settings?.dryRun === "true" || settings?.dry_run === true;
  }

  /** Dismiss contact form / overlays so Next or list open can work. */
  async function dismissContactForm() {
    for (let i = 0; i < 3; i++) {
      const formOpen = Boolean(
        firstVisible(["[role='dialog'] textarea", ".modal textarea", "textarea"])
      );
      if (!formOpen && profilePanelOpen()) break;
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
      );
      await sleep(400);
    }
    // If still on a form-only dialog, use close control
    if (firstVisible(["[role='dialog'] textarea", "textarea"])) {
      await closeModal();
    }
  }

  /** Wait until the open modal shows a real person name (after Next / open). */
  async function waitForProfileName(timeoutMs = 8000) {
    const end = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < end) {
      last = readOpenProfile();
      if (last.enterpriseLocked) return last;
      if (last.name && looksLikePersonName(last.name) && !isUiChromeName(last.name)) {
        return last;
      }
      await sleep(300);
    }
    return last || readOpenProfile();
  }

  /** Bottom-right next profile control (→) after a DM. */
  async function clickNextProfile() {
    const beforeId = profileIdFromUrl();
    const beforeInfo = readOpenProfile();
    const beforeKey = (beforeInfo.profileKey || "").toString();
    const beforeName = (beforeInfo.name || "").toString();
    const candidates = qsa("button, a, [role='button'], [aria-label]").filter(
      (el) => {
        if (!visible(el)) return false;
        const label = `${el.getAttribute("aria-label") || ""} ${el.getAttribute("title") || ""}`.toLowerCase();
        const t = (el.textContent || "").trim();
        if (/next|weiter|following|nächste/i.test(label)) return true;
        if (t === "→" || t === "➜" || t === "›" || t === ">" || t === "»") return true;
        if (/^→|➜|›$/.test(t)) return true;
        if (/next/i.test(label)) return true;
        return false;
      }
    );
    let best = null;
    let bestScore = -1;
    for (const el of candidates) {
      const r = el.getBoundingClientRect();
      const score = r.left + r.top * 0.25;
      if (score > bestScore) {
        bestScore = score;
        best = el;
      }
    }
    if (!best && candidates[0]) best = candidates[0];
    if (!best) return false;
    best.click();
    // Wait until the SERP modal actually changes profile (&id=, key, or name).
    for (let i = 0; i < 32; i++) {
      await sleep(300);
      const afterId = profileIdFromUrl();
      if (beforeId && afterId && afterId !== beforeId) return true;
      const after = readOpenProfile();
      const afterKey = (after.profileKey || "").toString();
      if (afterKey && beforeKey && afterKey !== beforeKey) return true;
      const afterName = (after.name || "").toString();
      if (
        afterName &&
        beforeName &&
        afterName !== beforeName &&
        looksLikePersonName(afterName)
      ) {
        return true;
      }
      // id appeared when we had none
      if (!beforeId && afterId) return true;
    }
    return "unchanged";
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

  /**
   * Contact → fill → send (or dry-run). Project select is skipped — FM allows send without it.
   * No per-step waits; only the configured DM interval runs between successful sends.
   */
  async function runContactAndSend(settings) {
    const T = globalThis.FMOutreach;
    const dry = isDryRun(settings);
    // After Next / open, modal content can lag — wait for a real name.
    let info = await waitForProfileName(8000);
    if (info.enterpriseLocked) {
      return { ok: false, reason: "enterprise_paywall", info };
    }
    if (!info.name || isUiChromeName(info.name) || !looksLikePersonName(info.name)) {
      return {
        ok: false,
        reason: "no_profile_name",
        info: { ...info, name: info.name || "" },
      };
    }

    if (
      (info.profileKey &&
        (await T.wasContactedByKey?.(settings, info.profileKey))) ||
      (await T.wasContactedByName(settings, info.name))
    ) {
      return { ok: false, reason: "already_contacted", info };
    }

    if (!(await pauseStep("before Contact"))) {
      return { ok: false, reason: "stopped", info };
    }
    await emitLog(`Clicking Contact on modal for ${info.name}…`);
    const clicked = await clickContact();
    if (!clicked) return { ok: false, reason: "contact_button_missing", info };
    await emitLog("Contact clicked — waiting for form…");

    // Wait for form to appear
    for (let i = 0; i < 20; i++) {
      if (firstVisible(["textarea", "[role='dialog'] textarea"])) break;
      await sleep(400);
    }
    if (!firstVisible(["textarea", "[role='dialog'] textarea"])) {
      return { ok: false, reason: "contact_form_missing", info };
    }

    if (!(await pauseStep("before fill form"))) {
      return { ok: false, reason: "stopped", info };
    }

    // DeepSeek rewrites a unique DM from the shared contact-form + profile.
    await emitLog(
      `Personalizing unique DM for ${info.name}` +
        (info.title ? ` · ${info.title.slice(0, 60)}` : "") +
        "…"
    );
    let personalized = await T.personalizeOutreach(settings, info);
    const looksTruncated = (b) => {
      const t = String(b || "").trim();
      if (t.length < 160) return true;
      if (/\bif you\s*$/i.test(t) || /\bif you'?re\s*$/i.test(t)) return true;
      if (/\blet me know\s*$/i.test(t) || /\blooking forward\s*$/i.test(t)) return true;
      if (/,\s*$/.test(t) || /[—–]\s*$/.test(t)) return true;
      if (!/[.!?]\s*$/.test(t) && !/(regards|cheers|sincerely|thanks)\b/i.test(t)) {
        return true;
      }
      return false;
    };
    if (
      /\[\s*[^\]]+\s*\]/.test(personalized.body || "") ||
      /specific detail from their profile/i.test(personalized.body || "") ||
      /^Hello\s+(Only|Remote|Full|Senior)\b/i.test(personalized.body || "") ||
      looksTruncated(personalized.body)
    ) {
      await emitLog(
        "Generated body invalid (placeholder/bad greeting/truncated) — using local template.",
        "warn"
      );
      const safeDetail = T.pickDetail(
        info.title,
        info.location,
        info.skills,
        info.name,
        info.experience
      );
      personalized = {
        detail: safeDetail,
        subject: T.renderSubject(settings.subject, info.name),
        body: T.renderBody(
          settings.messageBody || settings.body,
          info.name,
          safeDetail
        ),
        source: "local_sanitized",
      };
    }
    const { detail, subject, body } = personalized;
    await emitLog(
      `Unique DM ready (${personalized.source}, ${String(body || "").length} chars)` +
        (detail ? ` · hook: ${String(detail).slice(0, 100)}` : "")
    );

    const filled = await fillForm({ subject, body });
    if (!filled) return { ok: false, reason: "fill_failed", info };

    if (dry) {
      await emitLog(`Dry-run filled form for ${info.name} (not sending)`);
      await dismissContactForm();
      return { ok: true, reason: "dry_run", info, subject, detail };
    }

    await emitLog(`Sending live DM to ${info.name}…`);
    if (!(await pauseStep("before Send message"))) {
      return { ok: false, reason: "stopped", info };
    }
    const sent = await clickSend();
    if (!sent) return { ok: false, reason: "send_failed", info };
    await emitLog(`Send clicked for ${info.name}`);

    try {
      await T.markContacted(settings, {
        name: info.name,
        profileKey: info.profileKey,
        projectName: settings.projectName || null,
      });
    } catch (_e) {
      /* ignore ledger errors */
    }
    await sleep(800);
    await dismissContactForm();
    return { ok: true, reason: "sent", info, subject, detail };
  }

  async function runOne(settings) {
    return runContactAndSend(settings);
  }

  async function waitBetweenDms(settings) {
    const waitMs = randomIntervalMs(
      settings.minIntervalSec,
      settings.maxIntervalSec
    );
    await emitLog(
      `Waiting ${Math.round(waitMs / 1000)}s before next DM (random ${settings.minIntervalSec}–${settings.maxIntervalSec}s)…`
    );
    const end = Date.now() + waitMs;
    while (Date.now() < end) {
      if (await shouldStop()) return false;
      await sleep(Math.min(1000, end - Date.now()));
    }
    return true;
  }

  function isQuickSkipReason(reason) {
    return (
      reason === "already_contacted" ||
      reason === "enterprise_paywall" ||
      reason === "no_profile_name" ||
      reason === "contact_button_missing" ||
      reason === "contact_form_missing"
    );
  }

  async function runCampaign(settings) {
    if (campaignRunning) {
      await emitLog("Campaign already running in this tab — ignoring duplicate start.");
      return { ok: false, reason: "already_running" };
    }

    // Cross-context lock (background revive + page resume)
    try {
      const now = Date.now();
      const { campaignLockUntil } = await chrome.storage.local.get({
        campaignLockUntil: 0,
      });
      if (campaignLockUntil > now) {
        await emitLog("Campaign lock held — skipping duplicate start.");
        return { ok: false, reason: "locked" };
      }
      await chrome.storage.local.set({ campaignLockUntil: now + 10000 });
    } catch (_e) {
      /* ignore */
    }

    campaignRunning = true;
    activeJobId = settings.jobId || null;
    const dry = isDryRun(settings);
    await chrome.storage.local.set({
      campaignStop: false,
      campaignAliveAt: Date.now(),
      pendingCampaign: {
        settings: { ...settings, dryRun: dry },
        phase: settings.resumeProfile
          ? "on_profile"
          : settings.skipSearch
            ? "await_results"
            : "search",
        startedAt: Date.now(),
      },
    });
    await touchCampaignAlive();

    await emitLog(
      `Campaign start · dry_run=${dry} · skipSearch=${Boolean(settings.skipSearch)} · keyword=${JSON.stringify(settings.keyword || "")}`
    );

    const stats = {
      attempted: 0,
      sent: 0,
      skipped: 0,
      failed: 0,
      profiles_found: 0,
      contacted_since_search: 0,
    };
    let finishStatus = "completed";
    let finishError = null;

    async function pushLiveStats(extra = {}) {
      Object.assign(stats, extra);
      if (!activeJobId) return;
      try {
        await chrome.runtime.sendMessage({
          type: "FM_JOB_STATS",
          jobId: activeJobId,
          stats: {
            profiles_found: stats.profiles_found,
            contacted_since_search: stats.contacted_since_search,
            attempted: stats.attempted,
            sent: stats.sent,
            skipped: stats.skipped,
            failed: stats.failed,
          },
        });
      } catch (_e) {
        /* ignore */
      }
    }

    try {
      if (!settings.skipSearch) {
        const okSearch = await runSearch(settings.keyword);
        if (!okSearch) {
          finishStatus = "failed";
          finishError = "search_failed";
          await finishJob(finishStatus, stats, finishError);
          return { ok: false, reason: "search_failed", stats };
        }
      } else if (settings.resumeProfile) {
        await emitLog("Resuming on open profile — Contact → fill → send…");
        await sleep(800);
      } else {
        await emitLog("Using search page prepared by extension background…");
        await sleep(800);
        const kw = (settings.keyword || "").trim();
        const q = (new URLSearchParams(location.search).get("query") || "").trim();
        // Do NOT clear chips / re-navigate when query already matches — that was
        // aborting the DM flow when the background revived mid-profile.
        if (kw && q.toLowerCase() !== kw.toLowerCase()) {
          await clearSkillChips();
          await ensureSearchApplied(kw);
          await sleep(1500);
        }
        await emitLog(
          `Search page ready. query=${new URLSearchParams(location.search).get("query") || "(none)"}`
        );
      }

      if (settings.resumeProfile) {
        // Jump straight into Contact on the already-open profile.
        const limit = Math.max(1, Number(settings.limit) || 10);
        const maxAttempts = Math.max(limit * 8, limit + 30);
        let fallThroughToList = false;
        while (stats.sent < limit && stats.attempted < maxAttempts) {
          if (await shouldStop()) {
            finishStatus = "stopped";
            break;
          }
          stats.attempted += 1;
          const result = await runContactAndSend(settings);
          if (result.ok) {
            stats.sent += 1;
            stats.contacted_since_search += 1;
            await pushLiveStats();
            await emitLog(
              `${isDryRun(settings) ? "Dry-run" : "Sent"} → ${result.info?.name || "?"} (${result.reason})`
            );
          } else if (result.reason === "already_contacted") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog(`Skip already contacted → ${result.info?.name || "?"}`);
          } else if (result.reason === "enterprise_paywall") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog("Skip Enterprise paywall profile (no contact access).", "warn");
          } else if (result.reason === "no_profile_name") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog(
              `Skip — no real freelancer name on modal (got "${(result.info?.name || "").slice(0, 40)}").`,
              "warn"
            );
          } else if (result.reason === "stopped") {
            finishStatus = "stopped";
            break;
          } else {
            stats.failed += 1;
            await pushLiveStats();
            await emitLog(
              `Failed → ${result.info?.name || "?"} (${result.reason})`,
              "warn"
            );
          }
          if (stats.sent >= limit) break;
          if (!(await pauseStep("before Next →"))) {
            finishStatus = "stopped";
            break;
          }
          const nextResult = await clickNextProfile();
          if (nextResult === true) {
            await emitLog("Clicked next (→) profile.");
            await waitForProfileName(6000);
            await chrome.storage.local.set({
              pendingCampaign: {
                settings: { ...settings, resumeProfile: true, skipSearch: true },
                phase: "on_profile",
                startedAt: Date.now(),
              },
            });
            if (!(await pauseStep("after Next →"))) {
              finishStatus = "stopped";
              break;
            }
          } else {
            await emitLog(
              nextResult === "unchanged"
                ? "Next (→) did not change profile — closing modal; continue from card titles."
                : "Next (→) not found — closing modal; continue from card titles.",
              "warn"
            );
            await closeModal();
            fallThroughToList = true;
            break;
          }
          // Full anti-spam wait only after a real send; skips move on quickly.
          if (result.ok) {
            if (!(await waitBetweenDms(settings))) {
              finishStatus = "stopped";
              break;
            }
          } else if (isQuickSkipReason(result.reason)) {
            await sleep(1200);
          } else if (!(await waitBetweenDms(settings))) {
            finishStatus = "stopped";
            break;
          }
        }
        if (!fallThroughToList || stats.sent >= limit || finishStatus === "stopped") {
          await emitLog(
            `Done. attempted=${stats.attempted} sent=${stats.sent} skipped=${stats.skipped} failed=${stats.failed}`
          );
          await finishJob(finishStatus, stats, finishError);
          return { ok: true, stats };
        }
        // Continue below with SERP card titles (same job / stats).
        settings = { ...settings, resumeProfile: false, skipSearch: true };
        await emitLog("Continuing campaign from search result card titles…");
      }

      const { total, cards: initialCards } = await waitForSearchResults();
      let cards = initialCards;
      const found =
        total != null && total > 0
          ? total
          : Math.max(cards.length, 0);
      await emitLog(
        total != null
          ? `Search shows ${total.toLocaleString()} freelancers (titles on page: ${cards.length}).`
          : `No total heading found; titles on page: ${cards.length}.`
      );
      if (!cards.length) {
        // One more pass after a short settle (lazy list render)
        await sleep(2000);
        cards = collectTitleLinks();
        await emitLog(`Retried title scan: ${cards.length} title(s).`);
      }
      await pushLiveStats({
        profiles_found: found,
        contacted_since_search: stats.contacted_since_search || 0,
      });

      if (!cards.length) {
        finishStatus = "failed";
        finishError = "no_profile_cards";
        await emitLog(
          "No clickable card titles found on the SERP — cannot open profile modal.",
          "error"
        );
        await finishJob(finishStatus, stats, finishError);
        return { ok: false, reason: "no_profile_cards", stats };
      }

      const limit = Math.max(1, Number(settings.limit) || 10);
      const maxAttempts = Math.max(limit * 8, limit + 30);
      let openedFirst = false;
      let titleIndex = 0;

      while (stats.sent < limit && stats.attempted < maxAttempts) {
        if (await shouldStop()) {
          await emitLog("Campaign stopped.");
          finishStatus = "stopped";
          break;
        }

        stats.attempted += 1;
        try {
          if (!openedFirst) {
            const entries = collectTitleEntries();
            await emitLog(
              `Card titles found: ${entries.length}` +
                (entries[0] ? ` · first="${entries[0].text.slice(0, 60)}"` : "")
            );
            if (titleIndex >= entries.length) {
              await emitLog("No more card titles on this page.");
              break;
            }
            const entry = entries[titleIndex];
            titleIndex += 1;
            entry.el.scrollIntoView({ block: "center", behavior: "instant" });
            if (!(await pauseStep("before opening title"))) {
              finishStatus = "stopped";
              break;
            }
            if (
              !entry.el.getAttribute?.("data-id")?.includes("freelancer-card-title") &&
              (isPageChromeText(entry.text) ||
                isSkillOrFilterText(entry.text) ||
                !isJobTitleText(entry.text))
            ) {
              await emitLog(`Skipping non-card title: ${entry.text.slice(0, 80)}`, "warn");
              stats.attempted -= 1;
              continue;
            }

            await emitLog(
              `Opening card title: ${entry.text.slice(0, 90)}` +
                (entry.profileId ? ` (id=${entry.profileId})` : "")
            );
            const opened = await openCardProfile(entry, settings.keyword || "");
            if (!opened) {
              stats.failed += 1;
              await emitLog(
                `Failed to open profile modal for: ${entry.text.slice(0, 80)}`,
                "warn"
              );
              continue;
            }
            await emitLog("Profile modal open — looking for Contact…");
            const contactReady = await waitForContactButton(10000);
            if (!contactReady) {
              stats.failed += 1;
              await emitLog(
                "Profile modal open but Contact button not found.",
                "warn"
              );
              await closeModal();
              continue;
            }
            await emitLog("Contact button visible on modal.");

            try {
              await chrome.storage.local.set({
                pendingCampaign: {
                  settings: {
                    ...settings,
                    resumeProfile: true,
                    skipSearch: true,
                  },
                  phase: "on_profile",
                  startedAt: Date.now(),
                },
                campaignAliveAt: Date.now(),
              });
            } catch (_e) {
              /* ignore */
            }

            if (!(await pauseStep("after opening profile"))) {
              finishStatus = "stopped";
              break;
            }
            openedFirst = true;
          }

          // 2–4) Click Contact on modal → fill → send  (from 2nd DM onward, start here)
          const result = await runContactAndSend(settings);
          if (result.ok) {
            stats.sent += 1;
            stats.contacted_since_search += 1;
            await pushLiveStats();
            await emitLog(
              `${isDryRun(settings) ? "Dry-run" : "Sent"} → ${result.info?.name || "?"} (${result.reason})`
            );
          } else if (result.reason === "already_contacted") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog(`Skip already contacted → ${result.info?.name || "?"}`);
          } else if (result.reason === "enterprise_paywall") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog("Skip Enterprise paywall profile (no contact access).", "warn");
          } else if (result.reason === "no_profile_name") {
            stats.skipped += 1;
            await pushLiveStats();
            await emitLog(
              `Skip — no real freelancer name on modal (got "${(result.info?.name || "").slice(0, 40)}").`,
              "warn"
            );
          } else if (result.reason === "stopped") {
            finishStatus = "stopped";
            break;
          } else {
            stats.failed += 1;
            await pushLiveStats();
            await emitLog(
              `Failed → ${result.info?.name || "?"} (${result.reason})`,
              "warn"
            );
          }

          if (stats.sent >= limit) break;
          if (await shouldStop()) {
            finishStatus = "stopped";
            break;
          }

          // 5) Click → to move to the next freelancer in the modal/viewer
          if (!(await pauseStep("before Next →"))) {
            finishStatus = "stopped";
            break;
          }
          const nextOk = await clickNextProfile();
          if (nextOk === true) {
            await emitLog("Clicked next (→) profile.");
            await waitForProfileName(6000);
            if (!(await pauseStep("after Next →"))) {
              finishStatus = "stopped";
              break;
            }
          } else {
            await emitLog(
              nextOk === "unchanged"
                ? "Next (→) did not change profile — closing modal; open next card title."
                : "Next (→) not found — closing modal; open next card title.",
              "warn"
            );
            await closeModal();
            openedFirst = false;
            if (!collectTitleLinks().length && /\/freelancer\/[^/?#]+/i.test(location.pathname)) {
              history.back();
              await sleep(2000);
            }
          }

          if (result.ok) {
            if (!(await waitBetweenDms(settings))) {
              finishStatus = "stopped";
              break;
            }
          } else if (isQuickSkipReason(result.reason)) {
            await sleep(1200);
          } else if (!(await waitBetweenDms(settings))) {
            finishStatus = "stopped";
            break;
          }
        } catch (err) {
          stats.failed += 1;
          await pushLiveStats();
          await emitLog(`Error on profile: ${err}`, "error");
          await closeModal();
          openedFirst = false;
        }
      }

      await emitLog(
        `Done. attempted=${stats.attempted} sent=${stats.sent} skipped=${stats.skipped} failed=${stats.failed} contacted_since_search=${stats.contacted_since_search}`
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
      try {
        await chrome.storage.local.remove(["pendingCampaign", "campaignAliveAt"]);
      } catch (_e) {
        /* ignore */
      }
    }
  }

  async function maybeResumePendingCampaign() {
    try {
      const store = await chrome.storage.local.get({
        pendingCampaign: null,
        campaignStop: false,
        activeJobId: null,
      });
      if (store.campaignStop || !store.pendingCampaign?.settings) return;
      if (!store.activeJobId && !store.pendingCampaign.settings.jobId) return;
      if (Date.now() - (store.pendingCampaign.startedAt || 0) > 45 * 60 * 1000) {
        await chrome.storage.local.remove("pendingCampaign");
        return;
      }
      if (!/freelancermap\.(com|de)/i.test(location.hostname)) return;
      if (!/\/freelancer/i.test(location.pathname + location.href)) return;
      if (campaignRunning) return;

      const phase = store.pendingCampaign.phase || "search";
      const onProfile =
        phase === "on_profile" &&
        (profilePanelOpen() || /\/freelancer\/[^/?#]+/i.test(location.pathname));
      // Only resume Contact when we actually have a profile panel / known on_profile phase.
      // Do not treat skill filter pages as profiles.
      if (onProfile && profilePanelOpen()) {
        const settings = {
          ...store.pendingCampaign.settings,
          skipSearch: true,
          resumeProfile: true,
          jobId: store.pendingCampaign.settings.jobId || store.activeJobId,
        };
        await emitLog("Resuming Contact/Send on open profile panel…");
        runCampaign(settings);
        return;
      }
      if (phase === "on_profile" && !profilePanelOpen()) {
        // Stale flag after wrong navigation — fall through to search resume
        await emitLog("on_profile set but no Contact panel — resuming search flow.");
      }
      if (phase !== "await_results" && phase !== "search" && phase !== "on_profile") return;

      const settings = {
        ...store.pendingCampaign.settings,
        skipSearch: true,
        resumeProfile: false,
        jobId: store.pendingCampaign.settings.jobId || store.activeJobId,
      };
      await emitLog("Page reloaded during search — resuming campaign.");
      runCampaign(settings);
    } catch (e) {
      console.warn("[FM Outreach] resume failed", e);
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
      runCampaign(msg.settings || {}).catch((e) =>
        console.error("[FM Outreach] campaign error", e)
      );
      return true;
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

  // If Find freelancers caused a full navigation, pick up again on the results page.
  maybeResumePendingCampaign();
})();
