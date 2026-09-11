/* Background: outreach on /freelancer + inbox chat on /app/pobox/main in parallel. */

const DEFAULT_API_BASE_URL = "https://fm-bot.vercel.app";
const OUTREACH_URL = "https://www.freelancermap.com/freelancer";
const INBOX_URL = "https://www.freelancermap.com/app/pobox/main";

const JOB_POLL_MS = 12000;
const CHAT_POLL_MS = 20000;

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
  let tab = await findTabBy(isOutreachUrl);
  if (!tab) {
    // Prefer any non-inbox freelancermap tab, else create.
    const tabs = await chrome.tabs.query({
      url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
    });
    tab = tabs.find((t) => !isInboxUrl(t.url || "")) || null;
  }
  if (!tab) {
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
  let tab = await findTabBy(isInboxUrl);
  if (!tab) {
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
    minIntervalSec: Number(job.min_interval_sec) || 240,
    maxIntervalSec: Number(job.max_interval_sec) || 300,
    limit: Number(job.max_freelancers) || 10,
    dryRun: dry,
    apiBaseUrl: s.apiBaseUrl,
    botId: s.botId,
    botToken: s.botToken,
    jobId: job.id,
    skipSearch: Boolean(skipSearch),
    resumeProfile: Boolean(resumeProfile),
  };

  await chrome.storage.local.set({
    campaignStop: false,
    activeJobId: job.id,
    activeBotId: s.botId,
    pendingCampaign: {
      settings: campaignSettings,
      phase: resumeProfile ? "on_profile" : "await_results",
      startedAt: Date.now(),
    },
  });

  await heartbeat("busy");
  ensureInboxTab({ activate: false }).catch(() => {});

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
async function runInboxChatPass() {
  const s = await settings();
  if (!s.enabled || !s.botToken) return;
  const { chatPassBusy } = await chrome.storage.local.get({ chatPassBusy: false });
  if (chatPassBusy) return;

  try {
    await chrome.storage.local.set({ chatPassBusy: true });
    const tab = await ensureInboxTab({ activate: false });
    if (!/\/app\/pobox/i.test(tab.url || "")) {
      await chrome.tabs.update(tab.id, { url: INBOX_URL, active: false });
      await sleep(2500);
    }
    const result = await sendToTab(tab.id, { type: "FM_CHAT_PASS" });
    if (result?.results?.length) {
      console.info("inbox chat pass", result);
    }
  } catch (e) {
    console.warn("inbox chat pass failed", e);
  } finally {
    await chrome.storage.local.set({ chatPassBusy: false });
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
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
  if (msg?.type === "FM_CHAT_DUE") {
    settings().then(async (s) => {
      try {
        const due = await api(`/api/bots/${s.botId}/chat/due`);
        const items = [];
        for (const row of due?.applicants || []) {
          const sent = await api(`/api/bots/${s.botId}/chat/due/${row.id}`, {
            method: "POST",
          });
          if (sent?.reply) {
            items.push({
              applicant_id: row.id,
              conversation_id: row.conversation_id || sent.conversation_id,
              reply: sent.reply,
              stage: sent.stage,
              action: sent.action,
            });
          }
        }
        sendResponse({ items });
      } catch (e) {
        sendResponse({ items: [], error: String(e) });
      }
    });
    return true;
  }
  return false;
});

async function pollJobs() {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) return;

  try {
    const active = await api(`/api/bots/${s.botId}/jobs/active`);
    const job = active?.job;
    const { activeJobId } = await chrome.storage.local.get({ activeJobId: null });
    const shouldHalt =
      job?.status === "cancel_requested" || (activeJobId && !job);
    if (shouldHalt) {
      await chrome.storage.local.set({
        campaignStop: true,
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
  await runInboxChatPass();
}

function scheduleJobPoll() {
  chrome.alarms.create("fm_jobs", { when: Date.now() + JOB_POLL_MS });
}

function scheduleChatPoll() {
  chrome.alarms.create("fm_chat", { when: Date.now() + CHAT_POLL_MS });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "fm_jobs") {
    pollJobs().finally(scheduleJobPoll);
  }
  if (alarm.name === "fm_chat") {
    pollChat().finally(scheduleChatPoll);
  }
});

scheduleJobPoll();
scheduleChatPoll();
pollJobs();
pollChat();
