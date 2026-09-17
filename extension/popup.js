const DEFAULT_API_BASE_URL = "https://fm-bot.vercel.app";

const $ = (id) => document.getElementById(id);

function log(msg) {
  const el = $("log");
  const line = typeof msg === "string" ? msg : JSON.stringify(msg, null, 2);
  el.textContent = `${new Date().toLocaleTimeString()}  ${line}\n` + el.textContent;
}

function readWorkerSettings() {
  return {
    botId: Math.max(1, Math.min(3, Number($("botId").value) || 1)),
    apiBaseUrl: ($("apiBaseUrl").value.trim() || DEFAULT_API_BASE_URL).replace(/\/$/, ""),
    botToken: $("botToken").value.trim(),
    enabled: $("enabled").checked,
  };
}

function readLocalSettings() {
  const minInterval = Math.max(60, Number($("minInterval").value) || 360);
  let maxInterval = Math.max(60, Number($("maxInterval").value) || 540);
  if (maxInterval < minInterval) maxInterval = minInterval;
  return {
    keyword: $("keyword").value.trim(),
    minIntervalSec: minInterval,
    maxIntervalSec: maxInterval,
    limit: Math.max(1, Math.min(40, Number($("limit").value) || 5)),
    dryRun: $("dryRun").checked,
  };
}

async function loadSettings() {
  const data = await chrome.storage.sync.get({
    botId: 1,
    apiBaseUrl: DEFAULT_API_BASE_URL,
    botToken: "",
    enabled: true,
    keyword: "",
    minIntervalSec: 360,
    maxIntervalSec: 540,
    limit: 5,
    dryRun: true,
  });
  $("botId").value = String(data.botId || 1);
  $("apiBaseUrl").value = data.apiBaseUrl || DEFAULT_API_BASE_URL;
  $("botToken").value = data.botToken || "";
  $("enabled").checked = data.enabled !== false;
  $("keyword").value = data.keyword || "";
  $("minInterval").value = data.minIntervalSec ?? 360;
  $("maxInterval").value = data.maxIntervalSec ?? 540;
  $("limit").value = data.limit ?? 5;
  $("dryRun").checked = Boolean(data.dryRun);
}

async function saveSettings() {
  const worker = readWorkerSettings();
  const local = readLocalSettings();
  await chrome.storage.sync.set({ ...worker, ...local });
  log(
    `Saved Bot ${worker.botId} → ${worker.apiBaseUrl || "(no API URL)"} poll=${worker.enabled}`
  );
  return { ...worker, ...local };
}

async function pingApi() {
  const s = readWorkerSettings();
  if (!s.apiBaseUrl || !s.botToken) {
    log("Set API URL and bot token first.");
    return;
  }
  const res = await fetch(`${s.apiBaseUrl}/api/health`);
  const health = await res.json();
  log(`Health: ok=${health.ok} supabase=${health.supabase}`);
  const hb = await fetch(`${s.apiBaseUrl}/api/bots/${s.botId}/heartbeat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${s.botToken}`,
    },
    body: JSON.stringify({ status: "online" }),
  });
  if (!hb.ok) {
    const t = await hb.text();
    throw new Error(`Heartbeat failed: ${hb.status} ${t}`);
  }
  log(`Heartbeat OK for Bot ${s.botId}`);
}

async function activeFreelancerTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("No active tab");
  if (!/freelancermap\.(com|de)/i.test(tab.url)) {
    throw new Error("Activate the freelancermap.com/freelancer tab first");
  }
  return tab;
}

async function sendToTab(payload) {
  const tab = await activeFreelancerTab();
  try {
    return await chrome.tabs.sendMessage(tab.id, payload);
  } catch (_err) {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: [
        "shared/templates.js",
        "shared/supabase.js",
        "content/outreach.js",
      ],
    });
    return await chrome.tabs.sendMessage(tab.id, payload);
  }
}

$("btnSave").addEventListener("click", () => {
  saveSettings()
    .then(() => pingApi())
    .catch((e) => log(String(e.message || e)));
});

$("btnStop").addEventListener("click", async () => {
  try {
    await chrome.storage.local.set({
      campaignStop: true,
      automationPaused: true,
      inboxArmed: false,
      pendingChatReplies: {},
    });
    const alarms = await chrome.alarms.getAll();
    for (const a of alarms) {
      if (a.name === "fm_chat" || (a.name && a.name.startsWith("fm_chat_send_"))) {
        await chrome.alarms.clear(a.name);
      }
    }
    try {
      const s = readWorkerSettings();
      if (s.apiBaseUrl && s.botToken) {
        await fetch(`${s.apiBaseUrl}/api/bots/${s.botId}/automation/pause`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${s.botToken}`,
          },
        });
      }
    } catch (_e) {
      /* local pause already applied */
    }
    try {
      await sendToTab({ type: "FM_STOP_CAMPAIGN" });
    } catch (_e) {
      /* outreach tab may be closed */
    }
    log("Stopped — inbox disarmed. Dashboard Start will arm it again.");
  } catch (e) {
    log(String(e.message || e));
  }
});

$("btnLocalStart").addEventListener("click", async () => {
  try {
    const settings = await saveSettings();
    if (!settings.keyword) {
      log("Enter a search keyword first.");
      return;
    }
    await chrome.storage.local.set({ campaignStop: false });
    log(`Local start “${settings.keyword}”…`);
    const res = await sendToTab({ type: "FM_START_CAMPAIGN", settings });
    log(res?.reason === "started" ? "Campaign running in the tab." : res);
  } catch (e) {
    log(String(e.message || e));
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "FM_CAMPAIGN_LOG") {
    log(msg.text || msg);
  }
});

loadSettings().catch((e) => log(String(e)));
