# Mystery Night: casting vertical slice (M02–M04)

First working flow from *Game Design Specification v0.1* §13:
**host creates a game → guests join by invitation → casting questionnaire and
boundaries → host reviews and approves assignments → each guest sees their
private character dashboard.**

Not included yet (deliberately): AI dialogue, question tokens, evidence/QR,
live events, lighting/audio, accusations/scoring, and the production mystery.
The bundled package *The Lemon Tart Affair* is a tiny fictional test package.

See [DECISIONS.md](DECISIONS.md) for the open decisions, the defaults chosen
and the project structure.

## Requirements

- Python 3.11+ (developed on 3.13). Windows: use `py`; macOS/Linux: use `python3` below.
- For Postgres/Supabase only: `py -m pip install -r requirements.txt` (installs psycopg).
  Local SQLite mode needs no packages.

## Run it

From this folder:

```bash
py -m server.app
```

Then open <http://localhost:8000>. The startup log says which database is in use:

- **No `.env` file**: SQLite at `data/mystery.db` (local testing).
- **`.env` with `DATABASE_URL`**: Postgres (Supabase). Tables are created automatically in
  the `mystery` schema on first start.

Settings (environment variables, or lines in `.env`): `DATABASE_URL`, `MM_PORT` (default 8000),
`MM_HOST` (default 127.0.0.1), `MM_DB` (SQLite path), `MM_PG_SCHEMA` (default `mystery`),
`MM_SECURE_COOKIES=1` (set when served over HTTPS). Hosts that set `PORT` are detected
automatically.

## Supabase

1. In Supabase, open your project → **Connect** (top bar) → **Session pooler** → copy the URI.
2. Copy `.env.example` to `.env` and paste the URI as `DATABASE_URL`, replacing
   `[YOUR-PASSWORD]` with your database password. `.env` is git-ignored; never share or commit it.
3. Start the server. It creates the tables in a dedicated `mystery` schema, **not** `public`.
   Supabase publishes the `public` schema through its browser API, so story secrets must not
   live there. Row-level security is also enabled on every table with no policies, and the
   browser API roles are denied, so even if the schema were exposed it would return nothing.
   The game server is the only thing that reads the data.

Check it against Supabase with the same end-to-end test (uses a throwaway `mystery_test` schema):

```bash
$env:E2E_BACKEND="postgres"; py -m unittest tests.test_e2e -v; Remove-Item Env:E2E_BACKEND
```

## Deploy (GitHub → Render → Supabase)

GitHub stores the code, Render runs it, and Supabase stores the data.

1. Push this folder to a **private** GitHub repository. `.env` and `data/` are ignored automatically.
2. In Render: **New → Blueprint** → connect the repo. It reads `render.yaml`.
3. When asked for `DATABASE_URL`, paste the same Supabase session-pooler URI.
4. Render gives you an address like `https://mystery-night.onrender.com`. Invitation links
   use whatever address the host is on, so create games from that address.

The free plan sleeps after about 15 minutes idle (the first visit then takes about 30 seconds).
Use the Starter plan on party night.

## Try the flow on one computer

Login cookies are per hostname, so use a different hostname for each person:

| Person | Open |
|--------|------|
| Host   | `http://localhost:8000` |
| Guest 1 | `http://alice.localhost:8000` |
| Guest 2 | `http://bob.localhost:8000` |

(`*.localhost` resolves to your own machine in Chrome and Edge. Or use a private window per guest.)

1. **Host**: create an account, then create a game with *The Lemon Tart Affair*.
2. **Host**: create one invitation link per guest and copy it. Each link is shown once.
3. **Each guest**: open their link on their own hostname (swap `localhost` for
   `alice.localhost` in the link), create an account and accept.
4. **Each guest**: answer the 12 questions (each answer saves immediately, so refreshing is safe), set
   boundaries, prep time and preferences, then submit. To see casting reject a role, have one guest
   choose **"No thanks"** for *Playing the murderer*.
5. **Host**: *Recommend cast*. Review the explanations, optionally change a guest's character
   (incompatible roles can't be selected, and the server revalidates every change), then *Approve*
   (click twice to confirm).
6. **Each guest**: their page switches to their private character dashboard.

## Validate content

```bash
py -m server.validate_content
```

Checks for duplicate or missing IDs, unknown tags, roles, claims and facts, invalid phase gates,
wrong-owner memories, and a killer role that isn't required, among other things. The server
also refuses to start if content is invalid. If a game's package files change after the game
was created, that game refuses to load them; publish edits as a new `content_version`.

## Tests

```bash
py -m unittest discover -s tests -v
```

- `tests/test_casting.py`: scoring, hard-boundary rules, and the assignment solver
  (checked against brute force on 200 random cases).
- `tests/test_e2e.py`: starts a real server on a temporary database and runs the full
  flow with a host and **two separately authenticated guests**. It covers resuming after
  refresh, rejecting incompatible roles, nothing visible before approval, each guest seeing
  only their own character, direct requests for another character or the solution being
  denied, raw content files being unreachable, and assignments persisting across a
  **server restart**.

## Where privacy is enforced

- `server/store.py` decides who the caller is relative to the game (host, accepted guest,
  or nobody) on every request.
- `server/content.py` holds the only functions that turn package content into response
  data (`public_role`, `private_role_for_player`, `host_solution`, …). Guests never receive
  scores, scoring maps, other characters' private fields or solution data.
- Only `web/index.html`, `web/app.js` and `web/styles.css` are served as files. `content/`
  is never reachable over HTTP.
- Session cookies are HttpOnly and SameSite=Lax. State-changing requests must carry an
  `X-Requested-With` header and a JSON body (CSRF protection). Invitation tokens are stored
  only as hashes. Refused requests are written to `audit_log`.

## Known gaps before real use

No password reset, no login rate limiting, and no email delivery of invitations (the host
copies links). Writes are serialised (one at a time), which is plenty for a party but not
for many simultaneous games. The casting weights
are a first guess: in the manual test, a guest who scored 100 on investigation was
recommended the gardener just ahead of the reporter, and the host corrected it with an
override. Tune the weights with real questionnaire data.
