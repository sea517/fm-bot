/* global FMOutreach */
(function () {
  const OUTREACH_SUBJECT =
    "FastAPI/Next.js billing module – remote contract, ~1 month";

  const OUTREACH_BODY = `Hello {name},

Your profile came up in my search on freelancermap — specifically {detail} — so I wanted to send this your way.

We're hiring a contractor to build the billing and revenue layer of a live multi-tenant PSA platform (FastAPI, Next.js, PostgreSQL, Stripe). Remote, ~80–100 hours/month, starting Oct 1st.

The project brief is attached. If you're open to it, reply with your availability and we'll arrange a technical conversation.

Best regards,
David
`;

  function firstName(fullName) {
    const parts = String(fullName || "")
      .trim()
      .split(/\s+/);
    return parts[0] || "there";
  }

  function pickDetail(title, location) {
    const t = (title || "").trim();
    const loc = (location || "").trim();
    if (t && loc) return `your work as ${t} (${loc})`;
    if (t) return `your focus on ${t}`;
    if (loc) return `your profile based in ${loc}`;
    return "your background";
  }

  function renderBody(fullName, detail) {
    return OUTREACH_BODY.replace("{name}", firstName(fullName)).replace(
      "{detail}",
      (detail || "your background").trim()
    );
  }

  globalThis.FMOutreach = globalThis.FMOutreach || {};
  Object.assign(globalThis.FMOutreach, {
    OUTREACH_SUBJECT,
    OUTREACH_BODY,
    firstName,
    pickDetail,
    renderBody,
  });
})();
