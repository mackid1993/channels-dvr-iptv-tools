#!/usr/bin/env python3
"""Channels DVR Gracenote Station ID Matcher for Dispatcharr (GUI).

Matches Dispatcharr channels to Gracenote station IDs using your Channels DVR
server, lets you review the results, and writes the chosen IDs to each
channel's "Gracenote StationID" field (tvc_guide_stationid) in Dispatcharr.

Accuracy rules:
  * Candidates come only from the real TV lineups (cable, satellite, OTA) that
    Gracenote lists for the chosen country and postal code, so a feed from
    another country or a test/VOD entry can never be picked.
  * A match is only marked "Matched" (and pre-ticked for writing) when it is
    exact and unique: an explicit call sign such as "(WABC)", or a channel name
    equal to the Gracenote name or call sign after removing words like
    HD/East/Channel. Anything else is "Review" or "Unmatched" and is never
    written unless you pick it yourself.

Auto-created (M3U-synced) channels get the ID as an override, because M3U
refreshes overwrite the base field. Manual channels get the base field.

Runs on Windows, macOS and Linux with Python 3.8+. The only dependency is
Tk (tkinter). On launch the script checks for it and, if it is missing,
offers to install it (Homebrew, winget, apt/dnf/pacman/zypper) or switches
to another Python on the machine that already has it.

    python3 dispatcharr_stationid_matcher.py           (macOS / Linux)
    py dispatcharr_stationid_matcher.py                (Windows, or double-click)
    ... --check-deps    only report what would be installed, change nothing
    ... --yes           install missing dependencies without asking
"""
import concurrent.futures
import csv
import difflib
import errno
import functools
import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30
PROBE_TIMEOUT = 8  # first contact with each server, so a wrong IP fails fast
CACHE_DAYS = 7
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".dispatcharr_stationid_matcher")
# Folder used before this app was renamed from channels_dvr_stationid_matcher.py
OLD_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".channels_dvr_stationid_matcher")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")   # autosaved picks
UI_FILE = os.path.join(CONFIG_DIR, "window.json")         # window size and layout
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "matcher.log")
TOKEN_MAX_AGE = 20 * 60   # Dispatcharr access tokens expire after 30 minutes
WRITE_BATCH = 50

log = logging.getLogger("cdsm")


def write_private_json(path, data):
    """Write JSON readable only by the current user (it can hold an API key)."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(tmp, 0o600)  # also tighten a pre-existing file
    except OSError:
        pass
    os.replace(tmp, path)


def read_json(path):
    """Contents of a JSON object file, or {} if it is missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def setup_logging():
    """Rotating log file in the config folder. Passwords and tokens are never logged."""
    if log.handlers:
        return
    log.setLevel(logging.DEBUG)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5,
                                                 encoding="utf-8")
    except OSError:
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s"))
    log.addHandler(h)

    def hook(exc_type, exc, tb):
        log.critical("Unhandled error", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda a: log.critical(
            "Unhandled error in thread %s", a.thread.name if a.thread else "?",
            exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

# Countries where Gracenote (via Channels DVR) returns real TV lineups, with a
# postal code that works. Verified against a live Channels DVR server. Any other
# ISO 3166 alpha-3 code can still be typed in; it is tried and reported if empty.
# (display name, code, example postal code)
COUNTRIES = [
    ("United States", "USA", "10001"), ("Canada", "CAN", "M5H2N2"),
    ("Mexico", "MEX", "06600"), ("Puerto Rico", "PRI", "00901"),
    ("United Kingdom", "GBR", "SW1A"), ("Ireland", "IRL", "D02"),
    ("Germany", "DEU", "10115"), ("France", "FRA", "75001"), ("Spain", "ESP", "28001"),
    ("Italy", "ITA", "00118"), ("Netherlands", "NLD", "1012"), ("Belgium", "BEL", "1000"),
    ("Switzerland", "CHE", "8001"), ("Austria", "AUT", "1010"), ("Sweden", "SWE", "11120"),
    ("Norway", "NOR", "0150"), ("Denmark", "DNK", "1050"), ("Finland", "FIN", "00100"),
    ("Australia", "AUS", "2000"), ("South Korea", "KOR", "03000"),
    ("Brazil", "BRA", "01001"), ("Argentina", "ARG", "C1002"), ("Chile", "CHL", "8320000"),
    ("Colombia", "COL", "110111"), ("Peru", "PER", "15001"), ("Venezuela", "VEN", "1010"),
    ("Ecuador", "ECU", "170101"), ("Uruguay", "URY", "11000"),
    ("Dominican Republic", "DOM", "10101"), ("Jamaica", "JAM", "JMAKN01"),
    ("Panama", "PAN", "0801"), ("Costa Rica", "CRI", "10101"), ("Guatemala", "GTM", "01001"),
    ("Honduras", "HND", "11101"), ("Bermuda", "BMU", "HM11"), ("Cayman Islands", "CYM", "KY1-1001"),
]
COUNTRY_NAMES = {c: n for n, c, _ in COUNTRIES}
COUNTRY_POSTAL = {c: pc for _, c, pc in COUNTRIES}


def parse_countries(text):
    """'USA:10001, CAN, GBR:SW1A' -> [("USA", "10001"), ("CAN", "M5H2N2"), ...]."""
    out, seen = [], set()
    for part in re.split(r"[,;\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z]{3})\s*(?:[:=]\s*([A-Za-z0-9 -]{2,10}))?", part)
        if not m:
            raise ValueError(f"'{part}' is not a country. Use 3-letter codes like USA or USA:10001.")
        code = m.group(1).upper()
        postal = (m.group(2) or COUNTRY_POSTAL.get(code) or "").strip()
        if not postal:
            raise ValueError(f"{code} is not in the built-in list, so it needs a postal code, "
                             f"e.g. {code}:12345.")
        if code not in seen:
            seen.add(code)
            out.append((code, postal))
    if not out:
        raise ValueError("Pick at least one country.")
    return out


def format_countries(pairs):
    return ", ".join(f"{c}:{pc}" for c, pc in pairs)


# Words that describe a feed rather than the station.
DROP_WORDS = {
    "hd", "fhd", "uhd", "sd", "4k", "hevc", "h265", "raw", "backup", "alt",
    "the", "channel", "network", "tv", "east", "feed", "ip",
}
PROVIDER_PREFIX = re.compile(r"^\s*(?:\[[A-Za-z]{2,4}\]|[A-Za-z]{2,4}\s*[:|])\s*")
# US/Canada broadcast call signs, only when they look deliberate:
# "(WABC)", "KTLA-DT", or the whole name ("US| WNYC").
EXPLICIT_CALLSIGN = re.compile(
    r"\(([KWC][A-Z]{2,3})(?:-(?:DT\d*|TV|CD|LD))?\)"
    r"|\b([KWC][A-Z]{2,3})-(?:DT\d*|TV|CD|LD)\b"
    r"|^\s*(?:[A-Z]{2,4}\s*[:|]\s*)?([KW][A-Z]{2,3})\s*$"
)

MATCHED, REVIEW, UNMATCHED, VERIFIED, NOT_IN_LINEUP, ERROR, DELETED, CHANGE, REMOVE = (
    "Matched", "Review", "Unmatched", "OK (verified)", "Current ID not in lineups", "Error",
    "Deleted in Dispatcharr", "Will replace ID", "Will remove ID")
USER_PICK = ("picked by you", "entered by you")


# ======================================================================
# HTTP
# ======================================================================

class Canceled(Exception):
    """The user pressed Cancel."""


class NetError(RuntimeError):
    """The server could not be reached."""


class HttpError(RuntimeError):
    """The server answered with an HTTP error status."""

    def __init__(self, msg, code, body=""):
        super().__init__(msg)
        self.code, self.body = code, body


_tls = threading.local()  # per-worker cancel flag


def _canceled():
    evt = getattr(_tls, "cancel", None)
    return evt is not None and evt.is_set()


def _sleep(seconds):
    """Sleep that wakes up immediately on Cancel."""
    end = time.time() + seconds
    while time.time() < end:
        if _canceled():
            raise Canceled()
        time.sleep(0.1)


def _host_of(url):
    p = urllib.parse.urlparse(url)
    return f"{p.hostname}:{p.port}" if p.port else (p.hostname or url)


def _path_of(url):
    return urllib.parse.urlparse(url).path


def _reason(e):
    """Plain-English reason for a network failure."""
    r = getattr(e, "reason", e)
    if isinstance(r, (socket.timeout, TimeoutError)) or "timed out" in str(r).lower():
        return "the connection timed out. Check the IP address and port."
    if isinstance(r, ConnectionRefusedError):
        return "connection refused. The machine answered but nothing is listening on that port."
    if isinstance(r, socket.gaierror):
        return "that host name could not be found."
    if isinstance(r, OSError) and r.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, 65, 113, 10051, 10065):
        return "no route to host. Check the IP address."
    if isinstance(r, (ConnectionResetError, ConnectionAbortedError)):
        return "the connection was dropped. The server may be restarting."
    return str(r)


def _http_once(method, url, headers, data, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    host, path, t0 = _host_of(url), _path_of(url), time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")[:4000]
        except OSError:
            body = ""
        log.warning("%s %s -> HTTP %s in %.2fs: %s", method, path, e.code, time.time() - t0, body[:300])
        if e.code in (401, 403):
            raise HttpError(f"{host} rejected the login (HTTP {e.code}). "
                            "Check the API key or username and password.", e.code, body) from None
        if e.code == 404:
            raise HttpError(f"{host} has no {path} (HTTP 404). Is this the right server and port?",
                            404, body) from None
        if e.code == 429:
            m = re.search(r"available in (\d+) second", body)
            wait = int(m.group(1)) if m else 60
            err = HttpError(f"{host} is limiting requests (Dispatcharr allows 3 logins a minute). "
                            f"Wait {wait} seconds and try again.", 429, body)
            err.retry_after = wait
            raise err from None
        raise HttpError(f"{host} returned HTTP {e.code} for {path}: {body[:300]}", e.code, body) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        if _canceled():
            raise Canceled() from None
        log.warning("%s %s -> failed after %.2fs: %r", method, path, time.time() - t0, e)
        raise NetError(f"Could not reach {host}: {_reason(e)}") from None
    log.debug("%s %s -> %s, %d bytes in %.2fs", method, path, code, len(raw), time.time() - t0)
    return raw


def http(method, url, headers=None, body=None, timeout=TIMEOUT, retries=2):
    """JSON request with retries on network errors and HTTP 429/5xx."""
    if _canceled():
        raise Canceled()
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    for attempt in range(retries + 1):
        try:
            raw = _http_once(method, url, hdrs, data, timeout)
            break
        except (NetError, HttpError) as e:
            transient = isinstance(e, NetError) or e.code in (429, 500, 502, 503, 504)
            if not transient or attempt == retries:
                raise
            wait = 1.5 * (2 ** attempt)
            if getattr(e, "retry_after", None):
                if e.retry_after > 90:
                    raise
                wait = e.retry_after + 1
            log.info("Retrying %s %s in %.1fs (attempt %d of %d)", method, _path_of(url), wait,
                     attempt + 2, retries + 1)
            _sleep(wait)
    if _canceled():
        raise Canceled()
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        raise RuntimeError(f"{_host_of(url)} answered, but not with the data expected. "
                           "Is this the right server and port?") from None


_HOSTNAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*")


def validate_address(text, label, default_port):
    """Normalized base URL for a server address, or ValueError explaining what is wrong."""
    text = (text or "").strip()
    if not text:
        raise ValueError(f"Enter the {label} address.")
    url = text.rstrip("/")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    p = urllib.parse.urlparse(url)
    try:
        port = p.port
    except ValueError:
        port = -1
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{label}: '{text}' has an invalid port. Use a number from 1 to 65535.")
    if p.path not in ("", "/") or p.query:
        raise ValueError(f"{label}: enter just the address and port, e.g. 192.168.1.10:{default_port}.")
    host = p.hostname or ""
    if re.fullmatch(r"[0-9.]+", host):
        parts = host.split(".")
        if len(parts) != 4 or any(not x or int(x) > 255 for x in parts):
            raise ValueError(f"{label}: '{host}' is not a valid IP address.")
    elif ":" in host:
        host = f"[{host}]"  # IPv6 literal
    elif not _HOSTNAME.fullmatch(host):
        raise ValueError(f"{label}: '{host or text}' is not a valid host name or IP address.")
    return f"{p.scheme.lower()}://{host}:{port or default_port}"


def normalize_base(url, default_port):
    try:
        return validate_address(url, "server", default_port)
    except ValueError as e:
        raise RuntimeError(str(e)) from None


# ======================================================================
# Channels DVR / Gracenote
# ======================================================================

def clean_query(term):
    # Gracenote answers HTTP 503 to "&" and to non-ASCII characters.
    term = term.replace("&", " and ")
    term = unicodedata.normalize("NFKD", term).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", term).strip()


def search_stations(cdvr, term):
    term = clean_query(term)
    if not term:
        return []
    query = urllib.parse.quote(f'"{term}"', safe="")
    result = http("GET", f"{cdvr}/tms/stations/{query}", retries=1)
    return result if isinstance(result, list) else []


def build_index(cdvr, country, postal, progress):
    """All stations carried in the lineups for country/postal, with names."""
    try:
        lineups = http("GET", f"{cdvr}/tms/lineups/{country}/{urllib.parse.quote(postal)}")
    except RuntimeError as e:
        if "1005" in str(e) or "invalid_lineup" in str(e):
            raise RuntimeError(f"Gracenote does not recognize postal code '{postal}' for {country}. "
                               "UK uses the outward code only, e.g. SW1A.") from None
        raise
    if not isinstance(lineups, list) or not lineups:
        raise RuntimeError(
            f"Gracenote returned no lineups for {country} {postal}. "
            "Check the postal code (UK uses the outward code only, e.g. SW1A).")
    stations = {}
    for n, lu in enumerate(lineups, 1):
        lid = lu.get("lineupId")
        label = f"{lu.get('name')} {lu.get('location') or ''}".strip()
        progress(f"Reading lineup {n}/{len(lineups)}: {label}", n / len(lineups) * 20)
        try:
            chans = http("GET", f"{cdvr}/dvr/guide/stations/{urllib.parse.quote(lid)}") or []
        except NetError:
            raise  # server went away; stop instead of timing out on every lineup
        except RuntimeError as e:
            log.warning("Skipping lineup %s: %s", lid, e)
            continue
        for c in chans:
            sid = c.get("stationId")
            if not sid:
                continue
            st = stations.setdefault(sid, {
                "stationId": sid, "callSign": c.get("callSign") or "",
                "name": c.get("name") or "", "type": c.get("type") or "",
                "affiliate": c.get("affiliateCallSign") or "", "lineups": [],
            })
            if label not in st["lineups"]:
                st["lineups"].append(label)

    # Cable/satellite lineups carry no station names; look them up by call sign.
    calls = sorted({s["callSign"] for s in stations.values() if s["callSign"] and not s["name"]})
    done = 0

    cancel_evt = getattr(_tls, "cancel", None)

    def lookup(call):
        _tls.cancel = cancel_evt  # pool threads honor Cancel too
        try:
            return search_stations(cdvr, call)
        except NetError:
            raise
        except RuntimeError:
            return []

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for results in pool.map(lookup, calls):
            done += 1
            for r in results:
                st = stations.get(r.get("stationId"))
                if st and not st["name"]:
                    st["name"] = r.get("name") or ""
                    st["type"] = st["type"] or r.get("type") or ""
            if done % 25 == 0 or done == len(calls):
                progress(f"Looking up station names {done}/{len(calls)}",
                         20 + done / max(len(calls), 1) * 75)
    # An unknown postal code silently returns only nationwide satellite lineups.
    local = any((lu.get("type") or "").upper() in ("OTA", "CABLE", "VMVPD")
                and not (lu.get("lineupId") or "").endswith(("-AFN-X",))
                for lu in lineups)
    return {"country": country, "postal": postal, "built": time.time(),
            "lineups": len(lineups), "stations": stations, "local": local}


def load_indexes(cdvr, pairs, progress, rebuild=False):
    """Merged index for several (country, postal) pairs. Failing countries are reported, not fatal."""
    merged, problems, total_lineups = {}, [], 0
    for n, (country, postal) in enumerate(pairs, 1):
        name = COUNTRY_NAMES.get(country, country)

        def sub(text, pct=None, n=n):
            progress(f"[{name} {n}/{len(pairs)}] {text}",
                     None if pct is None else ((n - 1) + pct / 100) / len(pairs) * 95)
        try:
            idx = load_index(cdvr, country, postal, sub, rebuild)
        except (NetError, Canceled):
            raise
        except RuntimeError as e:
            log.warning("No lineups for %s %s: %s", country, postal, e)
            problems.append(f"{name} ({country} {postal}): {e}")
            continue
        total_lineups += idx["lineups"]
        if not idx.get("local", True):
            problems.append(f"{name} ({country} {postal}): only nationwide satellite lineups were "
                            "found, so the postal code is probably wrong and local stations won't match.")
        for sid, st in idx["stations"].items():
            have = merged.get(sid)
            if have is None:
                merged[sid] = dict(st, lineups=list(st["lineups"]), countries=[country])
            else:
                have["lineups"] += [x for x in st["lineups"] if x not in have["lineups"]]
                if country not in have["countries"]:
                    have["countries"].append(country)
                for k in ("name", "type", "callSign"):
                    have[k] = have[k] or st.get(k, "")
    if not merged:
        raise RuntimeError("Gracenote returned no lineups for any selected country:\n\n" + "\n".join(problems))
    return {"pairs": pairs, "lineups": total_lineups, "stations": merged, "problems": problems}


def load_index(cdvr, country, postal, progress, rebuild=False):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]", "", f"{country}_{postal}")
    path = os.path.join(CONFIG_DIR, f"index_{safe}.json")
    if not rebuild and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                idx = json.load(f)
            if time.time() - idx.get("built", 0) < CACHE_DAYS * 86400 and isinstance(idx.get("stations"), dict) \
                    and "lineups" in idx:
                progress(f"Using cached lineup index ({len(idx['stations'])} stations)", 95)
                return idx
        except (OSError, ValueError, AttributeError, TypeError):
            pass  # unreadable or damaged cache: build it again
    log.info("Building lineup index for %s %s", country, postal)
    idx = build_index(cdvr, country, postal, progress)
    log.info("Index built: %d lineups, %d stations, local=%s", idx["lineups"],
             len(idx["stations"]), idx.get("local"))
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(idx, f)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.warning("Could not cache the lineup index: %s", e)
    return idx


# ======================================================================
# Dispatcharr
# ======================================================================

class Dispatcharr:
    """Dispatcharr API client that renews its login automatically."""

    def __init__(self, base):
        self.base = base
        self.api_key = self.username = self.password = ""
        self.access = self.refresh = None
        self.token_time = 0.0
        self._lock = threading.Lock()

    def login(self, api_key="", username="", password="", probe=False):
        self.api_key, self.username, self.password = api_key, username, password
        if api_key:
            log.info("Using a Dispatcharr API key for %s", self.base)
            return
        self._password_login(retries=0 if probe else 2)

    def _password_login(self, retries=2):
        tok = http("POST", f"{self.base}/api/accounts/token/",
                   body={"username": self.username, "password": self.password},
                   timeout=PROBE_TIMEOUT if retries == 0 else TIMEOUT, retries=retries)
        if not isinstance(tok, dict) or not tok.get("access"):
            raise RuntimeError(f"{_host_of(self.base)} did not return a login token. "
                               "Is this the Dispatcharr address and port?")
        self.access, self.refresh = tok["access"], tok.get("refresh")
        self.token_time = time.time()
        log.info("Logged in to Dispatcharr at %s as %s", self.base, self.username)

    def _renew(self, stale):
        """Get a fresh access token: refresh token first, then a full login."""
        with self._lock:
            if self.access != stale:
                return  # another thread already renewed it
            if self.refresh:
                try:
                    tok = http("POST", f"{self.base}/api/accounts/token/refresh/",
                               body={"refresh": self.refresh}, retries=1)
                    if isinstance(tok, dict) and tok.get("access"):
                        self.access = tok["access"]
                        self.refresh = tok.get("refresh", self.refresh)
                        self.token_time = time.time()
                        log.info("Refreshed the Dispatcharr login")
                        return
                except HttpError as e:
                    log.info("Login refresh was refused (HTTP %s); logging in again", e.code)
            self._password_login()

    def _headers(self):
        if self.api_key:
            return {"X-API-Key": self.api_key}
        if time.time() - self.token_time > TOKEN_MAX_AGE:
            self._renew(self.access)
        return {"Authorization": f"Bearer {self.access}"}

    def api(self, method, path, body=None, timeout=TIMEOUT, retries=2):
        for attempt in (1, 2):
            hdrs, used = self._headers(), self.access
            try:
                return http(method, self.base + path, hdrs, body, timeout, retries)
            except HttpError as e:
                if e.code == 401 and not self.api_key and attempt == 1:
                    log.info("Dispatcharr login expired; renewing and retrying %s %s", method, path)
                    self._renew(used)
                    continue
                raise

    def groups(self):
        """{group_id: name}. Missing groups endpoint is not fatal."""
        try:
            data = self.api("GET", "/api/channels/groups/")
        except RuntimeError as e:
            log.warning("Could not load channel groups: %s", e)
            return {}
        if isinstance(data, dict):
            data = data.get("results", [])
        return {g["id"]: g.get("name") or str(g["id"]) for g in data or [] if isinstance(g, dict) and "id" in g}

    def channels(self, timeout=TIMEOUT, retries=2):
        data = self.api("GET", "/api/channels/channels/", timeout=timeout, retries=retries)
        if isinstance(data, dict):
            data = data.get("results", [])
        return data or []

    @staticmethod
    def _rejected(err):
        """{channel_id: reason} from a bulk-edit HTTP 400 body."""
        try:
            errors = json.loads(err.body).get("errors") or []
        except (ValueError, AttributeError):
            return {}
        out = {}
        for x in errors:
            if isinstance(x, dict) and x.get("channel_id") is not None:
                out[x["channel_id"]] = x.get("error") or json.dumps(x.get("errors"))[:200]
        return out

    def write_station_ids(self, rows, progress=lambda *a: None):
        """Write and verify station IDs. Returns {channel_id: ("ok"|"deleted"|"error", message)}."""
        results = {}
        progress("Checking channels in Dispatcharr...", 5)
        live = {c["id"]: c for c in self.channels()}
        payload = []
        for r in rows:
            cid = r["channel"]["id"]
            ch = live.get(cid)
            if ch is None:
                results[cid] = ("deleted", "channel no longer exists in Dispatcharr")
                log.warning("Skipping channel %s (%s): deleted in Dispatcharr", cid, r["channel"].get("name"))
            elif ch.get("auto_created"):
                # An empty override hides the provider's value; null would fall back to it.
                payload.append({"id": cid, "override": {"tvc_guide_stationid": "" if r.get("remove") else r["new_id"]}})
            else:
                payload.append({"id": cid, "tvc_guide_stationid": None if r.get("remove") else r["new_id"]})
        want = {p["id"]: (p.get("override") or p)["tvc_guide_stationid"] or "" for p in payload}
        log.info("Writing %d station IDs (%d skipped as deleted)", len(payload), len(results))

        for start in range(0, len(payload), WRITE_BATCH):
            batch = payload[start:start + WRITE_BATCH]
            while batch:
                try:
                    self.api("PATCH", "/api/channels/channels/edit/bulk/", batch)
                    break
                except HttpError as e:
                    ids = {p["id"] for p in batch}
                    bad = {k: v for k, v in self._rejected(e).items() if k in ids} if e.code == 400 else {}
                    if not bad:  # can't tell which channel failed; report the whole batch
                        for p in batch:
                            results[p["id"]] = ("error", str(e))
                        break
                    for cid, msg in bad.items():
                        kind = "deleted" if "not found" in msg.lower() else "error"
                        results[cid] = (kind, msg)
                        log.warning("Dispatcharr rejected channel %s: %s", cid, msg)
                    batch = [p for p in batch if p["id"] not in bad]
            done = min(start + WRITE_BATCH, len(payload))
            progress(f"Wrote {done} of {len(payload)}...", 10 + 80 * done / max(len(payload), 1))

        progress("Verifying in Dispatcharr...", 95)
        live = {c["id"]: c for c in self.channels()}
        for cid, sid in want.items():
            if cid in results:
                continue
            now = str((live.get(cid) or {}).get("effective_tvc_guide_stationid") or "")
            if now == str(sid):
                results[cid] = ("ok", "removed" if not sid else "written")
            else:
                results[cid] = ("error", f"Dispatcharr shows '{now or 'nothing'}' after writing "
                                         f"{sid or '(no ID)'}")
                log.warning("Verify failed for channel %s: wanted %s, got %r", cid, sid, now)
        ok = sum(1 for k, _ in results.values() if k == "ok")
        log.info("Write finished: %d ok, %d failed", ok, len(results) - ok)
        return results


# ======================================================================
# Matching
# ======================================================================

# Curated equivalences, applied to both channel names and Gracenote names.
ALIASES = [
    (re.compile(r"\bnat ?geo\b"), "national geographic"),
    (re.compile(r"\bsho\b"), "showtime"),
    (re.compile(r"\bid\b"), "investigation discovery"),
    (re.compile(r"\blmn\b"), "lifetime movie network"),
    (re.compile(r"\bhln\b"), "headline news"),
    (re.compile(r"\bgsn\b"), "game show network"),
]
LONG_PREFIX = re.compile(r"^\s*[A-Z][A-Z0-9 ]{0,14}\|\s*")   # "NFL TEAMS|", "US|"
GENERIC = {"news", "sports", "sport", "movies", "movie", "plus", "pacific", "central", "live",
           "music", "kids", "family", "national", "television", "america", "american"}


@functools.lru_cache(maxsize=20000)
def tokens(text):
    """Order-independent normalized word set for a channel or station name."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = PROVIDER_PREFIX.sub("", LONG_PREFIX.sub("", text)).lower()
    text = text.replace("&", " and ").replace("+", " plus ").replace("'", "")
    text = re.sub(r"(?<=[a-z0-9])-(?=[a-z0-9])", "", text)        # c-span -> cspan
    for pat, repl in ALIASES:
        text = pat.sub(repl, text)
    words = []
    for t in re.sub(r"[^a-z0-9]+", " ", text).split():
        if t.isdigit() and len(t) <= 2 and words and words[-1].isalpha() and words[-1] not in DROP_WORDS:
            words[-1] += t                                       # qvc 2 -> qvc2
        else:
            words.append(t)
    out = []
    for t in words:
        t = "pacific" if t == "west" else t
        if t not in DROP_WORDS and t not in out:
            out.append(t)
    return frozenset(out)


@functools.lru_cache(maxsize=200000)
def _tok_sim(a, b):
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.85
    if min(len(a), len(b)) >= 4 and a[0] == b[0] and abs(len(a) - len(b)) <= 3:
        r = difflib.SequenceMatcher(None, a, b).ratio()
        return r if r >= 0.8 else 0.0
    return 0.0


def similarity(a, b, call=""):
    """0..1, ignores word order. `call` (a call sign) may stand in for any word of `a`."""
    if not a or not (b or call):
        return 0.0
    b_plus = set(b) | ({call} if call else set())
    sa = sum(max(_tok_sim(x, y) for y in b_plus) for x in a)
    if not b:
        return sa / len(a) * 0.9
    sb = sum(max(_tok_sim(y, x) for x in a) for y in b)
    return (sa + sb) / (len(a) + len(b))


def _bare_call(st):
    return re.sub(r"hd$", "", (st.get("callSign") or "").lower())


def search_phrase(name):
    """Human search phrase for a channel name: no provider prefix, tags or HD/RAW noise."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    t = PROVIDER_PREFIX.sub("", LONG_PREFIX.sub("", t))
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\b(?:HD|FHD|UHD|SD|4K|RAW|HEVC|EAST)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -:|")


_JUNK = re.compile(r"\b(?:test|dummy|catalog|vod|on demand)\b", re.I)


def global_candidates(cdvr, name, matcher, limit=8):
    """Gracenote-wide candidates for a channel, outside the user's lineups (never auto-picked)."""
    want = tokens(name)
    phrase = search_phrase(name)
    queries = [phrase] if phrase else []
    distinct = sorted((t for t in want if len(t) >= 4 and t not in GENERIC and not t.isdigit()),
                      key=len, reverse=True)
    found = {}
    for q in queries + distinct[:2]:
        if q in queries[1:] or (found and q != phrase and max(v[0] for v in found.values()) >= 0.8):
            break
        try:
            results = search_stations(cdvr, q)
        except NetError:
            raise
        except RuntimeError as e:
            log.debug("Global search %r failed: %s", q, e)
            continue
        for r in results:
            sid = r.get("stationId")
            if not sid or r.get("type") == "VODProvider" or _JUNK.search(r.get("name") or ""):
                continue
            st = matcher.by_id.get(sid) or {
                "stationId": sid, "callSign": r.get("callSign") or "", "name": r.get("name") or "",
                "type": r.get("type") or "", "affiliate": r.get("affiliateCallSign") or "",
                "lineups": [], "outside": True}
            sc = name_score(name, st["name"], _bare_call(st))
            shares = bool(set(distinct) & tokens(st["name"]))
            if sc >= 0.45 or shares:
                prev = found.get(sid)
                if not prev or sc > prev[0]:
                    found[sid] = (sc, st)
    ranked = sorted(found.values(), key=lambda x: (-x[0], x[1]["name"]))
    return [st for _, st in ranked[:limit]]


def _compact_variants(text):
    t = re.sub(r"[^a-z0-9]", "", search_phrase(text).lower().replace("&", "and"))
    return {t, t[3:] if t.startswith("the") and len(t) > 6 else t}


@functools.lru_cache(maxsize=200000)
def name_score(channel_name, station_name, call=""):
    """Order-independent similarity, plus run-together names (SHOXBET = SHO x BET)."""
    sc = similarity(tokens(channel_name), tokens(station_name), call)
    a, b = _compact_variants(channel_name), _compact_variants(station_name)
    best = max((difflib.SequenceMatcher(None, x, y).ratio()
                for x in a for y in b if min(len(x), len(y)) >= 5), default=0.0)
    if best >= 0.88:
        sc = max(sc, best * 0.95)
    return sc


GOOD_LOCAL = 0.8   # below this, local results alone are not trusted; look Gracenote-wide too


def find_candidates(cdvr, matcher, text, wide=False, limit=30):
    """Local lineup results, plus Gracenote-wide ones when local matches are weak or asked for."""
    local = merge_candidates(matcher, text, matcher.search(text), matcher.suggest(text))
    best = name_score(text, local[0]["name"], _bare_call(local[0])) if local else 0.0
    if cdvr and (wide or best < GOOD_LOCAL):
        log.info("Search %r: best local score %.2f, searching Gracenote-wide", text, best)
        return merge_candidates(matcher, text, local, global_candidates(cdvr, text, matcher, limit=25),
                                limit=limit)
    return local


def merge_candidates(matcher, name, *lists, limit=15):
    """One ranked list: best similarity first, stations in your lineups before outside ones."""
    want = tokens(name)
    seen = {}
    for lst in lists:
        for st in lst or []:
            seen.setdefault(st["stationId"], st)
    ranked = sorted(seen.values(), key=lambda st: (
        -round(name_score(name, st["name"], _bare_call(st)), 2),
        bool(st.get("outside")), -len(st.get("lineups", [])), st["name"]))
    return ranked[:limit]


def wants_hd(text):
    return bool(re.search(r"\b(?:HD|FHD|UHD|4K)\b", text or "", re.I))


def is_hd_station(st):
    return bool(re.search(r"\bHD\b", st["name"])) or st["callSign"].upper().endswith("HD")


def explicit_callsign(text):
    m = EXPLICIT_CALLSIGN.search((text or "").upper())
    return next((g for g in m.groups() if g), None) if m else None


class Matcher:
    def __init__(self, index):
        self.stations = list(index["stations"].values())
        self.by_id = index["stations"]
        self.by_call, self.by_name, self.by_bare_call = {}, {}, {}
        for st in self.stations:
            call = st["callSign"].upper()
            self.by_call.setdefault(call, []).append(st)
            bare = re.sub(r"HD$", "", call.lower())
            if len(bare) >= 3:
                self.by_bare_call.setdefault(bare, []).append(st)
            key = tokens(st["name"])
            if key:
                self.by_name.setdefault(key, []).append(st)

    @staticmethod
    def _rank(cands):
        uniq = {s["stationId"]: s for s in cands}
        return sorted(uniq.values(), key=lambda s: (-len(s["lineups"]), s["name"]))

    def _resolve(self, cands, channel_name):
        """Return the single right station, or None if it is ambiguous."""
        uniq = {s["stationId"]: s for s in cands}
        cands = list(uniq.values())
        if len(cands) == 1:
            return cands[0]
        # Same network in SD and HD: take the one matching the channel's HD-ness.
        pref = [s for s in cands if is_hd_station(s) == wants_hd(channel_name)]
        if len(pref) == 1:
            return pref[0]
        return None

    def match(self, ch):
        name = ch.get("effective_name") or ch.get("name") or ""
        tvg = ch.get("effective_tvg_id") or ch.get("tvg_id") or ""
        call = explicit_callsign(name) or explicit_callsign(tvg)
        if call:
            exact = self.by_call.get(call, [])
            if len(exact) == 1:
                return MATCHED, exact[0], [exact[0]], f"call sign {call}"
        key = tokens(name)
        cands = list(self.by_name.get(key, [])) if key else []
        if key and len(key) == 1:
            token = next(iter(key))
            by_call = self.by_bare_call.get(token, [])
            # "CNN" -> Cable News Network is safe; "Retro" -> RetroPlex
            # (call sign RETRO) is not, so that one goes to Review.
            cands += [st for st in by_call if self._call_sign_is_name(st, key)]
            if not cands and by_call:
                return REVIEW, None, self._rank(by_call), "call sign matches but the name differs"
        if not cands and call:
            cands = [s for s in self.stations if s["callSign"].upper().startswith(call)]
        if cands:
            best = self._resolve(cands, name)
            if best:
                return MATCHED, best, self._rank(cands), "exact name"
            return REVIEW, None, self._rank(cands), "several stations share this name"
        loose = self.suggest(name)
        if loose:
            return REVIEW, None, loose, "similar names only"
        return UNMATCHED, None, [], "nothing similar in your lineups"

    @staticmethod
    def _call_sign_is_name(st, key):
        """True when a call sign really is the station's name or acronym."""
        if not st["name"] or tokens(st["name"]) == key:
            return True
        words = re.findall(r"[A-Za-z0-9]+", st["name"].replace("&", " "))
        initials = "".join(w[0] for w in words).lower()
        call = re.sub(r"hd$", "", st["callSign"].lower())
        # Acronyms: CNN = Cable News Network, HGTV starts with H-G-T.
        return initials == call or (len(initials) >= 3 and call.startswith(initials))

    def suggest(self, text, limit=15, threshold=0.45):
        """Ranked loose suggestions for a channel name, ignoring word order."""
        if not tokens(text):
            return []
        scored = []
        for st in self.stations:
            sc = name_score(text, st["name"], _bare_call(st))
            if sc >= threshold:
                scored.append((sc, st))
        scored.sort(key=lambda x: (-x[0], -len(x[1]["lineups"]), x[1]["name"]))
        return [st for _, st in scored[:limit]]

    def search(self, text, limit=40):
        """Stations whose name or call sign contains every word of text."""
        want = tokens(text)
        if not want:
            return []
        hits = []
        for st in self.stations:
            hay = " ".join(tokens(st["name"])) + " " + st["callSign"].lower()
            if all(w in hay for w in want):
                hits.append(st)
        return self._rank(hits)[:limit]


# ======================================================================
# GUI
# ======================================================================

def _fmt_num(num):
    if isinstance(num, float) and num.is_integer():
        return int(num)
    return "" if num is None else num


LEFT = "Left as is"   # you looked at it and chose no change; shown instead of the status
ATTENTION = (REVIEW, UNMATCHED, ERROR, CHANGE)   # a current ID outside your lineups is fine (streaming)
FILTERS = (("all", "All"), ("attention", "Needs attention"), ("ticked", "Ticked to write"),
           (MATCHED, MATCHED), (REVIEW, REVIEW), (UNMATCHED, UNMATCHED), (NOT_IN_LINEUP, NOT_IN_LINEUP),
           (VERIFIED, VERIFIED), (CHANGE, CHANGE), (REMOVE, REMOVE), (LEFT, LEFT), (ERROR, ERROR),
           (DELETED, DELETED))
ROW_COLORS = {MATCHED: "#d9f2d9", REVIEW: "#fff2c2", UNMATCHED: "#f8d4d4", NOT_IN_LINEUP: "#e8e8e8",
              VERIFIED: "#e8e8e8", ERROR: "#f4b6b6", DELETED: "#cccccc", CHANGE: "#ffd8a8",
              REMOVE: "#e0c8f0", LEFT: "#f1f1f1"}
ON, OFF = "☑", "☐"
COLS = ("write", "num", "channel", "group", "current", "status", "new", "gname", "call", "why")
HEADS = ("Write", "Ch #", "Dispatcharr channel", "Group", "Current ID", "Status",
         "Station ID", "Gracenote name", "Call sign", "Reason")
WIDTHS = (52, 60, 230, 130, 84, 150, 84, 210, 84, 260)
# the station list under the table (suggestions or search results): name first, station ID last
STATION_COLS = ("pick", "n", "name", "call", "type", "where", "sid")
STATION_HEADS = ("", "#", "Gracenote name", "Call sign", "Type", "Where", "Station ID")
STATION_WIDTHS = (30, 30, 300, 90, 90, 200, 90)
HELP = """\
1. Fill in your servers and press Connect & Match. Exact, unique matches are \
ticked for you (green, "Matched"). Nothing is written until you press Write.

2. Work through "Needs attention" (yellow and red rows). Click a channel, then \
click the right station in the list of suggestions below it, or press 1-9. \
Press Return (or "Done, next") to go to the next channel; double-click a \
station to pick it and move on in one go. A channel you leave without a pick \
becomes "Left as is".

3. Not in the list? Type in the search box above it: the list shows the search \
results, picked the same way. Esc or the ✕ brings the suggestions back. Return \
(or "All of Gracenote") also looks beyond your lineups. You can also type a \
station ID. Remove ID clears a wrong ID on Write.

4. Press "Write N channels to Dispatcharr". Every ID is read back to verify it. \
Auto-created (M3U-synced) channels get the ID as an override, so M3U refreshes \
keep it.

Ticked channels are written. Select several channels (Shift-click, or \
""" + ("Cmd" if sys.platform == "darwin" else "Ctrl") + """-click) and press \
Tick selected or Untick selected to include or leave them out in bulk; Space \
or a click in the Write column does the same. Your picks are autosaved, so \
closing the app loses nothing."""


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, ttk

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    aqua = root.tk.call("tk", "windowingsystem") == "aqua"
    MOD = "Command" if aqua else "Control"
    KEY = "⌘" if aqua else "Ctrl+"

    def allow_toggle_click(tree):
        """Cmd/Ctrl-click toggles one row on every platform (Shift-click already selects a range)."""
        def toggle(e):
            iid = tree.identify_row(e.y)
            if iid:
                tree.selection_toggle(iid)
                tree.focus(iid)
            return "break"
        for seq in ("<Command-Button-1>", "<Control-Button-1>", "<Meta-Button-1>", "<Mod1-Button-1>"):
            if aqua and seq == "<Control-Button-1>":
                continue   # Control-click is the right-click on a Mac
            try:
                tree.bind(seq, toggle)
            except tk.TclError:
                pass

    def bind_right_click(widget, fn):
        seqs = ("<Button-2>", "<Control-Button-1>") if aqua else ("<Button-3>",)
        for seq in seqs:
            widget.bind(seq, fn)

    def scrolled_tree(parent, cols, heads, widths, fill_col, **kw):
        """Treeview with both scrollbars. Columns never steal width from each other, so any
        column can be dragged as wide as you like; the fill column takes up spare room."""
        box = ttk.Frame(parent)
        tree = ttk.Treeview(box, columns=cols, show="headings", **kw)
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h, anchor="w")
            tree.column(c, width=w, minwidth=36, anchor="w", stretch=False)
        base = dict(zip(cols, widths))
        vs = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        def fit(shrink):
            w = tree.winfo_width()
            if w <= 1:
                return
            total = sum(tree.column(c, "width") for c in cols)
            fw = tree.column(fill_col, "width")
            if total < w:
                tree.column(fill_col, width=fw + w - total)
            elif shrink and total > w and fw > base[fill_col]:
                tree.column(fill_col, width=max(base[fill_col], fw - (total - w)))
        tree.bind("<Configure>", lambda e: fit(True), add="+")
        tree.bind("<ButtonRelease-1>", lambda e: tree.after_idle(lambda: fit(False)), add="+")
        return box, tree

    class Tip:
        """Small hover tooltip."""
        def __init__(self, widget, text):
            self.w, self.text, self.top, self.job = widget, text, None, None
            widget.bind("<Enter>", self.schedule, add="+")
            widget.bind("<Leave>", self.hide, add="+")
            widget.bind("<ButtonPress>", self.hide, add="+")

        def schedule(self, _e=None):
            self.hide()
            self.job = self.w.after(700, self.show)

        def show(self):
            self.job = None
            x, y = self.w.winfo_rootx() + 8, self.w.winfo_rooty() + self.w.winfo_height() + 4
            self.top = tk.Toplevel(self.w)
            self.top.wm_overrideredirect(True)
            self.top.wm_geometry(f"+{x}+{y}")
            tk.Label(self.top, text=self.text, justify="left", bg="#ffffe0", fg="black", relief="solid",
                     borderwidth=1, wraplength=360, padx=6, pady=3).pack()

        def hide(self, _e=None):
            if self.job:
                self.w.after_cancel(self.job)
                self.job = None
            if self.top:
                self.top.destroy()
                self.top = None

    class App:
        def __init__(self, root):
            self.root = root
            self.q = queue.Queue()   # worker threads never touch Tk; they post here
            self.rows = []           # one dict per channel
            self.matcher = None
            self.cdvr = None
            self.disp = None
            self.busy = False
            self.job_id = 0
            self.job_kind = ""
            self.quiet_job = False   # startup auto-connect: errors go to the status line
            self.cancel_evt = None
            self.disp_base = ""
            self.loaded_once = False
            self._save_after = None
            self.find_job = self.search_job = self.detail_job = self.extra_job = self.sugg_job = None
            self.extra_for = None
            self.search_gen = 0
            self.quiet_search = False
            self.shown_i = None
            self.cand_list = []      # what the station list shows: suggestions or search results
            self.hit_list, self.hit_note = None, ""   # search results while the search box has text
            self.sort_col, self.sort_rev = "num", False
            self.filter_idx = 0
            self.group_names = {}    # combobox label -> group name
            root.title("Channels DVR - Gracenote Station ID Matcher for Dispatcharr")
            self.v = {k: tk.StringVar() for k in
                      ("cdvr", "disp", "apikey", "user", "password", "countries")}
            self.include_existing = tk.BooleanVar(value=True)
            self.ui = read_json(UI_FILE)
            self.place_window()
            self._build()
            auto = self._load_settings()
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            if aqua:
                root.createcommand("::tk::mac::Quit", self.on_close)
            self._poll_job = root.after(100, self._poll)
            log.info("Window opened")
            if auto:
                self.note("Connecting with your saved settings…")
                root.after(150, lambda: self.start(False, quiet=True))
            else:
                self.note("Fill in your servers and press Connect & Match.")

        # ------------------------------------------------------------ window
        def place_window(self):
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            geo = self.ui.get("geometry")
            m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo if isinstance(geo, str) else "")
            if m and int(m.group(1)) <= sw and int(m.group(2)) <= sh:
                self.root.geometry(geo)
            else:
                self.root.geometry(f"{min(1320, sw - 60)}x{min(880, sh - 90)}")

        def set_minsize(self):
            """Never narrower than the action bar, so no button is ever cut off."""
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.update_idletasks()
            need = max(self.actions.winfo_reqwidth(), self.conn.winfo_reqwidth()) + 24
            self.root.minsize(min(max(980, need), sw - 40), min(640, sh - 80))

        def save_ui(self):
            try:
                ui = {"geometry": self.root.geometry(), "vsash": self.vpane.sash_coord(0)[1],
                      "columns": {c: self.tree.column(c, "width") for c in COLS if c != "why"}}
                os.makedirs(CONFIG_DIR, exist_ok=True)
                write_private_json(UI_FILE, ui)
            except (OSError, tk.TclError) as e:
                log.debug("Could not save the window layout: %s", e)

        # ------------------------------------------------------------ layout
        def _build(self):
            self.build_menu()
            bold = tkfont.nametofont("TkDefaultFont").copy()
            bold.configure(weight="bold", size=bold.cget("size") + 2)
            self.bold = bold
            pad = {"padx": 4, "pady": 3}

            # connection: full form until connected, then a one-line summary
            self.top_holder = ttk.Frame(self.root)
            self.top_holder.pack(side="top", fill="x", padx=10, pady=(8, 2))
            self.conn = top = ttk.LabelFrame(self.top_holder, text="Servers", padding=(8, 4))
            entries = []

            def field(row, col, label, key, tip=None, **kw):
                ttk.Label(top, text=label).grid(row=row, column=col, sticky="e", **pad)
                e = ttk.Entry(top, textvariable=self.v[key], width=20, **kw)
                e.grid(row=row, column=col + 1, sticky="ew", **pad)
                entries.append(e)
                if tip:
                    Tip(e, tip)
                return e
            field(0, 0, "Channels DVR", "cdvr", "Address of your Channels DVR server, e.g. 192.168.1.10 "
                                                "(port 8089 is assumed).")
            field(0, 2, "Dispatcharr", "disp", "Address of Dispatcharr, e.g. 192.168.1.10 (port 9191 is "
                                               "assumed).")
            field(1, 0, "API key", "apikey", "Dispatcharr: Users > edit your user > Generate API Key. "
                                             "Saved on this computer, readable only by you.", show="•")
            field(1, 2, "or username", "user")
            field(1, 4, "password", "password", "Only needed without an API key. Never saved.", show="•")
            ttk.Label(top, text="Countries").grid(row=2, column=0, sticky="e", **pad)
            cf = ttk.Frame(top)
            cf.grid(row=2, column=1, columnspan=3, sticky="ew", **pad)
            ce = ttk.Entry(cf, textvariable=self.v["countries"], width=30)
            ce.pack(side="left", fill="x", expand=True)
            entries.append(ce)
            Tip(ce, "3-letter country codes with your postal code, e.g. USA:10001, CAN, GBR:SW1A. "
                    "The postal code decides which local stations count.")
            ttk.Button(cf, text="Pick…", command=self.pick_countries).pack(side="left", padx=(4, 0))
            ttk.Checkbutton(top, text="Include channels that already have an ID",
                            variable=self.include_existing).grid(row=2, column=4, columnspan=2, sticky="w",
                                                                 **pad)
            brow = ttk.Frame(top)
            brow.grid(row=3, column=0, columnspan=6, sticky="ew", **pad)
            self.run_btn = ttk.Button(brow, text="Connect & Match", default="active",
                                      command=lambda: self.start(False))
            self.run_btn.pack(side="right")
            self.rebuild_btn = ttk.Button(brow, text="Rebuild lineup index", command=lambda: self.start(True))
            self.rebuild_btn.pack(side="right", padx=6)
            Tip(self.rebuild_btn, "Read every lineup from Gracenote again instead of using the cached copy "
                                  f"(kept for {CACHE_DAYS} days). Takes about a minute per country.")
            self.hide_btn = ttk.Button(brow, text="Hide", command=self.hide_conn)
            ttk.Label(brow, text="e.g. USA:10001, CAN, GBR:SW1A. Your postal code decides local stations.",
                      foreground="gray", width=1).pack(side="left", fill="x", expand=True)
            for c in (1, 3, 5):
                top.columnconfigure(c, weight=1)
            for e in entries:
                e.bind("<Return>", lambda ev: self.start(False))

            self.conn_bar = ttk.Frame(self.top_holder)
            self.reload_btn = ttk.Button(self.conn_bar, text="Reload", command=lambda: self.start(False))
            self.reload_btn.pack(side="right")
            Tip(self.reload_btn, f"Read the channels again and re-match ({KEY}R). Your unwritten picks are kept.")
            ttk.Button(self.conn_bar, text="Connection…", command=self.show_conn).pack(side="right", padx=4)
            self.conn_lbl = ttk.Label(self.conn_bar, text="", width=1)
            self.conn_lbl.pack(side="left", fill="x", expand=True)
            self.conn.pack(fill="x")

            # filters
            bar = ttk.Frame(self.root)
            bar.pack(side="top", fill="x", padx=10, pady=(4, 2))
            ttk.Label(bar, text="Show").pack(side="left")
            self.show_v = tk.StringVar()
            self.show_box = ttk.Combobox(bar, textvariable=self.show_v, state="readonly", width=28,
                                         values=[label for _, label in FILTERS])
            self.show_box.current(0)
            self.show_box.pack(side="left", padx=(4, 12))
            self.show_box.bind("<<ComboboxSelected>>", lambda e: self.set_filter(self.show_box.current()))
            ttk.Label(bar, text="Group").pack(side="left")
            self.group_v = tk.StringVar(value="All groups")
            self.group_box = ttk.Combobox(bar, textvariable=self.group_v, state="readonly", width=22,
                                          values=["All groups"])
            self.group_box.pack(side="left", padx=(4, 12))
            self.group_box.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
            ttk.Label(bar, text="Find").pack(side="left")
            self.find_v = tk.StringVar()
            self.find_entry = ttk.Entry(bar, textvariable=self.find_v, width=20)
            self.find_entry.pack(side="left", padx=(4, 0))
            Tip(self.find_entry, "Channel name, group, station ID or Gracenote name. A channel number finds "
                                 "that channel; 500-600 finds a range. Click a column heading to sort.")
            self.find_v.trace_add("write", lambda *a: self.schedule_find())
            self.find_entry.bind("<Escape>", lambda e: (self.find_v.set(""), "break")[1])
            self.find_entry.bind("<Down>", lambda e: (self.focus_table(), "break")[1])
            self.find_entry.bind("<Return>", lambda e: (self.focus_table(), "break")[1])
            ttk.Button(bar, text="✕", width=2, command=self.clear_filters).pack(side="left", padx=(2, 0))
            self.shown_lbl = ttk.Label(bar, text="", foreground="gray")
            self.shown_lbl.pack(side="left", padx=12)

            # bottom: status line, then the action buttons above it (packed before the panes so a
            # small window shrinks the table, never the buttons)
            sbar = ttk.Frame(self.root)
            sbar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
            self.cancel_btn = ttk.Button(sbar, text="Cancel", command=self.cancel)
            self.prog = ttk.Progressbar(sbar, length=180, maximum=100)
            self.status = ttk.Label(sbar, text="", anchor="w", justify="left", width=1)
            self.status.pack(side="left", fill="x", expand=True)
            self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=max(200, e.width - 4)))
            self.actions = actions = ttk.Frame(self.root)
            actions.pack(side="bottom", fill="x", padx=10, pady=(2, 4))
            self.write_btn = ttk.Button(actions, text="Write 0 channels to Dispatcharr…", command=self.write)
            self.write_btn.pack(side="right")
            Tip(self.write_btn, "Write every ticked channel's station ID to Dispatcharr and read it back to "
                                f"verify. Shortcut: {KEY}S")
            self.walk_btn = ttk.Button(actions, text="Match unmatched one by one…", command=self.walkthrough)
            self.walk_btn.pack(side="right", padx=8)
            Tip(self.walk_btn, "Show only the channels that need attention and start at the first one. "
                               "Pick a station (click or 1-9), then Return for the next.")
            for text, fn, tip in (
                    ("Tick selected", lambda: self.tick_selected(True),
                     "Include every selected channel that has a pick in the next Write. Select several "
                     f"with Shift-click or {'⌘' if aqua else 'Ctrl-'}click, or {KEY}A for all shown."),
                    ("Untick selected", lambda: self.tick_selected(False),
                     "Leave the selected channels out of the next Write. Their picks are kept."),
                    ("Mark reviewed", lambda: self.mark_reviewed(),
                     "Keep the selected channels exactly as they are and take them out of Needs attention "
                     "(they show as Left as is).")):
                b = ttk.Button(actions, text=text, command=fn)
                b.pack(side="left", padx=(0, 6))
                Tip(b, tip)

            # table above, channel detail below; drag the divider to resize
            self.vpane = tk.PanedWindow(self.root, orient="vertical", sashwidth=8, sashrelief="raised",
                                        bd=0, opaqueresize=True)
            self.vpane.pack(side="top", fill="both", expand=True, padx=10, pady=4)
            saved = self.ui.get("columns") if isinstance(self.ui.get("columns"), dict) else {}
            widths = [saved[c] if isinstance(saved.get(c), int) and 36 <= saved[c] <= 2000 else w
                      for c, w in zip(COLS, WIDTHS)]
            tbox, self.tree = scrolled_tree(self.vpane, COLS, HEADS, widths, "why", selectmode="extended")
            for c in COLS:
                self.tree.heading(c, command=lambda c=c: self.sort_by(c))
            self.tree.column("write", anchor="center")
            self.tree.heading("write", anchor="center")
            for tag, color in ROW_COLORS.items():
                self.tree.tag_configure(tag, background=color)
            self.tree.tag_configure("dark", foreground="black")
            self.tree.tag_configure("off", foreground="#777777")
            self.tree.bind("<<TreeviewSelect>>", lambda e: self.schedule_detail())
            self.tree.bind("<Button-1>", self.table_click)
            self.tree.bind("<Double-1>", self.table_double)
            self.tree.bind("<space>", lambda e: (self.toggle(), "break")[1])
            self.tree.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind("<Right>", lambda e: (self.focus_cands(), "break")[1])
            self.tree.bind("/", lambda e: (self.focus_search(), "break")[1])
            self.tree.bind(f"<{MOD}-a>", lambda e: (self.select_all(), "break")[1])
            for k in range(1, 10):
                self.tree.bind(str(k), lambda e, k=k: (self.pick_number(k), "break")[1])
            allow_toggle_click(self.tree)
            bind_right_click(self.tree, self.table_menu)
            self.vpane.add(tbox, minsize=120, stretch="always")
            self.update_headings()

            detail = ttk.Frame(self.vpane, padding=(0, 6, 0, 0))
            self.vpane.add(detail, minsize=230, stretch="never", height=320)
            head = ttk.Frame(detail)
            head.pack(side="top", fill="x")
            self.d_title = ttk.Label(head, text="", font=bold)
            self.d_title.pack(side="top", anchor="w")
            self.d_sub = ttk.Label(head, text="", foreground="gray", justify="left")
            self.d_sub.pack(side="top", anchor="w", fill="x")
            head.bind("<Configure>", lambda e: self.d_sub.configure(wraplength=max(200, e.width - 10)))

            # one station list: the matcher's suggestions, or search results while the box has text
            self.list_box = box = ttk.LabelFrame(detail, text="Suggestions", padding=4)
            box.pack(side="top", fill="both", expand=True, pady=(4, 0))
            lb = ttk.Frame(box)
            lb.pack(side="bottom", fill="x", pady=(4, 0))
            self.done_btn = ttk.Button(lb, text="Done, next  ⏎", command=self.done_next)
            self.done_btn.pack(side="right")
            Tip(self.done_btn, "Keep this channel as shown and go to the next one (Return). "
                               "Without a pick it becomes Left as is.")
            self.d_buttons = [self.done_btn]
            for text, fn, tip in (
                    ("Clear choice", self.clear_choice,
                     "Drop the pick: nothing changes for this channel on Write."),
                    ("Remove ID", self.remove_id,
                     "Clear the channel's current station ID on the next Write."),
                    ("Reset", self.reset_rows,
                     "Go back to what the matcher found for this channel.")):
                b = ttk.Button(lb, text=text, command=fn)
                b.pack(side="left", padx=(0, 6))
                Tip(b, tip)
                self.d_buttons.append(b)
            srow = ttk.Frame(box)
            srow.pack(side="top", fill="x")
            self.id_btn = ttk.Button(srow, text="Use ID", command=self.set_manual)
            self.id_btn.pack(side="right")
            self.manual_v = tk.StringVar()
            me = ttk.Entry(srow, textvariable=self.manual_v, width=9)
            me.pack(side="right", padx=4)
            me.bind("<Return>", lambda e: (self.set_manual(), "break")[1])
            Tip(me, "Type a Gracenote station ID (numbers only) and press Return or Use ID.")
            ttk.Label(srow, text="Station ID").pack(side="right", padx=(12, 0))
            self.d_buttons.append(self.id_btn)
            ttk.Label(srow, text="Search").pack(side="left")
            self.search_v = tk.StringVar()
            self.search_entry = ttk.Entry(srow, textvariable=self.search_v, width=12)
            self.search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
            Tip(self.search_entry, "Type a station name or call sign: the list below shows the search results. "
                                   "Return also searches all of Gracenote (with the box empty, for this "
                                   "channel's name). Esc or ✕ brings the suggestions back.")
            ttk.Button(srow, text="✕", width=2, command=lambda: self.search_v.set("")).pack(side="left",
                                                                                          padx=(2, 0))
            self.wide_v = tk.BooleanVar(value=False)
            wide = ttk.Checkbutton(srow, text="All of Gracenote", variable=self.wide_v,
                                   command=lambda: self.run_search() if self.search_v.get().strip() else None)
            wide.pack(side="left", padx=(6, 0))
            Tip(wide, "Also search stations outside your lineups (other countries and regions). Those are "
                      "marked not in your lineups. Without this, Gracenote-wide results are only added "
                      "when nothing in your lineups is close.")
            self.search_v.trace_add("write", lambda *a: self.schedule_search())
            self.search_entry.bind("<Down>", lambda e: (self.focus_cands(), "break")[1])
            self.search_entry.bind("<Return>", lambda e: (self.search_wide(), "break")[1])
            self.search_entry.bind("<KP_Enter>", lambda e: (self.search_wide(), "break")[1])
            self.search_entry.bind("<Escape>", lambda e: (self.search_v.set(""), "break")[1])
            self.search_entry.bind("<FocusIn>", lambda e: self.search_entry.after_idle(
                lambda: self.search_entry.select_range(0, "end")))
            self.cand_info = ttk.Label(box, text="", foreground="gray", width=1)
            self.cand_info.pack(side="top", fill="x")
            cbox, self.cand = scrolled_tree(box, STATION_COLS, STATION_HEADS, STATION_WIDTHS, "name",
                                            selectmode="browse", height=5)
            cbox.pack(side="top", fill="both", expand=True)
            self.cand.column("pick", anchor="center", minwidth=26)
            self.cand.column("n", anchor="center", minwidth=26)
            for tag, bg, fg in (("picked", "#d6f0d6", "black"), ("current", "#e3eaf6", "black"),
                                ("removed", "#fbe0e0", "#a00000"), ("outside", "", "#8a5a00")):
                self.cand.tag_configure(tag, background=bg, foreground=fg)
            self.cand.bind("<Button-1>", self.cand_click)
            self.cand.bind("<Double-1>", self.cand_double)
            self.cand.bind("<space>", lambda e: (self.pick_cand(self.cand.selection()), "break")[1])
            self.cand.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.cand.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            self.cand.bind("<Left>", lambda e: (self.tree.focus_set(), "break")[1])
            self.cand.bind("/", lambda e: (self.focus_search(), "break")[1])
            self.cand.bind("<Escape>", lambda e: (self.search_v.set(""), "break")[1]
                           if self.search_v.get() else None)
            for k in range(1, 10):
                self.cand.bind(str(k), lambda e, k=k: (self.pick_number(k), "break")[1])
            self.set_detail_enabled(False)

            # keyboard shortcuts that work anywhere in the window
            self.root.bind("<Escape>", lambda e: self.cancel())
            self.root.bind(f"<{MOD}-s>", lambda e: (self.write(), "break")[1])
            self.root.bind(f"<{MOD}-r>", lambda e: (self.start(False), "break")[1])
            self.root.bind(f"<{MOD}-f>", lambda e: (self.find_entry_focus(), "break")[1])
            self.root.bind(f"<{MOD}-l>", lambda e: (self.focus_search(), "break")[1])
            self.root.bind(f"<{MOD}-Return>", lambda e: (self.done_next(), "break")[1])
            self.write_btn.configure(text="Write 999 channels to Dispatcharr…")
            self.set_minsize()
            self.update_counts()
            self.root.after(80, self.place_sashes)

        def place_sashes(self):
            self.root.update_idletasks()
            h = self.vpane.winfo_height()
            if h > 400:
                y = self.ui.get("vsash")
                self.vpane.sash_place(0, 0, y if isinstance(y, int) and 120 < y < h - 230 else int(h * 0.55))

        def build_menu(self):
            mb = tk.Menu(self.root)
            fm = tk.Menu(mb, tearoff=0)
            fm.add_command(label="Connect & Match / Reload", accelerator=f"{KEY}R",
                           command=lambda: self.start(False))
            fm.add_command(label="Rebuild lineup index", command=lambda: self.start(True))
            fm.add_command(label="Connection settings…", command=self.show_conn)
            fm.add_separator()
            fm.add_command(label="Write ticked channels to Dispatcharr…", accelerator=f"{KEY}S",
                           command=self.write)
            fm.add_command(label="Export unmatched CSV…", command=self.export)
            fm.add_separator()
            fm.add_command(label="Open log", command=self.open_log)
            fm.add_command(label="Open data and log folder", command=lambda: self.open_path(CONFIG_DIR))
            if not aqua:
                fm.add_separator()
                fm.add_command(label="Quit", command=self.on_close)
            mb.add_cascade(label="File", menu=fm)
            cm = tk.Menu(mb, tearoff=0)
            self.fill_channel_menu(cm)
            cm.add_separator()
            cm.add_command(label="Match unmatched one by one…", command=self.walkthrough)
            cm.add_command(label="Mark every channel shown as reviewed",
                           command=lambda: self.mark_reviewed(all_shown=True))
            cm.add_separator()
            cm.add_command(label="Select all shown", accelerator=f"{KEY}A", command=self.select_all)
            cm.add_command(label="Find a channel", accelerator=f"{KEY}F", command=self.find_entry_focus)
            cm.add_command(label="Search Gracenote", accelerator=f"{KEY}L", command=self.focus_search)
            mb.add_cascade(label="Channels", menu=cm)
            hm = tk.Menu(mb, tearoff=0)
            hm.add_command(label="How it works", command=lambda: messagebox.showinfo(
                "How it works", HELP, parent=self.root))
            hm.add_command(label="Keyboard shortcuts", command=self.show_keys)
            hm.add_command(label="Open log", command=self.open_log)
            mb.add_cascade(label="Help", menu=hm)
            self.root.configure(menu=mb)

        def fill_channel_menu(self, m):
            m.add_command(label="Done, next", accelerator="Return", command=self.done_next)
            m.add_command(label="Tick selected", accelerator="Space", command=lambda: self.tick_selected(True))
            m.add_command(label="Untick selected", command=lambda: self.tick_selected(False))
            m.add_command(label="Mark reviewed", command=lambda: self.mark_reviewed())
            m.add_separator()
            m.add_command(label="Clear choice", command=self.clear_choice)
            m.add_command(label="Remove ID", command=self.remove_id)
            m.add_command(label="Reset to the matcher's result", command=self.reset_rows)

        def show_keys(self):
            messagebox.showinfo("Keyboard shortcuts", "\n".join((
                "Channel table",
                "   Up / Down: next or previous channel",
                "   Return: done with this channel, go to the next",
                "   1-9: pick that station and go to the next",
                "   Space: tick or untick the selected channels",
                "   Right: move into the station list   /: search",
                f"   {KEY}A: select every channel shown",
                "   Shift-click / " + ("⌘" if aqua else "Ctrl-") + "click: select several",
                "",
                "Station list (suggestions or search results)",
                "   Click or Space: pick that station",
                "   Double-click: pick it and go to the next channel",
                "   Return: go to the next channel   Left: back to the table",
                "",
                "Search",
                f"   {KEY}L or /: jump to the search box; typing shows search results",
                "   Return: search all of Gracenote   Down: move into the list",
                "   Esc or ✕: back to the suggestions",
                "",
                "Anywhere",
                f"   {KEY}F: find a channel      {KEY}R: reload",
                f"   {KEY}S: write               {KEY}Return: done, next",
                "   Esc: cancel what is running")), parent=self.root)

        def show_conn(self):
            self.conn_bar.pack_forget()
            if self.loaded_once:
                self.hide_btn.pack(side="right", padx=(0, 6), before=self.rebuild_btn)
            self.conn.pack(fill="x")

        def hide_conn(self):
            self.conn.pack_forget()
            self.hide_btn.pack_forget()
            self.conn_bar.pack(fill="x")

        def find_entry_focus(self):
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")

        def focus_search(self):
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")

        def focus_table(self):
            kids = self.tree.get_children()
            if kids and not self.tree.selection():
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])
                self.tree.see(kids[0])
            self.tree.focus_set()

        def focus_cands(self):
            self.show_detail_now()
            kids = [k for k in self.cand.get_children() if k != "none"]
            if not kids:
                return
            sel = self.cand.selection()
            k = sel[0] if sel else kids[0]
            self.cand.selection_set(k)
            self.cand.focus(k)
            self.cand.see(k)
            self.cand.focus_set()

        def select_all(self):
            self.tree.selection_set(self.tree.get_children())
            self.tree.focus_set()

        def note(self, text, warn=False):
            """Status line; warnings are red and ring the bell so a no-op is never silent."""
            self.status.config(text=text, foreground="#c00000" if warn else "")
            if warn:
                self.root.bell()

        # ------------------------------------------------------------ settings
        def pick_countries(self):
            """Multi-select dialog for countries; keeps any postal codes already typed."""
            try:
                current = dict(parse_countries(self.v["countries"].get()))
            except ValueError:
                current = {}
            win = tk.Toplevel(self.root)
            win.title("Pick countries")
            win.transient(self.root)
            ttk.Label(win, text="Select every country your IPTV channels come from "
                                "(Ctrl/Cmd-click for several).").pack(anchor="w", padx=10, pady=(10, 4))
            row = ttk.Frame(win)
            row.pack(side="bottom", fill="x", padx=10, pady=8)
            lb = tk.Listbox(win, selectmode="extended", height=20, width=40, exportselection=False)
            lb.pack(fill="both", expand=True, padx=10)
            for i, (n, c, _) in enumerate(COUNTRIES):
                lb.insert("end", f"{n} ({c})")
                if c in current:
                    lb.selection_set(i)
            extra = [c for c in current if c not in COUNTRY_NAMES]

            def ok():
                picked = [(COUNTRIES[i][1], current.get(COUNTRIES[i][1], COUNTRIES[i][2]))
                          for i in lb.curselection()]
                picked += [(c, current[c]) for c in extra]
                self.v["countries"].set(format_countries(picked))
                win.destroy()
            ttk.Label(row, text="Edit postal codes in the main window afterwards (CODE:postal).",
                      foreground="gray").pack(side="left")
            ttk.Button(row, text="OK", default="active", command=ok).pack(side="right")
            ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=4)
            win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])
            lb.bind("<Double-1>", lambda e: ok())
            try:
                win.grab_set()
            except tk.TclError:
                pass

        def _load_settings(self):
            """Fill the form from settings.json. True when it has everything to connect at startup."""
            s = read_json(SETTINGS_FILE)
            for k in ("cdvr", "disp", "user", "countries", "apikey"):
                if s.get(k) and isinstance(s[k], str):
                    self.v[k].set(s[k])
            if isinstance(s.get("include_existing"), bool):
                self.include_existing.set(s["include_existing"])
            if not self.v["countries"].get():
                old = re.search(r"\(([A-Z]{3})\)$", str(s.get("country") or ""))  # older settings format
                code = old.group(1) if old else "USA"
                self.v["countries"].set(f"{code}:{s.get('postal') or COUNTRY_POSTAL.get(code, '10001')}")
            try:
                pairs = parse_countries(self.v["countries"].get())
            except ValueError:
                return False
            return bool(self.v["cdvr"].get().strip() and self.v["disp"].get().strip()
                        and self.v["apikey"].get().strip()
                        and all(c in COUNTRY_NAMES for c, _ in pairs))

        def _save_settings(self):
            # The API key is saved too, so it doesn't have to be pasted every time. The file
            # is readable only by your user account; delete it to forget the API key.
            data = read_json(SETTINGS_FILE)   # keep anything else stored there
            data.update({k: self.v[k].get() for k in ("cdvr", "disp", "user", "countries", "apikey")})
            data["include_existing"] = bool(self.include_existing.get())
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                write_private_json(SETTINGS_FILE, data)
            except OSError as e:
                log.warning("Could not save settings: %s", e)

        # ------------------------------------------------------------ session autosave
        def _session_data(self):
            picks = {}
            for r in self.rows:
                if r["why"].startswith(USER_PICK) or r.get("remove") or r["status"] in (ERROR, DELETED) \
                        or not r["write"] or r.get("reviewed"):
                    picks[str(r["channel"]["id"])] = {
                        "name": r["channel"].get("effective_name") or r["channel"].get("name"),
                        "new_id": r["new_id"], "write": r["write"], "why": r["why"],
                        "status": r["status"], "best": r["best"], "remove": bool(r.get("remove")),
                        "reviewed": bool(r.get("reviewed"))}
            return {"saved": time.time(), "disp": self.disp_base, "picks": picks}

        def save_session(self, now=False):
            """Autosave picks shortly after every change (debounced)."""
            if not now:
                if self._save_after:
                    self.root.after_cancel(self._save_after)
                self._save_after = self.root.after(800, lambda: self.save_session(True))
                return
            if self._save_after:
                self.root.after_cancel(self._save_after)
            self._save_after = None
            if not self.rows:
                return
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                with open(SESSION_FILE + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(self._session_data(), f)
                os.replace(SESSION_FILE + ".tmp", SESSION_FILE)
            except (OSError, TypeError, ValueError) as e:
                log.warning("Could not autosave picks: %s", e)
                self.note(f"Could not autosave your picks: {e}", warn=True)

        def _restore_session(self, ask):
            """Re-apply work saved from an earlier run against the same Dispatcharr.

            Hand picks and removals are only restored if you say so; unticks and reviews are
            restored without asking, because they can only make Write do less.
            Returns how many channels got something back."""
            data = read_json(SESSION_FILE)
            picks = data.get("picks")
            if data.get("disp") != self.disp_base or not isinstance(picks, dict):
                return 0
            todo, quiet = [], []
            for r in self.rows:
                pk = picks.get(str(r["channel"]["id"]))
                if not isinstance(pk, dict):
                    continue
                why = str(pk.get("why") or "")
                base = next((p for p in USER_PICK if why.startswith(p)), None)
                nid = str(pk.get("new_id") or "")
                if base and nid and nid != r["current"]:
                    todo.append((r, pk, base, nid))
                elif pk.get("remove") and pk.get("write") and r["current"]:
                    todo.append((r, pk, None, ""))
                elif pk.get("reviewed") or self._unticked(pk, r):
                    quiet.append((r, pk))
            if not todo and not quiet:
                return 0
            if todo and ask:
                age = time.strftime("%b %d %H:%M", time.localtime(data.get("saved", 0) or 0))
                if not messagebox.askyesno(
                        "Restore your picks?",
                        f"Found {len(todo)} station IDs you picked by hand on {age} that are not in "
                        "Dispatcharr yet.\n\nRestore them?", parent=self.root):
                    return 0
            for r, pk, base, nid in todo:
                if not base:
                    self._set_remove(r)
                else:
                    best = pk.get("best")
                    st = self.matcher.by_id.get(nid) or (
                        best if isinstance(best, dict) and str(best.get("stationId")) == nid else None)
                    r.update(new_id=nid, write=bool(pk.get("write", True)), why=base, status=MATCHED,
                             best=st, remove=False)
                    self._mark_change(r)
                r["reviewed"] = bool(pk.get("reviewed"))
            for r, pk in quiet:
                how = self._unticked(pk, r)
                if how == "cleared":
                    self._clear(r)
                elif how:
                    r["write"] = False   # you unticked the matcher's pick
                r["reviewed"] = bool(pk.get("reviewed")) or how == "cleared"
            log.info("Restored %d hand picks and %d unticks/reviews from the saved session",
                     len(todo), len(quiet))
            return len(todo) + len(quiet)

        @staticmethod
        def _unticked(pk, r):
            """How a saved row turned off the matcher's ticked pick: "unticked", "cleared" or ""."""
            if pk.get("write") or pk.get("remove") or not r["write"]:
                return ""
            nid = str(pk.get("new_id") or "")
            if nid and nid == r["new_id"]:
                return "unticked"
            if not nid and (pk.get("status") == MATCHED or pk.get("why") == "choice cleared"):
                return "cleared"
            return ""

        def open_path(self, path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # noqa: S606 - opens the log in the default viewer
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except OSError as e:
                messagebox.showinfo("Log file", f"The log is at:\n{LOG_FILE}\n\n({e})", parent=self.root)

        def open_log(self):
            self.open_path(LOG_FILE if os.path.exists(LOG_FILE) else CONFIG_DIR)

        def on_close(self):
            if self.busy and self.job_kind == "write" and not messagebox.askyesno(
                    "Still writing", "IDs are still being written. Quit anyway?", parent=self.root):
                return
            try:
                self.save_session(True)
                self.save_ui()
            finally:
                log.info("Window closed")
                for job in (self._poll_job, self._save_after):
                    try:
                        self.root.after_cancel(job)
                    except (tk.TclError, ValueError):
                        pass
                self.root.destroy()

        # ------------------------------------------------------------ worker plumbing
        def _poll(self):
            try:
                while True:
                    kind, (jid, payload) = self.q.get_nowait()
                    if kind == "call":
                        payload()   # a background search result; it checks for itself if still wanted
                        continue
                    if jid != self.job_id:
                        continue  # result of a canceled job
                    if kind == "progress":
                        text, pct = payload
                        self.note(text)
                        if pct is not None:
                            self.prog["value"] = pct
                    elif kind == "done":
                        try:
                            payload()
                        finally:
                            self._set_busy(False)
                    elif kind == "error":
                        quiet = self.quiet_job
                        self._set_busy(False)
                        self.prog["value"] = 0
                        if quiet:
                            self.show_conn()
                            self.note("Could not connect with your saved settings: " + payload.splitlines()[0]
                                      + "  Check them and press Connect & Match.", warn=True)
                        else:
                            self.note("Error: " + payload.splitlines()[0], warn=True)
                            messagebox.showerror("Error", payload + f"\n\nDetails are in the log:\n{LOG_FILE}",
                                                 parent=self.root)
            except queue.Empty:
                pass
            except Exception as e:  # never let the poll loop die
                log.exception("Error in the UI loop")
                self._set_busy(False)
                self.note(f"Error: {e}", warn=True)
            self._poll_job = self.root.after(100, self._poll)

        def _set_busy(self, busy):
            self.busy = busy
            if not busy:
                self.quiet_job = False
            for b in (self.run_btn, self.rebuild_btn, self.reload_btn):
                b.state(["disabled"] if busy else ["!disabled"])
            if busy:
                self.prog["value"] = 0
                self.cancel_btn.pack(side="right", padx=(6, 0), before=self.status)
                self.prog.pack(side="right", padx=(8, 0), before=self.cancel_btn)
            else:
                self.prog.pack_forget()
                self.cancel_btn.pack_forget()
            self.update_counts()

        def _worker(self, fn, kind):
            self.job_id += 1
            jid, evt = self.job_id, threading.Event()
            self.cancel_evt, self.job_kind = evt, kind

            def progress(text, pct=None):
                if evt.is_set():
                    raise Canceled()
                self.q.put(("progress", (jid, (text, pct))))

            def run():
                _tls.cancel = evt
                try:
                    self.q.put(("done", (jid, fn(progress))))
                except Canceled:
                    log.info("%s job canceled", kind)
                except Exception as e:  # report every failure in the GUI
                    if isinstance(e, (RuntimeError, ValueError)):
                        log.error("%s job failed: %s", kind, e)
                    else:
                        log.exception("%s job crashed", kind)
                    self.q.put(("error", (jid, str(e) or type(e).__name__)))
            log.info("Starting %s job", kind)
            self._set_busy(True)
            threading.Thread(target=run, name=f"{kind}-{jid}", daemon=True).start()

        def cancel(self):
            if not self.busy:
                return
            if self.cancel_evt:
                self.cancel_evt.set()
            self.job_id += 1  # ignore anything the old job still reports
            log.info("User canceled the %s job", self.job_kind)
            self._set_busy(False)
            self.prog["value"] = 0
            if self.job_kind == "write":
                self.note("Canceled. Some IDs may already have been written; "
                          "press Reload to see the current state.", warn=True)
            else:
                self.note("Canceled. Fix the details and press Connect & Match again.")

        # ------------------------------------------------------------ connect and match
        def start(self, rebuild, quiet=False):
            if self.busy:
                self.note("Still working, one moment… (Esc cancels)", warn=True)
                return

            def problem(title, text):
                self.show_conn()
                if quiet:
                    self.note(text, warn=True)
                else:
                    messagebox.showwarning(title, text, parent=self.root)
            v = {k: var.get().strip() for k, var in self.v.items()}
            try:
                cdvr = validate_address(v["cdvr"], "Channels DVR", 8089)
                disp_url = validate_address(v["disp"], "Dispatcharr", 9191)
            except ValueError as e:
                problem("Check the address", str(e))
                return
            try:
                pairs = parse_countries(v["countries"])
            except ValueError as e:
                problem("Check the countries", str(e))
                return
            unknown = [c for c, _ in pairs if c not in COUNTRY_NAMES]
            if unknown and quiet:
                return   # startup auto-connect only runs with known countries
            if not v["apikey"] and not (v["user"] and v["password"]):
                problem("Missing info", "Enter a Dispatcharr API key or username and password.")
                return
            self._save_settings()
            self.save_session(True)
            include = self.include_existing.get()
            log.info("Connect & Match: cdvr=%s dispatcharr=%s countries=%s auth=%s rebuild=%s",
                     cdvr, disp_url, format_countries(pairs), "api-key" if v["apikey"] else "password",
                     rebuild)

            def job(progress):
                progress(f"Connecting to Channels DVR at {_host_of(cdvr)}...", 1)
                try:
                    st = http("GET", f"{cdvr}/status", timeout=PROBE_TIMEOUT)
                except RuntimeError as e:
                    raise RuntimeError(f"Channels DVR: {e}") from None
                if not (isinstance(st, dict) and "channels dvr" in str(st.get("description", "")).lower()):
                    raise RuntimeError(f"Channels DVR: {_host_of(cdvr)} answered but is not a Channels DVR "
                                       "server. Check the address and port (default 8089).")
                progress(f"Logging in to Dispatcharr at {_host_of(disp_url)}...", 2)
                disp = Dispatcharr(disp_url)
                try:
                    disp.login(v["apikey"], v["user"], v["password"], probe=True)
                    chans = disp.channels(timeout=PROBE_TIMEOUT if v["apikey"] else TIMEOUT,
                                          retries=0 if v["apikey"] else 2)
                except RuntimeError as e:
                    raise RuntimeError(f"Dispatcharr: {e}") from None
                log.info("Dispatcharr returned %d channels", len(chans))
                groups = disp.groups()
                idx = load_indexes(cdvr, pairs, progress, rebuild)
                matcher = Matcher(idx)
                progress("Matching channels...", 97)
                rows = []
                for ch in sorted(chans, key=lambda c: c.get("effective_channel_number")
                                 or c.get("channel_number") or 0):
                    current = ch.get("effective_tvc_guide_stationid") or ""
                    if current and not include:
                        continue
                    status, best, cands, why = matcher.match(ch)
                    if current:
                        if current in matcher.by_id:
                            best, status, why = matcher.by_id[current], VERIFIED, "current ID is in your lineups"
                        else:
                            why = f"{why}; current ID not carried in your lineups"
                            status = NOT_IN_LINEUP
                    rows.append({"channel": ch, "current": current, "status": status,
                                 "best": best, "cands": cands, "why": why,
                                 "new_id": best["stationId"] if best else "",
                                 # never pre-tick a change to an ID that is already set
                                 "write": status == MATCHED and not current,
                                 "remove": False, "reviewed": False})
                    if status == NOT_IN_LINEUP and not best:
                        rows[-1]["new_id"] = ""
                    r = rows[-1]
                    gid = ch.get("effective_channel_group_id") or ch.get("channel_group_id")
                    r["group"] = groups.get(gid, f"Group {gid}" if gid else "(no group)")
                    r["auto"] = {k: r[k] for k in ("status", "best", "new_id", "write", "why")}

                counts = {}
                for r in rows:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                log.info("Matched %d channels: %s", len(rows), counts)

                def finish():
                    first = not self.loaded_once
                    if self.rows and self.disp_base == disp_url:
                        self.save_session(True)   # keep edits made while this was loading
                    self.cdvr, self.disp, self.matcher, self.rows = cdvr, disp, matcher, rows
                    self.disp_base = disp_url
                    self.loaded_once = True
                    self.shown_i = None
                    names = sorted({r["group"] for r in rows}, key=str.lower)
                    gcount = {n: sum(1 for r in rows if r["group"] == n) for n in names}
                    self.group_names = {f"{n}  ({gcount[n]})": n for n in names}
                    self.group_box["values"] = ["All groups"] + list(self.group_names)
                    if self.group_v.get() not in self.group_box["values"]:
                        self.group_v.set("All groups")
                    self.prog["value"] = 100
                    restored = self._restore_session(ask=first)   # Reload keeps your work without asking
                    if first:
                        self.filter_idx = 1 if any(self.needs_attention(r) for r in rows) else 0
                    self.hide_conn()
                    self.conn_lbl.configure(
                        text=f"Connected to Dispatcharr at {_host_of(disp_url)} and Channels DVR at "
                             f"{_host_of(cdvr)}   ·   {len(chans)} channels, {idx['lineups']} lineups, "
                             f"{len(idx['stations'])} stations in {format_countries(pairs)}   ·   "
                             f"loaded {time.strftime('%H:%M')}")
                    self.rebuild(keep_view=not first)
                    if first or not self.tree.selection():
                        self.focus_table()
                    ready = sum(1 for r in rows if self.will_write(r))
                    att = sum(1 for r in rows if self.needs_attention(r))
                    msg = (f"{len(rows)} channels checked. {ready} ticked to write. "
                           + (f"{att} need attention: pick a station below, then press Return for the "
                              "next one. " if att else "Nothing needs attention. ")
                           + "Press Write when you're done.")
                    if restored:
                        msg = (f"Kept your unwritten work on {restored} channel"
                               f"{'' if restored == 1 else 's'}. " + msg)
                    probs = idx.get("problems")
                    if probs:
                        log.warning("Country problems: %s", probs)
                        self.note("Some countries had problems: " + " ".join(probs) + "  " + msg, warn=True)
                    else:
                        self.note(msg)
                return finish

            self.quiet_job = quiet
            self.note("Reloading…" if self.rows else "Connecting…")
            self._worker(job, "match")

        # ------------------------------------------------------------ row state
        @staticmethod
        def has_change(r):
            """True when Write would change this channel's station ID."""
            return bool((r.get("remove") and r["current"]) or (r["new_id"] and r["new_id"] != r["current"]))

        def will_write(self, r):
            return bool(r["write"]) and self.has_change(r)

        @staticmethod
        def shown_status(r):
            if r.get("reviewed") and not r["write"] and r["status"] in ATTENTION:
                return LEFT
            return r["status"]

        def needs_attention(self, r):
            return self.shown_status(r) in ATTENTION and not r["write"]

        @staticmethod
        def _name(r):
            return r["channel"].get("effective_name") or r["channel"].get("name") or ""

        def row_values(self, r):
            ch, st = r["channel"], r["best"] or {}
            tag = self.shown_status(r)
            mark = ON if self.will_write(r) else OFF if self.has_change(r) else ""
            num = _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number"))
            vals = (mark, num, self._name(r), r.get("group", ""), r["current"], tag,
                    "(remove)" if r.get("remove") else r["new_id"], st.get("name", ""),
                    st.get("callSign", ""), r["why"])
            fg = "off" if mark == OFF else "dark"
            return (tag, fg), vals

        # ------------------------------------------------------------ table
        def matches(self, row, find):
            key = FILTERS[self.filter_idx][0]
            if key == "attention":
                if not self.needs_attention(row):
                    return False
            elif key == "ticked":
                if not self.will_write(row):
                    return False
            elif key != "all" and self.shown_status(row) != key:
                return False
            g = self.group_names.get(self.group_v.get())
            if g is not None and g != row.get("group"):
                return False
            if find:
                ch = row["channel"]
                num = ch.get("effective_channel_number") or ch.get("channel_number")
                hay = " ".join(str(x) for x in (self._name(row), row["current"], row["new_id"], _fmt_num(num),
                                                row.get("group", ""), (row["best"] or {}).get("name", ""))).lower()
                for w in find.split():
                    rng = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", w)
                    if rng:
                        try:
                            if num is None or not float(rng.group(1)) <= float(num) <= float(rng.group(2)):
                                return False
                        except (TypeError, ValueError):
                            return False
                    elif w not in hay:
                        return False
            return True

        def set_filter(self, idx):
            self.filter_idx = max(0, idx)
            self.rebuild()
            self.root.after_idle(self.focus_table)

        def schedule_find(self):
            if self.find_job:
                self.root.after_cancel(self.find_job)
            self.find_job = self.root.after(150, self.rebuild)

        def clear_filters(self):
            self.filter_idx = 0
            self.group_v.set("All groups")
            if self.find_v.get():
                self.find_v.set("")   # rebuilds via schedule_find
            else:
                self.rebuild()

        def _sort_key(self, r):
            ch, st = r["channel"], r["best"] or {}
            num = ch.get("effective_channel_number") or ch.get("channel_number")
            col = self.sort_col
            if col == "num":
                try:
                    return (num is None, float(num or 0))
                except (TypeError, ValueError):
                    return (True, 0.0)
            if col in ("current", "new"):
                v = r["current"] if col == "current" else r["new_id"]
                return (not v, int(v) if str(v).isdigit() else 0)
            val = {"write": "0" if self.will_write(r) else "1", "channel": self._name(r),
                   "group": r.get("group"), "status": self.shown_status(r), "gname": st.get("name"),
                   "call": st.get("callSign"), "why": r["why"]}.get(col)
            try:
                n = float(num or 0)
            except (TypeError, ValueError):
                n = 0.0
            return (not val, str(val or "").lower(), n)

        def sort_by(self, col):
            self.sort_rev = not self.sort_rev if self.sort_col == col else False
            self.sort_col = col
            self.update_headings()
            self.rebuild()

        def update_headings(self):
            for c, h in zip(COLS, HEADS):
                arrow = (" ▼" if self.sort_rev else " ▲") if c == self.sort_col else ""
                self.tree.heading(c, text=h + arrow)

        def rebuild(self, keep_view=True):
            """Refill the table from the filters. Edits never call this; they update rows in place,
            so the table doesn't jump and an edited row stays put until you change the filters."""
            if self.find_job:
                self.root.after_cancel(self.find_job)
            self.find_job = None
            sel = set(self.tree.selection())
            top = self.tree.yview()[0]
            self.tree.delete(*self.tree.get_children())
            find = self.find_v.get().strip().lower()
            shown = [(i, r) for i, r in enumerate(self.rows) if self.matches(r, find)]
            shown.sort(key=lambda x: self._sort_key(x[1]), reverse=self.sort_rev)
            for i, r in shown:
                tags, vals = self.row_values(r)
                self.tree.insert("", "end", iid=str(i), tags=tags, values=vals)
            keep = [s for s in sel if self.tree.exists(s)]
            if keep:
                self.tree.selection_set(keep)
            if keep_view:
                self.tree.yview_moveto(top)
            if keep:
                self.tree.see(keep[0])
            self.update_counts()
            self.show_detail_now()

        def update_rows(self, idxs):
            for i in idxs:
                if self.tree.exists(str(i)):
                    tags, vals = self.row_values(self.rows[i])
                    self.tree.item(str(i), values=vals, tags=tags)
            self.update_counts()
            self.save_session()

        def update_counts(self):
            counts = {}
            att = ticked = 0
            for r in self.rows:
                t = self.shown_status(r)
                counts[t] = counts.get(t, 0) + 1
                att += self.needs_attention(r)
                ticked += self.will_write(r)
            labels = []
            for key, label in FILTERS:
                n = len(self.rows) if key == "all" else att if key == "attention" else \
                    ticked if key == "ticked" else counts.get(key, 0)
                labels.append(f"{label} ({n})" if self.rows else label)
            self.show_box.configure(values=labels)
            self.show_v.set(labels[self.filter_idx])
            self.write_btn.configure(text=f"Write {ticked} channel{'' if ticked == 1 else 's'} to Dispatcharr…")
            self.write_btn.state(["disabled"] if self.busy or not ticked else ["!disabled"])
            self.walk_btn.state(["disabled"] if not self.rows else ["!disabled"])
            shown = len(self.tree.get_children())
            self.shown_lbl.configure(text=f"{shown} of {len(self.rows)} channels shown" if self.rows else "")

        def selected(self):
            """[(index, row)] for the selected channels, in table order."""
            sel = set(self.tree.selection())
            return [(int(k), self.rows[int(k)]) for k in self.tree.get_children() if k in sel]

        def targets(self, all_shown=False):
            rows = [(int(k), self.rows[int(k)]) for k in self.tree.get_children()] if all_shown \
                else self.selected()
            if not rows:
                self.note("No channels are shown." if all_shown else "Select a channel in the table first.",
                          warn=True)
            return rows

        def current(self):
            sel = self.tree.selection()
            if len(sel) != 1:
                return None, None
            return int(sel[0]), self.rows[int(sel[0])]

        def table_click(self, e):
            """A click in the Write column ticks or unticks that channel."""
            if self.tree.identify_region(e.x, e.y) != "cell" or self.tree.identify_column(e.x) != "#1":
                return None
            iid = self.tree.identify_row(e.y)
            if not iid:
                return None
            if iid not in self.tree.selection() or len(self.tree.selection()) == 1:
                self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.focus_set()
            self.toggle()
            return "break"

        def table_double(self, e):
            if self.tree.identify_region(e.x, e.y) != "cell":
                return None
            if self.tree.identify_column(e.x) == "#1":
                return self.table_click(e)   # quick clicks in the Write column each count
            if self.tree.identify_row(e.y):
                self.toggle()
            return "break"

        def table_menu(self, e):
            iid = self.tree.identify_row(e.y)
            if not iid:
                return
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.tree.focus(iid)
            m = tk.Menu(self.root, tearoff=0)
            self.fill_channel_menu(m)
            m.tk_popup(e.x_root, e.y_root)

        def walkthrough(self):
            """Show the channels that still need a pick and start at the first one."""
            if not self.matcher:
                self.note("Connect first.", warn=True)
                return
            if not any(self.needs_attention(r) for r in self.rows):
                self.note("No unmatched channels left.")
                return
            self.filter_idx = 1
            self.rebuild()
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])
                self.tree.see(kids[0])
            self.focus_cands()
            if self.root.focus_get() is not self.cand:
                self.tree.focus_set()
            self.note("Pick the right station (click, or press 1-9), then Return for the next channel. "
                      "Down skips one; type in Search to look for other stations.")

        # ------------------------------------------------------------ edits (all update rows in place)
        def _changed(self, idxs, note=None):
            self.update_rows(idxs)
            self.show_detail_now(force=True)
            if note:
                self.note(note)

        def tick_selected(self, on):
            rows = self.targets()
            if not rows:
                return
            if on:
                can = [(i, r) for i, r in rows if self.has_change(r)]
                skipped = len(rows) - len(can)
                for _, r in can:
                    r["write"] = True
                what = f"Ticked {len(can)} channel{'' if len(can) == 1 else 's'}"
                if skipped:
                    what += f"; {skipped} {'has' if skipped == 1 else 'have'} no pick yet"
            else:
                can = [(i, r) for i, r in rows if r["write"]]
                skipped = len(rows) - len(can)
                for _, r in can:
                    r["write"] = False
                what = f"Unticked {len(can)} channel{'' if len(can) == 1 else 's'}"
                if skipped:
                    what += f"; {skipped} {'was' if skipped == 1 else 'were'} not ticked"
            self.update_rows([i for i, _ in can])
            self.show_detail_now(force=True)
            self.note(what + ".", warn=not can)
            if can:
                log.info("%s: %s", what, ", ".join(str(r["channel"]["id"]) for _, r in can))

        def toggle(self):
            """Space / Write column: tick the selection if any of it is unticked, else untick it."""
            rows = self.targets()
            if not rows:
                return
            can = [r for _, r in rows if self.has_change(r)]
            if not can:
                self.note(("This channel has" if len(rows) == 1 else "Those channels have")
                          + " no pick yet: pick a station below first.", warn=True)
                return
            self.tick_selected(not all(r["write"] for r in can))

        def mark_reviewed(self, all_shown=False):
            rows = self.targets(all_shown)
            for _, r in rows:
                r["reviewed"] = True
            if rows:
                self._changed([i for i, _ in rows],
                              f"Marked {len(rows)} channel{'s' if len(rows) > 1 else ''} reviewed.")

        def _apply(self, i, st, sid, why):
            r = self.rows[i]
            r["remove"] = False
            r["best"], r["new_id"], r["write"], r["why"] = st, sid, True, why
            r["status"], r["reviewed"] = MATCHED, True
            if r["current"] and sid != r["current"]:
                r["status"], r["why"] = CHANGE, f"{why}: replaces {r['current']}"
            elif r["current"] and sid == r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"
            log.info("Picked %s for channel %s (%s)", sid, r["channel"]["id"], why)
            outside = sid not in self.matcher.by_id
            self._changed([i], f"{self._name(r)}: "
                          + ("keeping the current ID" if r["status"] == VERIFIED else f"will write {sid}")
                          + (" (not in your lineups)" if outside else "")
                          + ". Press Return for the next channel.")

        def pick_cand(self, iids):
            i, r = self.current()
            iids = [k for k in iids if k != "none"]
            if r is None or not iids:
                return False
            st = next((s for s in self.cand_list if s["stationId"] == iids[0]), None)
            if st is None:
                return False
            sid = st["stationId"]
            if sid == r["new_id"] and r["write"] and not r.get("remove"):
                return True   # already the pick
            if st.get("_stub"):
                st = self.matcher.by_id.get(sid) or r["best"]
            self._apply(i, st, sid, "picked by you")
            if self.cand.exists(sid):
                self.cand.selection_set(sid)
                self.cand.focus(sid)
            return True

        def cand_click(self, e):
            if self.cand.identify_region(e.x, e.y) not in ("cell", "tree"):
                return None   # headings and column dividers work as usual
            iid = self.cand.identify_row(e.y)
            if not iid or iid == "none":
                return "break"
            self.cand.selection_set(iid)
            self.cand.focus(iid)
            self.cand.focus_set()
            self.pick_cand([iid])
            return "break"

        def cand_double(self, e):
            iid = self.cand.identify_row(e.y)
            if iid and iid != "none" and self.cand.identify_region(e.x, e.y) in ("cell", "tree"):
                if self.pick_cand([iid]):
                    self.done_next()
                return "break"
            return None

        def pick_number(self, k):
            """1-9: pick that station and go to the next channel."""
            self.show_detail_now()
            i, r = self.current()
            if r is None:
                self.note("Select one channel to pick a station for it.", warn=True)
                return
            if k > len(self.cand_list):
                self.note(f"There is no station {k} in the list.", warn=True)
                return
            if self.pick_cand([self.cand_list[k - 1]["stationId"]]):
                self.done_next()

        def set_manual(self):
            i, r = self.current()
            sid = self.manual_v.get().strip()
            if r is None:
                self.note("Select one channel in the table first.", warn=True)
                return
            if not sid:
                self.note("Type a station ID in the ID box first.", warn=True)
                return
            if not sid.isdigit():
                self.note("Gracenote station IDs are numbers only.", warn=True)
                return
            st = self.matcher.by_id.get(sid)
            self.manual_v.set("")
            self._apply(i, st, sid, "entered by you")

        def _clear(self, r):
            """No pick and no removal: Write leaves this channel alone."""
            auto = r.get("auto") or {}
            r["write"], r["new_id"], r["best"], r["remove"], r["reviewed"] = False, "", None, False, True
            if r["current"]:
                st = self.matcher.by_id.get(r["current"]) if self.matcher else None
                r["status"] = VERIFIED if st else NOT_IN_LINEUP
                r["best"], r["new_id"] = st, r["current"] if st else ""
                r["why"] = "kept the current ID"
            else:
                r["status"] = auto.get("status") if auto.get("status") in (REVIEW, UNMATCHED) else \
                    REVIEW if r["cands"] else UNMATCHED
                r["why"] = "choice cleared"

        def clear_choice(self):
            rows = self.targets()
            for _, r in rows:
                self._clear(r)
            if rows:
                self._changed([i for i, _ in rows], "Cleared. " + (
                    "That channel" if len(rows) == 1 else f"Those {len(rows)} channels") + " won't change on Write.")

        def reset_rows(self):
            rows = self.targets()
            for _, r in rows:
                r.update(r.get("auto") or {})
                r["remove"] = r["reviewed"] = False
            if rows:
                self._changed([i for i, _ in rows], "Back to what the matcher found.")

        def _set_remove(self, r):
            """Mark a row so Write clears its station ID. Returns False if there is nothing to remove."""
            if not r["current"]:
                return False
            r.update(remove=True, new_id="", best=None, write=True, status=REMOVE,
                     why=f"you chose to remove {r['current']}", reviewed=True)
            log.info("Marked channel %s to remove ID %s", r["channel"]["id"], r["current"])
            return True

        def remove_id(self):
            rows = self.targets()
            if not rows:
                return
            has = [r for _, r in rows if r["current"]]
            done = [i for i, r in rows if self._set_remove(r)]
            if not done:
                self.note(("This channel has" if len(rows) == 1 else "Those channels have")
                          + " no station ID to remove.", warn=True)
                return
            self._changed(done, f"{len(done)} station ID{'' if len(done) == 1 else 's'} will be removed on "
                                "Write." + (f" {len(rows) - len(done)} had no ID." if len(done) < len(rows) else ""))

        @staticmethod
        def _mark_change(r):
            if r["current"] and r["new_id"] != r["current"]:
                r["status"], r["why"] = CHANGE, f"{r['why']}: replaces {r['current']}"
            elif r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"

        def done_next(self):
            """Accept the selected channel(s) as they are and move to the next channel shown."""
            rows = self.selected()
            if not rows:
                self.focus_table()
                return
            for _, r in rows:
                r["reviewed"] = True
            kids = list(self.tree.get_children())
            last = max(kids.index(str(i)) for i, _ in rows)
            nxt = kids[last + 1] if last + 1 < len(kids) else None
            self.update_rows([i for i, _ in rows])
            find = self.find_v.get().strip().lower()
            for i, r in rows:   # handled rows leave a filtered view such as Needs attention
                if not self.matches(r, find):
                    self.tree.delete(str(i))
            self.update_counts()
            if len(rows) == 1:
                r = rows[0][1]
                what = "will be written" if self.will_write(r) else self.shown_status(r).lower()
                self.note(f"{self._name(r)}: {what}. "
                          + ("" if nxt else "That was the last channel shown. Press Write when you're ready."))
            if nxt and self.tree.exists(nxt):
                self.tree.selection_set(nxt)
                self.tree.focus(nxt)
                self.tree.see(nxt)
            else:
                self.tree.selection_set(())
            self.tree.focus_set()

        # ------------------------------------------------------------ channel detail
        def set_detail_enabled(self, on):
            for b in self.d_buttons:
                b.state(["!disabled"] if on else ["disabled"])

        def schedule_detail(self):
            """Coalesce selection changes (holding an arrow key) into one detail refresh."""
            if self.detail_job:
                self.root.after_cancel(self.detail_job)
            self.detail_job = self.root.after_idle(self.show_detail_now)

        def show_detail_now(self, force=False):
            if self.detail_job:
                self.root.after_cancel(self.detail_job)
            self.detail_job = None
            i, r = self.current()
            same = i is not None and i == self.shown_i
            if same and not force:
                return
            self.shown_i = i
            if not same:
                self.extra_for = None
                for job in (self.sugg_job, self.extra_job):
                    if job:
                        self.root.after_cancel(job)
                self.sugg_job = self.extra_job = None
                self._set_search_quiet("")   # a new channel starts with its suggestions
            if r is None:
                n = len(self.tree.selection())
                self.render_list(None, [], False, "Suggestions", "")
                self.set_detail_enabled(False)
                if n:
                    self.d_title.configure(text=f"{n} channels selected")
                    self.d_sub.configure(text="Tick selected, Untick selected and Mark reviewed (below the "
                                              "table, or right-click) change them all at once.")
                else:
                    self.d_title.configure(text="Select a channel above." if self.rows else "")
                    self.d_sub.configure(text="")
                return
            self.set_detail_enabled(True)
            ch = r["channel"]
            num = _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number"))
            self.d_title.configure(text=f"{str(num) + '   ' if num != '' else ''}{self._name(r)}"
                                        + (f"   ·   {r['group']}" if r.get("group") else ""))
            cur = r["current"]
            cur_st = self.matcher.by_id.get(cur) if cur else None
            sub = (f"tvg-id: {ch.get('effective_tvg_id') or '-'}   ·   current ID: "
                   + (f"{cur} ({cur_st['name']})" if cur_st else f"{cur} (not in your lineups)" if cur else "-")
                   + f"   ·   {r['why']}")
            st = r["best"] or {}
            if r.get("remove") and cur:
                plan = f"On Write: remove the station ID {cur}."
            elif self.has_change(r):
                pick = f"{st.get('name') or ''} [{st.get('callSign') or ''}] {r['new_id']}".replace("[] ", "")
                plan = (f"On Write: set {pick.strip()}." if r["write"]
                        else f"Picked {pick.strip()}, but unticked: not written (Space ticks it).")
            else:
                plan = "No change on Write." + ("" if r["new_id"] or cur else " Pick a suggestion, search, or "
                                                                            "type a station ID.")
            self.d_sub.configure(text=sub + "\n" + plan)
            if self.search_v.get().strip() and self.hit_list is not None:
                self.render_search(same=same)
            else:
                self.fill_cands(i, r, same)

        def _station(self, sid, label):
            st = self.matcher.by_id.get(sid) if self.matcher else None
            return st or {"stationId": sid, "name": label, "callSign": "", "type": "", "lineups": [],
                          "_stub": True}

        def render_list(self, r, lst, same, title, info):
            """Fill the station list. Both suggestions and search results go through here, so they
            look and work the same: click picks, 1-9 picks and moves on, Return moves on."""
            top = self.cand.yview()[0] if same else 0.0
            sel = self.cand.selection() if same else ()
            self.cand.delete(*self.cand.get_children())
            self.cand_list = list(lst)
            for n, s in enumerate(self.cand_list, 1):
                sid = s["stationId"]
                inside = self.matcher is not None and sid in self.matcher.by_id
                picked = r is not None and sid == r["new_id"] and not r.get("remove")
                is_cur = r is not None and sid == r["current"]
                where = []
                if is_cur:
                    where.append("current ID, will be REMOVED" if r.get("remove") else "current ID")
                where.append(f"in {len(self.matcher.by_id[sid]['lineups'])} lineups" if inside
                             else "not in your lineups")
                tag = "picked" if picked else "removed" if is_cur and r.get("remove") \
                    else "current" if is_cur else "" if inside else "outside"
                if self.cand.exists(sid):
                    continue
                self.cand.insert("", "end", iid=sid, tags=(tag,) if tag else (), values=(
                    "●" if picked else "", n if n <= 9 else "", s.get("name") or "", s.get("callSign") or "",
                    s.get("type") or "", ", ".join(where), sid))
            if not self.cand_list and r is not None:
                empty = ("Nothing found yet. Press Return to search all of Gracenote." if title.startswith("Search")
                         else "No suggestions. Type in the search box above, or enter a station ID.")
                self.cand.insert("", "end", iid="none", values=("", "", empty, "", "", "", ""))
            keep = [s for s in sel if self.cand.exists(s)]
            if keep:
                self.cand.selection_set(keep)
            self.cand.yview_moveto(top)
            self.list_box.configure(text=title)
            self.cand_info.configure(text=info)

        def fill_cands(self, i, r, same):
            """The matcher's suggestions for one channel (shown while the search box is empty)."""
            name = self._name(r)
            if self.matcher and "_sugg" not in r:
                why = str((r.get("auto") or {}).get("why") or "")
                if why.startswith(("similar names only", "nothing similar in your lineups")):
                    r["_sugg"] = list(r["cands"])   # the matcher already ran the same suggest()
                elif not same:
                    # A full suggest() scans every station (a few hundred ms on big lineups), so it
                    # waits until the selection settles instead of slowing down arrow keys.
                    if self.sugg_job:
                        self.root.after_cancel(self.sugg_job)
                    self.sugg_job = self.root.after(200, lambda: self._compute_sugg(i))
            lst = merge_candidates(self.matcher, name, r["cands"], r.get("_sugg"), r.get("_extra"), limit=30) \
                if self.matcher else []
            best = name_score(name, lst[0]["name"], _bare_call(lst[0])) if lst else 0.0
            ids = {s["stationId"] for s in lst}
            for sid, label in ((r["new_id"], "(entered by you)"), (r["current"], "(current ID)")):
                if sid and sid not in ids:
                    lst.insert(0, r["best"] if r["best"] and r["best"].get("stationId") == sid
                               else self._station(sid, label))
                    ids.add(sid)
            info = (f"{len(lst)} suggestion{'' if len(lst) == 1 else 's'}   ·   click to pick, double-click to "
                    "pick and go to the next, Return for the next channel") if lst else ""
            if r.get("_extra_busy"):
                info += "   ·   looking wider in Gracenote…"
            elif r.get("_extra_err"):
                info += f"   ·   Gracenote-wide search failed ({r['_extra_err']}); press Return in Search to retry"
            self.render_list(r, lst, same, f"Suggestions for {name}", info)
            if self.cdvr and "_sugg" in r and best < 0.9 and "_extra" not in r and not r.get("_extra_busy") \
                    and self.extra_for != i:
                if self.extra_job:
                    self.root.after_cancel(self.extra_job)
                self.extra_for = i
                self.extra_job = self.root.after(400, lambda: self._start_extra(i))

        def _showing_suggestions(self, i):
            return self.shown_i == i and not self.search_v.get().strip()

        def _compute_sugg(self, i):
            self.sugg_job = None
            if self.shown_i != i or i >= len(self.rows) or "_sugg" in self.rows[i]:
                return
            r = self.rows[i]
            r["_sugg"] = self.matcher.suggest(self._name(r))
            if self._showing_suggestions(i):
                self.fill_cands(i, r, True)

        def _start_extra(self, i):
            """Gracenote-wide suggestions for a weak match, fetched in the background."""
            self.extra_job = None
            if self.shown_i != i or i >= len(self.rows):
                return
            r = self.rows[i]
            if "_extra" in r or r.get("_extra_busy"):
                return
            r["_extra_busy"] = True
            r.pop("_extra_err", None)
            cdvr, matcher, name, rows = self.cdvr, self.matcher, self._name(r), self.rows
            if self._showing_suggestions(i):
                self.fill_cands(i, r, True)

            def bg():
                try:
                    extra, err = global_candidates(cdvr, name, matcher), None
                except Exception as e:  # network trouble only loses the extra suggestions
                    extra, err = None, e
                self.q.put(("call", (None, lambda: self._extra_done(rows, i, extra, err))))
            threading.Thread(target=bg, name="suggest", daemon=True).start()

        def _extra_done(self, rows, i, extra, err):
            if rows is not self.rows:
                return   # reloaded meanwhile
            r = rows[i]
            r.pop("_extra_busy", None)
            if err is not None:
                log.warning("Background Gracenote search for %r failed: %s", self._name(r), err)
                r["_extra_err"] = str(err).split(":")[0][:60]
            else:
                r["_extra"] = extra or []
            if self._showing_suggestions(i):
                self.fill_cands(i, r, True)

        # ------------------------------------------------------------ search
        def _set_search_quiet(self, text):
            """Set the search box without starting a search."""
            if self.search_job:
                self.root.after_cancel(self.search_job)
                self.search_job = None
            self.search_gen += 1
            self.hit_list = None
            self.quiet_search = True
            try:
                self.search_v.set(text)
            finally:
                self.quiet_search = False

        def schedule_search(self):
            if self.quiet_search:
                return
            if self.search_job:
                self.root.after_cancel(self.search_job)
            delay = 250 if self.search_v.get().strip() else 0   # clearing brings the suggestions straight back
            self.search_job = self.root.after(delay, self.run_search)

        def search_wide(self):
            """Return in the search box: search all of Gracenote now (empty box: this channel's name)."""
            if not self.search_v.get().strip():
                _, r = self.current()
                if r is None:
                    return
                self._set_search_quiet(search_phrase(self._name(r)) or self._name(r))
            self.run_search(wide=True, focus=True)

        def run_search(self, wide=None, focus=False):
            """Local lineup results right away; Gracenote-wide results follow in the background when
            the local ones are weak, when "All of Gracenote" is ticked, or on Return. Results from an
            older search are dropped."""
            if self.search_job:
                self.root.after_cancel(self.search_job)
            self.search_job = None
            self.search_gen += 1
            gen = self.search_gen
            text = self.search_v.get().strip()
            if not text or not self.matcher:
                self.hit_list = None
                self.shown_i = None   # show the suggestions again
                self.show_detail_now()
                return
            m = self.matcher
            local = merge_candidates(m, text, m.search(text), m.suggest(text))
            if text.isdigit() and text in m.by_id:
                local = [m.by_id[text]] + [s for s in local if s["stationId"] != text]
            wide = self.wide_v.get() if wide is None else wide
            best = name_score(text, local[0]["name"], _bare_call(local[0])) if local else 0.0
            go_wide = bool(self.cdvr) and len(text) >= 2 and not text.isdigit() and (wide or best < GOOD_LOCAL)
            self.hit_list, self.hit_note = local, "searching all of Gracenote…" if go_wide else ""
            self.render_search(focus=focus)
            if not go_wide:
                return
            cdvr = self.cdvr

            def bg():
                try:
                    extra, err = global_candidates(cdvr, text, m, limit=25), None
                except Exception as e:  # only the extra results are lost
                    extra, err = None, e
                self.q.put(("call", (None, lambda: self._wide_done(gen, m, text, local, extra, err, focus))))
            threading.Thread(target=bg, name="search", daemon=True).start()

        def _wide_done(self, gen, matcher, text, local, extra, err, focus):
            if gen != self.search_gen or matcher is not self.matcher:
                return
            if err is not None:
                log.debug("Gracenote search %r failed: %s", text, err)
                self.hit_note = f"Gracenote-wide search failed: {err}"
            else:
                self.hit_list = merge_candidates(matcher, text, local, extra, limit=30)
                self.hit_note = ""
            self.render_search(same=True, focus=focus and self.root.focus_get() is not self.cand)

        def render_search(self, same=False, focus=False):
            _, r = self.current()
            text = self.search_v.get().strip()
            lst = self.hit_list or []
            info = (f"{len(lst)} found   ·   click to pick, Esc or ✕ for the suggestions" if lst
                    else "Nothing found yet")
            if self.hit_note:
                info += f"   ·   {self.hit_note}"
            if r is None and lst:
                info += "   ·   select one channel to pick for it"
            self.render_list(r, lst, same, f"Search results for '{text}'", info)
            if focus:
                self.focus_cands()

        # ------------------------------------------------------------ export and write
        def export(self):
            rows = [r for r in self.rows if not r["write"] and r["status"] != VERIFIED]
            if not rows:
                self.note("Nothing to export: every channel has an ID ticked or verified.", warn=True)
                return
            path = filedialog.asksaveasfilename(defaultextension=".csv", parent=self.root,
                                                initialfile="unmatched_stationids.csv")
            if not path:
                return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["channel_number", "name", "tvg_id", "current_id", "status", "reason",
                                "candidates"])
                    for r in rows:
                        ch = r["channel"]
                        w.writerow([ch.get("effective_channel_number"), ch.get("effective_name"),
                                    ch.get("effective_tvg_id") or "", r["current"], r["status"], r["why"],
                                    "; ".join(f"{s['stationId']} {s['name']} [{s['callSign']}]"
                                              for s in r["cands"][:5])])
            except OSError as e:
                messagebox.showerror("Export failed", f"Could not save {path}:\n\n{e}", parent=self.root)
                return
            self.note(f"Saved {len(rows)} unmatched channels to {path}")

        def write(self):
            if self.busy:
                self.note("Still working, one moment… (Esc cancels)", warn=True)
                return
            todo = [r for r in self.rows if self.will_write(r)]
            if not todo or not self.disp:
                self.note("Nothing to write: no ticked channel has a new station ID.", warn=True)
                return
            removals = [r for r in todo if r.get("remove")]
            changes = [r for r in todo if r["current"] and not r.get("remove")]
            msg = f"Write {len(todo)} station ID changes to Dispatcharr?"
            if removals:
                lines = [f"  {self._name(r)}: {r['current']} -> (no ID)" for r in removals[:12]]
                if len(removals) > 12:
                    lines.append(f"  ...and {len(removals) - 12} more")
                msg += f"\n\n{len(removals)} of these REMOVE the ID:\n" + "\n".join(lines)
            if changes:
                lines = [f"  {self._name(r)}: {r['current']} -> {r['new_id']}" for r in changes[:12]]
                if len(changes) > 12:
                    lines.append(f"  ...and {len(changes) - 12} more")
                msg += f"\n\n{len(changes)} of these REPLACE an existing ID:\n" + "\n".join(lines)
            if not messagebox.askyesno("Write to Dispatcharr", msg, parent=self.root):
                return
            log.info("Writing %d IDs, %d replacing existing, %d removing: %s", len(todo), len(changes),
                     len(removals), ", ".join(f"{r['channel']['id']}:{r['current']}->{r['new_id'] or '(none)'}"
                                              for r in changes + removals))
            self.save_session(True)
            # The worker gets a copy, so picks you change while it runs are not mixed into this write.
            plan = [{"channel": r["channel"], "new_id": r["new_id"], "remove": bool(r.get("remove"))}
                    for r in todo]
            disp, rows = self.disp, self.rows

            def job(progress):
                results = disp.write_station_ids(plan, progress)

                def finish():
                    ok = deleted = 0
                    errors = []
                    for r, p in zip(todo, plan):
                        kind, msg = results.get(p["channel"]["id"], ("error", "no result"))
                        same = r["new_id"] == p["new_id"] and bool(r.get("remove")) == p["remove"]
                        if kind == "ok":
                            ok += 1
                            r["current"] = "" if p["remove"] else p["new_id"]
                            if not same:   # you picked something else while this was writing
                                self._mark_change(r)
                            elif p["remove"]:
                                r.update(status=UNMATCHED, write=False, remove=False, why="ID removed")
                            else:
                                r.update(status=VERIFIED, write=False, why="written")
                            if same:
                                r["reviewed"] = False   # a removed ID is back in Needs attention
                                r["auto"] = {k: r[k] for k in ("status", "best", "new_id", "write", "why")}
                        elif kind == "deleted":
                            r.update(status=DELETED, write=False, why=msg)
                            deleted += 1
                        else:
                            r.update(status=ERROR, why=f"write failed: {msg}")  # stays ticked for a retry
                            errors.append(f"{self._name(r)}: {msg}")
                    if rows is self.rows:
                        written = {id(r) for r in todo}
                        self.update_rows([i for i, r in enumerate(rows) if id(r) in written])
                        self.show_detail_now(force=True)
                    self.prog["value"] = 100
                    self.note(f"Wrote {ok} station IDs."
                              + (f" {deleted} skipped (deleted)." if deleted else "")
                              + (f" {len(errors)} failed and are still ticked." if errors else ""),
                              warn=bool(errors or deleted))
                    self.save_session(True)
                    if deleted or errors:
                        msg = f"Wrote and verified {ok} of {len(todo)} station IDs.\n"
                        if deleted:
                            msg += (f"\n{deleted} channels no longer exist in Dispatcharr and were skipped. "
                                    "Press Reload to refresh the list.")
                        if errors:
                            msg += (f"\n{len(errors)} failed and are still ticked, so you can press Write "
                                    "again:\n  " + "\n  ".join(errors[:8]))
                            if len(errors) > 8:
                                msg += f"\n  ...and {len(errors) - 8} more"
                        msg += f"\n\nDetails are in the log:\n{LOG_FILE}"
                        messagebox.showwarning("Write finished with problems", msg, parent=self.root)
                return finish

            self._worker(job, "write")

    App(root)
    root.mainloop()


# ======================================================================
# Dependency bootstrap
# ======================================================================

def _has_tk(python):
    """True if the given interpreter can import tkinter."""
    try:
        return subprocess.run([python, "-c", "import tkinter"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _other_pythons_with_tk():
    """Other interpreters on this machine that already have Tk."""
    found = []
    if sys.platform == "win32" and shutil.which("py"):
        try:
            out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=30).stdout
            found += re.findall(r"([A-Za-z]:\\\S.*?python(?:w)?\.exe)", out, re.I)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        found += ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"]
        found += [shutil.which(n) for n in ("python3", "python3.13", "python3.12", "python3.11")]
    seen, out = set(), []
    me = os.path.realpath(sys.executable)
    for p in found:
        if not p or not os.path.exists(p):
            continue
        real = os.path.realpath(p)
        if real in seen or real == me:
            continue
        seen.add(real)
        if _has_tk(p):
            out.append(p)
    return out


def _install_command():
    """(description, command) that installs Tk for this Python, or None."""
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.platform == "darwin":
        brew = shutil.which("brew") or next(
            (b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(b)), None)
        exe = os.path.realpath(sys.executable)
        if brew and ("/Cellar/" in exe or exe.startswith(("/opt/homebrew", "/usr/local/opt"))):
            return f"Homebrew python-tk@{ver}", [brew, "install", f"python-tk@{ver}"]
        return None
    if sys.platform == "win32":
        if shutil.which("winget"):
            # python.org builds include Tk by default; this repairs/installs one that has it.
            return ("Python with Tk from python.org (winget)",
                    ["winget", "install", "-e", "--id", f"Python.Python.{ver}",
                     "--accept-package-agreements", "--accept-source-agreements"])
        return None
    managers = [("apt-get", ["apt-get", "install", "-y", "python3-tk"]),
                ("dnf", ["dnf", "install", "-y", "python3-tkinter"]),
                ("pacman", ["pacman", "-S", "--noconfirm", "tk"]),
                ("zypper", ["zypper", "--non-interactive", "install", "python3-tk"])]
    for name, cmd in managers:
        if shutil.which(name):
            if os.geteuid() != 0:
                cmd = ["sudo"] + cmd
            return f"{cmd[-1]} via {name}", cmd
    return None


def _manual_help():
    return ("Install Tk for Python manually:\n"
            "  Windows: reinstall Python from https://www.python.org/downloads/ and keep\n"
            "           'tcl/tk and IDLE' ticked\n"
            "  macOS:   brew install python-tk   (or run with /usr/bin/python3)\n"
            "  Linux:   sudo apt install python3-tk   (or your distro's equivalent)")


def _relaunch(python):
    args = [python, os.path.abspath(__file__)] + [a for a in sys.argv[1:] if a != "--yes"]
    env = dict(os.environ, CDSM_RELAUNCHED="1")
    raise SystemExit(subprocess.call(args, env=env))


def ensure_dependencies(assume_yes=False, check_only=False):
    """Make sure Tk is available, installing or switching interpreters if needed."""
    if sys.version_info < (3, 8):
        raise SystemExit(f"Python 3.8 or newer is required (this is {sys.version.split()[0]}).")
    try:
        import tkinter  # noqa: F401
        if check_only:
            print(f"OK: {sys.executable} (Python {sys.version.split()[0]}) has Tk "
                  f"{tkinter.TkVersion}. Nothing to install.")
        return
    except ImportError:
        pass
    if os.environ.get("CDSM_RELAUNCHED"):
        raise SystemExit("Tk is still missing after the install.\n" + _manual_help())

    print(f"This Python ({sys.executable}) has no Tk support, which the GUI needs.")
    plan = _install_command()
    others = _other_pythons_with_tk()
    if check_only:
        print("Would install: " + (f"{plan[0]}\n  {' '.join(plan[1])}" if plan else "nothing available"))
        print("Pythons that already have Tk: " + (", ".join(others) or "none"))
        return

    if plan:
        ok = assume_yes
        if not ok and sys.stdin and sys.stdin.isatty():
            ok = input(f"Install {plan[0]} now?\n  {' '.join(plan[1])}\n[Y/n]: ").strip().lower() in ("", "y", "yes")
        if ok:
            print("Running: " + " ".join(plan[1]))
            if subprocess.call(plan[1]) == 0:
                print("Installed. Restarting...")
                _relaunch(sys.executable)
            print("The install did not succeed.")
    if others:
        print(f"Using {others[0]} instead, which already has Tk.")
        _relaunch(others[0])
    raise SystemExit(_manual_help())


def migrate_config_dir():
    """Move settings from the folder used before the rename, once."""
    if os.path.isdir(OLD_CONFIG_DIR) and not os.path.exists(CONFIG_DIR):
        try:
            os.rename(OLD_CONFIG_DIR, CONFIG_DIR)
        except OSError:
            pass


def main():
    migrate_config_dir()
    setup_logging()
    log.info("Starting %s on %s, Python %s", os.path.basename(__file__), sys.platform,
             sys.version.split()[0])
    ensure_dependencies(assume_yes="--yes" in sys.argv, check_only="--check-deps" in sys.argv)
    if "--check-deps" in sys.argv:
        return
    run_gui()


if __name__ == "__main__":
    main()
