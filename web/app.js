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
  if (!res.ok) throw Object.assign(new Error(data.error || "Something went wrong."), { status: res.status });
  return data;
}

function show(...nodes) {
  app.replaceChildren(...nodes.filter(Boolean));
}

function errorCard(title, text) {
  return el("div", { class: "card center" }, el("h1", {}, title), el("p", { class: "muted" }, text));
}

function list(items) {
  return el("ul", {}, items.map((t) => el("li", {}, t)));
}

// -------------------------------------------------------------- guest page

async function loadGuest() {
  let v;
  try {
    v = await api("GET", "/api/me");
  } catch (e) {
    if (e.status === 404) {
      return show(errorCard("Link not recognised",
        "This link isn't valid any more. Ask your host to send you a new one."));
    }
    return show(errorCard("Couldn't load", e.message),
      el("button", { class: "wide", onclick: loadGuest }, "Try again"));
  }
  document.title = v.title;
  const header = el("header", {},
    el("p", { class: "eyebrow" }, v.title),
    el("p", { class: "muted" }, `Hello, ${v.guest_name}`));

  if (!v.character) {
    return show(header,
      el("div", { class: "card center" },
        el("h1", {}, "Your character is coming"),
        el("p", {}, "The host hasn't given you a character yet. Check back soon."),
        el("button", { class: "wide", onclick: loadGuest }, "Check again")));
  }

  const c = v.character;
  show(header,
    el("div", { class: "card hero" },
      el("p", { class: "eyebrow" }, "You are playing"),
      el("h1", {}, c.name),
      el("p", { class: "tagline" }, c.tagline)),
    el("section", { class: "card" }, el("h2", {}, "The evening"), el("p", {}, v.setting)),
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

async function hostAction(method, path, body) {
  try {
    hostState = await api(method, path, body);
    hostMsg = null;
  } catch (e) {
    if (e.status === 403) return showHostDenied();
    hostMsg = e.message;
  }
  renderHost();
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

function guestCard(s, g) {
  const taken = new Map(s.guests.filter((o) => o.character_id && o.id !== g.id)
    .map((o) => [o.character_id, o.name]));
  const select = el("select", {
    "aria-label": `Character for ${g.name}`,
    onchange: (e) => hostAction("POST", `/api/host/guests/${g.id}/assign`, { character_id: e.target.value }),
  },
    el("option", { value: "" }, "No character yet"),
    s.characters.map((c) => el("option", {
      value: c.id, selected: c.id === g.character_id, disabled: taken.has(c.id),
    }, taken.has(c.id) ? `${c.name} (${taken.get(c.id)})` : `${c.name}: ${c.tagline}`)));

  const copyBtn = el("button", { onclick: (e) => copyLink(g, e.target) }, "Copy link");
  return el("li", { class: "card guest" },
    el("div", { class: "row" },
      el("strong", {}, g.name),
      el("span", { class: g.character_name ? "pill" : "pill empty" }, g.character_name || "unassigned")),
    select,
    el("div", { class: "row buttons" },
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
  show(
    el("header", {},
      el("p", { class: "eyebrow" }, "Host controls"),
      el("h1", {}, s.title),
      s.placeholder && el("p", { class: "note" }, "Placeholder story for testing.")),
    hostMsg && el("p", { class: "error", role: "alert" }, hostMsg),
    el("section", {},
      el("h2", {}, `Guests (${s.guests.length})`),
      s.guests.length
        ? el("ul", { class: "plain" }, s.guests.map((g) => guestCard(s, g)))
        : el("p", { class: "muted" }, "No guests yet. Add one below."),
      addForm),
    el("details", { class: "card secret" },
      el("summary", {}, "Solution (host only, tap to show)"),
      el("dl", {},
        el("dt", {}, "Victim"), el("dd", {}, sol.victim),
        el("dt", {}, "Killer"), el("dd", {}, sol.killer),
        el("dt", {}, "Motive"), el("dd", {}, sol.motive),
        el("dt", {}, "Method"), el("dd", {}, sol.method),
        sol.summary && [el("dt", {}, "Notes"), el("dd", {}, sol.summary)])),
    el("p", { class: "muted small" }, "Keep this page's link private: anyone with it can run the game."));
  if (hostMsg) nameInput.focus();
}

// ------------------------------------------------------------------- start

if (mode === "play") loadGuest();
else if (mode === "host") loadHost();
