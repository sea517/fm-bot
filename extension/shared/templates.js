/* global FMOutreach */
(function () {
  const DEFAULT_SUBJECT =
    "FastAPI/Next.js billing module – remote contract, about 1 month";

  const DEFAULT_BODY = `Hello {name},

I found your profile on freelancermap. Your work with {detail} is relevant to this role.

We need a contractor for the billing and revenue layer of a live multi-tenant PSA platform. The stack is FastAPI, Next.js, PostgreSQL, and Stripe. The work is remote. Plan for about 80 to 100 hours each month. Start date is 1 October.

The project brief is attached. If you can do this work, reply with your availability. Then we can set a technical call.

Best regards,
David
`;

  const PLACEHOLDER_RE =
    /[\[\(\{<]\s*(?:specific\s+)?(?:detail|reason|experience|skill|something)[^\]\)\}>]*[\]\)\}>]|\[\s*[^\]]{3,80}\s*\]/gi;

  const ROLE_HINT =
    /\b(senior|lead|engineer|developer|architect|consultant|manager|designer|devops|backend|frontend|full[\s-]?stack|scientist|analyst|software|python|java|react|fastapi|django|next\.?js|node|cloud|data|mobile)\b/i;

  const NAME_BLOCKED = new Set([
    "full",
    "senior",
    "lead",
    "principal",
    "staff",
    "junior",
    "only",
    "remote",
    "available",
    "verified",
    "premium",
    "contact",
    "watchlist",
    "find",
    "the",
    "freelancer",
    "profile",
    "hello",
    "dear",
    "hi",
    "hey",
    "there",
  ]);

  function firstName(fullName) {
    const parts = String(fullName || "")
      .trim()
      .split(/\s+/);
    const first = parts[0] || "";
    if (!first) return "";
    if (NAME_BLOCKED.has(first.toLowerCase())) return "";
    if (ROLE_HINT.test(fullName || "") && parts.length <= 3) return "";
    if (/only\s+remote|available/i.test(fullName || "")) return "";
    if (!/^[A-Za-zÀ-ÖØ-öø-ÿ'’-]+$/.test(first)) return "";
    return first;
  }

  function looksLikePersonName(text) {
    const s = String(text || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!s || s.length > 60) return false;
    if (ROLE_HINT.test(s) || /[|■▪]/.test(s)) return false;
    if (/only\s+remote|^only\b|^remote\b|available|verified|premium|watchlist/i.test(s)) {
      return false;
    }
    if (/\d|[/\\|@]/.test(s)) return false;
    const parts = s.split(/\s+/);
    if (parts.length < 2 || parts.length > 4) return false;
    if (parts.some((p) => NAME_BLOCKED.has(p.toLowerCase()))) return false;
    const caps = parts.filter((p) => /^[A-Z]/.test(p)).length;
    return caps >= parts.length - 1;
  }

  function looksLikeJobTitle(text) {
    const s = String(text || "").trim();
    if (!s || s.length < 8 || looksLikePersonName(s)) return false;
    if (/[|■▪]/.test(s) && s.length >= 12) return true;
    return ROLE_HINT.test(s);
  }

  function sanitizeTitle(title, displayName) {
    const t = String(title || "").trim();
    if (!t) return "";
    if (looksLikePersonName(t)) return "";
    const name = String(displayName || "").trim().toLowerCase();
    if (name && t.toLowerCase() === name) return "";
    if (name && t.toLowerCase().includes(name) && !ROLE_HINT.test(t)) return "";
    return t;
  }

  function detailLooksBad(detail, displayName) {
    const d = String(detail || "").trim();
    if (!d || d.length < 8) return true;
    const low = d.toLowerCase();
    if (/specific detail|their profile|^here/.test(low)) return true;
    if (/your (profile )?based in/.test(low)) return true;
    if (
      /\b(lahore|karachi|islamabad|pakistan)\b/.test(low) &&
      !ROLE_HINT.test(d)
    ) {
      return true;
    }
    const name = String(displayName || "").trim();
    if (name && low.includes(name.toLowerCase())) return true;
    const asMatch = d.match(/your work as\s+(.+)$/i);
    if (asMatch && looksLikePersonName(asMatch[1].split("(")[0].trim())) return true;
    for (const part of name.split(/\s+/)) {
      if (part.length >= 3 && new RegExp(`\\bas\\s+${part}\\b`, "i").test(d)) {
        return true;
      }
    }
    return false;
  }

  /** Technical-only local detail. Never uses personal name or city. */
  function pickDetail(title, location, skills, displayName, experience) {
    const t = sanitizeTitle(title, displayName);
    const sk = String(skills || "").trim();
    const exp = String(experience || "").trim();
    let parts = sk
      .split(/[,|/]/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 4);
    if (!parts.length && exp) {
      const techRe =
        /\b(FastAPI|Django|Flask|Next\.?js|React|Angular|Vue|Node\.?js|TypeScript|Python|PostgreSQL|Stripe|AWS|Docker|Kubernetes|GraphQL|MongoDB|Redis)\b/gi;
      const found = exp.match(techRe) || [];
      parts = [...new Set(found)].slice(0, 3);
    }
    if (t && parts.length) return `your experience as ${t} with ${parts.join(", ")}`;
    if (parts.length) return `your experience with ${parts.join(", ")}`;
    if (t && looksLikeJobTitle(t)) return `your background as ${t}`;
    const role = (exp || t).match(ROLE_HINT);
    if (role) return `your ${role[0].toLowerCase()} experience`;
    void location;
    return "your technical background";
  }

  function stripPlaceholders(text, fallback) {
    const fb = (fallback || "your technical background").trim();
    let out = String(text || "");
    out = out.replaceAll("{detail}", fb).replaceAll("{DETAIL}", fb);
    out = out.replace(PLACEHOLDER_RE, fb);
    out = out.replace(/[^\S\n]{2,}/g, " ");
    out = out.replace(/[^\S\n]+([,.;:])/g, "$1");
    out = out.replace(/specifically[^\S\n]+[—–-][^\S\n]+/gi, "specifically ");
    return out.trim();
  }

  function personalizeHello(body, fullName) {
    let out = String(body || "");
    // Strip UI-chrome greetings the model sometimes emits
    out = out.replace(
      /^(Hello|Hi|Hey|Dear)\s+(Only|Remote|Available|Verified|Premium|Contact|Watchlist|Full|Senior|Lead|Principal|Staff|Junior|Find|The|Freelancer|Profile|There)\b\s*,?\s*/i,
      "Hello,\n\n"
    );
    const name = firstName(fullName);
    if (!name) {
      return out.replace(
        /^Hello\s+(Full|Senior|Lead|Principal|Staff|Junior|Only|Remote)\b\s*,?/i,
        "Hello,"
      );
    }
    if (
      new RegExp(
        `^Hello\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`,
        "i"
      ).test(out)
    ) {
      return out;
    }
    return out.replace(/^(Hello|Hi)(\s*,)?/i, `Hello ${name}$2`);
  }

  function renderSubject(template, fullName) {
    const raw = (template || "").trim() || DEFAULT_SUBJECT;
    let out = raw;
    if (out.includes("{name}")) {
      out = out.replaceAll("{name}", firstName(fullName) || "there");
    }
    return stripPlaceholders(out, firstName(fullName) || "there");
  }

  function renderBody(template, fullName, detail) {
    const raw = (template || "").trim() || DEFAULT_BODY;
    let d = (detail || "your technical background").trim();
    if (detailLooksBad(d, fullName)) {
      d = "your technical background";
    }
    let out = raw;
    if (out.includes("{name}")) {
      out = out.replaceAll("{name}", firstName(fullName) || "there");
    }
    if (out.includes("{detail}")) {
      out = out.replaceAll("{detail}", d);
    }
    out = stripPlaceholders(out, d);
    return personalizeHello(out, fullName);
  }

  async function personalizeOutreach(settings, info) {
    const localDetail = pickDetail(
      info.title,
      info.location,
      info.skills,
      info.name,
      info.experience
    );
    const template = settings.messageBody || settings.body || DEFAULT_BODY;
    const subjectTpl = settings.subject || DEFAULT_SUBJECT;

    try {
      const data = await chrome.runtime.sendMessage({
        type: "FM_OUTREACH_PERSONALIZE",
        profile: {
          display_name: info.name || null,
          title: sanitizeTitle(info.title, info.name) || null,
          location: info.location || null,
          skills: info.skills || null,
          experience: info.experience || null,
          subject: subjectTpl,
          message_body: template,
          project_hint: subjectTpl,
        },
      });
      if (data?.body) {
        let detail = stripPlaceholders(data.detail || localDetail, localDetail);
        if (detailLooksBad(detail, info.name)) detail = localDetail;
        let body = stripPlaceholders(data.body, detail);
        body = personalizeHello(body, info.name);
        const subject = stripPlaceholders(
          data.subject || renderSubject(subjectTpl, info.name),
          detail
        );
        if (
          /\[\s*[^\]]+\s*\]/.test(body) ||
          /specific detail from their profile/i.test(body) ||
          /\{name\}|\{detail\}/i.test(body)
        ) {
          return {
            detail: localDetail,
            subject: renderSubject(subjectTpl, info.name),
            body: renderBody(template, info.name, localDetail),
            source: "local_sanitized",
          };
        }
        return {
          detail,
          subject,
          body,
          source: data.source === "fallback" ? "fallback" : "deepseek",
        };
      }
    } catch (_e) {
      /* fall through */
    }

    return {
      detail: localDetail,
      subject: renderSubject(subjectTpl, info.name),
      body: renderBody(template, info.name, localDetail),
      source: "local",
    };
  }

  globalThis.FMOutreach = globalThis.FMOutreach || {};
  Object.assign(globalThis.FMOutreach, {
    OUTREACH_SUBJECT: DEFAULT_SUBJECT,
    OUTREACH_BODY: DEFAULT_BODY,
    firstName,
    pickDetail,
    stripPlaceholders,
    renderSubject,
    renderBody,
    personalizeOutreach,
  });
})();
