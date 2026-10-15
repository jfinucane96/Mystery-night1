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

let quietRedraw = false;   // true while redrawing for a live update: keep the scroll position

function show(...nodes) {
  const y = window.scrollY;
  app.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  window.scrollTo(0, quietRedraw ? y : 0);
}

// Live updates: ask the server every few seconds and redraw only if something changed.
// If the connection drops, show "Reconnecting…" and keep trying; it catches up on its own.
function startPolling(fetchState, onChange, everyMs) {
  const banner = el("div", { class: "reconnecting", role: "status" }, "Reconnecting…");
  let last = null;
  async function tick() {
    try {
      const fresh = await fetchState();
      banner.remove();
      const text = JSON.stringify(fresh);
      // onChange returns false to postpone (e.g. the host is typing); we'll try again next tick
      if (last === null || text === last || onChange(fresh) !== false) last = text;
    } catch (e) {
      if (e.status === 0) document.body.append(banner);
    }
    setTimeout(tick, everyMs);
  }
  setTimeout(tick, everyMs);
  return { remember: (state) => { last = JSON.stringify(state); } };
}

function newRequestId() {
  // A fresh id per tap, so the server can ignore accidental repeats of the same tap.
  return (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^A-Za-z0-9-]/g, "");
}

function redrawQuietly(render) {
  quietRedraw = true;
  try { render(); } finally { quietRedraw = false; }
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
let chapterOpen = null;  // light-prep guests: has the guest opened their chapter?

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
  if (!guestPoll) guestPoll = startPolling(() => api("GET", "/api/me"), onGuestUpdate, 4000);
  guestPoll.remember(me);
}

let guestPoll = null;

function onGuestUpdate(fresh) {
  const stageChanged = fresh.stage !== me.stage;
  // Don't redraw under someone who is halfway through the questions
  if (!stageChanged && me.stage !== "character") return;
  // ...or under someone typing a question, or while their question is being answered
  const active = document.activeElement;
  if (!stageChanged && (asking || (active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)))) return false;
  me = fresh;
  if (stageChanged) {
    step = null;
    renderGuest();
  } else {
    redrawQuietly(renderGuest);
  }
}

// ---- P04: asking questions

let askMode = null;          // "remember" | "interrogate" | null (form closed)
let askDraft = "";
let askTarget = "";
let askEvidence = "";
let asking = false;          // a question is being answered right now
let askRequestId = null;     // reused if the network drops mid-ask, so it's never charged twice
let askError = null;
let lastAsked = null;        // id of the question just answered, to highlight it

const rerender = () => redrawQuietly(renderCharacter);   // redraw without jumping to the top

const ASK = {
  remember: { token: "private", title: "Private question", verb: "Search your memory",
    blurb: "Ask what your character remembers. Only you see the answer.",
    placeholder: "e.g. What did I see near the champagne?", waiting: "Searching your memory…" },
  interrogate: { token: "public", title: "Public question", verb: "Question a suspect",
    blurb: "Ask another guest out loud. Their phone tells them how to answer.",
    placeholder: "e.g. Where were you during the toast?", waiting: "Sending your question…" },
};

async function submitQuestion() {
  if (asking) return;
  askError = null;
  if (askMode === "interrogate" && !askTarget) { askError = "Choose who to question."; return rerender(); }
  if (askDraft.trim().length < 3) { askError = "Type your question first."; return rerender(); }
  asking = true;
  askRequestId = askRequestId || newRequestId();
  redrawQuietly(renderCharacter);
  try {
    me = await api("POST", "/api/me/ask", {
      request_id: askRequestId, kind: askMode, question: askDraft,
      target_guest_id: askMode === "interrogate" ? askTarget : "",
      evidence_id: askMode === "interrogate" ? askEvidence : "",
    });
    lastAsked = me.asked;
    askRequestId = null;
    askDraft = ""; askEvidence = ""; askMode = null;
  } catch (e) {
    askError = e.message;
    if (e.status !== 0) askRequestId = null;   // a real refusal: next try is a new question
  }
  asking = false;
  rerender();
  const result = document.getElementById("latest-question");
  if (result) result.scrollIntoView({ behavior: "smooth", block: "center" });
}

// "You're being questioned!" cards, shown at the top until answered
function questionedCards() {
  const inv = me.investigation;
  if (!inv) return null;
  return inv.questioned.filter((q) => !q.done).map((q) => el("section", { class: "card questioned", role: "alert" },
    el("p", { class: "eyebrow" }, "You're being questioned!"),
    el("p", {}, el("strong", {}, q.asker), " asks: ", el("em", {}, `"${q.question}"`)),
    q.evidence && el("p", { class: "small warn" }, `They present evidence: ${q.evidence}`),
    el("p", { class: "small muted" }, "Say this out loud (in your own words is fine; keep the facts):"),
    el("p", { class: "spoken" }, q.spoken),
    el("p", { class: "small" }, el("strong", {}, "Acting tip (only you see this): "), q.acting_tip),
    el("button", {
      class: "wide",
      onclick: async () => {
        try { me = await api("POST", `/api/me/interrogations/${q.id}/done`, {}); } catch { /* retry on next tap */ }
        rerender();
      },
    }, "Done, I've answered")));
}

function askForm(inv) {
  const info = ASK[askMode];
  const left = me.tokens[info.token];
  const box = el("textarea", { rows: 2, maxlength: 300, placeholder: info.placeholder, "aria-label": "Your question" });
  box.value = askDraft;
  box.addEventListener("input", () => { askDraft = box.value; });
  const chips = (inv.suggestions[askMode] || []).map((sq) => el("button", {
    class: "chip", type: "button", onclick: () => { askDraft = sq; rerender(); },
  }, sq));
  const who = askMode === "interrogate" && el("div", {},
    el("p", { class: "small muted" }, "Who do you want to question?"),
    el("div", { class: "targets" }, inv.targets.map((tg) => el("button", {
      type: "button", class: askTarget === tg.guest_id ? "choice small chosen" : "choice small",
      "aria-pressed": askTarget === tg.guest_id ? "true" : "false",
      onclick: () => { askTarget = tg.guest_id; rerender(); },
    }, tg.label))));
  const evidence = askMode === "interrogate" && inv.evidence.length > 0 && el("label", { class: "limit" },
    el("span", { class: "small muted" }, "Present evidence (optional)"),
    el("select", { onchange: (e) => { askEvidence = e.target.value; } },
      el("option", { value: "" }, "No evidence"),
      inv.evidence.map((ev) => el("option", { value: ev.id, selected: askEvidence === ev.id }, ev.name))));
  return el("div", { class: "ask-form" },
    el("p", { class: "small" }, info.blurb),
    who,
    box,
    chips.length > 0 && el("div", { class: "chips" }, chips),
    evidence,
    askError && el("p", { class: "error small", role: "alert" }, askError),
    el("button", {
      class: "wide", disabled: asking || left < 1, onclick: submitQuestion,
    }, asking ? info.waiting : left < 1 ? `No ${info.token} questions left` : `Ask (uses 1 ${info.token} question)`),
    el("button", { class: "wide quiet", disabled: asking, onclick: () => { askMode = null; askError = null; rerender(); } }, "Cancel"));
}

function notebookEntry(q) {
  const latest = q.id === lastAsked;
  let body;
  if (q.status === "pending") body = el("p", { class: "muted" }, "Waiting for an answer…");
  else if (q.status !== "answered") body = el("p", { class: "muted" }, q.note);
  else if (q.kind === "remember") body = el("p", {}, q.answer);
  else body = el("p", { class: q.answered_aloud ? "muted" : "warn" },
    q.answered_aloud ? `${q.target} has answered.` : `Listen! ${q.target} is answering out loud.`);
  return el("li", { class: latest ? "latest" : "", id: latest ? "latest-question" : null },
    el("p", { class: "small muted" },
      q.kind === "remember" ? "Private · you remembered" : `Public · you asked ${q.target}`,
      q.evidence ? ` · showed ${q.evidence}` : "", ` · ${when(q.at)}`),
    el("p", {}, el("strong", {}, `"${q.question}"`)),
    body);
}

function investigationPanel() {
  const inv = me.investigation;
  if (!inv || !["investigation", "accusation", "reveal"].includes(me.game.phase)) return null;
  const choice = (mode) => {
    const info = ASK[mode];
    const left = me.tokens[info.token];
    return el("button", {
      class: askMode === mode ? `ask-choice ${mode} chosen` : `ask-choice ${mode}`,
      disabled: !inv.open || asking,
      onclick: () => { askMode = askMode === mode ? null : mode; askError = null; rerender(); },
    },
      el("span", { class: "ask-title" }, info.title),
      el("span", { class: "ask-verb" }, info.verb),
      el("span", { class: left ? "ask-count" : "ask-count none" }, `${left} left`));
  };
  return el("section", { class: "card investigate" },
    el("h2", {}, me.game.phase === "investigation" ? `Investigate · Round ${me.game.round}` : "Your investigation"),
    !inv.open && me.game.phase === "investigation" && el("p", { class: "small warn" }, "Paused: questions are closed for now."),
    me.game.phase !== "investigation" && el("p", { class: "small muted" }, "Questions are closed. Your notebook is below."),
    el("div", { class: "ask-choices" }, choice("remember"), choice("interrogate")),
    askMode && inv.open && askForm(inv),
    inv.evidence.length > 0 && el("details", { class: "contact" },
      el("summary", {}, `Your evidence (${inv.evidence.length})`),
      el("ul", {}, inv.evidence.map((ev) => el("li", {}, el("strong", {}, ev.name), ev.description ? `: ${ev.description}` : "")))),
    inv.notebook.length > 0 && el("div", {},
      el("h3", {}, "Your notebook"),
      el("ul", { class: "plain notebook" }, inv.notebook.map(notebookEntry))));
}

// ---- P03: status, messages, the murder announcement

function murderSeenKey() {
  return `murder-seen-${KEY.slice(0, 12)}`;
}

function murderSeen() {
  try { return localStorage.getItem(murderSeenKey()) === "1"; } catch { return murderDismissed; }
}

let murderDismissed = false;   // fallback when the browser blocks storage

function murderOverlay() {
  if (!me.murder || murderSeen() || murderDismissed) return null;
  const overlay = el("div", { class: "overlay", role: "dialog", "aria-modal": "true" },
    el("div", { class: "overlay-inner" },
      el("p", { class: "eyebrow" }, me.title),
      el("h1", {}, me.murder.title),
      el("p", {}, me.murder.text),
      el("button", {
        class: "wide",
        onclick: () => {
          murderDismissed = true;
          try { localStorage.setItem(murderSeenKey(), "1"); } catch { /* storage blocked: fine */ }
          overlay.remove();
        },
      }, "I'm ready to investigate")));
  return overlay;
}

function statusBar() {
  const g = me.game;
  const t = me.tokens;
  const showTokens = ["investigation", "accusation", "reveal"].includes(g.phase);
  return [
    el("div", { class: "status" },
      el("span", {}, g.phase === "investigation" ? `${g.phase_name} · Round ${g.round}` : g.phase_name),
      showTokens && el("span", { class: "tokens" },
        el("span", { title: "Public questions" }, `${t.public} public`), " · ",
        el("span", { title: "Private questions" }, `${t.private} private`))),
    g.paused && el("p", { class: "paused", role: "status" }, "The game is paused. Hold on until the host resumes."),
    g.phase === "accusation" && el("p", { class: "notice" }, "Accusations are open! The host will tell you how to submit your theory."),
    g.phase === "reveal" && el("p", { class: "notice" }, "The reveal is happening. Listen to the host!"),
  ];
}

let markingRead = false;

function messagesSection() {
  const msgs = me.messages || [];
  if (!msgs.length) return null;
  const unread = msgs.filter((m) => !m.read).length;
  if (unread && !markingRead && document.visibilityState === "visible") {
    markingRead = true;           // tell the host they've been seen, after a moment on screen
    setTimeout(async () => {
      try { me = await api("POST", "/api/me/messages/read", {}); } catch { /* try again next time */ }
      markingRead = false;
    }, 2500);
  }
  return el("section", { class: "card messages", id: "messages" },
    el("h2", {}, "Private messages from the host"),
    el("p", { class: "note" }, "Only you can see these."),
    el("ul", { class: "plain" }, msgs.map((m) => el("li", { class: m.read ? "" : "unread" },
      el("p", { class: "small muted" }, when(m.at), m.read ? "" : " · new"),
      el("p", {}, m.body)))));
}

function newMessageBanner() {
  const unread = (me.messages || []).filter((m) => !m.read).length;
  if (!unread) return null;
  return el("button", {
    class: "wide new-message",
    onclick: () => document.getElementById("messages").scrollIntoView({ behavior: "smooth" }),
  }, unread === 1 ? "You have a new private message" : `You have ${unread} new private messages`);
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
  // Deep-prep guests read their chapter first; light-prep guests can open it if they like
  if (chapterOpen === null) chapterOpen = c.prep === "deep";
  const chapter = c.chapter && (chapterOpen
    ? el("section", { class: "card chapter" },
      el("h2", {}, "Your story"),
      c.chapter.split(/\n\s*\n/).map((para) => el("p", {}, para.trim())),
      el("button", { class: "wide quiet", onclick: () => { chapterOpen = false; redrawQuietly(renderCharacter); } }, "Hide the story"))
    : el("button", { class: "wide quiet", onclick: () => { chapterOpen = true; redrawQuietly(renderCharacter); } },
      "Read your character's story (about 10 minutes)"));
  show(guestHeader(),
    statusBar(),
    questionedCards(),
    investigationPanel(),
    newMessageBanner(),
    murderOverlay(),
    messagesSection(),
    el("div", { class: "card hero" },
      el("p", { class: "eyebrow" }, "You are playing"),
      el("h1", {}, c.name),
      el("p", { class: "tagline" }, c.tagline)),
    chapter,
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
let textPrompt = null;   // after a message: offer to text a heads-up from the host's phone
let busy = null;   // what the host is waiting on, e.g. "suggest" or "text-g1234abcd"

async function hostAction(method, path, body, busyKey) {
  if (busyKey) { busy = busyKey; renderHost(); }
  try {
    hostState = await api(method, path, body);
    hostMsg = hostState.error_detail || null;
    hostNotice = hostState.notice || null;
    textPrompt = hostState.text_prompt || null;
    if (hostPoll) {
      // Tell the live updater what we now have (minus one-off notices), so it doesn't redraw again
      const { notice, error_detail, text_prompt, just_released, ...plain } = hostState;
      hostPoll.remember(plain);
    }
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
  if (!hostPoll) hostPoll = startPolling(() => api("GET", "/api/host"), onHostUpdate, 5000);
  hostPoll.remember(hostState);
}

let hostPoll = null;

function onHostUpdate(fresh) {
  const active = document.activeElement;
  const typing = active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName);
  if (busy || typing) return false;           // never redraw under the host's fingers
  hostState = fresh;
  redrawQuietly(renderHost);
  return true;
}

// ---- P03: running the party

const NEXT_STEP = {
  setup: { phase: "arrival", label: "Start arrival" },
  arrival: { phase: "investigation", label: "Start the investigation (the murder!)" },
  accusation: { phase: "reveal", label: "Start the reveal" },
};

function setPhase(s, phase, label) {
  const g = s.game;
  let question = `${label}?`;
  if (phase === "investigation" && g.round === 0) {
    question = `Start the investigation? Everyone sees the murder announcement and gets ` +
      `${g.start_tokens.public} public + ${g.start_tokens.private} private question tokens.`;
  }
  if (phase !== "setup" && !s.released) question += "\n\nNote: characters haven't been released yet.";
  if (confirm(question)) hostAction("POST", "/api/host/phase", { phase }, "phase");
}

function gamePanel(s) {
  const g = s.game;
  const step = NEXT_STEP[g.phase];
  const buttons = [];
  if (step) buttons.push(el("button", { class: "wide", disabled: !!busy, onclick: () => setPhase(s, step.phase, step.label) }, step.label));
  if (g.phase === "investigation") {
    if (g.round < g.rounds) {
      const rt = g.round_tokens;
      buttons.push(el("button", {
        class: "wide", disabled: !!busy,
        onclick: () => confirm(`Start round ${g.round + 1}? Everyone gets +${rt.public} public, +${rt.private} private.`)
          && hostAction("POST", "/api/host/round", { number: g.round + 1 }, "round"),
      }, `Start round ${g.round + 1} of ${g.rounds}`));
    }
    buttons.push(el("button", {
      class: g.round < g.rounds ? "wide quiet" : "wide", disabled: !!busy,
      onclick: () => setPhase(s, "accusation", "Open accusations"),
    }, "Open accusations"));
  }
  const grantAll = (kind) => {
    if (confirm(`Give every guest +1 ${kind} question token?`)) {
      hostAction("POST", "/api/host/grant",
        { request_id: newRequestId(), target: "all", public: kind === "public" ? 1 : 0, private: kind === "private" ? 1 : 0 }, "grant");
    }
  };
  const jump = el("select", {
    "aria-label": "Jump to phase",
    onchange: (e) => { if (e.target.value) setPhase(s, e.target.value, `Jump to ${e.target.selectedOptions[0].text}`); e.target.value = ""; },
  }, el("option", { value: "" }, "Jump to phase…"), g.phases.filter((p) => p.id !== g.phase).map((p) => el("option", { value: p.id }, p.name)));

  return el("section", { class: g.paused ? "card game paused-card" : "card game" },
    el("div", { class: "row" },
      el("h2", { class: "grow" }, g.phases.find((p) => p.id === g.phase).name,
        g.phase === "investigation" ? ` · Round ${g.round} of ${g.rounds}` : ""),
      el("button", {
        class: g.paused ? "small" : "small quiet", disabled: !!busy,
        onclick: () => hostAction("POST", "/api/host/pause", { paused: !g.paused }, "pause"),
      }, g.paused ? "Resume" : "Pause")),
    g.paused && el("p", { class: "paused" }, "Paused: guests can't ask questions or scan clues."),
    buttons,
    el("div", { class: "row buttons" },
      el("button", { class: "small quiet", disabled: !!busy, onclick: () => grantAll("public") }, "+1 public to everyone"),
      el("button", { class: "small quiet", disabled: !!busy, onclick: () => grantAll("private") }, "+1 private to everyone")),
    jump,
    roomLinkRow(s));
}

// The watch-only room screen for the computer on the speaker (safe to show guests)
function roomLinkRow(s) {
  const link = `${location.origin}/room/${s.room_key}`;
  const copy = el("button", {
    class: "small quiet",
    onclick: async (e) => {
      try { await navigator.clipboard.writeText(link); e.target.textContent = "Copied!"; }
      catch { window.prompt("Copy this link:", link); }
      setTimeout(() => (e.target.textContent = "Copy link"), 1500);
    },
  }, "Copy link");
  return el("div", { class: "room-link" },
    el("p", { class: "small" }, el("strong", {}, "Room screen"), " (for the computer on your speaker or TV; shows nothing secret):"),
    el("div", { class: "row buttons" },
      el("a", { class: "button small", href: link, target: "_blank", rel: "noopener" }, "Open"),
      copy,
      el("button", {
        class: "small quiet",
        onclick: () => confirm("Make a new room link? The old one stops working.")
          && hostAction("POST", "/api/host/room/new-link", {}),
      }, "New link")));
}

const openMessages = new Set();
const drafts = {};   // unsent message text per guest, kept across live redraws

function messagesPanel(g, s) {
  const sending = busy === `msg-${g.id}`;
  const send = (payload) => hostAction("POST", `/api/host/guests/${g.id}/message`,
    { request_id: newRequestId(), ...payload }, `msg-${g.id}`);
  const box = el("textarea", { rows: 2, maxlength: 1000, placeholder: `Private message to ${g.name}`, "aria-label": `Message to ${g.name}` });
  box.value = drafts[g.id] || "";
  box.addEventListener("input", () => { drafts[g.id] = box.value; });
  const alsoText = g.phone && el("input", { type: "checkbox", checked: g.sms_consent });
  const unread = g.private_messages.filter((m) => !m.read).length;
  const details = el("details", { class: "contact", open: openMessages.has(g.id) },
    el("summary", {}, `Messages (${g.private_messages.length} sent${unread ? `, ${unread} unread` : ""})`),
    g.directions.length > 0 && el("div", {},
      el("p", { class: "small muted" }, "Prepared instructions for this role:"),
      el("ul", { class: "plain directions" }, g.directions.map((d) => el("li", {},
        el("p", { class: "small" }, d.phase ? `[${d.phase}] ` : "", d.text),
        el("button", {
          class: d.sent ? "small quiet" : "small", disabled: sending,
          onclick: () => (!d.sent || confirm("You've already sent this one. Send it again?"))
            && send({ direction_id: d.id, also_text: !!(alsoText && alsoText.checked) }),
        }, d.sent ? "Sent ✓ (send again)" : "Send"))))),
    box,
    alsoText && el("label", { class: "check" }, alsoText, " Also text them a heads-up (it won't include the message)"),
    el("button", {
      class: "small", disabled: sending,
      onclick: async () => {
        await send({ body: box.value, also_text: !!(alsoText && alsoText.checked) });
        if (!hostMsg) { drafts[g.id] = ""; redrawQuietly(renderHost); }
      },
    }, sending ? "Sending…" : "Send message"),
    g.private_messages.length > 0 && el("ul", { class: "plain history" }, g.private_messages.map((m) => el("li", { class: "small" },
      el("span", { class: "muted" }, `${when(m.created_at)} · ${m.read ? "read ✓" : "not read yet"}: `), m.body))));
  details.addEventListener("toggle", () => {
    if (details.open) openMessages.add(g.id); else openMessages.delete(g.id);
  });
  return details;
}

function textPromptLine(s) {
  const g = textPrompt && s.guests.find((x) => x.id === textPrompt.guest_id);
  if (!g) return null;
  return el("p", { class: "notice" }, "Let them know: ",
    smsLink(g.phone, g.message_alert, `Text ${g.name} a heads-up from my phone`));
}

const DELIVERY = { truthful: "truth", evasive: "dodged", lying: "LIED", confessing: "CONFESSED" };

// Every question asked, newest first, so the host can follow the game (and step in)
function questionsFeed(s) {
  if (!s.questions.length && s.game.phase !== "investigation") return null;
  const details = el("details", { class: "card feed", open: openFeed },
    el("summary", {}, `Questions asked (${s.questions.length})`),
    !s.questions.length && el("p", { class: "small muted" }, "Nothing yet. Guests' questions appear here as they ask them."),
    el("ul", { class: "plain" }, s.questions.map((q) => el("li", {},
      el("p", { class: "small muted" },
        q.kind === "remember" ? `${q.asker} · private memory` : `${q.asker} → ${q.target} · public`,
        q.evidence ? ` · showed ${q.evidence}` : "", ` · ${when(q.at)}`,
        q.via === "backup" ? " · backup answer (AI unavailable)" : ""),
      el("p", {}, el("strong", {}, `"${q.question}"`)),
      el("p", { class: q.status === "answered" ? "" : "muted" },
        q.status === "answered"
          ? [q.delivery && el("span", { class: `pill ${q.delivery}` }, DELIVERY[q.delivery]), " ", q.answer]
          : q.status === "pending" ? "Answering…" : `Not answered (token refunded): ${q.answer}`)))));
  details.addEventListener("toggle", () => { openFeed = details.open; });
  return details;
}

let openFeed = true;
const openEvidence = new Set();

function evidencePanel(g, s) {
  if (!s.evidence.length) return null;
  const has = new Set(g.evidence);
  const details = el("details", { class: "contact", open: openEvidence.has(g.id) },
    el("summary", {}, `Evidence (${g.evidence.length})`),
    el("ul", { class: "plain directions" }, s.evidence.map((ev) => el("li", { class: "row" },
      el("span", { class: "grow small" }, ev.name),
      el("button", {
        class: has.has(ev.id) ? "small quiet" : "small", disabled: !!busy,
        onclick: () => hostAction("POST", `/api/host/guests/${g.id}/evidence`, { evidence_id: ev.id, give: !has.has(ev.id) }, `ev-${g.id}`),
      }, has.has(ev.id) ? "Take back" : "Give")))));
  details.addEventListener("toggle", () => {
    if (details.open) openEvidence.add(g.id); else openEvidence.delete(g.id);
  });
  return details;
}

function tokenLine(g, s) {
  const give = (kind) => hostAction("POST", "/api/host/grant",
    { request_id: newRequestId(), target: g.id, public: kind === "public" ? 1 : 0, private: kind === "private" ? 1 : 0 }, `grant-${g.id}`);
  return el("div", { class: "row small token-line" },
    el("span", { class: "grow" }, `Tokens: ${g.tokens.public} public · ${g.tokens.private} private`),
    el("button", { class: "small quiet", disabled: !!busy, onclick: () => give("public") }, "+1 public"),
    el("button", { class: "small quiet", disabled: !!busy, onclick: () => give("private") }, "+1 private"));
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
    g.assessment_done && el("p", { class: "small muted" }, g.prep_level === "deep"
      ? "Prep: deep (story chapter + character sheet)" : "Prep: light (character sheet)"),
    s.released && tokenLine(g, s),
    s.released && messagesPanel(g, s),
    s.released && evidencePanel(g, s),
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
    textPromptLine(s),
    gamePanel(s),
    questionsFeed(s),
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

// ------------------------------------------------- room screen (speaker / TV)
// Watch-only. Announces public moments with a sound and the browser's built-in voice.

let room = null;
let roomSoundOn = false;
let roomLast = null;               // the last announcement shown on screen
const roomSeen = new Set();        // events already announced
let audioCtx = null;
const speakQueue = [];
let speaking = false;

function tone(freq, start, length, type = "sine", volume = 0.25) {
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  const t0 = audioCtx.currentTime + start;
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(volume, t0 + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + length);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start(t0);
  osc.stop(t0 + length + 0.05);
}

function thunder(start, length) {
  // A burst of filtered noise that rumbles away
  const samples = Math.floor(audioCtx.sampleRate * length);
  const buffer = audioCtx.createBuffer(1, samples, audioCtx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < samples; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / samples, 2);
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  const filter = audioCtx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 400;
  const gain = audioCtx.createGain();
  gain.gain.value = 0.9;
  src.connect(filter).connect(gain).connect(audioCtx.destination);
  src.start(audioCtx.currentTime + start);
}

const SOUNDS = {
  chime: () => { tone(880, 0, 0.5); tone(1320, 0.18, 0.7); },
  phase: () => { tone(392, 0, 0.6, "triangle"); tone(523, 0.2, 0.6, "triangle"); tone(659, 0.4, 0.9, "triangle"); },
  murder: () => { thunder(0, 3.5); tone(73, 0.2, 3.2, "sawtooth", 0.12); tone(77.8, 0.2, 3.2, "sawtooth", 0.12); },
  confession: () => { [233, 277, 330, 466].forEach((f) => tone(f, 0, 2.2, "sawtooth", 0.08)); tone(116, 0, 2.5, "square", 0.1); },
};
const SOUND_LENGTH = { chime: 900, phase: 1400, murder: 3200, confession: 2000 };

function pickVoice() {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  return voices.find((v) => /natural/i.test(v.name)) || voices.find((v) => /google uk english male/i.test(v.name))
    || voices.find((v) => /en-GB/.test(v.lang)) || voices[0] || null;
}

function announce(sound, text, show = text) {
  speakQueue.push({ sound, text, show });
  if (!speaking) nextAnnouncement();
}

function nextAnnouncement() {
  const item = speakQueue.shift();
  if (!item) { speaking = false; return; }
  speaking = true;
  roomLast = item.show;
  renderRoom();
  if (!roomSoundOn) return setTimeout(nextAnnouncement, 4000);
  if (audioCtx.state === "suspended") audioCtx.resume();
  SOUNDS[item.sound]();
  setTimeout(() => {
    const u = new SpeechSynthesisUtterance(item.text);
    const voice = pickVoice();
    if (voice) u.voice = voice;
    u.rate = 0.92;
    u.pitch = 0.9;
    u.onend = () => setTimeout(nextAnnouncement, 600);
    u.onerror = () => setTimeout(nextAnnouncement, 600);
    speechSynthesis.speak(u);
  }, SOUND_LENGTH[item.sound]);
}

function roomChanges(before, now) {
  // Compare two snapshots of the game and announce what changed
  if (now.phase !== before.phase) {
    if (now.phase === "investigation" && before.phase !== "accusation" && before.phase !== "reveal") {
      announce("murder", `${now.murder.title} ${now.murder.text}`, now.murder.title);
    } else {
      const lines = { arrival: "Welcome! The guests are arriving.", investigation: "The investigation resumes.",
        accusation: "Accusations are now open!", reveal: "The time has come for the truth. The reveal begins!",
        setup: "The game has been reset." };
      announce("phase", lines[now.phase] || `${now.phase_name} begins.`);
    }
  } else if (now.phase === "investigation" && now.round !== before.round) {
    announce("phase", `Round ${now.round} begins!`);
  }
  if (now.paused !== before.paused) announce("phase", now.paused ? "The game is paused." : "The game resumes!");
}

async function loadRoom() {
  try {
    room = await api("GET", "/api/room");
  } catch (e) {
    if (e.status === 403) return show(errorCard("Room link not recognised", "Ask the host for the current room link."));
    return show(errorCard("Couldn't load", e.message), el("button", { class: "wide", onclick: loadRoom }, "Try again"));
  }
  room.events.forEach((ev) => roomSeen.add(ev.id));      // don't replay what happened before this page opened
  renderRoom();
  startPolling(() => api("GET", "/api/room"), (fresh) => {
    const before = room;
    room = fresh;
    roomChanges(before, fresh);
    fresh.events.slice().reverse().forEach((ev) => {     // oldest first
      if (roomSeen.has(ev.id)) return;
      roomSeen.add(ev.id);
      announce(ev.kind === "confession" ? "confession" : "chime", ev.text);
    });
    renderRoom();
  }, 3000);
}

async function keepAwake() {
  try { if (navigator.wakeLock) await navigator.wakeLock.request("screen"); } catch { /* not supported: fine */ }
}

function startRoomSound() {
  audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
  audioCtx.resume();
  roomSoundOn = true;
  keepAwake();
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });
  announce("chime", "Sound is on. Let the mystery begin.", "Sound is on");
}

function renderRoom() {
  document.title = `Room · ${room.title}`;
  document.body.classList.add("room-mode");
  const phaseLine = room.phase === "investigation" ? `${room.phase_name} · Round ${room.round} of ${room.rounds}` : room.phase_name;
  show(
    el("div", { class: "room" },
      el("p", { class: "eyebrow" }, room.title),
      el("h1", { class: "room-phase" }, phaseLine),
      room.paused && el("p", { class: "paused" }, "Paused"),
      el("p", { class: "room-announcement" }, roomLast || "Waiting for something to happen…"),
      roomSoundOn
        ? el("p", { class: "small muted" }, "Sound on. Leave this page open. ",
          el("button", { class: "small quiet", onclick: () => announce("chime", "Testing, testing.", "Test") }, "Test sound"))
        : el("button", { class: "room-start", onclick: () => { startRoomSound(); renderRoom(); } }, "Start sound")));
}

if (mode === "play") loadGuest();
else if (mode === "host") loadHost();
else if (mode === "room") loadRoom();
