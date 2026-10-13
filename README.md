# Mystery Night

A small web app for running one murder-mystery party. Each guest gets a private
link that shows only their own character. The host gets a separate link to run the game.

**Current milestone: P02. Short assessment and manual casting**, using a placeholder story.

How a game runs so far: the host adds guests and sends each their link, guests answer six quick
questions and set their limits, the host assigns characters (the app warns about poor fits and
blocks roles a guest said no to), then the host releases all characters at once.

## What's in this folder

| Path | What it is |
| --- | --- |
| `server/app.py` | The web server: pages and the API the phones talk to |
| `server/game.py` | Game rules: guests, links, answers, assigning and releasing characters |
| `server/casting.py` | The assessment, and the rules comparing guests' answers with each role |
| `server/story.py` | Loads the story, and decides what each person is allowed to see |
| `server/db.py` | Saving data: a local file for testing, Supabase when online |
| `server/manage.py` | Command to replace a lost or leaked host link |
| `content/story.json` | The mystery itself (placeholder until P07), incl. each role's casting needs |
| `content/assessment.json` | The six questions and the hard limits (edit the wording here) |
| `web/` | The phone screens (HTML, CSS, JavaScript) |
| `tests/` | Automatic checks for each milestone |

## Run it on your computer

You need Python 3.11 or newer (this PC has 3.13). Nothing else to install.

1. Open a terminal in this folder.
2. Run:
   ```
   py -m server.app
   ```
3. The terminal prints a line starting `HOST LINK (keep private):`. Ctrl+click it to open the host screen.
4. Press **Ctrl+C** in the terminal to stop the server.

Testing on your computer saves everything to `data/party.db`. To start over with an
empty game, stop the server, delete the `data` folder and start it again (you'll get a new host link).

## Run the automatic checks

```
py -m unittest -v
```

All tests should say `ok`.

## Put it online (Render + Supabase)

The online app runs on **Render** (hosting, about $7 USD/month on the Starter plan) and stores
its data in your **Supabase** database (free tier), in a section called `party`.

First-time setup is described step by step in the chat. In short:

1. The code is on GitHub (`jfinucane96/Mystery-night1`).
2. In Render: **New → Blueprint**, pick the repo. Render reads `render.yaml`.
3. Paste your Supabase `DATABASE_URL` when Render asks (same value as in your `.env` file).
4. After it deploys, open the service's **Logs** and find the line `HOST LINK (keep private): ...`.

Every time new code is pushed to GitHub's `main` branch, Render redeploys automatically.
Saved games are not affected.

## If a link leaks

- **A guest link:** on the host screen, press **New link** for that guest and send them the new one.
- **The host link:** run one of these, then use the new link it prints:
  ```
  py -m server.manage new-host-link            (the test game on your computer)
  py -m server.manage new-host-link --online   (the online game)
  ```

## Privacy rules (how it works)

- Links are long random codes (43 characters), so nobody can guess one.
- The server checks every request: guest links can only fetch their own character,
  and only the host link can see the solution or change anything.
- Story secrets never go to a phone unless that phone's character owns them.
- `.env` holds your database password. It's listed in `.gitignore`, so it never goes to GitHub.
