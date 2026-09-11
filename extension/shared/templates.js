/* global FMOutreach */
(function () {
  const DEFAULT_SUBJECT =
    "FastAPI/Next.js billing module – remote contract, ~1 month";

  const DEFAULT_BODY = `Hello,

Your profile came up in my search on freelancermap, so I wanted to send this your way.

We're hiring a contractor to build the billing and revenue layer of a live multi-tenant PSA platform (FastAPI, Next.js, PostgreSQL, Stripe). Remote, ~80–100 hours/month, starting Oct 1st.

The project brief is attached. If you're open to it, reply with your availability and we'll arrange a technical conversation.

Best regards,
David
`;

  function firstName(fullName) {
    const parts = String(fullName || "")
      .trim()
      .split(/\s+/);
    return parts[0] || "";
  }

  function pickDetail(title, location) {
    const t = (title || "").trim();
    const loc = (location || "").trim();
    if (t && loc) return `your work as ${t} (${loc})`;
    if (t) return `your focus on ${t}`;
    if (loc) return `your profile based in ${loc}`;
    return "your background";
  }

  /** Turn leading "Hello," / "Hello" into "Hello FirstName," */
  function personalizeHello(body, fullName) {
    const name = firstName(fullName);
    if (!name) return body;
    // Already personalized: "Hello Anna," / "Hello Anna"
    if (new RegExp(`^Hello\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(body)) {
      return body;
    }
    return body.replace(/^Hello(\s*,)?/i, `Hello ${name}$1`);
  }

  function renderSubject(template, fullName) {
    const raw = (template || "").trim() || DEFAULT_SUBJECT;
    if (!raw.includes("{name}")) return raw;
    return raw.replaceAll("{name}", firstName(fullName) || "there");
  }

  function renderBody(template, fullName, detail) {
    const raw = (template || "").trim() || DEFAULT_BODY;
    let out = raw;
    if (out.includes("{name}")) {
      out = out.replaceAll("{name}", firstName(fullName) || "there");
    }
    if (out.includes("{detail}")) {
      out = out.replaceAll("{detail}", (detail || "your background").trim());
    }
    return personalizeHello(out, fullName);
  }

  globalThis.FMOutreach = globalThis.FMOutreach || {};
  Object.assign(globalThis.FMOutreach, {
    OUTREACH_SUBJECT: DEFAULT_SUBJECT,
    OUTREACH_BODY: DEFAULT_BODY,
    firstName,
    pickDetail,
    renderSubject,
    renderBody,
  });
})();
