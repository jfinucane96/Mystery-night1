"""Text messages to guests.

Automatic mode: when TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER
are set, the server sends texts itself through Twilio.
Manual mode (otherwise, or if Twilio fails): the host's page opens their own
phone's Messages app with the text pre-written, and the host taps Send.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class TextError(Exception):
    pass


def is_automatic():
    return all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))


def normalize_phone(raw):
    """Turn what the host typed into +1XXXXXXXXXX form. Returns '' for blank, None if invalid."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def first_name(name):
    return (name or "").split()[0] if (name or "").split() else name


def invite_text(title, guest_name, link):
    return (f"Hi {first_name(guest_name)}! You're invited to {title}, a murder mystery party. "
            f"Open your private link to answer 6 quick questions: {link} "
            f"(Please don't share it.) Reply STOP to opt out.")


def release_text(title, guest_name, link):
    return (f"{first_name(guest_name)}, your character for {title} is ready! "
            f"Find out who you are (keep it secret): {link}")


def send(to, body, timeout=15):
    """Send one text through Twilio. Returns Twilio's message id; raises TextError on failure."""
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    data = urllib.parse.urlencode({"To": to, "From": os.environ["TWILIO_FROM_NUMBER"], "Body": body}).encode()
    req = urllib.request.Request(TWILIO_URL.format(sid=sid), data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["sid"]
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read())
            msg = info.get("message") or str(e)
        except ValueError:
            msg = str(e)
        raise TextError(f"Twilio refused the text: {msg}")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
        raise TextError(f"Couldn't reach Twilio: {e}")
