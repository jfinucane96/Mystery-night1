// The Hollow Order (v2): the guest's phone, the host's controls and the TV.
// Loaded before app.js; uses its helpers (el, api, show, redrawQuietly...) at run time.
"use strict";

const money = (n) => `£${Number(n || 0).toLocaleString("en-GB")}`;
const hDraft = {};          // form values kept across live redraws
let hErr = null;
let hBusy = false;

function hTimer(end) {
  if (!end) return null;
  return el("p", { class: "h-timer", "data-end": end }, timeLeft(end));
}

function timeLeft(end) {
  const ms = new Date(end) - Date.now();
  if (ms <= 0) return "Time's up";
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} left`;
}

setInterval(() => {
  document.querySelectorAll(".h-timer").forEach((n) => { n.textContent = timeLeft(n.dataset.end); });
}, 1000);

function draft(key, fallback = "") {
  return key in hDraft ? hDraft[key] : fallback;
}

function select(key, options, placeholder, fallback = "") {
  return el("select", { onchange: (e) => { hDraft[key] = e.target.value; redrawQuietly(renderGuest); } },
    el("option", { value: "" }, placeholder),
    options.map(([value, label]) => el("option", { value, selected: draft(key, fallback) === value }, label)));
}

function amountInput(key, max) {
  return el("input", { type: "number", min: 10000, step: 10000, max: max || null, inputmode: "numeric",
    placeholder: "Amount in £ (steps of 10,000)", value: draft(key),
    oninput: (e) => { hDraft[key] = e.target.value; } });
}

async function hAct(action, body, clear = []) {
  if (hBusy) return;
  hBusy = true;
  hErr = null;
  try {
    me = await api("POST", `/api/me/hollow/${action}`, body);
    clear.forEach((k) => delete hDraft[k]);
  } catch (e) {
    hErr = e.message;
  } finally {
    hBusy = false;
  }
  if (guestPoll) guestPoll.remember(me);
  redrawQuietly(renderGuest);
}

// ------------------------------------------------------------------ guest

const H_SECTIONS = {
  dossier: { title: "My Dossier", blurb: "Who you are, your secret, what you saw" },
  memories: { title: "The Night Eleanor Died", blurb: "Your part in the fire of 1867" },
  evidence: { title: "Evidence", blurb: "Everything the Order has made public" },
  deals: { title: "Deals & Ledger", blurb: "Your offers, promises and payments" },
  clues: { title: "Private Intelligence", blurb: "What your family's investigation found" },
};

function hSection() {
  const id = location.hash.slice(1);
  return H_SECTIONS[id] ? id : "home";
}

function nameOf(h, id) {
  if (id === h.me) return "you";
  const o = h.others.find((x) => x.id === id);
  return o ? o.name : id;
}

function renderHollowGuest() {
  const h = me.hollow;
  const c = me.character;
  const section = hSection();
  const top = el("header", { class: "game-top" },
    el("p", { class: "eyebrow" }, h.phase),
    el("h1", { class: "who" }, c.name),
    el("p", { class: "tagline" }, `${c.tagline} · played by ${me.guest_name}`));
  const body = section === "home" ? hollowHome(h) : {
    dossier: hollowDossier, memories: hollowMemories, evidence: hollowEvidence, deals: hollowDeals, clues: hollowClues,
  }[section](h);
  show(
    section === "home" ? top : el("div", { class: "screen-top" },
      el("a", { class: "back", href: "#" }, "‹ Menu"),
      el("h1", { class: "screen-title" }, H_SECTIONS[section].title)),
    hErr && el("p", { class: "error", role: "alert" }, hErr),
    body);
}

function hollowHome(h) {
  const tiles = Object.entries(H_SECTIONS).map(([id, sec]) => {
    let count = 0;
    if (id === "deals") count = h.deals.filter((d) => !d.mine && d.status === "offered").length;
    if (id === "clues") count = h.clues.length;
    return el("a", { class: "tile", href: `#${id}` },
      el("span", { class: "tile-title" }, sec.title),
      el("span", { class: "tile-blurb" }, sec.blurb),
      count ? el("span", { class: "tile-badge" }, String(count)) : null);
  });
  return [
    h.envelope && h.step === "envelopes" && el("a", { class: "alert urgent", href: "#dossier" },
      el("strong", {}, "Silas has left you an envelope."), " Tap to open it. For your eyes only."),
    el("section", { class: "card h-now" },
      el("p", { class: "eyebrow" }, "Now"),
      el("h2", {}, h.script.title || h.step_name),
      hTimer(h.timer_end),
      hollowAction(h)),
    el("nav", { class: "tiles", "aria-label": "Game sections" }, tiles),
  ];
}

function ballotLabel(b) {
  return b.kind === "inheritance"
    ? { title: `Inheritance ballot ${b.round}`, note: [
      "Rank three fellow guests by who most deserves the fortune. Not yourself, and not your spouse.",
      "Your 1st choice gets 3 points, 2nd gets 2, 3rd gets 1. Each family's points (both spouses together) decide its share.",
      "The family in last place inherits nothing; the other three split the £12 million by points. Suspicion votes later in the night take points away.",
      `This is round ${b.round} of 4. Every round adds to a running total, so the standings can change all night; the final total decides the inheritance.`,
      "Your vote is secret, and you can change it until the host closes the ballot."] }
    : { title: `Suspicion ballot ${b.round}`, note: "Rank your three prime suspects in Silas's murder. Every point of suspicion costs the accused one point of influence." };
}

function hollowAction(h) {
  const a = h.action;
  const others = h.others.map((o) => [o.id, o.name]);
  switch (a.kind) {
    case "paused": return el("p", { class: "paused" }, "The game is paused. Watch the screen.");
    case "watch": return [
      h.script.text && el("p", {}, h.script.text),
      el("p", { class: "muted" }, h.step === "lobby" ? "Read your dossier while you wait. The evening begins soon." : "Watch the screen and listen."),
      h.step === "reunion" && el("p", {}, el("a", { href: "#dossier" }, "Your introduction is in your dossier: read it aloud when it's your turn.")),
    ];
    case "polls": {
      const chosen = Object.fromEntries(a.polls.map((p) => [p.id, draft(`poll-${p.id}`, a.answers[p.id] || "")]));
      const done = a.polls.every((p) => a.answers[p.id]);
      const changed = a.polls.some((p) => chosen[p.id] !== (a.answers[p.id] || ""));
      const complete = a.polls.every((p) => chosen[p.id]);
      return [
        el("p", {}, el("strong", {}, "Vote for the real person, not their character!")),
        el("p", { class: "small muted" }, "Answer privately. Results go on the screen. No points, just glory."),
        a.polls.map((p) => el("label", { class: "h-field" }, el("span", {}, p.text),
          select(`poll-${p.id}`, [[h.me, "Me"], ...others], "Choose…", a.answers[p.id] || ""))),
        el("button", { class: "wide", disabled: hBusy || !complete || (done && !changed),
          onclick: () => hAct("polls", { answers: chosen }, a.polls.map((p) => `poll-${p.id}`)) },
        done ? (changed ? "Update my answers" : "Answers submitted ✓") : "Submit my answers"),
        !complete && el("p", { class: "small muted" }, "Answer all the questions to submit."),
        done && !changed && el("p", { class: "ok-text small" }, "Submitted. You can change your answers until the host closes the poll."),
      ];
    }
    case "ballots": return a.ballots.map((b) => {
      const key = `${b.kind}-${b.round}`;
      const saved = a.mine[key];
      const lab = ballotLabel(b);
      const picks = [0, 1, 2].map((i) => draft(`${key}-${i}`, saved ? saved[i] : ""));
      return el("div", { class: "h-ballot" },
        el("h3", {}, lab.title),
        (Array.isArray(lab.note) ? lab.note : [lab.note]).map((n) => el("p", { class: "small muted" }, n)),
        ["1st (+3)", "2nd (+2)", "3rd (+1)"].map((label, i) => el("label", { class: "h-field" }, el("span", {}, label),
          select(`${key}-${i}`, b.kind === "inheritance" ? others.filter(([id]) => id !== h.dossier.spouse_id) : others,
            "Choose…", saved ? saved[i] : ""))),
        el("button", { class: "wide", disabled: hBusy, onclick: () => hAct("ballot", { kind: b.kind, round: b.round, picks }, [0, 1, 2].map((i) => `${key}-${i}`)) },
          saved ? "Change my vote" : "Cast my vote"),
        saved && el("p", { class: "ok-text small" }, `Voted: ${saved.map((p) => nameOf(h, p)).join(", ")}. You can change it until the ballot closes.`));
    });
    case "deals": return dealForm(h, a);
    case "interrogations": return interrogationAction(h, a);
    case "investigations": return investigationAction(h, a);
    case "hotseat": return hotseatAction(h, a);
    case "wagers": return [
      el("p", {}, "The accused: ", el("strong", {}, a.nominee || "—")),
      el("p", { class: "small muted" }, `Wager GUILTY or NOT GUILTY, up to ${money(a.limit)}. Losing stakes are shared among the winners.`),
      a.mine && el("p", { class: "ok-text small" }, `Your wager: ${a.mine.side === "guilty" ? "GUILTY" : "NOT GUILTY"}, ${money(a.mine.amount)}.`),
      amountInput("wager", a.limit),
      el("div", { class: "row" },
        el("button", { class: "grow", onclick: () => hAct("wager", { side: "guilty", amount: parseInt(draft("wager", "0"), 10) || 0 }) }, "GUILTY"),
        el("button", { class: "grow quiet", onclick: () => hAct("wager", { side: "not_guilty", amount: parseInt(draft("wager", "0"), 10) || 0 }) }, "NOT GUILTY")),
    ];
    case "verdict": return [
      el("p", {}, "The accused: ", el("strong", {}, a.nominee || "—")),
      el("p", { class: "small muted" }, "Four YES votes of eight convict, and the accused's family is disinherited, guilty or not."),
      a.voted ? el("p", { class: "ok-text" }, `You voted ${a.voted.toUpperCase()}.`)
        : el("div", { class: "row" },
          el("button", { class: "grow danger", onclick: () => confirm("Vote YES, guilty? You can't change it.") && hAct("vote", { yes: true }) }, "YES, guilty"),
          el("button", { class: "grow quiet", onclick: () => confirm("Vote NO? You can't change it.") && hAct("vote", { yes: false }) }, "NO")),
    ];
    case "reveal": return [
      el("p", {}, a.eliminated ? "Your family has been disinherited." : `Your family inherits ${money(a.family_payout)}.`),
      el("p", {}, a.correct ? "The tribunal judged rightly. The spirits are free." : "The tribunal got it wrong. The spirits remain."),
    ];
    default: return null;
  }
}

function dealForm(h, a) {
  const kinds = { protect: "Protect me: pay them not to interrogate me", protect_couple: "Protect me & my spouse",
    force: "Force an interrogation: pay them to ask a set question", bribe: `Buy an inheritance vote (ballot ${a.round})` };
  const kind = draft("dk", a.allowed[0]);
  const everyone = h.others.map((o) => [o.id, o.name]);
  const fam = everyone.filter(([id]) => id !== h.dossier.spouse_id);       // deals are with other families
  const terms = {};
  const extra = [];
  if (kind === "force") {
    const track = draft("dtrack", "silas");
    extra.push(el("label", { class: "h-field" }, el("span", {}, "They must question"), select("dtarget", [[h.me, "Me"], ...everyone].filter(([v]) => v !== draft("drec")), "Choose…")),
      el("label", { class: "h-field" }, el("span", {}, "Asking"), select("dqid", a.questions[track].map((q) => [q.id, q.text]), "Choose…")));
    Object.assign(terms, { target: draft("dtarget"), track, qid: draft("dqid") });
  }
  if (kind === "bribe") {
    extra.push(el("label", { class: "h-field" }, el("span", {}, "They must rank"), select("dsubject", [[h.me, "Me"], ...everyone], "Choose…")),
      el("label", { class: "h-field" }, el("span", {}, "In position"), select("dpos", [["1", "1st (+3)"], ["2", "2nd (+2)"], ["3", "3rd (+1)"]], "Choose…")));
    Object.assign(terms, { subject: draft("dsubject"), position: parseInt(draft("dpos"), 10) });
  }
  const incoming = h.deals.filter((d) => !d.mine && d.status === "offered");
  return [
    el("p", { class: "small muted" }, `Your family can still promise up to ${money(h.available)}. Promises can be broken; the app checks afterwards.`),
    incoming.length > 0 && el("div", { class: "h-offers" }, el("h3", {}, "Offers to you"),
      incoming.map((d) => el("div", { class: "h-offer" },
        el("p", {}, d.summary, " ", el("strong", {}, money(d.amount))),
        el("div", { class: "row" },
          el("button", { class: "grow small", onclick: () => hAct("decide", { id: d.id, accept: true }) }, "Accept"),
          el("button", { class: "grow small quiet", onclick: () => hAct("decide", { id: d.id, accept: false }) }, "Reject"))))),
    el("h3", {}, "Make an offer"),
    a.allowed.length > 1 && el("label", { class: "h-field" }, el("span", {}, "Deal"), select("dk", a.allowed.map((k) => [k, kinds[k]]), "Choose…", a.allowed[0])),
    a.allowed.length === 1 && el("p", {}, kinds[kind]),
    el("label", { class: "h-field" }, el("span", {}, "With"), select("drec", fam, "Choose…")),
    extra,
    amountInput("damt", h.available),
    el("button", { class: "wide", disabled: hBusy, onclick: () => hAct("offer", {
      kind, recipient: draft("drec"), amount: parseInt(draft("damt", "0"), 10) || 0, terms,
    }, ["drec", "damt", "dtarget", "dqid", "dsubject", "dpos"]) }, "Send offer"),
    el("p", { class: "small" }, el("a", { href: "#deals" }, "See all your deals")),
  ];
}

function interrogationAction(h, a) {
  const out = [];
  if (a.questioned) {
    const q = a.questioned;
    out.push(el("div", { class: "questioned" },
      el("p", { class: "eyebrow" }, `${q.by} is questioning you, out loud`),
      el("p", { class: "spoken" }, `“${q.question}”`),
      el("p", { class: "small muted" }, "Choose your answer, then say it to the room. Nobody sees which you chose, but the Truth & Lies board will."),
      el("button", { class: "choice", onclick: () => hAct("answer", { choice: "truth" }) }, el("span", {}, el("strong", {}, "The truth: "), q.truth)),
      el("button", { class: "choice", onclick: () => confirm("Lie? The board will show you LIED.") && hAct("answer", { choice: "lie" }) }, el("span", {}, el("strong", {}, "A lie: "), q.lie))));
  }
  if (a.my_turn) {
    const target = draft("itarget");
    const track = draft("itrack", "silas");
    const used = new Set([...a.taken, ...a.reserved].map((x) => x.join("|")));
    const qs = a.questions[track].filter((q) => !used.has(`${target}|${track}|${q.id}`));
    out.push(el("div", { class: "h-turn" },
      el("p", { class: "eyebrow" }, "Your turn to interrogate"),
      el("label", { class: "h-field" }, el("span", {}, "Who"), select("itarget", h.others.map((o) => [o.id, o.name]), "Choose…")),
      el("label", { class: "h-field" }, el("span", {}, "Question"), select("iqid", qs.map((q) => [q.id, q.text]), "Choose…")),
      el("button", { class: "wide", disabled: !target || !draft("iqid") || hBusy,
        onclick: () => hAct("interrogate", { target, track, qid: draft("iqid") }, ["itarget", "iqid"]) }, "Ask, out loud")));
  }
  if (!a.questioned && !a.my_turn) {
    out.push(el("p", {}, a.done ? "All interrogations are done." : `${a.asker} is interrogating. Listen closely.`));
  }
  out.push(el("details", { class: "small" }, el("summary", {}, "Order and answers so far"),
    el("p", { class: "muted" }, a.order.join(" → ")),
    el("ul", {}, a.log.map((r) => el("li", {}, `${r.asker} → ${r.target}: “${r.question}” `, r.answer ? el("em", {}, `“${r.answer}”`) : "…")))));
  return out;
}

function investigationAction(h, a) {
  const out = [];
  if (!a.mine) {
    const target = draft("vtarget");
    const taken = new Set(a.taken.map((x) => x.join("|")));
    const cats = a.categories.filter((c) => !taken.has(`${target}|${c.id}`));
    const spouseName = me.hollow.dossier.spouse;
    out.push(el("p", { class: "small muted" }, `One investigation per couple. Agree it with ${spouseName} first.`),
      el("label", { class: "h-field" }, el("span", {}, "Investigate"), select("vtarget",
        h.others.filter((o) => o.id !== me.hollow.dossier.spouse_id).map((o) => [o.id, o.name]), "Choose…")),
      el("label", { class: "h-field" }, el("span", {}, "Looking into"), select("vcat", cats.map((c) => [c.id, `${c.name}: ${c.blurb}`]), "Choose…")),
      el("button", { class: "wide", disabled: !target || !draft("vcat") || hBusy,
        onclick: () => hAct("investigate", { target, category: draft("vcat") }, ["vtarget", "vcat"]) }, "Begin investigation"));
  } else {
    const m = a.mine;
    out.push(el("p", {}, `Your family is investigating ${m.target}: ${m.category}.`));
    if (m.status === "waiting") {
      out.push(el("p", { class: "muted" }, `${m.target} has been told what you are looking into (not who you are) and may try to buy your silence.`),
        m.can_open && el("button", { class: "wide", onclick: () => hAct("inv_decide", { action: "open" }) }, "They're stalling: open the file"));
    }
    if (m.status === "offered") {
      out.push(el("p", {}, `${m.target} offers ${money(m.offer)} for you to drop it. If you accept, you never see the evidence.`),
        el("div", { class: "row" },
          el("button", { class: "grow small", onclick: () => hAct("inv_decide", { action: "accept" }) }, "Take the money"),
          el("button", { class: "grow small quiet", onclick: () => hAct("inv_decide", { action: "reject" }) }, "Refuse, open it")),
        amountInput("vcounter"),
        el("button", { class: "wide quiet", onclick: () => hAct("inv_decide", { action: "counter", amount: parseInt(draft("vcounter", "0"), 10) || 0 }, ["vcounter"]) }, "Counter-offer (once)"));
    }
    if (m.status === "countered") out.push(el("p", { class: "muted" }, `You asked for ${money(m.counter)}. Waiting for their answer.`));
    if (m.status === "suppressed") out.push(el("p", {}, "You were paid to drop it. The file stays closed."));
    if (m.status === "delivered") out.push(el("p", {}, el("a", { href: "#clues" }, "The file is open: read it in Private Intelligence.")));
  }
  for (const r of a.about_me) {
    out.push(el("div", { class: "questioned" },
      el("p", { class: "eyebrow" }, "Someone is investigating you"),
      el("p", {}, `They are looking into: ${r.category}.`),
      r.status === "waiting" ? [
        el("p", { class: "small muted" }, "Offer money for them to drop it, or let them see it."),
        amountInput(`hush-${r.family}`, h.available),
        el("div", { class: "row" },
          el("button", { class: "grow small", onclick: () => hAct("inv_target", { family: r.family, action: "offer", amount: parseInt(draft(`hush-${r.family}`, "0"), 10) || 0 }) }, "Offer money"),
          el("button", { class: "grow small quiet", onclick: () => hAct("inv_target", { family: r.family, action: "decline" }) }, "Let them look")),
      ] : [
        el("p", {}, `They want ${money(r.counter)} instead.`),
        el("div", { class: "row" },
          el("button", { class: "grow small", onclick: () => hAct("inv_target", { family: r.family, action: "accept" }) }, "Pay it"),
          el("button", { class: "grow small quiet", onclick: () => hAct("inv_target", { family: r.family, action: "reject" }) }, "Refuse")),
      ]));
  }
  return out;
}

function hotseatAction(h, a) {
  if (!a.in_seat) return el("p", {}, `${a.pair.join(" and ")} are in the hot seat. Watch the screen.`);
  if (a.chosen) return el("p", { class: "ok-text" }, "Your choice is in. The evidence appears on the screen when both have chosen.");
  const pick = h.others.filter((o) => !a.pair_ids.includes(o.id)).map((o) => [o.id, o.name]);
  return [
    el("p", {}, "You're in the hot seat. Choose in secret:"),
    el("button", { class: "wide", onclick: () => confirm("Defend yourself?") && hAct("hotseat", { choice: "defend" }) }, "DEFEND YOURSELF: genuine evidence in your favour"),
    el("p", { class: "center muted" }, "or"),
    el("label", { class: "h-field" }, el("span", {}, "Accuse"), select("haccuse", pick, "Choose…")),
    el("button", { class: "wide danger", disabled: !draft("haccuse"),
      onclick: () => confirm("Accuse them?") && hAct("hotseat", { choice: "accuse", target: draft("haccuse") }) }, "ACCUSE SOMEONE ELSE: genuine evidence against them"),
    el("p", { class: "small muted" }, "The evidence goes on the TV. Then you have 90 seconds to make your case."),
  ];
}

function hollowDossier(h) {
  const d = h.dossier;
  const c = me.character;
  const card = c.card || {};
  return [
    h.envelope && el("section", { class: "card h-envelope" },
      el("p", { class: "eyebrow" }, "Silas's envelope · for your eyes only"),
      el("h2", {}, h.envelope.title),
      el("p", {}, h.envelope.text),
      el("p", {}, el("strong", {}, "Silas has proof: "), h.envelope.proof),
      el("p", { class: "small muted" }, "For now, this is yours alone. Whether it stays that way depends on how the evening unfolds.")),
    d.truth && el("section", { class: "card h-truth" },
      el("h2", {}, "You are the murderer"),
      el("p", { class: "note" }, "Only you can see this. Never show this screen to anyone."),
      el("h3", {}, "Why"), el("p", {}, d.truth.why),
      el("h3", {}, "Tonight"), el("p", {}, d.truth.tonight),
      el("h3", {}, "Your story"), el("p", {}, d.truth.cover),
      el("p", {}, el("strong", {}, d.truth.goal)),
      el("p", { class: "small muted" }, "Nobody acts out the killing: the screen and lights do it. You just have to keep your nerve.")),
    el("section", { class: "card char-card" },
      card.photo ? el("img", { class: "portrait", src: card.photo, alt: `Portrait of ${c.name}` }) : null,
      el("p", { class: "eyebrow" }, c.tagline),
      el("h2", { class: "char-name" }, c.name),
      card.occupation && el("p", { class: "char-occupation" }, card.occupation),
      el("p", {}, `${d.family} · married to ${d.spouse}`)),
    me.background && el("section", { class: "card" }, el("h2", {}, me.story_name || "The story so far"),
      me.background.map((para, i) => el("p", { class: i === 0 ? "eyebrow" : "" }, para))),
    el("section", { class: "card" }, el("h2", {}, "Your introduction"),
      el("p", { class: "note" }, "Read or perform this to the room in Phase 1 (about 90 seconds). Ham it up."),
      el("p", { class: "spoken" }, d.intro)),
    card.personality && el("section", { class: "card" }, el("h2", {}, "Who you are"),
      el("p", {}, el("strong", {}, "Personality: "), card.personality),
      card.how_met && [el("p", { class: "eyebrow" }, `How you met ${d.spouse}`), el("p", {}, card.how_met)],
      card.costume && el("p", { class: "small muted" }, `Dress: ${card.costume}`)),
    el("section", { class: "card secret" }, el("h2", {}, `Your darkest secret: ${d.secret.title}`),
      el("p", { class: "note" }, "Only you can see this."),
      el("p", {}, d.secret.text),
      el("p", { class: "muted" }, el("em", {}, `What you tell yourself: ${d.secret.excuse}`)),
      d.silas_holds && el("p", {}, el("strong", {}, "Silas has proof: "), d.silas_holds)),
    el("section", { class: "card" }, el("h2", {}, "Why you need Silas alive"), el("p", {}, d.need_alive)),
    el("section", { class: "card" }, el("h2", {}, "A scandal you know"),
      el("p", {}, d.scandal.text), el("p", { class: "small muted" }, "Use it for leverage, comedy or revenge. It has nothing to do with the murders.")),
    el("section", { class: "card" }, el("h2", {}, "What you did last night"),
      el("p", {}, d.last_night),
      h.seen_by_someone && el("p", { class: "warn" }, "Someone saw you. You don't know who.")),
    h.witnessed && el("section", { class: "card" }, el("h2", {}, "What you saw last night"), el("p", {}, h.witnessed),
      el("p", { class: "small muted" }, "Reveal it, hide it, or sell it. Your choice.")),
    h.blackout && el("section", { class: "card" }, el("h2", {}, "In the blackout"), el("p", {}, h.blackout)),
    el("section", { class: "card" }, el("h2", {}, "Your marks"),
      el("p", { class: "small" }, `Tonight, in the Covenant register: ${d.traits_1892.hand}-handed, ${d.traits_1892.side} arc, ${d.traits_1892.sigil} sigil.`)),
  ];
}

function hollowMemories(h) {
  return [
    el("p", { class: "muted small" }, "Summer, 1867. Eleanor Vale's death was ruled an accident, and it was one. But every one of you played a part, and Silas blames you all. This is yours. Share it, hide it, or rewrite it: it's up to you."),
    h.memories.map((m) => el("section", { class: "card" }, el("p", {}, m))),
  ];
}

function evidenceCard(e) {
  return el("section", { class: "card h-evidence" }, el("h2", {}, e.title), e.text.split("\n").map((line) => el("p", {}, line)));
}

function hollowEvidence(h) {
  return h.evidence.length ? h.evidence.slice().reverse().map(evidenceCard)
    : el("section", { class: "card" }, el("p", { class: "muted" }, "Nothing has been made public yet."));
}

function hollowClues(h) {
  return h.clues.length ? h.clues.map(evidenceCard)
    : el("section", { class: "card" }, el("p", { class: "muted" }, "Your family hasn't opened an investigation file yet. That happens in Phase 4."));
}

const DEAL_STATUS = { offered: "Waiting", accepted: "Agreed", rejected: "Rejected", expired: "Lapsed", honored: "Honoured", broken: "BROKEN" };

function hollowDeals(h) {
  if (!h.deals.length) return el("section", { class: "card" }, el("p", { class: "muted" }, "No deals yet. The first deal window opens before dinner interrogations."));
  return el("section", { class: "card" }, el("ul", { class: "plain h-deals" }, h.deals.map((d) => el("li", { class: `deal-${d.status}` },
    el("p", {}, d.summary), el("p", { class: "small" }, `${money(d.amount)} · ${DEAL_STATUS[d.status] || d.status}`)))),
  el("p", { class: "small muted" }, "Payments are provisional: if either family is disinherited, the deal is void."));
}

// ------------------------------------------------------------------- host

function hollowHostPanel(s) {
  const h = s.hollow;
  const idx = h.steps.findIndex((x) => x.id === h.step);
  const cur = h.steps[idx];
  const next = h.steps[idx + 1];
  const prev = h.steps[idx - 1];
  const go = (st, label) => confirm(label) && hostAction("POST", "/api/host/hollow/step", { step: st.id }, "hstep");
  if (!h.ready) {
    return el("section", { class: "card h-host" }, el("h2", {}, "The Hollow Order"),
      el("p", {}, "Release the characters to begin. The app secretly chooses the murderer at that moment. Nobody, including you, is told unless it's them."));
  }
  return el("section", { class: "card h-host" },
    el("p", { class: "eyebrow" }, cur.phase),
    el("h2", {}, cur.name),
    hTimer(h.timer_end),
    h.paused && el("p", { class: "paused" }, "Paused"),
    Object.keys(h.counts).length > 0 && el("ul", { class: "plain small" },
      Object.entries(h.counts).map(([k, v]) => el("li", {}, `${k}: ${v} of ${h.total}`))),
    h.interrogation && el("p", { class: "small" }, h.interrogation.done ? "All interrogations done."
      : `Turn ${h.interrogation.turn + 1} of ${h.interrogation.order.length}: ${h.interrogation.asker}`,
    !h.interrogation.done && el("button", { class: "small quiet", onclick: () => confirm("Skip this turn?") && hostAction("POST", "/api/host/hollow/skip-turn", {}) }, "Skip turn")),
    next && el("button", { class: "wide", disabled: !!busy, onclick: () => go(next, `Move on to: ${next.name}?`) }, `Next: ${next.name}`),
    el("div", { class: "row" },
      prev && el("button", { class: "small quiet", disabled: !!busy, onclick: () => go(prev, `Go back to: ${prev.name}? (Reopens it.)`) }, "‹ Back"),
      el("button", { class: "small quiet", onclick: () => hostAction("POST", "/api/host/hollow/pause", { paused: !h.paused }) }, h.paused ? "Resume" : "Pause"),
      el("button", { class: "small danger", onclick: () => confirm("Safety stop: pause everything and show 'lights up' on the TV?") && hostAction("POST", "/api/host/hollow/cue", { cue: "safety" }) }, "Safety stop")),
    el("p", { class: "eyebrow" }, "Timer"),
    el("div", { class: "row" }, [2, 5, 10, 15].map((m) => el("button", { class: "small quiet", onclick: () => hostAction("POST", "/api/host/hollow/timer", { minutes: m }) }, `${m} min`)),
      el("button", { class: "small quiet", onclick: () => hostAction("POST", "/api/host/hollow/timer", { minutes: 0 }) }, "Clear")),
    el("p", { class: "eyebrow" }, "Sound cues on the TV"),
    el("div", { class: "row wrap" }, h.cues.map((c) => el("button", { class: "small quiet", onclick: () => hostAction("POST", "/api/host/hollow/cue", { cue: c }) }, c))),
    el("details", { class: "small" }, el("summary", {}, "The whole evening"),
      el("ol", {}, h.steps.map((x) => el("li", { class: x.id === h.step ? "h-current" : "" }, `${x.name}`)))),
    el("details", { class: "small" }, el("summary", {}, "Rehearsal tools"),
      el("p", { class: "muted" }, "Start the evening again from the beginning: clears votes, deals and answers, and secretly picks a NEW murderer, so a rehearsal never spoils the real party."),
      el("button", { class: "small danger", onclick: () => confirm("Restart the whole evening? All votes, deals and answers are wiped.") && hostAction("POST", "/api/host/hollow/restart", {}) }, "Restart the evening")),
    el("p", { class: "small muted" }, "You never see the murderer, votes or deals. Your own role is on your guest link."));
}

// -------------------------------------------------------------------- TV

const hSeen = new Set();
let hLastStep = null;

SOUNDS.ghost = () => { [196, 233, 277].forEach((f, i) => tone(f, i * 0.4, 3, "sine", 0.12)); tone(98, 0, 4, "triangle", 0.15); };
SOUNDS.scream = () => { tone(900, 0, 0.9, "sawtooth", 0.15); tone(1200, 0.05, 0.8, "sawtooth", 0.1); };
SOUNDS.bell = () => { tone(440, 0, 2.5, "sine", 0.3); tone(880, 0, 2, "sine", 0.1); };
SOUND_LENGTH.ghost = 3000;
SOUND_LENGTH.scream = 1000;
SOUND_LENGTH.bell = 1500;

function hollowRoomStart(view) {
  view.events.forEach((e) => hSeen.add(e.seq));
  hLastStep = view.step;
  document.body.classList.toggle("blackout", view.step === "blackout");
}

function hollowRoomTick(view) {
  const fresh = view.events.slice().reverse().filter((e) => !hSeen.has(e.seq));
  for (const e of fresh) {
    hSeen.add(e.seq);
    hollowAnnounce(view, e);
  }
  document.body.classList.toggle("blackout", view.step === "blackout");
  renderHollowTv(view);
}

function hollowAnnounce(view, e) {
  if (e.kind === "step") {
    if (e.step !== view.step) return;
    stopSpeaking();                    // a new step cuts off whatever the last one was saying
    droneOff();
    if (e.step === "blackout") { strikeLightning(true); if (audioCtx) thunder(0, 5, 1); return; }
    if (view.sequence) return runSequence(view);
    const sound = e.step === "murder" ? "murder" : (["ghost", "revelations", "bridge"].includes(e.step) ? "ghost" : "phase");
    // one part per paragraph, so the voice pauses naturally between them
    const spoken = [view.title, ...(view.silas || view.text || "").split(/\n\s*\n/)].map((x) => x.trim()).filter(Boolean);
    // Read the full script aloud; show only the short screen line
    if (spoken.length) announce(sound, spoken, { title: view.title, text: view.screen || view.text });
  } else if (e.kind === "question" || e.kind === "answer") {
    const r = (view.interrogation || { log: [] }).log.find((x) => x.turn === e.turn);
    if (!r) return;
    if (e.kind === "question") {
      announce("chime", [`${r.target}, you are being questioned by ${r.asker}.`, r.question],
        { title: `${r.asker} questions ${r.target}`, text: `“${r.question}”` });
    } else if (r.answer) {
      announce("chime", [`${r.target} answers.`, r.answer], { title: `${r.target} answers`, text: `“${r.answer}”` });
    }
  } else if (e.kind === "hotseat") {
    const cards = (view.hotseat || {}).cards || [];
    cards.forEach((c) => announce("bell", [`${c.who} chooses to ${c.choice === "defend" ? "defend themselves" : `accuse ${c.target}`}.`, c.text],
      { title: c.title, text: c.text }));
  } else if (e.kind === "cue") {
    if (e.cue === "thunder") { strikeLightning(false); if (audioCtx) thunder(0, 4, 0.9); }
    else if (e.cue === "lightning") strikeLightning(true);
    else if (e.cue === "ghost") announce("ghost", "Death does not release you from the Covenant.", { title: "Death does not release you from the Covenant" });
    else if (e.cue === "safety") announce("bell", "Lights up, please. The game is paused.", { title: "Lights up", text: "The game is paused." });
    else announce(e.cue, "", { title: "" });
  } else if (e.kind === "pause" || e.kind === "resume") {
    announce("phase", e.kind === "pause" ? "The game is paused." : "The game resumes.");
  }
}

// ---- timed shows (e.g. the Covenant, part 2): lines, sound effects and visuals, in order

let seqRunning = false;

function seqLayer() {
  if (!roomShell) return null;
  if (!roomShell.seq) {
    roomShell.seq = el("div", { class: "h-seq", "aria-live": "polite" });
    app.append(roomShell.seq);
  }
  return roomShell.seq;
}

function clearSeq() {
  if (roomShell && roomShell.seq) { roomShell.seq.remove(); roomShell.seq = null; }
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  kids.forEach((k) => n.append(typeof k === "string" ? document.createTextNode(k) : k));
  return n;
}

function crest() {
  const gold = "#d9a441";
  return svg("svg", { viewBox: "0 0 200 200", class: "h-crest", "aria-label": "The Hollow Order crest" },
    svg("circle", { cx: 100, cy: 100, r: 92, fill: "none", stroke: gold, "stroke-width": 3 }),
    svg("circle", { cx: 100, cy: 100, r: 80, fill: "#0b0710", stroke: gold, "stroke-width": 1 }),
    svg("path", { d: "M100 40 a22 22 0 0 1 12 40 l10 62 h-44 l10 -62 a22 22 0 0 1 12 -40 z", fill: "none", stroke: gold, "stroke-width": 3 }),
    svg("path", { d: "M58 120 L142 120 M66 132 L134 132", stroke: gold, "stroke-width": 1.5 }),
    svg("text", { x: 100, y: 165, "text-anchor": "middle", fill: gold, "font-size": 11, "letter-spacing": 3, "font-family": "Georgia, serif" }, "THE HOLLOW ORDER"),
    svg("text", { x: 100, y: 30, "text-anchor": "middle", fill: gold, "font-size": 9, "letter-spacing": 2, "font-family": "Georgia, serif" }, "MDCCCLXVII"));
}

function seqShow(view, item) {
  const layer = seqLayer();
  if (!layer) return;
  const kind = item.show;
  layer.className = `h-seq ${kind}`;
  const kids = [];
  if (kind === "shadow" || kind === "warm" || kind === "glitch") kids.push(el("p", { class: "h-seq-name" }, el("span", {}, "Voice of Silas Vale"), el("span", { class: "h-seq-role" }, "(The Butler)")));
  if (kind === "money") kids.push(el("p", { class: "h-seq-money" }, "£12,000,000"));
  if (kind === "notice") kids.push(el("p", { class: "h-seq-text" }, item.text), item.sub && el("p", { class: "h-seq-sub" }, item.sub));
  if (kind === "rules") {
    kids.push(el("div", { class: "h-seq-rules" },
      el("h2", {}, "The Inheritance"),
      el("ol", {}, el("li", {}, "Rank three fellow guests: 1st = 3 points, 2nd = 2, 3rd = 1"),
        el("li", {}, "Not yourself. Not your spouse."),
        el("li", {}, "Points decide each family's share of £12,000,000"),
        el("li", { class: "h-seq-warn" }, "Last place receives nothing"))));
  }
  if (kind === "fire") kids.push(el("img", { class: "h-seq-fire", src: "/static/mansion.webp", alt: "" }));
  if (kind === "portraits") {
    kids.push(el("div", { class: "h-seq-portraits" }, (view.portraits || []).map((p) => el("figure", {},
      p.photo ? el("img", { src: p.photo, alt: p.name }) : el("div", { class: "portrait placeholder" }),
      el("figcaption", {}, p.name)))));
    if (item.text) kids.push(el("p", { class: "h-seq-line" }, item.text));
  }
  if (kind === "crest") kids.push(crest(), item.text && el("p", { class: "h-seq-text" }, item.text),
    item.sub && el("p", { class: "h-seq-sub" }, item.sub));
  layer.replaceChildren(...kids.filter(Boolean));
}

function seqFinal(view) {
  // What the screen shows after the show ends (or if the TV page is reloaded mid-step)
  const last = [...view.sequence].reverse().find((x) => x.show);
  if (last) seqShow(view, last);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runSequence(view) {
  const gen = speechGen;
  seqRunning = true;
  speaking = true;
  showBox(null);
  const items = view.sequence;
  const lines = items.filter((x) => x.say).map((x) => x.say);
  const voiced = roomSoundOn && room && room.voice && audioCtx;
  let fetched = 0;
  let lineNo = 0;
  const prefetch = () => { if (voiced) while (fetched < lines.length && fetched < lineNo + 3) elevenAudio(lines[fetched++]).catch(() => null); };
  prefetch();
  try {
    for (const item of items) {
      if (gen !== speechGen) return;
      if (item.show) seqShow(view, item);
      if (item.sfx && roomSoundOn && audioCtx) SFX[item.sfx]();
      if (item.wait) await sleep(item.wait);
      if (item.say) {
        lineNo++;
        prefetch();
        if (roomSoundOn && audioCtx && claimSpeaker()) {
          await new Promise((res) => speakParts([item.say], res));
        } else {
          await sleep(1500 + item.say.length * 55);   // no sound: keep the pictures in time
        }
      }
    }
  } finally {
    if (gen === speechGen) { seqRunning = false; speaking = false; }
  }
}

function noise(seconds, filterHz, volume, shape) {
  const n = Math.floor(audioCtx.sampleRate * seconds);
  const buffer = audioCtx.createBuffer(1, n, audioCtx.sampleRate);
  const d = buffer.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * shape(i / n);
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  const f = audioCtx.createBiquadFilter();
  f.type = "lowpass";
  f.frequency.value = filterHz;
  const g = audioCtx.createGain();
  g.gain.value = volume;
  src.connect(f).connect(g).connect(audioCtx.destination);
  src.start();
}

// Low, ominous music under the second half of the Covenant: two detuned low notes through a slowly
// breathing filter. Stopped by "drone_off" or by any change of step.
let drone = null;

function droneOn() {
  if (drone || !audioCtx) return;
  const gain = audioCtx.createGain();
  gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.06, audioCtx.currentTime + 4);
  setVoiceVolume(1.8);                 // keep Silas clearly above the music
  const filter = audioCtx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 260;
  const lfo = audioCtx.createOscillator();
  const lfoGain = audioCtx.createGain();
  lfo.frequency.value = 0.08;
  lfoGain.gain.value = 120;
  lfo.connect(lfoGain).connect(filter.frequency);
  const oscs = [55, 55.6, 82.4].map((f) => {
    const o = audioCtx.createOscillator();
    o.type = "sawtooth";
    o.frequency.value = f;
    o.connect(filter);
    o.start();
    return o;
  });
  filter.connect(gain).connect(audioCtx.destination);
  lfo.start();
  drone = { gain, oscs: [...oscs, lfo] };
}

function droneOff() {
  if (!drone || !audioCtx) return;
  const { gain, oscs } = drone;
  drone = null;
  setVoiceVolume(1);
  const t = audioCtx.currentTime;
  gain.gain.cancelScheduledValues(t);
  gain.gain.setValueAtTime(Math.max(gain.gain.value, 0.0001), t);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 2.5);
  oscs.forEach((o) => o.stop(t + 2.6));
}

const SFX = {
  drone_on: droneOn,
  drone_off: droneOff,
  rumble: () => thunder(0, 6, 0.7),
  fire: () => {                          // crackling: a low roar with random pops
    noise(4, 900, 0.25, (t) => Math.min(1, t * 4) * (1 - t));
    for (let i = 0; i < 26; i++) setTimeout(() => noise(0.04, 3000, 0.5, (t) => 1 - t), Math.random() * 3800);
  },
  door: () => {                          // a heavy iron door slamming, with an echo
    noise(0.35, 400, 1.4, (t) => Math.pow(1 - t, 3));
    tone(55, 0, 0.6, "sine", 0.6);
    setTimeout(() => noise(1.2, 250, 0.35, (t) => Math.pow(1 - t, 2)), 180);
  },
};

function board(title, rows) {
  return el("div", { class: "h-board" }, el("h3", {}, title), el("ol", {}, rows));
}

function renderHollowTv(v) {
  if (!roomShell) return;
  if (v.sequence) { if (!seqRunning && !roomShell.seq) seqFinal(v); }
  else if (roomShell.seq) { seqRunning = false; clearSeq(); }
  if (!roomShell.hollow) {
    roomShell.hollow = el("div", { class: "h-tv" });
    app.append(roomShell.hollow);
  }
  const parts = [];
  const b = v.boards || {};
  const main = [];
  if (v.step === "reunion" && v.guests) main.push(el("ul", { class: "h-guests" }, v.guests.map((g) => el("li", {}, g.name, el("span", {}, ` · ${g.player}`)))));
  if (v.polls) {
    main.push(el("table", { class: "h-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Who is most likely to…"), el("th", {}, "Winner"), el("th", {}, "Votes"))),
      el("tbody", {}, v.polls.map((p) => el("tr", {},
        el("td", {}, p.text.replace(/^Who is most likely to /, "").replace(/\?$/, "").replace(/^./, (ch) => ch.toUpperCase())),
        el("td", { class: "h-win" }, p.winners.join(" & ")),
        el("td", { class: "h-num" }, String(p.votes)))))));
  }
  if (v.interrogation) {
    const i = v.interrogation;
    main.push(el("p", { class: "h-order" }, i.order.map((n, k) => el("span", { class: k === i.turn ? "now" : k < i.turn ? "past" : "" }, n))));
    const last = i.log[i.log.length - 1];
    if (last) {
      main.push(el("div", { class: "h-card" }, el("h3", {}, `${last.asker} questions ${last.target}`),
        el("p", {}, `“${last.question}”`),
        el("p", { class: "h-note" }, last.answer ? `${last.target}: “${last.answer}”` : `Waiting for ${last.target} to answer…`)));
    }
  }
  if (v.truth_board) {
    main.push(board("Truth & Lies", v.truth_board.map((r) => el("li", { class: `tb-${r.status.split(" ")[0].toLowerCase()}` }, `${r.name}: ${r.status}`))));
    main.push(board("The Ledger of Corruption", v.ledger.map((r) => el("li", {}, `${r.name}: paid ${money(r.paid)} · received ${money(r.received)}`))));
    if (v.ledger_notes.spender) main.push(el("p", { class: "h-note" }, `Biggest spender: ${v.ledger_notes.spender}. Biggest earner: ${v.ledger_notes.earner || "nobody"}. Silas is not impressed.`));
  }
  if (v.hotseat) {
    main.push(el("p", { class: "h-note" }, `In the hot seat: ${v.hotseat.pair.join(" and ")}`));
    v.hotseat.cards.forEach((c) => main.push(el("div", { class: "h-card" }, el("h3", {}, c.title), el("p", {}, c.text))));
  }
  if (v.nominee && v.step !== "reveal") main.push(el("p", { class: "h-accused" }, `The accused: ${v.nominee}`));
  if (v.step === "verdict") main.push(el("p", { class: "h-note" }, `${v.votes_cast} of 8 votes cast`));
  if (v.reveal) {
    const r = v.reveal;
    main.push(el("div", { class: "h-reveal" },
      el("div", { class: "h-recon" }, r.reconstruction.map((p) => el("p", {}, p))),
      el("div", {}, el("p", { class: "h-verdict" }, r.convicted
        ? (r.correct ? `The tribunal convicted ${r.convicted}. Correctly. Eleanor and Silas are free!` : `The tribunal convicted ${r.convicted}. Wrongly. The spirits remain in these walls.`)
        : `No conviction (${r.yes} of 8). The killer walks free, and the spirits remain.`),
      el("p", {}, `Disinherited: ${r.eliminated}`),
      board("The Inheritance", r.payouts.map((p) => el("li", {}, `${p.name}: ${money(p.amount)}`))))));
  }
  if (v.evidence && v.evidence.length && !v.reveal) {          // the evidence itself lives on the phones
    main.push(el("p", { class: "h-note" }, `New evidence on your phones: ${v.evidence.map((e) => e.title).join(" · ")}`));
  }
  parts.push(el("div", { class: "h-main" },
    el("p", { class: "h-phase" }, v.phase),
    v.title && el("h1", {}, v.title),
    (v.screen || v.text) && el("p", {}, v.screen || v.text),
    hTimer(v.timer_end),
    main));
  if ((b.families || b.suspicion) && !v.reveal) {
    parts.push(el("div", { class: "h-side" },
      b.families && board("Family Inheritance (provisional)", b.families.map((f) => el("li", {}, `${f.name} ${money(f.amount)}`))),
      b.suspicion && board("Murder Suspicion", b.suspicion.map((n) => el("li", {}, n)))));
  }
  roomShell.hollow.replaceChildren(...parts);
  roomShell.hud.replaceChildren(el("p", { class: "room-title" }, "The Hollow Order"),
    el("p", { class: "room-phase" }, v.step_name, v.paused ? " · Paused" : ""));
}
