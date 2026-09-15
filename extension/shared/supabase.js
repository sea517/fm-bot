/* Contact ledger via Control API (preferred) or legacy Supabase REST. */
/* global FMOutreach */
(function () {
  function headers(key) {
    return {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal",
    };
  }

  function useControlApi(settings) {
    return Boolean(settings?.apiBaseUrl && settings?.botToken);
  }

  async function wasContactedByName(settings, name) {
    if (!name) return false;
    if (useControlApi(settings)) {
      try {
        const data = await chrome.runtime.sendMessage({
          type: "FM_CONTACT_CHECK",
          profile_key: "",
          display_name: name.trim(),
        });
        return Boolean(data?.known);
      } catch (_e) {
        return false;
      }
    }
    if (!settings?.supabaseUrl || !settings?.supabaseKey) return false;
    const url = new URL(
      `${settings.supabaseUrl.replace(/\/$/, "")}/rest/v1/contacted_freelancers`
    );
    url.searchParams.set("display_name", `ilike.${name.trim()}`);
    url.searchParams.set("select", "id,first_contacted_at,last_contacted_at");
    url.searchParams.set("limit", "1");
    const res = await fetch(url.toString(), {
      headers: {
        apikey: settings.supabaseKey,
        Authorization: `Bearer ${settings.supabaseKey}`,
      },
    });
    if (!res.ok) return false;
    const rows = await res.json();
    return Boolean(
      rows?.[0] && (rows[0].first_contacted_at || rows[0].last_contacted_at)
    );
  }

  async function wasContactedByKey(settings, profileKey) {
    if (!profileKey || !useControlApi(settings)) return false;
    try {
      const data = await chrome.runtime.sendMessage({
        type: "FM_CONTACT_CHECK",
        profile_key: String(profileKey).trim(),
        display_name: "",
      });
      return Boolean(data?.known);
    } catch (_e) {
      return false;
    }
  }

  async function markContacted(settings, { name, profileKey, projectName, subject, body }) {
    const slug = String(profileKey || name || "unknown")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 80);

    if (useControlApi(settings)) {
      try {
        await chrome.runtime.sendMessage({
          type: "FM_CONTACT_UPSERT",
          contact: {
            profile_key: slug || "unknown",
            display_name: name || null,
            conversation_id: null,
            status: "contacted",
            stage: "outreach_dm_sent",
          },
        });
        await chrome.runtime.sendMessage({
          type: "FM_APPLICANT_UPSERT",
          applicant: {
            profile_key: slug || "unknown",
            display_name: name || null,
            conversation_id: null,
            stage: "outreach_sent",
            outreach_subject: subject || null,
            outreach_body: body || null,
          },
        });
        return true;
      } catch (_e) {
        return false;
      }
    }

    if (!settings?.supabaseUrl || !settings?.supabaseKey) return false;
    const now = new Date().toISOString();
    const row = {
      fm_conversation_id: `outreach-${slug || "unknown"}`,
      display_name: name || null,
      project_title: projectName || null,
      stage: "outreach_dm_sent",
      last_contacted_at: now,
      first_contacted_at: now,
      updated_at: now,
    };
    const res = await fetch(
      `${settings.supabaseUrl.replace(/\/$/, "")}/rest/v1/contacted_freelancers?on_conflict=fm_conversation_id`,
      {
        method: "POST",
        headers: headers(settings.supabaseKey),
        body: JSON.stringify(row),
      }
    );
    return res.ok;
  }

  globalThis.FMOutreach = globalThis.FMOutreach || {};
  Object.assign(globalThis.FMOutreach, {
    wasContactedByName,
    wasContactedByKey,
    markContacted,
  });
})();
