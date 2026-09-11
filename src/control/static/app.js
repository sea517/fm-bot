const state = {
  botId: 1,
  token: localStorage.getItem("fm_dashboard_token") || "",
  busyAction: false,
};

const $ = (id) => document.getElementById(id);

function token() {
  return state.token || $("apiToken").value.trim();
}

async function api(path, opts = {}) {
  const t = token();
  if (!t) throw new Error("Set dashboard API token first");
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${t}`,
      ...(opts.headers || {}),
    },
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.raw || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function statusBadge(status) {
  const s = status || "offline";
  return `<span class="badge ${s}">${s}</span>`;
}

/** Job still owns the bot (show Stop). */
function isJobRunning(job) {
  return job && ["queued", "running"].includes(job.status);
}

function setRunControls(running) {
  $("btnStart").classList.toggle("hidden", running);
  $("btnStop").classList.toggle("hidden", !running);
  if (!running) {
    $("btnStop").textContent = "Stop";
    $("btnStop").disabled = false;
  }
  if (running) {
    $("btnStart").textContent = "Start";
    $("btnStart").disabled = false;
  }
}

function setActionBusy(which, busy, label) {
  state.busyAction = busy;
  const start = $("btnStart");
  const stop = $("btnStop");
  const refresh = $("btnRefresh");
  if (which === "start") {
    start.disabled = busy;
    start.textContent = busy ? label || "Starting…" : "Start";
  }
  if (which === "stop") {
    stop.disabled = busy;
    stop.textContent = busy ? label || "Stopping…" : "Stop";
  }
  refresh.disabled = busy;
}

async function findActiveJob(bot) {
  let job = bot.current_job;
  if (isJobRunning(job)) return job;
  const jobs = await api(`/api/bots/${state.botId}/jobs?limit=10`);
  return jobs.find((j) => isJobRunning(j)) || null;
}

async function refreshKeywordStats() {
  if (!token()) {
    $("statAvailable").textContent = "—";
    $("statContacted").textContent = "—";
    return;
  }
  const keyword = $("keyword").value.trim();
  const qs = keyword ? `?keyword=${encodeURIComponent(keyword)}` : "";
  try {
    const s = await api(`/api/bots/${state.botId}/outreach-stats${qs}`);
    if (!keyword) {
      $("statAvailable").textContent = "—";
      $("statContacted").textContent = "—";
      $("statAvailable").title = "Enter a keyword to load counts for the last search";
      $("statContacted").title = "";
      return;
    }
    if (s.profiles_found == null) {
      $("statAvailable").textContent = "—";
      $("statContacted").textContent = "—";
      $("statAvailable").title =
        "No search yet for this keyword — Start a job (dry run is fine) to count results";
      $("statContacted").title = "";
      return;
    }
    $("statContacted").textContent = String(s.contacted ?? 0);
    $("statAvailable").textContent = String(s.available ?? 0);
    $("statContacted").title =
      "Contacted since last search for this keyword (counts even if Supabase ledger write fails)";
    $("statAvailable").title = s.last_search_at
      ? `Remaining = found (${s.profiles_found}) − contacted · last search ${s.last_search_at}`
      : `Remaining = found (${s.profiles_found}) − contacted`;
  } catch (e) {
    $("statAvailable").textContent = "—";
    $("statContacted").textContent = "—";
    $("statContacted").title = e.message || String(e);
  }
}

function formatJobEvents(events) {
  return [...events]
    .sort((a, b) => {
      const ta = Date.parse(a.created_at || 0) || 0;
      const tb = Date.parse(b.created_at || 0) || 0;
      if (tb !== ta) return tb - ta;
      return (b.id || 0) - (a.id || 0);
    })
    .map((e) => `${e.created_at} [${e.level}] ${e.message}`)
    .join("\n");
}

function setJobLog(text) {
  const el = $("jobLog");
  el.textContent = text;
  el.scrollTop = 0;
}

async function refreshBot() {
  if (state.busyAction) return;
  const bot = await api(`/api/bots/${state.botId}`);
  const job = await findActiveJob(bot);
  const running = Boolean(job);
  setRunControls(running);

  const jobLine = job
    ? `Job #${job.id} [${job.status}] keyword="${job.keyword}" dry_run=${job.dry_run}`
    : bot.current_job
      ? `Last: #${bot.current_job.id} [${bot.current_job.status}]`
      : "No current job";
  $("botCard").innerHTML =
    `<div><strong>${bot.label || "Bot " + bot.id}</strong> ${statusBadge(bot.status)}</div>` +
    `<div>Last heartbeat: ${bot.last_heartbeat_at || "—"}</div>` +
    `<div>${jobLine}</div>` +
    (bot.last_error ? `<div>Error: ${bot.last_error}</div>` : "");

  await refreshKeywordStats();

  const logJobId = job?.id || bot.current_job?.id;
  if (logJobId) {
    const events = await api(`/api/jobs/${logJobId}/events?limit=50`);
    setJobLog(events.length ? formatJobEvents(events) : "No events.");
  } else {
    const jobs = await api(`/api/bots/${state.botId}/jobs?limit=1`);
    if (jobs[0]) {
      const events = await api(`/api/jobs/${jobs[0].id}/events?limit=50`);
      setJobLog(
        `Last job #${jobs[0].id} [${jobs[0].status}]\n` +
          (events.length ? formatJobEvents(events) : "No events.")
      );
    } else {
      setJobLog("No jobs yet.");
    }
  }
}

async function refreshPipeline() {
  const rows = await api(`/api/bots/${state.botId}/applicants`);
  $("pipeline").innerHTML = rows.length
    ? rows
        .map(
          (a) =>
            `<div class="row"><div>${a.display_name || "—"} · ${a.stage}</div><div>${a.status}</div></div>`
        )
        .join("")
    : `<div class="muted">No applicants yet for Bot ${state.botId}.</div>`;
}

async function refreshSettings() {
  const s = await api(`/api/bots/${state.botId}/settings`);
  $("githubAfter").value = s.github_unlock_after_messages ?? 20;
  $("maxMessages").value = s.max_messages_per_applicant ?? 30;
}

async function refreshAll() {
  await refreshBot();
  await refreshPipeline();
  await refreshSettings();
}

$("apiToken").value = state.token;
$("btnSaveToken").addEventListener("click", () => {
  state.token = $("apiToken").value.trim();
  localStorage.setItem("fm_dashboard_token", state.token);
  refreshAll().catch((e) => alert(e.message));
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.botId = Number(tab.getAttribute("data-bot"));
    refreshAll().catch((e) => alert(e.message));
  });
});

$("btnRefresh").addEventListener("click", () => {
  if (state.busyAction) return;
  refreshAll().catch((e) => alert(e.message));
});

let keywordTimer = null;
$("keyword").addEventListener("input", () => {
  clearTimeout(keywordTimer);
  keywordTimer = setTimeout(() => {
    refreshKeywordStats().catch(() => {});
  }, 350);
});

$("btnStart").addEventListener("click", async () => {
  if (state.busyAction) return;
  try {
    const keyword = $("keyword").value.trim();
    if (!keyword) {
      alert("Enter a keyword in Outreach first");
      return;
    }
    const subject = $("subject").value.trim();
    const messageBody = $("messageBody").value.trim();
    if (!subject || !messageBody) {
      alert("Enter subject and contact form before Start");
      return;
    }
    setActionBusy("start", true, "Starting…");
    const body = {
      keyword,
      subject,
      message_body: messageBody,
      min_interval_sec: Number($("minInterval").value),
      max_interval_sec: Number($("maxInterval").value),
      max_freelancers: Number($("limit").value),
      dry_run: $("dryRun").checked,
    };
    await api(`/api/bots/${state.botId}/jobs`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    setRunControls(true);
  } catch (e) {
    alert(e.message);
    setRunControls(false);
  } finally {
    setActionBusy("start", false);
    await refreshAll().catch(() => {});
  }
});

$("btnStop").addEventListener("click", async () => {
  if (state.busyAction) return;
  try {
    setActionBusy("stop", true, "Stopping…");
    const bot = await api(`/api/bots/${state.botId}`);
    let job = await findActiveJob(bot);
    if (!job && bot.current_job_id) {
      job = { id: bot.current_job_id };
    }
    if (!job?.id) {
      setRunControls(false);
      return;
    }
    await api(`/api/bots/${state.botId}/jobs/${job.id}/stop`, { method: "POST" });
    // Switch to Start immediately — server marks job stopped.
    setRunControls(false);
  } catch (e) {
    alert(e.message);
  } finally {
    setActionBusy("stop", false);
    await refreshAll().catch(() => {});
  }
});

$("btnSaveSettings").addEventListener("click", async () => {
  try {
    await api(`/api/bots/${state.botId}/settings`, {
      method: "PUT",
      body: JSON.stringify({
        github_unlock_after_messages: Number($("githubAfter").value),
        max_messages_per_applicant: Number($("maxMessages").value),
      }),
    });
    alert("Settings saved");
  } catch (e) {
    alert(e.message);
  }
});

if (state.token) {
  refreshAll().catch((e) => {
    $("botCard").textContent = e.message;
  });
} else {
  $("botCard").textContent = "Enter dashboard API token and click Save.";
}

setInterval(() => {
  if (token()) refreshBot().catch(() => {});
}, 10000);
