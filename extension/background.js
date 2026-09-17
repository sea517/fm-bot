/* Background: outreach on /freelancer + inbox chat on /app/pobox/main in parallel. */

const DEFAULT_API_BASE_URL = "https://fm-bot.vercel.app";
const OUTREACH_URL = "https://www.freelancermap.com/freelancer";
const INBOX_URL = "https://www.freelancermap.com/app/pobox/main";

const JOB_POLL_MS = 12000;
const CHAT_POLL_MS = 45000;
const CHAT_POLL_BUSY_MS = 90000;

async function settings() {
  const s = await chrome.storage.sync.get({
    apiBaseUrl: DEFAULT_API_BASE_URL,
    botId: 1,
    botToken: "",
    enabled: true,
  });
  return {
    ...s,
    apiBaseUrl: (s.apiBaseUrl || DEFAULT_API_BASE_URL).replace(/\/$/, ""),
  };
}

async function clearPendingChatAlarms() {
  const all = await chrome.alarms.getAll();
  for (const a of all) {
    if (a.name && a.name.startsWith("fm_chat_send_")) {
      await chrome.alarms.clear(a.name);
    }
  }
  await chrome.storage.local.set({ pendingChatReplies: {} });
}

async function setLocalAutomationPaused(paused) {
  if (paused) {
    await disarmInboxChat("pause");
    return;
  }
  await chrome.storage.local.set({
    automationPaused: false,
    campaignStop: false,
  });
  console.info("automation pause cleared (inboxArmed unchanged)");
}

async function armInboxChat() {
  await chrome.storage.local.set({
    inboxArmed: true,
    automationPaused: false,
    campaignStop: false,
  });
  scheduleChatPoll();
  console.info("inbox chat armed");
}

async function disarmInboxChat(reason) {
  await chrome.storage.local.set({
    inboxArmed: false,
    automationPaused: true,
    campaignStop: true,
  });
  await clearPendingChatAlarms();
  try {
    await chrome.alarms.clear("fm_chat");
  } catch (_e) {
    /* ignore */
  }
  console.info("inbox chat disarmed:", reason || "");
}

async function readLocalPaused() {
  const local = await chrome.storage.local.get({
    automationPaused: true,
    inboxArmed: false,
  });
  // Not armed → treat as paused (fail closed).
  if (local.inboxArmed !== true) return true;
  return local.automationPaused !== false;
}

async function syncAutomationPausedFromServer() {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) return true;
  try {
    const botSettings = await api(`/api/bots/${s.botId}/settings`);
    if (typeof botSettings?.automation_paused === "boolean") {
      if (botSettings.automation_paused) {
        await disarmInboxChat("server pause");
        return true;
      }
      // Server resume does not arm inbox by itself — Start/claim does.
      await chrome.storage.local.set({ automationPaused: false });
      return readLocalPaused();
    }
    return readLocalPaused();
  } catch (e) {
    console.warn("settings sync failed — keeping local pause state", e);
    return readLocalPaused();
  }
}

async function isAutomationPaused() {
  if (await readLocalPaused()) return true;
  return syncAutomationPausedFromServer();
}

async function api(path, { method = "GET", body } = {}) {
  const s = await settings();
  if (!s.apiBaseUrl || !s.botToken) throw new Error("Configure API URL + bot token");
  const res = await fetch(`${s.apiBaseUrl.replace(/\/$/, "")}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${s.botToken}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    throw new Error(data?.detail || data?.raw || res.statusText);
  }
  return data;
}

async function heartbeat(status = "online", last_error = null) {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) return;
  try {
    await api(`/api/bots/${s.botId}/heartbeat`, {
      method: "POST",
      body: { status, last_error },
    });
  } catch (e) {
    console.warn("heartbeat failed", e);
  }
}

function isOutreachUrl(url) {
  return /freelancermap\.(com|de)\/freelancer(?!\/)/i.test(url || "") ||
    /freelancermap\.(com|de)\/freelancer(\?|$)/i.test(url || "");
}

function isInboxUrl(url) {
  return /\/app\/pobox/i.test(url || "") || /\/pobox/i.test(url || "");
}

async function findTabBy(pred) {
  const tabs = await chrome.tabs.query({
    url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
  });
  return tabs.find((t) => pred(t.url || "")) || null;
}

/** Keep a dedicated search/DM tab (never reuse the inbox tab). */
async function ensureOutreachTab({ activate = false } = {}) {
  const { outreachTabId } = await chrome.storage.local.get({ outreachTabId: null });
  if (outreachTabId) {
    try {
      const existing = await chrome.tabs.get(outreachTabId);
      const url = existing.url || "";
      if (
        existing &&
        /freelancermap\.(com|de)/i.test(url) &&
        !isInboxUrl(url)
      ) {
        if (activate) await chrome.tabs.update(existing.id, { active: true });
        return existing;
      }
    } catch (_e) {
      /* tab closed */
    }
  }

  let tab = await findTabBy(isOutreachUrl);
  if (!tab) {
    // Prefer any non-inbox freelancermap tab, else create.
    const tabs = await chrome.tabs.query({
      url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
    });
    tab = tabs.find((t) => !isInboxUrl(t.url || "") && !/\/profile\//i.test(t.url || "")) || null;
  }
  if (!tab) {
    await chrome.storage.local.set({ allowFmTabCreateUntil: Date.now() + 5000 });
    tab = await chrome.tabs.create({ url: OUTREACH_URL, active: activate });
  } else if (!isOutreachUrl(tab.url || "") && !/\/freelancer\//i.test(tab.url || "")) {
    await chrome.tabs.update(tab.id, { url: OUTREACH_URL, active: activate });
    await sleep(2000);
  } else if (activate) {
    await chrome.tabs.update(tab.id, { active: true });
  }
  await chrome.storage.local.set({ outreachTabId: tab.id });
  return tab;
}

/** Keep a dedicated inbox tab for assessment chat. */
async function ensureInboxTab({ activate = false } = {}) {
  const { inboxTabId } = await chrome.storage.local.get({ inboxTabId: null });
  if (inboxTabId) {
    try {
      const existing = await chrome.tabs.get(inboxTabId);
      if (existing && isInboxUrl(existing.url || "")) {
        if (activate) await chrome.tabs.update(existing.id, { active: true });
        return existing;
      }
    } catch (_e) {
      /* closed */
    }
  }

  let tab = await findTabBy(isInboxUrl);
  if (!tab) {
    await chrome.storage.local.set({ allowFmTabCreateUntil: Date.now() + 5000 });
    tab = await chrome.tabs.create({ url: INBOX_URL, active: activate });
  } else if (!/\/app\/pobox/i.test(tab.url || "")) {
    await chrome.tabs.update(tab.id, { url: INBOX_URL, active: activate });
    await sleep(2000);
  } else if (activate) {
    await chrome.tabs.update(tab.id, { active: true });
  }
  await chrome.storage.local.set({ inboxTabId: tab.id });
  return tab;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Card title links often open /profile in a new tab. During an active job,
 * close those stray tabs so Chrome does not flood.
 */
chrome.tabs.onCreated.addListener((tab) => {
  setTimeout(async () => {
    try {
      const store = await chrome.storage.local.get({
        activeJobId: null,
        campaignStop: false,
        outreachTabId: null,
        inboxTabId: null,
        allowFmTabCreateUntil: 0,
      });
      if (!store.activeJobId || store.campaignStop) return;
      if (Date.now() < Number(store.allowFmTabCreateUntil || 0)) return;

      let live;
      try {
        live = await chrome.tabs.get(tab.id);
      } catch (_e) {
        return;
      }
      const url = live.url || live.pendingUrl || "";
      if (!/freelancermap\.(com|de)/i.test(url) && url !== "" && url !== "about:blank") {
        return;
      }
      if (live.id === store.outreachTabId || live.id === store.inboxTabId) return;
      if (isInboxUrl(url)) return;

      // Wait briefly for URL to settle (new tabs often start blank)
      await sleep(600);
      try {
        live = await chrome.tabs.get(tab.id);
      } catch (_e) {
        return;
      }
      const settled = live.url || "";
      if (!/freelancermap\.(com|de)/i.test(settled)) return;
      if (live.id === store.outreachTabId || live.id === store.inboxTabId) return;
      if (isInboxUrl(settled)) return;

      const isStrayProfile =
        /\/profile\//i.test(settled) ||
        (/[?&]id=\d+/i.test(settled) && live.id !== store.outreachTabId);
      if (isStrayProfile || /\/freelancer/i.test(settled)) {
        console.warn("closing stray freelancermap tab", settled);
        await chrome.tabs.remove(live.id);
      }
    } catch (e) {
      console.warn("stray tab guard failed", e);
    }
  }, 200);
});

const CONTENT_SCRIPTS = [
  "shared/templates.js",
  "shared/supabase.js",
  "content/outreach.js",
  "content/chat.js",
];

async function sendToTab(tabId, payload) {
  try {
    return await chrome.tabs.sendMessage(tabId, payload);
  } catch (_e) {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: CONTENT_SCRIPTS,
    });
    return chrome.tabs.sendMessage(tabId, payload);
  }
}

async function reportEvent(botId, jobId, message, level = "info") {
  try {
    await api(`/api/bots/${botId}/jobs/${jobId}/events`, {
      method: "POST",
      body: { message, level },
    });
  } catch (e) {
    console.warn("event failed", e);
  }
}

function jobSearchUrl(keyword) {
  const u = new URL(OUTREACH_URL);
  if (keyword) u.searchParams.set("query", String(keyword).trim());
  return u.toString();
}

function isDryRunJob(job) {
  return job?.dry_run === true || job?.dry_run === "true";
}

async function pingTabCampaign(tabId) {
  try {
    const res = await sendToTab(tabId, { type: "FM_PING" });
    return Boolean(res?.campaignRunning);
  } catch (_e) {
    return false;
  }
}

/**
 * Start or revive outreach. Never tear down an in-progress profile/DM by
 * re-navigating to search while a campaign is alive.
 */
async function runClaimedJob(job, { revive = false } = {}) {
  const s = await settings();
  const dry = isDryRunJob(job);
  const searchUrl = jobSearchUrl(job.keyword);

  // If ANY freelancermap tab already runs a campaign, do not restart.
  const allTabs = await chrome.tabs.query({
    url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
  });
  for (const t of allTabs) {
    if (isInboxUrl(t.url || "")) continue;
    if (await pingTabCampaign(t.id)) {
      await heartbeat("busy");
      return;
    }
  }

  let tab = await ensureOutreachTab({ activate: true });
  const url = tab.url || "";
  const onProfile = /\/freelancer\/[^/?#]+/i.test(url);
  const onListing =
    /\/freelancer\/?(\?|$)/i.test(url) || /\/freelancer\?/i.test(url);
  let currentQ = "";
  try {
    currentQ = new URL(url).searchParams.get("query") || "";
  } catch (_e) {
    /* ignore */
  }
  const queryOk =
    currentQ.trim().toLowerCase() === String(job.keyword || "").trim().toLowerCase();

  // If we landed on a detail/skill URL with no live campaign, go back to search —
  // profile panels are usually modals on the SERP, not separate /freelancer/slug pages.
  if (revive && onProfile) {
    await reportEvent(
      s.botId,
      job.id,
      "Revive from detail URL — returning to search results (profile opens as panel on SERP)"
    );
    await chrome.tabs.update(tab.id, { url: searchUrl, active: true });
    await sleep(4500);
    try {
      tab = await chrome.tabs.get(tab.id);
    } catch (_e) {
      tab = await ensureOutreachTab({ activate: true });
    }
    if (await pingTabCampaign(tab.id)) {
      await heartbeat("busy");
      return;
    }
    await startCampaignInTab(tab, job, {
      dry,
      skipSearch: true,
      resumeProfile: false,
      revive: true,
    });
    return;
  }

  // Fresh start or revive on listing: only navigate when query missing.
  const needNav = !onListing || !queryOk;
  if (needNav && !onProfile) {
    await reportEvent(
      s.botId,
      job.id,
      `${revive ? "Revive" : "Start"}: opening search ${searchUrl}`
    );
    await chrome.tabs.update(tab.id, { url: searchUrl, active: true });
    await sleep(4500);
    try {
      tab = await chrome.tabs.get(tab.id);
    } catch (_e) {
      tab = await ensureOutreachTab({ activate: true });
    }
  }

  // Re-check after possible navigation
  if (await pingTabCampaign(tab.id)) {
    await heartbeat("busy");
    return;
  }

  await startCampaignInTab(tab, job, {
    dry,
    skipSearch: true,
    resumeProfile: false,
    revive,
  });
}

async function startCampaignInTab(tab, job, { dry, skipSearch, resumeProfile, revive }) {
  const s = await settings();
  const campaignSettings = {
    keyword: job.keyword,
    subject: job.subject || "",
    messageBody: job.message_body || "",
    minIntervalSec: Number(job.min_interval_sec) || 360,
    maxIntervalSec: Number(job.max_interval_sec) || 540,
    limit: Math.min(Number(job.max_freelancers) || 5, 40),
    dryRun: dry,
    apiBaseUrl: s.apiBaseUrl,
    botId: s.botId,
    botToken: s.botToken,
    jobId: job.id,
    skipSearch: Boolean(skipSearch),
    resumeProfile: Boolean(resumeProfile),
  };

  const patch = {
    campaignStop: false,
    activeJobId: job.id,
    activeBotId: s.botId,
    pendingCampaign: {
      settings: campaignSettings,
      phase: resumeProfile ? "on_profile" : "await_results",
      startedAt: Date.now(),
    },
  };
  // Arm inbox only on a fresh Start/claim — never on post-reboot revive.
  if (!revive) {
    patch.inboxArmed = true;
    patch.automationPaused = false;
  }
  await chrome.storage.local.set(patch);
  if (!revive) scheduleChatPoll();

  await heartbeat("busy");
  // Inbox chat is started by pollChat only when armed — do not open Postfach here.

  await reportEvent(
    s.botId,
    job.id,
    `${revive ? "Reviving" : "Starting"} campaign keyword=${JSON.stringify(job.keyword)} dry_run=${dry} resumeProfile=${Boolean(resumeProfile)}`
  );

  try {
    const result = await sendToTab(tab.id, {
      type: "FM_START_CAMPAIGN",
      settings: campaignSettings,
    });
    console.info("campaign start ack", result);
    await reportEvent(
      s.botId,
      job.id,
      `Content script ack: ${result?.reason || "ok"}`
    );
  } catch (e) {
    await reportEvent(
      s.botId,
      job.id,
      `Failed to start content script: ${e.message || e}`,
      "error"
    );
  }
}

/**
 * Inbox chat runs on its own tab and is never blocked by outreach.
 * Two Chrome tabs = DM campaign + chat at the same time.
 */
async function runFollowUpPass(tabId) {
  if (await isAutomationPaused()) return;
  const s = await settings();
  if (!s.enabled || !s.botToken) return;
  try {
    const data = await api(`/api/bots/${s.botId}/chat/follow-ups`);
    const items = data?.follow_ups || [];
    const item = items.find((x) => x?.applicant?.conversation_id && x?.reply);
    if (!item) return;
    const applicantId = item.applicant.id;
    const conversationId = String(item.applicant.conversation_id);
    console.info("follow-up due", applicantId, conversationId);
    // Schedule like normal replies (SPEC 2–10 min delay via alarm)
    await scheduleChatReply({
      conversation_id: conversationId,
      text: item.reply,
      send_after_sec: item.send_after_sec || 120,
      fingerprint: `followup:${applicantId}:${Date.now()}`,
      applicant_id: applicantId,
      is_follow_up: true,
    });
  } catch (e) {
    console.warn("follow-up pass failed", e);
  }
}

async function scheduleChatReply(payload) {
  const id = String(payload.conversation_id);
  const sec = Math.max(120, Math.min(600, Number(payload.send_after_sec) || 120));
  const key = `pendingChat_${id}`;
  const store = await chrome.storage.local.get({ pendingChatReplies: {} });
  const map = store.pendingChatReplies || {};
  if (map[id] && map[id].dueAt > Date.now() - 60000) {
    console.info("chat reply already pending", id);
    return { ok: true, deduped: true };
  }
  map[id] = {
    ...payload,
    conversation_id: id,
    send_after_sec: sec,
    dueAt: Date.now() + sec * 1000,
  };
  await chrome.storage.local.set({ pendingChatReplies: map });
  chrome.alarms.create(`fm_chat_send_${id}`, { when: map[id].dueAt });
  console.info("scheduled chat reply", id, "in", sec, "s");
  return { ok: true };
}

async function deliverScheduledChatReply(conversationId) {
  if (await isAutomationPaused()) {
    console.info("skip scheduled chat — automation paused");
    return;
  }
  const id = String(conversationId);
  const store = await chrome.storage.local.get({ pendingChatReplies: {} });
  const map = store.pendingChatReplies || {};
  const pending = map[id];
  if (!pending?.text) {
    console.warn("no pending chat reply for", id);
    return;
  }
  try {
    // Fresh list first — opening a thread hides rows; content-script reload kills handlers.
    let tab = await ensureInboxTab({ activate: false });
    await chrome.tabs.update(tab.id, { url: INBOX_URL, active: false });
    await sleep(3200);
    tab = await chrome.tabs.get(tab.id);
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: CONTENT_SCRIPTS,
    });
    await sleep(800);
    const sent = await sendToTab(tab.id, {
      type: "FM_CHAT_SEND",
      conversation_id: id,
      text: pending.text,
      send_after_sec: 0,
    });
    if (sent?.ok) {
      console.info("delivered scheduled chat reply", id);
      if (pending.applicant_id) {
        try {
          const s = await settings();
          await api(`/api/bots/${s.botId}/chat/delivered`, {
            method: "POST",
            body: {
              applicant_id: pending.applicant_id,
              conversation_id: id,
              body: pending.text,
            },
          });
        } catch (e) {
          console.warn("chat/delivered failed", e);
        }
      }
      const fpsStore = await chrome.storage.local.get({
        chatFingerprints: {},
        chatLastOutbound: {},
      });
      const fps = fpsStore.chatFingerprints || {};
      const outs = fpsStore.chatLastOutbound || {};
      if (pending.fingerprint) fps[id] = pending.fingerprint;
      outs[id] = String(pending.text || "").trim().slice(0, 240);
      await chrome.storage.local.set({
        chatFingerprints: fps,
        chatLastOutbound: outs,
      });
      if (pending.is_follow_up && pending.applicant_id) {
        const s = await settings();
        await api(`/api/bots/${s.botId}/chat/follow-ups/${pending.applicant_id}`, {
          method: "POST",
          body: { delivered: true },
        });
      }
      delete map[id];
      await chrome.storage.local.set({ pendingChatReplies: map });
    } else {
      const tries = (pending.tries || 0) + 1;
      map[id] = { ...pending, tries };
      await chrome.storage.local.set({ pendingChatReplies: map });
      console.warn("scheduled chat send failed", id, sent, "try", tries);
      if (tries < 6) {
        chrome.alarms.create(`fm_chat_send_${id}`, {
          when: Date.now() + 60000,
        });
      }
    }
  } catch (e) {
    console.warn("deliverScheduledChatReply failed", e);
    chrome.alarms.create(`fm_chat_send_${id}`, { when: Date.now() + 60000 });
  }
}

async function runInboxChatPass() {
  const s = await settings();
  if (!s.enabled || !s.botToken) {
    console.info("inbox chat skip: disabled or missing bot token");
    return;
  }
  if (await isAutomationPaused()) {
    console.info("inbox chat skip: automation paused (dashboard Stop)");
    return;
  }
  const store = await chrome.storage.local.get({
    chatPassBusy: false,
    chatPassBusyAt: 0,
  });
  const busyAge = Date.now() - (store.chatPassBusyAt || 0);
  // Pass is short now (schedule only); 3 min stuck timeout
  if (store.chatPassBusy && busyAge > 3 * 60 * 1000) {
    console.warn("inbox chat clearing stale chatPassBusy", busyAge, "ms");
    await chrome.storage.local.set({ chatPassBusy: false, chatPassBusyAt: 0 });
  } else if (store.chatPassBusy) {
    console.info("inbox chat skip: busy");
    return;
  }

  try {
    await chrome.storage.local.set({
      chatPassBusy: true,
      chatPassBusyAt: Date.now(),
    });
    const tab = await ensureInboxTab({ activate: false });
    if (!/\/app\/pobox/i.test(tab.url || "")) {
      await chrome.tabs.update(tab.id, { url: INBOX_URL, active: false });
      await sleep(2500);
    }
    const result = await sendToTab(tab.id, { type: "FM_CHAT_PASS" });
    console.info("inbox chat pass", {
      ok: result?.ok,
      reason: result?.reason,
      count: result?.results?.length || 0,
      results: result?.results || [],
    });
    const scheduled = (result?.results || []).some(
      (r) => r?.reason === "scheduled" || r?.delayMs
    );
    if (!scheduled) {
      await runFollowUpPass(tab.id);
    }
  } catch (e) {
    console.warn("inbox chat pass failed", e);
  } finally {
    await chrome.storage.local.set({ chatPassBusy: false, chatPassBusyAt: 0 });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "FM_AM_I_OUTREACH") {
    chrome.storage.local
      .get({ outreachTabId: null })
      .then((store) => {
        const tabId = sender.tab?.id;
        sendResponse({
          ok: Boolean(
            tabId && store.outreachTabId && tabId === store.outreachTabId
          ),
          tabId: tabId || null,
          outreachTabId: store.outreachTabId || null,
        });
      });
    return true;
  }
  if (msg?.type === "FM_JOB_EVENT") {
    settings().then(async (s) => {
      if (msg.jobId) {
        await reportEvent(s.botId, msg.jobId, msg.text, msg.level || "info");
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "FM_JOB_STATS") {
    settings().then(async (s) => {
      try {
        if (msg.jobId) {
          await api(`/api/bots/${s.botId}/jobs/${msg.jobId}/stats`, {
            method: "POST",
            body: { stats: msg.stats || {} },
          });
        }
      } catch (e) {
        console.warn("stats update failed", e);
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "FM_JOB_FINISHED") {
    settings().then(async (s) => {
      try {
        await api(`/api/bots/${s.botId}/jobs/${msg.jobId}/finish`, {
          method: "POST",
          body: {
            status: msg.status || "completed",
            stats: msg.stats || {},
            error: msg.error || null,
          },
        });
        await heartbeat("online");
        await chrome.storage.local.set({
          activeJobId: null,
          pendingCampaign: null,
          campaignLockUntil: 0,
          campaignStop: true,
          campaignAliveAt: 0,
        });
      } catch (e) {
        console.error(e);
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "FM_CONTACT_UPSERT") {
    settings().then(async (s) => {
      try {
        await api(`/api/bots/${s.botId}/contacts`, {
          method: "POST",
          body: msg.contact,
        });
      } catch (e) {
        console.warn(e);
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "FM_APPLICANT_UPSERT") {
    settings().then(async (s) => {
      try {
        await api(`/api/bots/${s.botId}/applicants/upsert`, {
          method: "POST",
          body: msg.applicant,
        });
      } catch (e) {
        console.warn(e);
      }
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg?.type === "FM_CONTACT_CHECK") {
    settings().then(async (s) => {
      try {
        const q = new URLSearchParams();
        if (msg.profile_key) q.set("profile_key", msg.profile_key);
        if (msg.display_name) q.set("display_name", msg.display_name);
        const data = await api(
          `/api/bots/${s.botId}/contacts/check?${q.toString()}`
        );
        sendResponse(data);
      } catch (e) {
        sendResponse({ known: false, error: String(e) });
      }
    });
    return true;
  }
  if (msg?.type === "FM_OUTREACH_PERSONALIZE") {
    settings().then(async (s) => {
      try {
        const data = await api(`/api/bots/${s.botId}/outreach/personalize`, {
          method: "POST",
          body: msg.profile || {},
        });
        sendResponse(data);
      } catch (e) {
        sendResponse({ error: String(e) });
      }
    });
    return true;
  }
  if (msg?.type === "FM_CHAT_TURN") {
    settings().then(async (s) => {
      try {
        const data = await api(`/api/bots/${s.botId}/chat/turn`, {
          method: "POST",
          body: {
            conversation_id: String(msg.conversation_id),
            message: msg.message,
            display_name: msg.display_name || null,
            profile_key: msg.profile_key || null,
          },
        });
        sendResponse(data);
      } catch (e) {
        sendResponse({ reply: null, error: String(e), action: "error" });
      }
    });
    return true;
  }
  if (msg?.type === "FM_SCHEDULE_CHAT_REPLY") {
    scheduleChatReply(msg)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (msg?.type === "FM_CHAT_RELOAD_INBOX") {
    (async () => {
      try {
        const tab = await ensureInboxTab({ activate: false });
        await chrome.tabs.update(tab.id, { url: INBOX_URL, active: false });
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true;
  }
  if (msg?.type === "FM_CHAT_DUE") {
    // Scheduled rejection drip removed (SPEC: no outbound without inbound).
    sendResponse({ items: [] });
    return true;
  }
  return false;
});

async function pollJobs() {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) {
    console.info("job poll skip: enable Poll dashboard + set API URL + bot token");
    return;
  }

  try {
    const active = await api(`/api/bots/${s.botId}/jobs/active`);
    const job = active?.job;
    const { activeJobId } = await chrome.storage.local.get({ activeJobId: null });

    // Queued on dashboard → claim immediately (do not wait for a later poll).
    if (job?.status === "queued") {
      await armInboxChat();
      await heartbeat("online");
      const claimed = await api(`/api/bots/${s.botId}/jobs/claim`, { method: "POST" });
      if (claimed?.job) {
        console.info("claimed queued job", claimed.job.id);
        await runClaimedJob(claimed.job, { revive: false });
      } else {
        console.warn("claim returned no job for queued", job.id);
      }
      return;
    }

    const shouldHalt =
      job?.status === "cancel_requested" || (activeJobId && !job);
    if (shouldHalt) {
      await disarmInboxChat("job halted");
      await chrome.storage.local.set({
        activeJobId: null,
        pendingCampaign: null,
      });
      const outreach = await findTabBy((u) => /\/freelancer/i.test(u));
      if (outreach) {
        try {
          await chrome.tabs.sendMessage(outreach.id, { type: "FM_STOP_CAMPAIGN" });
        } catch (_e) {
          /* ignore */
        }
      }
      if (!job) await heartbeat("online");
      return;
    }

    // Job running: only revive if no content script is actively campaigning.
    // Do not re-arm inbox here — after VPS/browser reboot inbox stays off until Start.
    if (job?.status === "running") {
      await chrome.storage.local.set({ activeJobId: job.id });
      const tabs = await chrome.tabs.query({
        url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
      });
      let alive = false;
      for (const t of tabs) {
        if (isInboxUrl(t.url || "")) continue;
        if (await pingTabCampaign(t.id)) {
          alive = true;
          break;
        }
      }
      if (alive) {
        await heartbeat("busy");
        return;
      }
      // Content script may be mid step-wait / navigation — trust recent heartbeat.
      const { campaignAliveAt, pendingCampaign } = await chrome.storage.local.get({
        campaignAliveAt: 0,
        pendingCampaign: null,
      });
      const aliveAge = Date.now() - Number(campaignAliveAt || 0);
      if (campaignAliveAt && aliveAge < 90000) {
        await heartbeat("busy");
        return;
      }
      // Modal already open (&id=) — resume Contact flow, do not re-search.
      if (pendingCampaign?.phase === "on_profile") {
        const outreach = tabs.find((t) => !isInboxUrl(t.url || ""));
        const hasModal =
          outreach && /[?&]id=\d+/i.test(outreach.url || "");
        if (hasModal) {
          await startCampaignInTab(outreach, job, {
            dry: isDryRunJob(job),
            skipSearch: true,
            resumeProfile: true,
            revive: true,
          });
          return;
        }
      }
      await runClaimedJob(job, { revive: true });
      return;
    }

    if (activeJobId && !job) {
      await chrome.storage.local.set({ activeJobId: null, pendingCampaign: null });
    }

    await heartbeat("online");
    const claimed = await api(`/api/bots/${s.botId}/jobs/claim`, { method: "POST" });
    if (claimed?.job) {
      await runClaimedJob(claimed.job, { revive: false });
    }
  } catch (e) {
    console.warn("job poll error", e);
    await heartbeat("error", String(e.message || e));
  }
}

async function pollChat() {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) return;
  const store = await chrome.storage.local.get({ inboxArmed: false });
  if (store.inboxArmed !== true) {
    console.info("inbox chat poll skipped — not armed (Start a job first)");
    return;
  }
  const paused = await syncAutomationPausedFromServer();
  if (paused) {
    console.info("inbox chat poll skipped — paused");
    return;
  }
  await runInboxChatPass();
}

function scheduleJobPoll() {
  chrome.alarms.create("fm_jobs", { when: Date.now() + JOB_POLL_MS });
}

function scheduleChatPoll() {
  chrome.storage.local
    .get({ inboxArmed: false, automationPaused: true, activeJobId: null })
    .then((store) => {
      if (store.inboxArmed !== true || store.automationPaused !== false) {
        console.info("chat poll not scheduled — inbox not armed");
        return;
      }
      const ms = store.activeJobId ? CHAT_POLL_BUSY_MS : CHAT_POLL_MS;
      chrome.alarms.create("fm_chat", { when: Date.now() + ms });
    });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "fm_jobs") {
    pollJobs().finally(scheduleJobPoll);
  }
  if (alarm.name === "fm_chat") {
    pollChat().finally(scheduleChatPoll);
  }
  if (alarm.name && alarm.name.startsWith("fm_chat_send_")) {
    const conversationId = alarm.name.slice("fm_chat_send_".length);
    deliverScheduledChatReply(conversationId).catch((e) =>
      console.warn("alarm deliver failed", e)
    );
  }
});

scheduleJobPoll();

/** Always disarm inbox on browser/SW start — chrome.storage + alarms survive VPS reboot. */
async function bootDisarmInbox() {
  try {
    const all = await chrome.alarms.getAll();
    for (const a of all) {
      if (
        a.name === "fm_chat" ||
        (a.name && a.name.startsWith("fm_chat_send_"))
      ) {
        await chrome.alarms.clear(a.name);
      }
    }
  } catch (_e) {
    /* ignore */
  }
  await chrome.storage.local.set({
    inboxArmed: false,
    automationPaused: true,
    campaignStop: true,
    pendingChatReplies: {},
    chatPassBusy: false,
    chatPassBusyAt: 0,
    // Do not auto-revive inbox from a previous session
  });
  console.info(
    "boot: inbox disarmed — open dashboard and click Start to enable inbox chat"
  );
}

chrome.runtime.onStartup.addListener(() => {
  bootDisarmInbox().catch((e) => console.warn("onStartup disarm failed", e));
});

chrome.runtime.onInstalled.addListener(() => {
  bootDisarmInbox().catch((e) => console.warn("onInstalled disarm failed", e));
});

(async () => {
  await bootDisarmInbox();
  // Job poll may re-arm only if it claims/revives a live dashboard job.
  pollJobs();
})();
