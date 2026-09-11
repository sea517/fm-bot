/* Background: heartbeat + claim outreach jobs from Control API. */

const DEFAULT_API_BASE_URL = "https://fm-bot.vercel.app";
const POLL_MS = 12000;

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

async function ensureFreelancerTab() {
  const tabs = await chrome.tabs.query({
    url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
  });
  if (tabs[0]) return tabs[0];
  return chrome.tabs.create({
    url: "https://www.freelancermap.com/freelancer",
    active: true,
  });
}

async function sendToTab(tabId, payload) {
  try {
    return await chrome.tabs.sendMessage(tabId, payload);
  } catch (_e) {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: [
        "shared/templates.js",
        "shared/supabase.js",
        "content/outreach.js",
      ],
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

async function runClaimedJob(job) {
  const s = await settings();
  const tab = await ensureFreelancerTab();
  await chrome.tabs.update(tab.id, { active: true });
  await reportEvent(s.botId, job.id, `Starting campaign keyword=${job.keyword}`);

  const campaignSettings = {
    keyword: job.keyword,
    minIntervalSec: job.min_interval_sec,
    maxIntervalSec: job.max_interval_sec,
    limit: job.max_freelancers,
    dryRun: job.dry_run,
    apiBaseUrl: s.apiBaseUrl,
    botId: s.botId,
    botToken: s.botToken,
    jobId: job.id,
  };

  // Store cancel check helpers for content script
  await chrome.storage.local.set({
    campaignStop: false,
    activeJobId: job.id,
    activeBotId: s.botId,
  });

  await heartbeat("busy");
  const result = await sendToTab(tab.id, {
    type: "FM_START_CAMPAIGN",
    settings: campaignSettings,
  });

  // Poll until content script finishes (it returns started immediately)
  // Content script will call finish via runtime message
  console.info("campaign start ack", result);
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
        await chrome.storage.local.set({ activeJobId: null });
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
  return false;
});

async function poll() {
  const s = await settings();
  if (!s.enabled || !s.apiBaseUrl || !s.botToken) return;

  await heartbeat("online");

  // Stop campaign if dashboard ended the job
  try {
    const active = await api(`/api/bots/${s.botId}/jobs/active`);
    const job = active?.job;
    const { activeJobId } = await chrome.storage.local.get({ activeJobId: null });
    const shouldHalt =
      job?.status === "cancel_requested" || (activeJobId && !job);
    if (shouldHalt) {
      await chrome.storage.local.set({ campaignStop: true, activeJobId: null });
      const tabs = await chrome.tabs.query({
        url: ["https://www.freelancermap.com/*", "https://www.freelancermap.de/*"],
      });
      for (const tab of tabs) {
        try {
          await chrome.tabs.sendMessage(tab.id, { type: "FM_STOP_CAMPAIGN" });
        } catch (_e) {
          /* ignore */
        }
      }
      if (!job) await heartbeat("online");
      return;
    }

    if (activeJobId || job?.status === "running") {
      return; // already running
    }

    const claimed = await api(`/api/bots/${s.botId}/jobs/claim`, { method: "POST" });
    if (claimed?.job) {
      await runClaimedJob(claimed.job);
    }
  } catch (e) {
    console.warn("poll error", e);
    await heartbeat("error", String(e.message || e));
  }
}

function scheduleNextPoll() {
  chrome.alarms.create("fm_poll", { when: Date.now() + POLL_MS });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "fm_poll") {
    poll().finally(scheduleNextPoll);
  }
});

scheduleNextPoll();
poll();
