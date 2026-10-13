"""Host maintenance commands.

    py -m server.manage new-host-link            replace the host link (local test game)
    py -m server.manage new-host-link --online   same, for the online game (uses DATABASE_URL from .env)

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
    if not argv or argv[0] != "new-host-link":
        sys.exit(__doc__)
    online = "--online" in argv
    if online:
        load_dotenv()
        if not os.environ.get("DATABASE_URL"):
            sys.exit("DATABASE_URL is not set in .env.")
    else:
        os.environ.pop("DATABASE_URL", None)
    database = dbmod.from_env()
    database.init_schema()
    with database.tx() as t:
        key = game.reset_host_key(t)
    base = os.environ.get("MM_PUBLIC_URL") or ("https://YOUR-APP.onrender.com" if online else public_base_url(8000))
    print(f"Database: {database.describe()}")
    print(f"New host link (old one no longer works): {base.rstrip('/')}/host/{key}")


if __name__ == "__main__":
    main(sys.argv[1:])
