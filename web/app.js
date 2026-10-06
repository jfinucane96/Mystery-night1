"use strict";
// Browser client. It only renders what the server returns; all privacy
// decisions are made server-side. DOM is built with textContent (no innerHTML).

const app = document.getElementById("app");
let me = null;
let pollTimer = null;

// ---------------------------------------------------------------- utilities

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-Requested-With"] = "mystery";
    opts.body = JSON.stringify(body || {});
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}

function render(...nodes) {
  app.replaceChildren(...nodes);
  window.scrollTo(0, 0);
}

function errorBox() { return h("div", { class: "error", role: "alert" }); }

function busy(button, fn) {
  return async (ev) => {
    ev && ev.preventDefault();
    button.disabled = true;
    try { await fn(); } finally { button.disabled = false; }
  };
}

const PHASE_LABEL = { DRAFT: "Inviting", CASTING: "Casting", TRAINING: "Characters released" };
function phaseBadge(phase) {
  return h("span", { class: "badge " + (phase === "TRAINING" ? "ok" : "warn") }, PHASE_LABEL[phase] || phase);
}

// ---------------------------------------------------------------- auth

function renderUserbox() {
  const box = document.getElementById("userbox");
  if (!me) { box.replaceChildren(); return; }
  box.replaceChildren(
    h("span", {}, me.display_name),
    h("button", { class: "link", onclick: async () => { await api("POST", "/api/auth/logout"); me = null; route(); } }, "Sign out"));
}

function authForm(intro, onDone) {
  let mode = "register";
  const err = errorBox();
  const name = h("input", { type: "text", id: "f-name", autocomplete: "nickname", maxlength: 60 });
  const email = h("input", { type: "email", id: "f-email", autocomplete: "email" });
  const pw = h("input", { type: "password", id: "f-pw", autocomplete: "new-password", minlength: 8 });
  const nameRow = h("div", {}, h("label", { for: "f-name" }, "Your name (shown to other guests)"), name);
  const submit = h("button", { type: "submit" }, "Create account");
  const toggle = h("button", { class: "link", type: "button" }, "I already have an account");
  const title = h("h2", {}, "Create your account");
  toggle.addEventListener("click", () => {
    mode = mode === "register" ? "login" : "register";
    nameRow.hidden = mode === "login";
    title.textContent = mode === "login" ? "Sign in" : "Create your account";
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    toggle.textContent = mode === "login" ? "I need an account" : "I already have an account";
    pw.setAttribute("autocomplete", mode === "login" ? "current-password" : "new-password");
    err.textContent = "";
  });
  const form = h("form", { class: "stack" }, nameRow,
    h("div", {}, h("label", { for: "f-email" }, "Email"), email),
    h("div", {}, h("label", { for: "f-pw" }, "Password (8+ characters)"), pw),
    h("div", { class: "row spread" }, submit, toggle), err);
  form.addEventListener("submit", busy(submit, async () => {
    err.textContent = "";
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const r = await api("POST", path, { email: email.value, password: pw.value, display_name: name.value });
      me = r.user;
      renderUserbox();
      onDone();
    } catch (e) { err.textContent = e.message; }
  }));
  return h("div", { class: "card narrow" }, intro || null, title, form);
}

// ---------------------------------------------------------------- home

async function homeView() {
  if (!me) {
    return render(h("div", { class: "narrow" },
      h("h1", {}, "Host-directed murder mystery"),
      h("p", { class: "muted" }, "Hosts create a game and invite guests. Guests: open the invitation link your host sent you."),
      authForm(null, route)));
  }
  const [games, pk] = await Promise.all([api("GET", "/api/games"), api("GET", "/api/packages")]);
  const list = (items, empty) => items.length
    ? h("ul", {}, items.map((g) => h("li", {}, h("a", { href: `#/game/${g.id}` }, g.title), " ", phaseBadge(g.phase))))
    : h("p", { class: "muted small" }, empty);

  const err = errorBox();
  const title = h("input", { type: "text", id: "g-title", placeholder: "e.g. Saturday dinner party", maxlength: 100 });
  const pkg = h("select", { id: "g-pkg" }, pk.packages.map((p) =>
    h("option", { value: `${p.package_id}@${p.content_version}` },
      `${p.title} (v${p.content_version}, ${p.min_players}-${p.max_players} guests)`)));
  const create = h("button", { type: "submit" }, "Create game");
  const form = h("form", { class: "stack" },
    h("div", {}, h("label", { for: "g-title" }, "Game title"), title),
    h("div", {}, h("label", { for: "g-pkg" }, "Mystery package"), pkg), create, err);
  form.addEventListener("submit", busy(create, async () => {
    err.textContent = "";
    const [package_id, package_version] = pkg.value.split("@");
    try {
      const r = await api("POST", "/api/games", { title: title.value, package_id, package_version });
      location.hash = `#/game/${r.id}`;
    } catch (e) { err.textContent = e.message; }
  }));

  render(
    h("h1", {}, `Welcome, ${me.display_name}`),
    h("div", { class: "grid2" },
      h("div", { class: "card" }, h("h2", {}, "Games you're playing"), list(games.playing, "None yet. Open the invitation link from your host to join.")),
      h("div", { class: "card" }, h("h2", {}, "Games you're hosting"), list(games.hosting, "None yet."))),
    h("div", { class: "card" }, h("h2", {}, "Host a new game"), form));
}

// ---------------------------------------------------------------- invitation

async function inviteView(token) {
  let inv;
  try { inv = await api("GET", `/api/invitations/${encodeURIComponent(token)}`); }
  catch (e) { return render(h("div", { class: "card narrow" }, h("h2", {}, "Invitation"), h("p", {}, e.message))); }

  const head = h("div", {},
    h("p", { class: "muted small" }, `${inv.host_name} invites ${inv.label ? inv.label + " " : "you "}to`),
    h("h1", {}, inv.game_title),
    h("p", {}, h("em", {}, inv.package_title), " · ", inv.tagline));

  if (inv.claimed_by_you && inv.status === "accepted") {
    return render(h("div", { class: "card narrow" }, head, h("p", {}, "You've already accepted."),
      h("a", { href: `#/game/${inv.game_id}` }, "Go to your game →")));
  }
  if (!["pending", "declined"].includes(inv.status) || (inv.status === "declined" && !inv.claimed_by_you)) {
    const msg = { accepted: "This invitation has already been used.", revoked: "The host withdrew this invitation.",
      closed: "Casting is complete; this invitation is closed.", expired: "This invitation has expired." };
    return render(h("div", { class: "card narrow" }, head, h("p", {}, msg[inv.status] || "This invitation is no longer active.")));
  }
  if (!me) {
    return render(h("div", { class: "narrow" }, h("div", { class: "card" }, head),
      authForm(h("p", { class: "muted small" }, "Create an account (or sign in) to reply."), route)));
  }
  const err = errorBox();
  const respond = (response) => async () => {
    err.textContent = "";
    try {
      const r = await api("POST", `/api/invitations/${encodeURIComponent(token)}/respond`, { response });
      if (r.rsvp === "accepted") location.hash = `#/game/${r.game_id}`;
      else render(h("div", { class: "card narrow" }, head, h("p", {}, "You've declined. You can reopen this link to change your mind until casting closes.")));
    } catch (e) { err.textContent = e.message; }
  };
  render(h("div", { class: "card narrow" }, head,
    h("p", {}, `Signed in as ${me.display_name}.`),
    h("div", { class: "row" },
      h("button", { onclick: respond("accept") }, "Accept invitation"),
      h("button", { class: "secondary", onclick: respond("decline") }, "Decline")), err));
}

// ---------------------------------------------------------------- game router

async function gameView(gid) {
  if (!me) return render(authForm(h("p", { class: "muted small" }, "Sign in to open this game."), route));
  let g;
  try { g = await api("GET", `/api/games/${gid}`); }
  catch (e) { return render(h("div", { class: "card narrow" }, h("p", {}, e.message), h("a", { href: "#/" }, "← Home"))); }
  if (g.role === "host") return hostView(g);
  return playerView(g);
}

function gameHeader(g, extra) {
  return h("div", { class: "row spread", style: { marginBottom: "16px" } },
    h("div", {}, h("p", { class: "muted small", style: { margin: 0 } }, g.package.title + (g.role === "host" ? " · you are the host" : ` · hosted by ${g.host_name}`)),
      h("h1", { style: { margin: 0 } }, g.title)),
    h("div", { class: "row" }, phaseBadge(g.phase), extra || null));
}

// ---------------------------------------------------------------- host

async function hostView(g) {
  const gid = g.id;
  const reload = () => gameView(gid);

  // Invitations
  const invErr = errorBox();
  const label = h("input", { type: "text", id: "inv-label", placeholder: "Guest name (optional)", maxlength: 60 });
  const newLink = h("div");
  const addBtn = h("button", { type: "submit" }, "Create invitation link");
  const invForm = h("form", { class: "row" }, h("div", { style: { flex: "1 1 220px" } }, label), addBtn);
  invForm.addEventListener("submit", busy(addBtn, async () => {
    invErr.textContent = "";
    try {
      const r = await api("POST", `/api/games/${gid}/invitations`, { label: label.value });
      const url = location.origin + "/" + r.path.replace(/^\//, "");
      const box = h("input", { type: "text", readonly: true, value: url, "aria-label": "Invitation link" });
      newLink.replaceChildren(h("div", { class: "notice" },
        h("strong", {}, `Link for ${r.label || "guest"}`), " (shown once, so copy it now):",
        h("div", { class: "linkbox" }, box, h("button", { type: "button", class: "secondary",
          onclick: () => { box.select(); navigator.clipboard?.writeText(url); toast("Link copied"); } }, "Copy"))));
      label.value = "";
      await refreshTables();
    } catch (e) { invErr.textContent = e.message; }
  }));
  const editable = ["DRAFT", "CASTING"].includes(g.phase);
  const invTable = h("div", { class: "table-wrap" });
  const guestTable = h("div", { class: "table-wrap" });

  function drawTables(view) {
    invTable.replaceChildren(view.invitations.length ? h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Label"), h("th", {}, "Status"), h("th", {}, "Used by"), h("th", {}, ""))),
      h("tbody", {}, view.invitations.map((i) => h("tr", {},
        h("td", {}, i.label || "—"),
        h("td", {}, h("span", { class: "badge " + ({ accepted: "ok", declined: "bad", revoked: "bad" }[i.status] || "") }, i.status)),
        h("td", {}, i.claimed_by || "—"),
        h("td", {}, i.status === "pending" && editable ? h("button", { class: "link", onclick: async () => {
          try { await api("POST", `/api/games/${gid}/invitations/${i.id}/revoke`); refreshTables(); } catch (e) { toast(e.message); }
        } }, "Revoke") : null))))) : h("p", { class: "muted small" }, "No invitations yet."));

    guestTable.replaceChildren(view.players.length ? h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Guest"), h("th", {}, "RSVP"), h("th", {}, "Questionnaire"), h("th", {}, "Character"))),
      h("tbody", {}, view.players.map((p) => h("tr", {},
        h("td", {}, p.display_name, h("div", { class: "muted small" }, p.email)),
        h("td", {}, h("span", { class: "badge " + (p.rsvp === "accepted" ? "ok" : "bad") }, p.rsvp)),
        h("td", {}, p.assessment_status === "complete" ? h("span", { class: "badge ok" }, "complete")
          : `${p.answered}/${view.question_count} answered`),
        h("td", {}, p.role_name ? [p.role_name, " ", h("span", { class: "badge " + (p.approval_state === "approved" ? "ok" : "") }, p.approval_state),
          p.acknowledged ? h("div", { class: "muted small" }, "briefing read") : null] : "—"))))) :
      h("p", { class: "muted small" }, "Nobody has replied yet."));
  }
  async function refreshTables() { drawTables(await api("GET", `/api/games/${gid}`)); }
  drawTables(g);

  const sections = [
    gameHeader(g, h("button", { class: "secondary", onclick: reload }, "Refresh")),
    h("div", { class: "card" }, h("h2", {}, "Invitations"),
      editable ? [h("p", { class: "muted small" }, "Create one link per guest. Each link works for one account only."), invForm, invErr, newLink] : null,
      invTable),
    h("div", { class: "card" }, h("h2", {}, "Guests"),
      h("p", { class: "muted small" }, `${g.players.filter((p) => p.rsvp === "accepted").length} of ${g.player_range[1]} places taken ` +
        `(this package plays ${g.player_range[0]}-${g.player_range[1]} guests).`), guestTable),
  ];

  if (editable) sections.push(await castingPanel(g, reload));
  else sections.push(await castPanel(gid, true));
  render(...sections);
}

async function castingPanel(g, reload) {
  const gid = g.id;
  const err = errorBox();
  const card = h("div", { class: "card" });
  const runRecommend = (force) => async () => {
    err.textContent = "";
    try { await api("POST", `/api/games/${gid}/casting/recommend`, { force }); reload(); } catch (e) { err.textContent = e.message; }
  };
  const recommend = h("button", { class: g.phase === "CASTING" ? "secondary" : null },
    g.phase === "CASTING" ? "Re-run recommendation" : "Recommend cast");
  recommend.addEventListener("click", busy(recommend, runRecommend(false)));
  const intro = h("p", { class: "muted small" },
    "When every accepted guest has finished the questionnaire, ask for a recommendation. It respects boundaries and preparation time; " +
    "as host you can override them. Nobody sees a character until you approve.");
  if (g.phase !== "CASTING") {
    card.append(h("h2", {}, "Casting"), intro, recommend, err);
    return card;
  }
  const c = await api("GET", `/api/games/${gid}/casting`);
  const roles = Object.fromEntries(c.roles.map((r) => [r.id, r]));
  const problems = c.recommendation_problems.length ? c.recommendation_problems : c.validation_problems;
  const needsForce = c.recommendation_problems.some((p) => p.startsWith("Required role"));

  const approve = h("button", { disabled: problems.length > 0 || c.stale || null }, "Approve cast & release characters");
  approve.addEventListener("click", busy(approve, async () => {
    if (!approve.dataset.armed) {
      approve.dataset.armed = "1";
      approve.textContent = "Confirm: release characters to every guest";
      err.textContent = "This can't be undone in this version.";
      return;
    }
    err.textContent = "";
    try { await api("POST", `/api/games/${gid}/casting/approve`); toast("Characters released"); reload(); }
    catch (e) { err.textContent = e.message; }
  }));

  const autoAssign = h("button", {}, "Auto-assign necessary roles (override)");
  autoAssign.addEventListener("click", busy(autoAssign, async () => {
    if (!autoAssign.dataset.armed) {
      autoAssign.dataset.armed = "1";
      autoAssign.textContent = "Confirm: override guests' answers";
      err.textContent = "Required roles will go to the most willing guests, overriding as few answers as possible. Every override is listed below and logged.";
      return;
    }
    await runRecommend(true)();
  }));

  // Willingness grid: host-only, guests never receive these numbers.
  const grid = h("div", { class: "table-wrap" }, h("table", { class: "will" },
    h("thead", {}, h("tr", {}, h("th", {}, "Guest"), c.roles.map((r) =>
      h("th", { title: r.public_bio }, r.name, r.required ? h("div", { class: "tiny" }, "required") : null)))),
    h("tbody", {}, c.players.map((p) => h("tr", {}, h("td", {}, p.name), p.options.map((o) => {
      const mine = p.assignment && p.assignment.role_id === o.role_id;
      return h("td", { class: `lvl-${o.level}${mine ? " assigned" : ""}`,
        title: o.willingness_reasons.length ? o.willingness_reasons.join("; ") : "No boundary or prep problems" },
        h("strong", {}, o.willingness),
        h("div", { class: "tiny" }, o.level === "fits" ? "fits" : o.willingness_reasons.join(", ")));
    }))))));

  const playerCards = c.players.map((p) => {
    const a = p.assignment;
    const current = a && p.options.find((o) => o.role_id === a.role_id);
    const forceBox = h("div");
    const select = h("select", { "aria-label": `Role for ${p.name}` }, p.options.map((o) =>
      h("option", { value: o.role_id, selected: a && a.role_id === o.role_id || null, title: o.conflicts.join("; ") },
        `${roles[o.role_id].name}${roles[o.role_id].required ? " (required)" : ""} · willingness ${o.willingness}` +
        (o.compatible ? "" : ` · ⚠ ${o.level === "refused" ? "refused" : "stretch"}`))));
    const move = async (force) => {
      err.textContent = "";
      await api("PUT", `/api/games/${gid}/casting/assignments/${p.id}`, { role_id: select.value, force });
      reload();
    };
    select.addEventListener("change", async () => {
      forceBox.replaceChildren();
      try { await move(false); }
      catch (e) {
        if (!(e.data && e.data.can_force)) { err.textContent = e.message; select.value = a.role_id; return; }
        const forceBtn = h("button", {}, "Force this assignment");
        forceBtn.addEventListener("click", busy(forceBtn, async () => {
          try { await move(true); } catch (e2) { forceBox.replaceChildren(h("div", { class: "error" }, e2.message)); }
        }));
        forceBox.replaceChildren(h("div", { class: "notice bad" }, h("p", { class: "small" }, e.message),
          h("div", { class: "row" }, forceBtn,
            h("button", { class: "secondary", onclick: () => { select.value = a.role_id; forceBox.replaceChildren(); } }, "Cancel"))));
      }
    });
    const dims = ["spotlight", "performance", "deception", "investigation", "mischief", "commitment"];
    const target = a ? roles[a.role_id].dimension_targets : null;
    const bars = h("div", { class: "bars" }, dims.map((d) => [
      h("span", {}, d), h("div", { class: "bar", title: target ? `role target ${target[d]}` : "" }, h("div", { style: { width: p.scores[d] + "%" } })),
      h("span", { class: "muted" }, p.scores[d])]));
    const notAllowed = Object.entries(p.boundaries).filter(([, v]) => v !== "allowed").map(([t, v]) => `${t}: ${v}`);
    const forced = a ? a.forced_conflicts : [];
    return h("div", { class: "player-card" },
      h("div", { class: "row spread" }, h("h3", { style: { margin: 0 } }, p.name),
        a ? h("span", { class: "badge " + (a.source === "forced" ? "bad" : "") }, a.source) : null),
      a ? h("div", { style: { marginTop: ".5rem" } }, h("label", {}, "Character"), select) : h("p", { class: "muted" }, "Not placed."),
      forceBox,
      forced.length ? h("div", { class: "notice bad small" }, h("strong", {}, "You overrode: "), forced.join("; ")) : null,
      current && current.breakdown.notes.length ? h("ul", { class: "small muted" }, current.breakdown.notes.map((n) => h("li", {}, n))) : null,
      h("details", {}, h("summary", { class: "small" }, "Scores, boundaries and why (host only)"),
        bars,
        h("p", { class: "small" }, `Involvement ${p.scores.involvement}; prep ${p.preparation_minutes} min; acting help: ${p.assistance}; stretch: ${p.stretch_preference}`),
        h("p", { class: "small" }, "Not marked “Happy to”: " + (notAllowed.join(", ") || "none"))));
  });

  card.append(...[h("h2", {}, "Casting review"),
    c.stale ? h("div", { class: "notice" }, "Guests or answers changed since this recommendation. Re-run it before approving.") : null,
    problems.length ? h("div", { class: "notice bad" }, h("strong", {}, "Can't approve yet"), h("ul", {}, problems.map((x) => h("li", {}, x))),
      needsForce ? h("p", { class: "small" }, "You can override this: “Auto-assign necessary roles” gives each required role to the most willing guest. " +
        "Or invite another guest, or ask a guest to change their answers (“Review my answers” on their page) and re-run.")
        : h("p", { class: "small", style: { margin: 0 } }, "Invite another guest, or ask a guest to change their answers, then re-run the recommendation."),
      needsForce ? autoAssign : null) : null,
    h("h3", { style: { marginTop: "1rem" } }, "Willingness (host only)"),
    h("p", { class: "small muted" }, "0–100: questionnaire fit, reduced for each “No thanks”, “Not sure” or prep shortfall. Outlined = current choice. Hover a cell for details."),
    grid,
    h("h3", { style: { marginTop: "1rem" } }, "Assignments"),
    ...playerCards,
    h("div", { class: "row" }, approve, recommend), err].filter(Boolean));
  return card;
}

async function castPanel(gid, isHost) {
  const cast = await api("GET", `/api/games/${gid}/cast`);
  const card = h("div", { class: "card" }, h("h2", {}, isHost ? "Approved cast" : "Who's who"),
    cast.victim ? h("p", { class: "muted small" }, `The victim: ${cast.victim.name}. ${cast.victim.public_summary}`) : null,
    h("div", { class: "grid2" }, cast.cast.map((c) => h("div", { class: "mini" },
      h("h3", {}, c.name), h("p", { class: "small muted" }, `played by ${c.played_by}`), h("p", { class: "small" }, c.public_bio)))));
  if (isHost) {
    const out = h("div");
    const btn = h("button", { class: "secondary" }, "Show solution (host only)");
    btn.addEventListener("click", busy(btn, async () => {
      const s = await api("GET", `/api/games/${gid}/solution`);
      btn.hidden = true;
      out.replaceChildren(h("div", { class: "notice" },
        h("p", {}, h("strong", {}, "Killer: "), s.killer.name), h("p", {}, h("strong", {}, "Motive: "), s.motive),
        h("p", {}, h("strong", {}, "Method: "), s.method),
        h("ul", { class: "small" }, s.timeline.map((f) => h("li", {}, `${f.time} — ${f.statement} (${f.criticality})`)))));
    }));
    card.append(h("div", { style: { marginTop: "12px" } }, btn, out));
  }
  return card;
}

// ---------------------------------------------------------------- player

async function playerView(g) {
  if (g.me.character_released) return characterView(g);
  const a = await api("GET", `/api/games/${g.id}/assessment`);
  if (a.status === "complete" && !playerView.editing) return waitingView(g, a);
  return questionnaire(g, a);
}

function waitingView(g, a) {
  const edit = a.editable ? h("button", { class: "secondary", onclick: () => { playerView.editing = true; gameView(g.id); } }, "Review my answers") : null;
  render(gameHeader(g), h("div", { class: "card narrow", style: { textAlign: "center" } },
    h("h2", {}, "You're all set"),
    h("p", {}, "Your host is putting the cast together. Your character will appear here as soon as it's approved."),
    h("p", { class: "muted small" }, "This page checks for updates automatically."), edit));
  pollTimer = setInterval(async () => {
    try { const fresh = await api("GET", `/api/games/${g.id}`); if (fresh.me.character_released) gameView(g.id); } catch (_) { /* keep waiting */ }
  }, 10000);
}

function questionnaire(g, a) {
  const answers = { ...a.answers };
  const total = a.questions.length;
  let idx = a.questions.findIndex((q) => !answers[q.id]);
  if (idx === -1) idx = total;               // all answered -> preferences page
  if (playerView.editing) idx = 0;
  const err = errorBox();
  const body = h("div");
  const bar = h("div", { style: { width: "0%" } });

  function draw() {
    err.textContent = "";
    const done = Object.keys(answers).length;
    bar.style.width = Math.round((Math.min(idx, total) / (total + 1)) * 100) + "%";
    if (idx < total) {
      const q = a.questions[idx];
      body.replaceChildren(
        h("p", { class: "muted small" }, `Question ${idx + 1} of ${total} · answers save automatically`),
        h("h2", {}, q.prompt),
        h("div", { class: "options", role: "radiogroup" }, q.options.map((o) => {
          const b = h("button", { class: "option" + (answers[q.id] === o.id ? " selected" : ""), role: "radio",
            "aria-checked": String(answers[q.id] === o.id), disabled: !a.editable || null }, o.text);
          b.addEventListener("click", busy(b, async () => {
            try {
              await api("PUT", `/api/games/${g.id}/assessment/answers/${q.id}`, { option_id: o.id });
              answers[q.id] = o.id; idx += 1; draw();
            } catch (e) { err.textContent = e.message; }
          }));
          return b;
        })),
        h("div", { class: "row spread", style: { marginTop: "1rem" } },
          idx > 0 ? h("button", { class: "link", onclick: () => { idx -= 1; draw(); } }, "← Back") : h("span"),
          done === total ? h("button", { class: "link", onclick: () => { idx = total; draw(); } }, "Skip to boundaries →") : h("span")),
        err);
    } else {
      body.replaceChildren(preferences(g, a, answers, () => { idx = total - 1; draw(); }));
    }
  }
  draw();
  render(gameHeader(g), h("div", { class: "card narrow" },
    h("h2", {}, "Casting questionnaire"),
    h("p", { class: "muted small" }, "There are no right answers. This helps your host give you a role you'll enjoy. Your answers are private to the host."),
    h("div", { class: "progress" }, bar), body));
}

function preferences(g, a, answers, back) {
  const bounds = {};
  a.tags.forEach((t) => { bounds[t.id] = a.boundaries[t.id] || "unspecified"; });
  const err = errorBox();
  const tagRows = a.tags.map((t) => {
    const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": t.label });
    const draw = () => seg.replaceChildren(...[["allowed", "Happy to"], ["unspecified", "Not sure"], ["blocked", "No thanks"]].map(([v, txt]) =>
      h("button", { type: "button", class: bounds[t.id] === v ? "on" : "", role: "radio", "aria-checked": String(bounds[t.id] === v),
        onclick: () => { bounds[t.id] = v; draw(); } }, txt)));
    draw();
    return h("div", { class: "tagrow" }, h("strong", {}, t.label), h("div", { class: "small muted" }, t.description,
      t.sensitive ? " Only given to guests who choose “Happy to”." : ""), seg);
  });
  const prep = h("input", { type: "number", id: "p-prep", min: 0, max: 240, step: 5, value: a.preparation_minutes });
  const assist = h("select", { id: "p-assist" },
    [["none", "No help needed"], ["some", "A few prompts would help"], ["full", "Please script things for me"]].map(([v, t]) =>
      h("option", { value: v, selected: a.assistance === v || null }, t)));
  const stretch = h("select", { id: "p-stretch" },
    [["none", "Give me what I asked for"], ["modest", "A little surprise is fine"], ["high", "Surprise me"]].map(([v, t]) =>
      h("option", { value: v, selected: a.stretch_preference === v || null }, t)));
  const submit = h("button", { disabled: !a.editable || null }, a.status === "complete" ? "Save changes" : "Submit questionnaire");
  submit.addEventListener("click", busy(submit, async () => {
    err.textContent = "";
    try {
      await api("PUT", `/api/games/${g.id}/assessment/preferences`, { boundaries: bounds,
        preparation_minutes: parseInt(prep.value, 10), assistance: assist.value, stretch_preference: stretch.value });
      await api("POST", `/api/games/${g.id}/assessment/submit`);
      playerView.editing = false;
      toast("Saved");
      gameView(g.id);
    } catch (e) { err.textContent = e.message; }
  }));
  return h("div", { class: "stack" },
    h("p", { class: "muted small" }, "Last step"),
    h("h2", {}, "Boundaries and preferences"),
    h("p", { class: "small" }, "Anything you mark “No thanks” is never assigned to you, whatever else you answered."),
    h("div", {}, tagRows),
    h("div", {}, h("label", { for: "p-prep" }, "Minutes you're happy to spend preparing beforehand"), prep),
    h("div", {}, h("label", { for: "p-assist" }, "Acting help"), assist),
    h("div", {}, h("label", { for: "p-stretch" }, "How much of a stretch are you up for?"), stretch),
    h("div", { class: "row spread" }, h("button", { class: "link", onclick: back }, "← Back to questions"), submit), err);
}

async function characterView(g) {
  const c = await api("GET", `/api/games/${g.id}/me/character`);
  const veil = h("div", { class: "veiled" });
  const toggle = h("button", { class: "secondary" }, "Reveal secrets");
  toggle.addEventListener("click", () => {
    veil.classList.toggle("veiled");
    toggle.textContent = veil.classList.contains("veiled") ? "Reveal secrets" : "Hide secrets";
  });
  veil.append(...c.secrets.map((s) => h("div", { class: "secret" }, h("span", { class: "secret-text" }, s))));
  const certainty = { certain: "You're sure of this", certain_heard: "You definitely heard this, but it may not be true", uncertain: "You believe this, but aren't certain" };
  const ack = h("button", { disabled: c.acknowledged || null }, c.acknowledged ? "Briefing read ✓" : "I've read my briefing");
  ack.addEventListener("click", busy(ack, async () => { await api("POST", `/api/games/${g.id}/me/acknowledge`); toast("Thanks! Your host can see you're ready."); characterView(g); }));
  render(gameHeader(g),
    h("div", { class: "card char-hero" },
      h("p", { class: "muted small" }, "You are playing"), h("h1", {}, c.name), h("p", {}, c.public_bio),
      h("p", { class: "small muted" }, `Preparation: about ${c.preparation_minutes} minutes. Keep this page to yourself.`)),
    h("div", { class: "card" }, h("h2", {}, "Your story"), h("p", {}, c.backstory),
      c.goal ? h("p", {}, h("strong", {}, "Your goal: "), c.goal) : null),
    h("div", { class: "card" }, h("div", { class: "row spread" }, h("h2", { style: { margin: 0 } }, "Your secrets"), toggle),
      h("p", { class: "small muted" }, "Never show these to anyone."), veil),
    c.relationships.length ? h("div", { class: "card" }, h("h2", {}, "People you know"),
      c.relationships.map((r) => h("div", { class: "memory" }, h("strong", {}, r.with), h("div", {}, r.description)))) : null,
    h("div", { class: "card" }, h("h2", {}, "What you remember"),
      c.briefing.map((m) => h("div", { class: "memory" }, h("div", {}, m.claim),
        h("div", { class: "small muted" }, `${m.time} · ${certainty[m.certainty] || m.certainty}`))),
      h("div", { style: { marginTop: "12px" } }, ack)),
    await castPanel(g.id, false));
}

// ---------------------------------------------------------------- routing

async function route() {
  clearInterval(pollTimer);
  const hash = location.hash.replace(/^#/, "") || "/";
  try {
    if (me === null) me = (await api("GET", "/api/auth/me")).user;
    renderUserbox();
    let m;
    if ((m = hash.match(/^\/invite\/([\w-]+)$/))) return await inviteView(m[1]);
    if ((m = hash.match(/^\/game\/([\w-]+)$/))) return await gameView(m[1]);
    return await homeView();
  } catch (e) {
    render(h("div", { class: "card narrow" }, h("p", {}, e.message), h("a", { href: "#/" }, "← Home")));
  }
}

window.addEventListener("hashchange", () => { playerView.editing = false; route(); });
route();
