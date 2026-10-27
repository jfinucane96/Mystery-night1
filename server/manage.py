"""Host maintenance commands.

    py -m server.manage new-host-link            replace the host link (local test game)
    py -m server.manage new-host-link --online   same, for the online game (uses DATABASE_URL from .env)
    py -m server.manage reset-game --yes [--online]
        start the party over: keeps guests' names/phones, the host link and the room link;
        wipes answers, characters and all game progress; every guest gets a fresh link
    py -m server.manage reset-progress --yes [--online]
        back to before characters were released: keeps guests, links and questionnaire
        answers; clears role assignments and all game progress

The old host link stops working straight away. The online app prints the new
link in its Render logs on its next restart, or copy it from this output.
"""

import os
import sys

from . import db as dbmod, game
from .app import public_base_url

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        sys.exit("No .env file found. Copy .env.example to .env and fill in DATABASE_URL.")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main(argv):
    if not argv or argv[0] not in ("new-host-link", "reset-game", "reset-progress"):
        sys.exit(__doc__)
    if argv[0] in ("reset-game", "reset-progress") and "--yes" not in argv:
        sys.exit("This wipes all answers and game progress. Add --yes to confirm.")
    online = "--online" in argv
    if online:
        load_dotenv()
        if not os.environ.get("DATABASE_URL"):
            sys.exit("DATABASE_URL is not set in .env.")
    else:
        os.environ.pop("DATABASE_URL", None)
    database = dbmod.from_env()
    database.init_schema()
    print(f"Database: {database.describe()}")
    if argv[0] == "reset-progress":
        with database.tx() as t:
            game.reset_progress(t)
            names = [g["name"] for g in game.list_guests(t)]
        print(f"Progress reset; answers and links kept for: {', '.join(names) or 'none'}")
        return
    if argv[0] == "reset-game":
        with database.tx() as t:
            game.reset_game(t)
            names = [g["name"] for g in game.list_guests(t)]
        print(f"Game reset. Guests kept (each has a fresh link): {', '.join(names) or 'none'}")
        return
    with database.tx() as t:
        key = game.reset_host_key(t)
    base = os.environ.get("MM_PUBLIC_URL") or ("https://YOUR-APP.onrender.com" if online else public_base_url(8000))
    print(f"New host link (old one no longer works): {base.rstrip('/')}/host/{key}")


if __name__ == "__main__":
    main(sys.argv[1:])
