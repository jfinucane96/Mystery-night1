// Mystery Night phone screens. One file serves both the guest page (/play/...)
// and the host page (/host/...). All text is inserted with textContent, never
// as HTML, so story or guest names can't break the page.
"use strict";

const [, mode, KEY] = location.pathname.split("/");
const app = document.getElementById("app");

// ------------------------------------------------------------------ helpers

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(method, path, body) {
  const opts = { method, headers: { "X-Key": KEY } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw Object.assign(new Error("Can't reach the server. Check your connection and try again."), { status: 0 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw Object.assign(new Error(data.error || "Something went wrong."), { status: res.status, code: data.code });
  }
  return data;
}

function show(...nodes) {
  app.replaceChildren(...nodes.filter(Boolean));
  window.scrollTo(0, 0);
}

function errorCard(title, text) {
  return el("div", { class: "card center" }, el("h1", {}, title), el("p", { class: "muted" }, text));
}

function list(items) {
  return el("ul", {}, items.map((t) => el("li", {}, t)));
}

// -------------------------------------------------------------- guest page

let me = null;        // latest guest state from the server
let step = null;      // which assessment screen is showing (null = choose automatically)
let guestMsg = null;

async function loadGuest() {
  try {
    me = await api("GET", "/api/me");
  } catch (e) {
    if (e.status === 404) {
      return show(errorCard("Link not recognised",
        "This link isn't valid any more. Ask your host to send you a new one."));
    }
    return show(errorCard("Couldn't load", e.message),
      el("button", { class: "wide", onclick: loadGuest }, "Try again"));
  }
  renderGuest();
}

async function saveAssessment(body) {
  try {
    me = await api("POST", "/api/me/assessment", body);
    guestMsg = null;
    return true;
  } catch (e) {
    if (e.status === 404) return loadGuest();
    guestMsg = e.message;
    if (e.status === 409) step = null;   // characters were released meanwhile
    renderGuest();
    return false;
  }
}

function guestHeader() {
  return el("header", {},
    el("p", { class: "eyebrow" }, me.title),
    el("p", { class: "muted" }, `Hello, ${me.guest_name}`));
}

function renderGuest() {
  document.title = me.title;
  if (me.stage === "character") return renderCharacter();
  if (me.stage === "waiting" && step === null) return renderWaiting();
  renderAssessment();
}

function renderWaiting() {
  show(guestHeader(),
    guestMsg && el("p", { class: "error", role: "alert" }, guestMsg),
    el("div", { class: "card center" },
      el("h1", {}, "Thanks, you're all set"),
      el("p", {}, "The host is choosing characters. You'll see yours here once they release them."),
      el("button", { class: "wide", onclick: loadGuest }, "Check again"),
      el("button", { class: "wide quiet", onclick: () => { step = 0; renderGuest(); } }, "Change my answers")));
}

// The assessment is a series of screens: 0..5 = questions, 6 = limits.
function renderAssessment() {
  const a = me.assessment;
  const qs = a.questions;
  if (step === null) {
    const firstUnanswered = qs.findIndex((q) => !(q.id in a.answers));
    step = firstUnanswered === -1 ? qs.length : firstUnanswered;
  }
  const total = qs.length + 1;
  const progress = el("p", { class: "eyebrow" }, `Step ${step + 1} of ${total}`);
  const back = step > 0 && el("button", { class: "quiet", onclick: () => { step -= 1; renderGuest(); } }, "Back");

  if (step < qs.length) {
    const q = qs[step];
    return show(guestHeader(),
      step === 0 && el("p", { class: "card intro" }, a.intro),
      guestMsg && el("p", { class: "error", role: "alert" }, guestMsg),
      el("section", { class: "card" },
        progress,
        el("h2", { class: "question" }, q.prompt),
        el("div", { class: "choices" }, q.options.map((text, i) => el("button", {
          class: a.answers[q.id] === i + 1 ? "choice chosen" : "choice",
          "aria-pressed": a.answers[q.id] === i + 1 ? "true" : "false",
          onclick: async () => {
            if (await saveAssessment({ answers: { [q.id]: i + 1 } })) { step += 1; renderGuest(); }
          },
        }, text)))),
      back);
  }

  // Limits screen
  const chosen = { ...a.chosen_limits };
  const note = el("textarea", { rows: 3, maxlength: 500, placeholder: "Optional", "aria-label": "Anything else to avoid" });
  note.value = a.note || "";
  const labels = { ok: "OK", no: "Not OK", unsure: "No preference" };
  const limitRows = a.limits.map((l) => {
    const buttons = Object.entries(labels).map(([value, label]) => el("button", {
      class: chosen[l.id] === value ? "choice small chosen" : "choice small",
      "aria-pressed": chosen[l.id] === value ? "true" : "false",
      onclick: (e) => {
        chosen[l.id] = value;
        for (const b of e.target.parentNode.children) {
          const on = b === e.target;
          b.classList.toggle("chosen", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
        }
      },
    }, label));
    return el("div", { class: "limit" },
      el("p", {}, el("strong", {}, l.prompt)),
      el("p", { class: "note" }, l.help),
      el("div", { class: "row segmented" }, buttons));
  });

  show(guestHeader(),
    guestMsg && el("p", { class: "error", role: "alert" }, guestMsg),
    el("section", { class: "card" },
      progress,
      el("h2", { class: "question" }, "Anything you'd rather not do?"),
      el("p", { class: "note" }, "Only the host sees this. They won't give you a role you say no to."),
      limitRows,
      el("label", { class: "limit" }, el("strong", {}, "Anything else the host should avoid?"), note),
      el("button", {
        class: "wide",
        onclick: async () => {
          const missing = a.limits.filter((l) => !chosen[l.id]);
          if (missing.length) {
            guestMsg = `Please choose an option for: ${missing.map((l) => l.prompt).join(", ")}.`;
            a.chosen_limits = chosen;
            a.note = note.value;
            return renderGuest();
          }
          if (await saveAssessment({ limits: chosen, note: note.value, submit: true })) {
            step = null;
            renderGuest();
          }
        },
      }, "Finish")),
    back);
}

function renderCharacter() {
  const c = me.character;
  if (!c) {
    return show(guestHeader(),
      el("div", { class: "card center" },
        el("h1", {}, "Your character is coming"),
        el("p", {}, "The host hasn't given you a character yet. Check back soon."),
        el("button", { class: "wide", onclick: loadGuest }, "Check again")));
  }
  show(guestHeader(),
    el("div", { class: "card hero" },
      el("p", { class: "eyebrow" }, "You are playing"),
      el("h1", {}, c.name),
      el("p", { class: "tagline" }, c.tagline)),
    el("section", { class: "card" }, el("h2", {}, "The evening"), el("p", {}, me.setting)),
    el("section", { class: "card" }, el("h2", {}, "Who you are"), el("p", {}, c.biography)),
    el("section", { class: "card" }, el("h2", {}, "People you know"),
      el("dl", {}, c.relationships.map((r) => [el("dt", {}, r.name), el("dd", {}, r.text)]))),
    el("section", { class: "card secret" }, el("h2", {}, "Your secrets"),
      el("p", { class: "note" }, "Only you can see this. Don't show your screen to anyone."),
      list(c.secrets)),
    el("section", { class: "card" }, el("h2", {}, "Your objectives"), list(c.objectives)));
}

// --------------------------------------------------------------- host page

let hostState = null;
let hostMsg = null;
const openDetails = new Set();   // which guests' answer panels are expanded

let hostNotice = null;
let busy = null;   // what the host is waiting on, e.g. "suggest" or "text-g1234abcd"

async function hostAction(method, path, body, busyKey) {
  if (busyKey) { busy = busyKey; renderHost(); }
  try {
    hostState = await api(method, path, body);
    hostMsg = hostState.error_detail || null;
    hostNotice = hostState.notice || null;
  } catch (e) {
    busy = null;
    if (e.status === 403) return showHostDenied();
    if (e.code === "needs_confirm" && window.confirm(e.message)) {
      return hostAction(method, path, { ...body, confirm: true }, busyKey);
    }
    hostMsg = e.code === "needs_confirm" ? null : e.message;
    hostNotice = null;
  }
  busy = null;
  renderHost();
}

// A link that opens the host's own Messages app with the text pre-written.
function smsLink(phone, text, label) {
  return el("a", { class: "button", href: `sms:${phone}?&body=${encodeURIComponent(text)}` }, label);
}

function when(iso) {
  return new Date(iso).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function textStatus(t, what) {
  if (!t) return null;
  if (t.status === "sent") return el("p", { class: "small ok-text" }, `${what} texted ${when(t.at)}`);
  if (t.status === "sending") return el("p", { class: "small muted" }, `Sending ${what.toLowerCase()}…`);
  return el("p", { class: "small bad" }, `${what} text failed: ${t.detail}`);
}

function showHostDenied() {
  show(errorCard("Host link not recognised",
    "This host link isn't valid. If you replaced it, use the newest one."));
}

async function loadHost() {
  try {
    hostState = await api("GET", "/api/host");
  } catch (e) {
    if (e.status === 403) return showHostDenied();
    return show(errorCard("Couldn't load", e.message),
      el("button", { class: "wide", onclick: loadHost }, "Try again"));
  }
  renderHost();
}

function guestLink(g) {
  return `${location.origin}/play/${g.token}`;
}

async function copyLink(g, button) {
  const link = guestLink(g);
  try {
    await navigator.clipboard.writeText(link);
    button.textContent = "Copied!";
  } catch {
    window.prompt("Copy this link:", link);
  }
  setTimeout(() => (button.textContent = "Copy link"), 1500);
}

function progressPill(g, s) {
  if (g.assessment_done) return el("span", { class: "pill ok" }, "Questions done");
  const n = Object.keys(g.answers).length;
  return el("span", { class: "pill empty" }, n ? `Questions ${n}/${s.questions.length}` : "Not started");
}

function answersPanel(g, s) {
  const limitWords = { ok: "OK", no: "Not OK", unsure: "No preference" };
  const details = el("details", { class: "answers", open: openDetails.has(g.id) },
    el("summary", {}, "Answers and limits"),
    el("dl", {},
      s.questions.map((q) => {
        const v = g.answers[q.id];
        return [el("dt", {}, `${q.label}: ${v ? `${v}/4` : "—"}`),
          el("dd", { class: "muted" }, v ? q.options[v - 1] : "Not answered")];
      }),
      Object.entries(s.limit_labels).map(([id, label]) => {
        const v = g.limits[id];
        return [el("dt", {}, label),
          el("dd", { class: v === "no" ? "bad" : v === "ok" ? "" : "warn" }, v ? limitWords[v] : "Not answered")];
      }),
      g.note && [el("dt", {}, "Also avoid"), el("dd", {}, g.note)]));
  details.addEventListener("toggle", () => {
    if (details.open) openDetails.add(g.id); else openDetails.delete(g.id);
  });
  return details;
}

function charName(s, id) {
  const c = s.characters.find((x) => x.id === id);
  return c ? c.name : "No role";
}

// The AI's two best roles for this guest (from the last suggestion)
function aiPicks(g, s) {
  const picks = s.suggestion && s.suggestion.top_picks[g.id];
  if (!picks || !picks.length) return null;
  return el("div", { class: "ai-picks small" },
    el("span", { class: "muted" }, "AI top picks: "),
    picks.map((p, i) => [i ? " · " : "", el("strong", {}, charName(s, p.role_id)), ` (${p.reason})`]));
}

const openContact = new Set();

function contactPanel(g, s) {
  const phone = el("input", { type: "tel", placeholder: "Mobile number", "aria-label": `${g.name}'s mobile number`, value: g.phone });
  const consent = el("input", { type: "checkbox", checked: g.sms_consent });
  const isHost = el("input", { type: "checkbox", checked: g.is_host });
  const details = el("details", { class: "contact", open: openContact.has(g.id) },
    el("summary", {}, g.phone ? `Phone: ${g.phone}${g.sms_consent ? "" : " (no consent yet)"}` : "Add phone number"),
    phone,
    el("label", { class: "check" }, consent, " They agreed to get texts about this party"),
    el("label", { class: "check" }, isHost, " This is me (the host)"),
    el("button", {
      class: "small",
      onclick: () => hostAction("POST", `/api/host/guests/${g.id}/contact`,
        { phone: phone.value, consent: consent.checked, is_host: isHost.checked }),
    }, "Save"));
  details.addEventListener("toggle", () => {
    if (details.open) openContact.add(g.id); else openContact.delete(g.id);
  });
  return [details, textStatus(g.texts.invite, "Invite"), textStatus(g.texts.release, "Release")];
}

function inviteButton(g, s) {
  if (!g.phone) return null;
  if (!s.texting_automatic || (g.texts.invite && g.texts.invite.status === "failed")) {
    return smsLink(g.phone, g.messages.invite, "Text from my phone");
  }
  const sending = busy === `text-${g.id}`;
  return el("button", {
    disabled: sending || !g.sms_consent,
    title: g.sms_consent ? "" : "Tick that they agreed to texts first",
    onclick: () => {
      const again = g.texts.invite ? " again" : "";
      if (confirm(`Text ${g.name} their invite link${again}?`)) {
        hostAction("POST", `/api/host/guests/${g.id}/text-invite`, {}, `text-${g.id}`);
      }
    },
  }, sending ? "Sending…" : g.texts.invite ? "Text again" : "Text invite");
}

function suggestionPanel(s) {
  const sug = s.suggestion;
  const thinking = busy === "suggest";
  const ask = el("button", {
    class: sug ? "quiet" : "",
    disabled: thinking || !s.ai_configured || !s.guests.length,
    onclick: () => hostAction("POST", "/api/host/suggest", {}, "suggest"),
  }, thinking ? "Thinking… (up to a minute)" : sug ? "Suggest again" : "Suggest a cast");
  const byId = Object.fromEntries(s.guests.map((g) => [g.id, g]));
  return el("section", { class: "card" },
    el("h2", {}, "Casting help (AI)"),
    !s.ai_configured && el("p", { class: "note" }, "Not set up yet: needs an Anthropic API key."),
    s.ai_configured && !sug && el("p", { class: "note" },
      "The AI reads everyone's answers and limits and suggests who should play whom. You still decide."),
    sug && [
      el("p", {}, sug.summary),
      el("p", { class: "small muted" }, `Suggested ${when(sug.created_at)}. If answers changed since, suggest again.`),
      el("ul", { class: "plain plan" }, sug.cast.filter((r) => byId[r.guest_id]).map((r) => {
        const g = byId[r.guest_id];
        const already = g.character_id === r.role_id || (!g.character_id && r.role_id === "none");
        return el("li", {},
          el("div", { class: "row" },
            el("span", { class: "grow" }, el("strong", {}, g.name), " → ", charName(s, r.role_id)),
            r.role_id !== "none" && (already
              ? el("span", { class: "pill ok" }, "Assigned")
              : el("button", {
                class: "small quiet",
                onclick: () => hostAction("POST", `/api/host/guests/${g.id}/assign`, { character_id: r.role_id }),
              }, "Use this"))),
          el("p", { class: "small muted" }, r.reason));
      })),
      el("button", {
        class: "wide",
        onclick: () => confirm("Assign every role as suggested? This replaces current assignments for these guests.")
          && hostAction("POST", "/api/host/suggest/apply", {}),
      }, "Use the whole plan"),
    ],
    ask);
}

function guestCard(s, g) {
  const taken = new Map(s.guests.filter((o) => o.character_id && o.id !== g.id)
    .map((o) => [o.character_id, o.name]));
  const select = el("select", {
    "aria-label": `Character for ${g.name}`,
    onchange: (e) => hostAction("POST", `/api/host/guests/${g.id}/assign`, { character_id: e.target.value }),
  },
    el("option", { value: "" }, "No character yet"),
    s.characters.map((c) => {
      const f = g.fit[c.id];
      let label = `${c.name} (${c.casting.load})`;
      if (taken.has(c.id)) label += `: taken by ${taken.get(c.id)}`;
      else if (f.blocked.length) label += ": said no";
      else if (f.check.length) label += ": check first";
      else if (f.warnings.length) label += " ⚠";
      return el("option", {
        value: c.id, selected: c.id === g.character_id,
        disabled: c.id !== g.character_id && (taken.has(c.id) || f.blocked.length > 0),
      }, label);
    }));

  // Issues with the current assignment
  const f = g.character_id && g.fit[g.character_id];
  const issues = f && [
    f.blocked.map((t) => el("li", { class: "bad" }, t)),
    f.check.map((t) => el("li", { class: "warn" }, `${t} (you confirmed you checked)`)),
    f.warnings.map((t) => el("li", { class: "warn" }, t)),
  ].flat();

  const copyBtn = el("button", { onclick: (e) => copyLink(g, e.target) }, "Copy link");
  return el("li", { class: "card guest" },
    el("div", { class: "row" },
      el("strong", {}, g.name, g.is_host && el("span", { class: "muted small" }, " (you)")),
      progressPill(g, s)),
    answersPanel(g, s),
    aiPicks(g, s),
    select,
    g.character_name && el("p", { class: "small" }, `Role: ${g.character_name}`),
    issues && issues.length > 0 && el("ul", { class: "issues" }, issues),
    contactPanel(g, s),
    el("div", { class: "row buttons" },
      inviteButton(g, s),
      copyBtn,
      el("a", { class: "button", href: guestLink(g), target: "_blank", rel: "noopener" }, "Open"),
      el("button", {
        class: "quiet",
        onclick: () => confirm(`Make a new link for ${g.name}? Their old link will stop working.`)
          && hostAction("POST", `/api/host/guests/${g.id}/new-link`, {}),
      }, "New link"),
      el("button", {
        class: "quiet danger",
        onclick: () => confirm(`Remove ${g.name} from the game? Their link will stop working.`)
          && hostAction("POST", `/api/host/guests/${g.id}/remove`, {}),
      }, "Remove")));
}

function releasePanel(s) {
  if (s.released) {
    // Guests to text from the host's own phone: everyone (manual mode) or failed automatic texts
    const manual = s.guests.filter((g) => g.character_id && g.phone &&
      (!s.texting_automatic || (g.texts.release && g.texts.release.status === "failed")));
    return el("section", { class: "card released" },
      el("p", {}, el("strong", {}, "Characters are released."), " Guests can see their roles."),
      manual.length > 0 && el("div", {},
        el("p", { class: "small" }, "Let these guests know from your phone:"),
        el("div", { class: "row buttons" }, manual.map((g) => smsLink(g.phone, g.messages.release, `Text ${g.name}`)))),
      el("button", {
        class: "quiet small",
        onclick: () => confirm("Hide characters from guests again? (Useful for testing.)")
          && hostAction("POST", "/api/host/unrelease", {}),
      }, "Hide characters again"));
  }
  const unassigned = s.guests.filter((g) => !g.character_id).map((g) => g.name);
  const unfinished = s.guests.filter((g) => !g.assessment_done).map((g) => g.name);
  const texted = s.guests.filter((g) => g.character_id && g.phone && g.sms_consent).length;
  return el("section", { class: "card" },
    el("h2", {}, "Release characters"),
    el("p", { class: "note" }, "Guests only see their character after you release. Everyone gets theirs at the same moment."),
    s.texting_automatic && el("p", { class: "small" }, `${texted} guest${texted === 1 ? "" : "s"} will get a text when you release.`),
    unfinished.length > 0 && el("p", { class: "small warn" }, `Still answering: ${unfinished.join(", ")}`),
    unassigned.length > 0 && el("p", { class: "small warn" }, `No character yet: ${unassigned.join(", ")}`),
    el("button", {
      class: "wide",
      disabled: s.guests.length === 0,
      onclick: () => {
        const warn = unassigned.length ? `\n\n${unassigned.join(", ")} won't have a character yet.` : "";
        if (confirm(`Show every guest their character now?${warn}`)) hostAction("POST", "/api/host/release", {}, "release");
      },
    }, busy === "release" ? "Releasing…" : "Release characters to everyone"));
}

function renderHost() {
  const s = hostState;
  document.title = `Host · ${s.title}`;
  const nameInput = el("input", { type: "text", maxlength: 40, placeholder: "Guest name", "aria-label": "Guest name", required: true });
  const addForm = el("form", {
    class: "row add",
    onsubmit: (e) => {
      e.preventDefault();
      hostAction("POST", "/api/host/guests", { name: nameInput.value });
    },
  }, nameInput, el("button", { type: "submit" }, "Add guest"));

  const sol = s.solution;
  const y = window.scrollY;
  show(
    el("header", {},
      el("p", { class: "eyebrow" }, "Host controls"),
      el("h1", {}, s.title),
      s.placeholder && el("p", { class: "note" }, "Placeholder story for testing."),
      el("p", { class: "small muted" }, s.texting_automatic
        ? "Texting: automatic (Twilio)"
        : "Texting: from your phone (automatic texting not set up yet)")),
    hostMsg && el("p", { class: "error", role: "alert" }, hostMsg),
    hostNotice && el("p", { class: "notice", role: "status" }, hostNotice),
    suggestionPanel(s),
    el("section", {},
      el("div", { class: "row" },
        el("h2", { class: "grow" }, `Guests (${s.guests.length})`),
        el("button", { class: "quiet small", onclick: loadHost }, "Refresh")),
      s.guests.length
        ? el("ul", { class: "plain" }, s.guests.map((g) => guestCard(s, g)))
        : el("p", { class: "muted" }, "No guests yet. Add one below. If you're playing too, add yourself."),
      addForm),
    releasePanel(s),
    el("details", { class: "card secret" },
      el("summary", {}, "Solution (host only, tap to show)"),
      el("dl", {},
        el("dt", {}, "Victim"), el("dd", {}, sol.victim),
        el("dt", {}, "Killer"), el("dd", {}, sol.killer),
        el("dt", {}, "Motive"), el("dd", {}, sol.motive),
        el("dt", {}, "Method"), el("dd", {}, sol.method),
        sol.summary && [el("dt", {}, "Notes"), el("dd", {}, sol.summary)])),
    el("p", { class: "muted small" }, "Keep this page's link private: anyone with it can run the game."));
  window.scrollTo(0, hostMsg ? 0 : y);
}

// ------------------------------------------------------------------- start

if (mode === "play") loadGuest();
else if (mode === "host") loadHost();
