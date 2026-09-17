"""Sending a message out of the factory, through the WhatsApp bot next door.

The bot is a separate service on the same host with its own dashboard API:
login for a bearer token, then post {jid, text}. Nothing about it belongs in
this app beyond "here is a number, here is a line of text".

Everything is configuration, and it ships disabled. An alert that reaches a
phone is a message to a real person, so it never happens because a default in
this file said so — an admin has to enter the numbers and turn it on.

Reaching the host from inside the container is why the base URL defaults to
the docker bridge address rather than localhost: `localhost` inside the app
container is the app container.
"""

import os

import requests

SETTINGS_KEY = "whatsapp"

DEFAULT_BASE_URL = "http://172.17.0.1:3030"
TIMEOUT = 10

# Cached bearer token, refreshed on the first 401. Module-level and therefore
# per worker process, which is fine: it is a cache, not state.
_token = None


def config(settings=None):
    """The WhatsApp section of the operator settings, with env fallbacks.

    Credentials come from the environment rather than the settings file: the
    file is edited through a web form and backed up in the clear, and a bot
    password does not belong in either.
    """
    if settings is None:
        from app.routes.admin import load_app_settings

        settings = load_app_settings()
    section = (settings or {}).get(SETTINGS_KEY) or {}
    return {
        "enabled": bool(section.get("enabled")),
        "recipients": [r.strip() for r in
                       (section.get("recipients") or "").replace("\n", ",").split(",")
                       if r.strip()],
        # "critical" sends only the bad ones; "warn" sends everything.
        "min_severity": section.get("min_severity") or "critical",
        "base_url": section.get("base_url") or os.environ.get(
            "WHATSAPP_BASE_URL", DEFAULT_BASE_URL),
        "user": os.environ.get("WHATSAPP_USER"),
        "password": os.environ.get("WHATSAPP_PASS"),
        "token": os.environ.get("WHATSAPP_TOKEN"),
    }


def has_credentials():
    """Whether this install could send at all, credentials-wise.

    Separate from `is_configured` so the settings screen can stay silent about
    WhatsApp entirely until the bot is reachable: a switch that cannot send
    anything is a switch somebody turns on and then wonders about.
    """
    cfg = config({})
    return bool(cfg["token"] or (cfg["user"] and cfg["password"]))


def is_configured(settings=None):
    cfg = config(settings)
    return bool(cfg["enabled"] and cfg["recipients"]
                and (cfg["token"] or (cfg["user"] and cfg["password"])))


def _login(cfg):
    if cfg["token"]:
        return cfg["token"]
    r = requests.post(f"{cfg['base_url']}/api/auth/login",
                      json={"username": cfg["user"], "password": cfg["password"]},
                      timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json() or {}
    return data.get("token") or data.get("access_token")


def _jid(number):
    """A bare number becomes a WhatsApp JID; anything already shaped is left."""
    if "@" in number:
        return number
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"{digits}@s.whatsapp.net"


def send(text, settings=None):
    """Send one line to every configured recipient.

    Returns the number of recipients it reached. Never raises: a factory
    dashboard must not fall over because a chat bot is down — the caller logs
    what came back and the alert stays unsent, so the next run tries again.
    """
    global _token

    cfg = config(settings)
    if not is_configured(settings):
        return 0

    sent = 0
    for attempt in (1, 2):
        try:
            if _token is None:
                _token = _login(cfg)
            if not _token:
                return 0
            headers = {"Authorization": f"Bearer {_token}"}
            unauthorized = False
            for number in cfg["recipients"]:
                r = requests.post(f"{cfg['base_url']}/api/whatsapp/send",
                                  json={"jid": _jid(number), "text": text},
                                  headers=headers, timeout=TIMEOUT)
                if r.status_code == 401:
                    unauthorized = True
                    break
                if r.ok:
                    sent += 1
            if not unauthorized:
                return sent
            # The cached token expired; drop it and go round once.
            _token = None
            sent = 0
        except Exception:
            _token = None
            return sent
    return sent
