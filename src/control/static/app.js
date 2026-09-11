const state = {
  botId: 1,
  token: localStorage.getItem("fm_dashboard_token") || "",
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

async function refreshBot() {
  const bot = await api(`/api/bots/${state.botId}`);
  const job = bot.current_job;
  const jobLine = job
    ? `Job #${job.id} [${job.status}] keyword="${job.keyword}" dry_run=${job.dry_run}`
    : "No current job";
  $("botCard").innerHTML =
    `<div><strong>${bot.label || "Bot " + bot.id}</strong> ${statusBadge(bot.status)}</div>` +
    `<div>Last heartbeat: ${bot.last_heartbeat_at || "—"}</div>` +
    `<div>${jobLine}</div>` +
    (bot.last_error ? `<div>Error: ${bot.last_error}</div>` : "");

  if (job?.id) {
    const events = await api(`/api/jobs/${job.id}/events?limit=50`);
    $("jobLog").textContent = events.length
      ? events
          .map((e) => `${e.created_at} [${e.level}] ${e.message}`)
          .join("\n")
      : "No events.";
  } else {
    const jobs = await api(`/api/bots/${state.botId}/jobs?limit=1`);
    if (jobs[0]) {
      const events = await api(`/api/jobs/${jobs[0].id}/events?limit=50`);
      $("jobLog").textContent =
        `Last job #${jobs[0].id} [${jobs[0].status}]\n` +
        events.map((e) => `${e.created_at} [${e.level}] ${e.message}`).join("\n");
    } else {
      $("jobLog").textContent = "No jobs yet.";
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

$("btnRefresh").addEventListener("click", () => refreshAll().catch((e) => alert(e.message)));

$("btnStart").addEventListener("click", async () => {
  try {
    const body = {
      keyword: $("keyword").value.trim(),
      min_interval_sec: Number($("minInterval").value),
      max_interval_sec: Number($("maxInterval").value),
      max_freelancers: Number($("limit").value),
      dry_run: $("dryRun").checked,
    };
    const job = await api(`/api/bots/${state.botId}/jobs`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    alert(`Job #${job.id} queued for Bot ${state.botId}`);
    refreshAll();
  } catch (e) {
    alert(e.message);
  }
});

$("btnStop").addEventListener("click", async () => {
  try {
    const bot = await api(`/api/bots/${state.botId}`);
    const jobId = bot.current_job_id || bot.current_job?.id;
    if (!jobId) {
      const jobs = await api(`/api/bots/${state.botId}/jobs?limit=5`);
      const active = jobs.find((j) =>
        ["queued", "running", "cancel_requested"].includes(j.status)
      );
      if (!active) {
        alert("No active job");
        return;
      }
      await api(`/api/bots/${state.botId}/jobs/${active.id}/stop`, { method: "POST" });
    } else {
      await api(`/api/bots/${state.botId}/jobs/${jobId}/stop`, { method: "POST" });
    }
    refreshAll();
  } catch (e) {
    alert(e.message);
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
