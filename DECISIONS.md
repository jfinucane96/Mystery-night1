# Milestone slice: decisions and structure

Scope: host creates a game → guests join by invitation → casting assessment and
boundaries → host approves assignments → each guest sees a private character
dashboard. Everything else in the spec (AI dialogue, tokens, evidence, events,
lighting, scoring, production mystery) is deferred.

## Missing decisions (and the default chosen for this slice)

Each item is a gap or ambiguity in *Game Design Specification v0.1*. The default
is what the code does today; all are easy to revisit.

| # | Decision | Default taken | Why / how to change |
|---|----------|---------------|---------------------|
| D1 | Tech stack | Python 3.13 standard library + vanilla JS. Database: Postgres on **Supabase** when `DATABASE_URL` is set (needs psycopg), otherwise SQLite for local tests. Hosting: **Render**, deployed from GitHub. | Node/TypeScript isn't installed here; the spec lists TS only as a *candidate*. Supabase is used purely as a database. Tables live in a private `mystery` schema with row-level security on and no policies, so Supabase's browser API can't read secrets; the Python server is the only gatekeeper. |
| D2 | Accounts | Email + password accounts, PBKDF2-SHA256 (600k iterations), server-side sessions in an HttpOnly, SameSite=Lax cookie. | Spec says "hosting/account choices" are open. Magic links or SSO can replace `server/auth.py` later. No password reset or rate limiting yet. |
| D3 | Invitations | Host creates one single-use invite per guest (optional label such as "Alice"). Link format `/#/invite/<token>`. Only a SHA-256 hash of the token is stored. Valid for 30 days. The token goes in the URL fragment, so it never reaches server logs. | Spec doesn't define the invitation mechanism. |
| D4 | Host as player | The host can't accept an invitation to their own game. | Spec §1: the host sees the solution and doesn't compete. |
| D5 | Phase gating | `DRAFT` = inviting and assessments. "Recommend cast" moves to `CASTING`. "Approve" moves to `TRAINING`. Invitations and assessment edits are still allowed in `CASTING`, but approval revalidates everything. Pending invitations are closed on approval. | Spec says casting needs accepted invitations and completed assessments. Here, approval is blocked if any *accepted* guest hasn't finished; unanswered invitations don't block. |
| D6 | Assessment content | 12 scenario questions, 4 options each, scored 0/33/67/100 per spec §4. Stored as versioned *engine* content (`content/assessment/v1.json`), separate from any mystery package. Each answer saves immediately, so a refresh resumes. | Questions written for this slice; they need human review. |
| D7 | Boundaries | Each package content tag is `allowed` / `blocked` / `unspecified`. A **blocked** tag excludes the role. A tag marked `sensitive` in the package also needs an explicit `allowed`. Prep minutes are a hard limit. | Spec §3/§4: "unspecified does not imply permission for a sensitive role"; hard boundaries always win. |
| D8 | Stretch | Stretch preference `none` / `modest` / `high` gives a tolerance of 0 / 8 / 15 points before a "role asks more than you want" gap is penalized. It never affects hard constraints. | Spec: high tolerance allows only a *modest* stretch. |
| D9 | Casting score | Cost per pair = weighted dimension mismatch (shortfall ×1.5 after stretch tolerance, excess ×0.75) + 0.3 × \|involvement − role burden\| + 10 when the guest wants full acting help and the role has 2+ mandatory performance moments. Solved globally with the Hungarian algorithm. Required roles are filled first. | Spec §4 heuristic; the weights are a first guess, not validated. Code is in `server/casting.py`. |
| D10 | Overrides | The host picks a role for a guest; if another guest holds it, the two swap. If either guest doesn't fit their new role, the server refuses unless the host explicitly **forces** it. **Auto-assign necessary roles** fills required roles even when nobody fits, overriding as few guests as possible and choosing the most willing. The specific conflicts the host accepted are stored on the assignment and logged. Approval rejects any *new* conflict, e.g. a guest changed an answer after the override. | **Deliberate departure from spec §4** ("hard boundaries always win"), decided by the product owner on 2026-10-06: the host may override anything, including "No thanks". Guests are not asked to confirm. Revisit before strangers play. |
| D16 | Willingness score | Host-only, 0–100 per guest × character: questionnaire fit (100 − fit cost), × 0.1 per "No thanks", × 0.6 per "Not sure" on sensitive content, × 0.9 per "Not sure" on other content, × offered/needed prep. Labelled *fits*, *stretch* or *refused*. Never sent to guests. | Lets the host choose who to override. The multipliers are first guesses. |
| D11 | What the character dashboard shows | Public bio, private backstory, secrets, relationships, and onboarding briefing claims chosen by `disclosure_rules.json` (channel `onboarding`, phase gate `TRAINING`). The first view writes disclosure receipts (append-only, idempotent). | The spec doesn't list onboarding content. Delivering through disclosure rules keeps the knowledge model intact for later milestones. |
| D12 | What players see about each other | After approval, a public cast list: character name, public bio and the guest's display name. Never scores, tiers, boundaries or private role data. | Spec §3, §11. |
| D13 | Package immutability | A game records `package_id`, `content_version` and a SHA-256 of the package files at creation. If the files on disk later differ, the server refuses to serve that game's content. | Spec §12: immutable package version per session. Migrations are out of scope. |
| D14 | Test package size | `lemon-tart@1.1.0` (the default): 10 roles (1 required), 2–10 guests. Once the package's maximum number of guests have accepted, more acceptances are refused; the host can still create spare links. `1.0.0` (4 roles) is kept unchanged because existing games are locked to it. | Spec §1 working default is 10 guests. Three roles need a sensitive opt-in (killer, two romance), so the other 7 suit guests who leave sensitive content as "Not sure". |
| D15 | Build checklist | Not provided (only the spec was attached). The spec §13 "Required demonstration" list is used as the acceptance checks. | Send the checklist and I'll map it. |

### Still open for the product owner (not blocking this slice)
Guest count and date, game duration, hosting and account provider, data
retention and export/delete policy, whether the host can see assessment answers
(currently they can see *scores and boundaries* on the casting screen but not
the raw answers), and review of the assessment wording and casting weights.

## Project structure

```
server/                 application logic (no story content in here)
  app.py                HTTP server, routing, security headers, static files
  db.py                 SQLite schema and connection
  auth.py               password hashing, session cookies
  content.py            loads and validates packages; builds the *privacy projections*
  assessment.py         scoring (spec §4)
  casting.py            compatibility, cost model, Hungarian assignment
  store.py              game, invitation, player and assignment operations
  validate_content.py   CLI: python -m server.validate_content
content/                story and assessment content only (JSON, versioned)
  assessment/v1.json
  packages/lemon-tart/1.0.0/
    package.json        root manifest (spec §12)
    characters.json     roles + content tags (public and private parts separated)
    knowledge.json      knowledge items
    disclosure_rules.json
    timeline.json       canonical facts       (host-only)
    solution.json       killer/motive/method  (host-only)
web/                    browser client (static; receives only what the API returns)
  index.html  app.js  styles.css
tests/
  test_casting.py       unit tests for scoring and the assignment solver
  test_e2e.py           full flow over HTTP: host + 2 players, privacy checks, restart
data/                   SQLite database (created at runtime, git-ignored)
```

**Privacy rule:** the browser never filters secrets. Every API response is built
by an explicit projection in `server/content.py` (`public_role`,
`private_role_for_player`, `host_solution`). Only the projection for the caller's
verified relationship to the game is ever called. Content files are not under
the static web root.
