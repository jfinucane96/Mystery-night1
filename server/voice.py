"""Silas's voice for the room screen, from ElevenLabs (optional).

Settings (Render: Environment; your computer: .env):
  ELEVENLABS_API_KEY    the secret key (never sent to any browser)
  ELEVENLABS_VOICE_ID   which voice to use
Without them the room screen uses the browser's built-in voice, as before.
Each line is generated once and kept in memory, so repeats cost no credits."""

import json
import os
import threading
import urllib.error
import urllib.request

MAX_TEXT = 3000
MODEL = "eleven_multilingual_v2"
_cache = {}
_lock = threading.Lock()


def is_configured():
    return bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_VOICE_ID"))


def speak(text):
    """MP3 bytes for `text` in Silas's voice. Raises VoiceError on any failure."""
    text = (text or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise VoiceError("Nothing to say, or too much.")
    if not is_configured():
        raise VoiceError("ElevenLabs isn't set up.")
    voice = os.environ["ELEVENLABS_VOICE_ID"].strip()
    key = (voice, text)
    with _lock:
        if key in _cache:
            return _cache[key]
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": MODEL,
                         "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "style": 0.35}}).encode(),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip(), "Content-Type": "application/json",
                 "Accept": "audio/mpeg"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        # The response body explains the problem (bad key, out of credits); never includes the key itself
        raise VoiceError(f"ElevenLabs said {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise VoiceError(f"Couldn't reach ElevenLabs: {e}")
    with _lock:
        _cache[key] = audio
    return audio


class VoiceError(Exception):
    pass
