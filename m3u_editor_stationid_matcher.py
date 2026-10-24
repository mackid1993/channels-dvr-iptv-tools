#!/usr/bin/env python3
"""Channels DVR Gracenote Station ID Matcher for m3u-editor (GUI).

Matches m3u-editor channels to Gracenote station IDs using your Channels DVR
server and lets you review the results. m3u-editor's API cannot set a station
ID, so the tool exports a small m3u-editor plugin ("Gracenote Station IDs")
that you install once, then copies your picks for its Apply Station IDs action.
m3u-editor writes them to the channels' station_id field, which it outputs as
tvc-guide-stationid and keeps across provider re-syncs.

Accuracy rules:
  * Candidates come only from the real TV lineups (cable, satellite, OTA) that
    Gracenote lists for the chosen country and postal code, so a feed from
    another country or a test/VOD entry can never be picked.
  * A match is only marked "Matched" (and pre-ticked) when it is exact and
    unique: an explicit call sign such as "(WABC)", or a channel name equal to
    the Gracenote name or call sign after removing words like HD/East/Channel.
    Anything else is "Review" or "Unmatched" and is never included unless you
    pick it yourself.

Runs on Windows, macOS and Linux with Python 3.8+. The only dependency is
Tk (tkinter). On launch the script checks for it and, if it is missing,
offers to install it (Homebrew, winget, apt/dnf/pacman/zypper) or switches
to another Python on the machine that already has it.

    python3 m3u_editor_stationid_matcher.py            (macOS / Linux)
    py m3u_editor_stationid_matcher.py                 (Windows, or double-click)
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
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".m3u_editor_stationid_matcher")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")   # autosaved picks
UI_FILE = os.path.join(CONFIG_DIR, "window.json")         # window size and layout
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "matcher.log")

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
    """A JSON object from a file, or {} if it is missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


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
    "Not in m3u-editor", "Will replace ID", "Will remove ID")
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
                            "Check the API token.", e.code, body) from None
        if e.code == 404:
            raise HttpError(f"{host} has no {path} (HTTP 404). Is this the right server and port?",
                            404, body) from None
        if e.code == 429:
            m = re.search(r"available in (\d+) second", body)
            wait = int(m.group(1)) if m else 60
            err = HttpError(f"{host} is limiting requests. Wait {wait} seconds and try again.", 429, body)
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
            pass  # unreadable cache: build it again
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
# m3u-editor
# ======================================================================

# The REST API can list channels but cannot set a Gracenote station ID, so writes go
# through this small m3u-editor plugin. The tool exports it as a zip to install once.
PLUGIN_ID = "gracenote-station-ids"
PLUGIN_ZIP = f"{PLUGIN_ID}.zip"
PLUGIN_JSON = r'''{
  "id": "gracenote-station-ids",
  "name": "Gracenote Station IDs",
  "version": "1.0.0",
  "api_version": "1.0.0",
  "description": "Applies Gracenote station IDs (tvc-guide-stationid) picked in the Channels DVR Station ID Matcher to a playlist's channels.",
  "entrypoint": "Plugin.php",
  "class": "AppLocalPlugins\\GracenoteStationIds\\Plugin",
  "capabilities": [
    "channel_processor"
  ],
  "hooks": [],
  "permissions": [
    "db_read",
    "db_write",
    "queue_jobs"
  ],
  "schema": {
    "tables": []
  },
  "data_ownership": {
    "tables": [],
    "directories": [],
    "files": [],
    "default_cleanup_policy": "preserve"
  },
  "settings": [],
  "actions": [
    {
      "id": "apply_station_ids",
      "label": "Apply Station IDs",
      "dry_run": false,
      "requires_confirmation": true,
      "fields": [
        {
          "id": "playlist_id",
          "label": "Playlist",
          "type": "model_select",
          "model": "App\\Models\\Playlist",
          "label_attribute": "name",
          "scope": "owned",
          "required": true
        },
        {
          "id": "station_ids",
          "label": "Station IDs from the matcher",
          "type": "textarea",
          "required": true,
          "helper_text": "Paste what the matcher copied. One channel per line: channel_id=station_id. An empty station ID removes it."
        }
      ]
    }
  ]
}'''
PLUGIN_PHP = r'''<?php

namespace AppLocalPlugins\GracenoteStationIds;

use App\Models\Channel;
use App\Plugins\Contracts\ChannelProcessorPluginInterface;
use App\Plugins\Contracts\PluginInterface;
use App\Plugins\Support\PluginActionResult;
use App\Plugins\Support\PluginExecutionContext;

/**
 * Writes station IDs chosen in the Channels DVR Station ID Matcher.
 *
 * m3u-editor's REST API cannot set a channel's Gracenote station ID, so the
 * matcher copies its picks as "channel_id=station_id" lines and this action
 * applies them. Nothing is written unless every line is valid, and only
 * channels that belong to the selected playlist are touched.
 */
class Plugin implements ChannelProcessorPluginInterface, PluginInterface
{
    public function runAction(string $action, array $payload, PluginExecutionContext $context): PluginActionResult
    {
        return match ($action) {
            'apply_station_ids' => $this->apply($payload, $context),
            default => PluginActionResult::failure("Unsupported action [{$action}]."),
        };
    }

    private function apply(array $payload, PluginExecutionContext $context): PluginActionResult
    {
        $playlistId = (int) ($payload['playlist_id'] ?? 0);
        if ($playlistId === 0) {
            return PluginActionResult::failure('Pick the playlist the station IDs belong to.');
        }

        [$wanted, $errors] = $this->parse((string) ($payload['station_ids'] ?? ''));
        if ($errors !== []) {
            return PluginActionResult::failure(
                'Nothing was written. Fix these lines and run it again: '.implode('; ', array_slice($errors, 0, 10)),
                ['errors' => $errors]
            );
        }
        if ($wanted === []) {
            return PluginActionResult::failure('No station IDs were pasted.');
        }

        $query = Channel::query()->where('playlist_id', $playlistId)->whereIn('id', array_keys($wanted));
        if ($context->user) {
            $query->where('user_id', $context->user->id);
        }
        $found = $query->pluck('id')->map(fn ($id) => (int) $id)->all();
        $missing = array_values(array_diff(array_keys($wanted), $found));
        foreach ($missing as $id) {
            $context->warning("Channel #{$id} is not in this playlist (deleted, or a different playlist). Skipped.");
        }

        $written = 0;
        foreach ($found as $i => $id) {
            if ($context->dryRun) {
                continue;
            }
            $sid = $wanted[$id];
            Channel::query()->whereKey($id)->update(['station_id' => $sid === '' ? null : $sid]);
            $written++;
            if (($i + 1) % 100 === 0) {
                $context->heartbeat(progress: (int) ((($i + 1) / count($found)) * 100));
            }
        }

        // Read back every channel and confirm it holds the requested value.
        $mismatched = [];
        if (! $context->dryRun && $found !== []) {
            $now = Channel::query()->whereIn('id', $found)->pluck('station_id', 'id');
            foreach ($found as $id) {
                if ((string) ($now[$id] ?? '') !== $wanted[$id]) {
                    $mismatched[] = $id;
                }
            }
        }

        $summary = sprintf('Wrote %d station IDs to playlist #%d.', $written, $playlistId);
        if ($missing !== []) {
            $summary .= sprintf(' %d channels were not in the playlist and were skipped.', count($missing));
        }
        if ($mismatched !== []) {
            return PluginActionResult::failure(
                $summary.' '.count($mismatched).' did not read back correctly: #'.implode(', #', $mismatched),
                ['written' => $written, 'skipped' => $missing, 'mismatched' => $mismatched]
            );
        }

        return PluginActionResult::success($summary, [
            'written' => $written,
            'skipped' => $missing,
            'dry_run' => $context->dryRun,
        ]);
    }

    /**
     * @return array{0: array<int, string>, 1: array<int, string>}
     */
    private function parse(string $text): array
    {
        $wanted = [];
        $errors = [];
        foreach (preg_split('/\R/', $text) as $n => $line) {
            $line = trim((string) preg_replace('/#.*$/', '', $line));
            if ($line === '') {
                continue;
            }
            if (! preg_match('/^(\d+)\s*[=,\t ]\s*(\d{1,12})?$/', $line, $m)) {
                $errors[] = 'line '.($n + 1).': "'.mb_substr($line, 0, 40).'"';

                continue;
            }
            $id = (int) $m[1];
            $sid = $m[2] ?? '';
            if (isset($wanted[$id]) && $wanted[$id] !== $sid) {
                $errors[] = 'line '.($n + 1).": channel #{$id} is listed twice with different IDs";

                continue;
            }
            $wanted[$id] = $sid;
        }

        return [$wanted, $errors];
    }
}'''
PAGE = 1000  # m3u-editor's maximum page size for /channel/get


def export_plugin(path):
    """Write the m3u-editor plugin archive (plugin.json + Plugin.php at the zip root)."""
    import zipfile
    with zipfile.ZipFile(path + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("plugin.json", PLUGIN_JSON)
        z.writestr("Plugin.php", PLUGIN_PHP)
    os.replace(path + ".tmp", path)
    log.info("Exported the m3u-editor plugin to %s", path)


_PLAYLIST_PATH = re.compile(r"^/([0-9a-fA-F-]{36})/playlist\.m3u$")
_EXTINF_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_INTERNAL_URL = re.compile(r"/live/[^/]+/[^/]+/(\d+)\.[A-Za-z0-9]+$")


def parse_playlist_url(text):
    """(base, uuid, full_url) from the playlist URL you gave Channels DVR, or ValueError."""
    url = (text or "").strip()
    if not url:
        raise ValueError("Paste the m3u-editor playlist URL (the one you gave Channels DVR).")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    p = urllib.parse.urlparse(url)
    m = _PLAYLIST_PATH.match(p.path)
    if not m:
        raise ValueError("That is not an m3u-editor playlist URL. It looks like "
                         "http://192.168.1.10:36400/<playlist id>/playlist.m3u")
    base = validate_address(f"{p.scheme}://{p.netloc}", "m3u-editor", 36400)
    return base, m.group(1).lower(), url


def parse_m3u(text):
    """[{attrs..., "title", "url"}] for every channel in an M3U playlist."""
    out, cur = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            head, _, title = line.partition(",")
            cur = dict(_EXTINF_ATTR.findall(head))
            cur["title"] = title.strip()
        elif line and not line.startswith("#") and cur is not None:
            cur["url"] = line
            out.append(cur)
            cur = None
    return out


def fetch_text(url, timeout=TIMEOUT, retries=2):
    for attempt in range(retries + 1):
        try:
            return _http_once("GET", url, {"Accept": "*/*"}, None, timeout).decode("utf-8", "replace")
        except (NetError, HttpError) as e:
            if (isinstance(e, HttpError) and e.code not in (429, 500, 502, 503, 504)) or attempt == retries:
                raise
            _sleep(1.5 * (2 ** attempt))


class M3uEditor:
    """m3u-editor client: channels from the API, current station IDs from the playlist output."""

    def __init__(self, playlist_url, token):
        self.base, self.uuid, self.m3u_url = parse_playlist_url(playlist_url)
        self.token = token
        self.playlist = {"name": self.uuid, "id": None}

    def api(self, path, timeout=TIMEOUT, retries=2):
        return http("GET", self.base + path, {"Authorization": f"Bearer {self.token}"},
                    timeout=timeout, retries=retries)

    def _find_playlist(self, timeout, retries):
        lists = self.api("/user/playlists", timeout, retries) or []
        pl = next((x for x in lists if isinstance(x, dict) and x.get("uuid") == self.uuid), None)
        if pl is None:
            raise RuntimeError("This API token's user has no playlist with that URL. Check the "
                               "playlist URL, or create the token as the user that owns it.")
        if pl.get("type") != "playlist":
            raise RuntimeError(f"'{pl.get('name')}' is a {str(pl.get('type')).replace('_', ' ')}. "
                               "Station IDs are stored on the provider playlist's channels, so use "
                               "the URL of the playlist that imports from your provider.")
        self.playlist = {"name": pl.get("name") or self.uuid, "id": None}

    def _api_channels(self):
        chans, offset = [], 0
        while True:
            q = urllib.parse.urlencode({"playlist_uuid": self.uuid, "is_vod": "false", "enabled": "true",
                                        "limit": PAGE, "offset": offset, "sort": "id"})
            page = self.api(f"/channel/get?{q}") or {}
            data = page.get("data") or []
            # The API ignores an unknown playlist_uuid, so never trust a row from another playlist.
            chans += [c for c in data if (c.get("playlist") or {}).get("uuid") == self.uuid]
            offset += len(data)
            if len(data) < PAGE or offset >= int((page.get("meta") or {}).get("total") or 0):
                return chans

    def load(self, timeout=TIMEOUT, retries=2):
        """Channels as row dicts for the matcher, with their current station ID."""
        self._find_playlist(timeout, retries)
        chans = self._api_channels()
        if chans:
            self.playlist["id"] = (chans[0].get("playlist") or {}).get("id")
        entries = parse_m3u(fetch_text(self.m3u_url))
        by_url = {c.get("url"): c["id"] for c in chans if c.get("url")}
        ids = {c["id"] for c in chans}
        here = urllib.parse.urlparse(self.m3u_url).netloc.lower()
        found = {}
        for e in entries:
            cid = by_url.get(e["url"])                       # "Disable M3U Xtream format" on
            if cid is None:                                  # default internal URLs
                u = urllib.parse.urlparse(e["url"])
                m = _INTERNAL_URL.search(u.path)
                if m and u.netloc.lower() == here and int(m.group(1)) in ids:
                    cid = int(m.group(1))
            if cid is not None:
                found[cid] = e
        rows = []
        for c in chans:
            e = found.get(c["id"])
            name = c.get("title") or c.get("name") or ""
            rows.append({
                "id": c["id"], "name": name, "effective_name": name,
                "effective_tvg_id": (e or {}).get("tvg-id") or c.get("stream_id") or "",
                "effective_channel_number": c.get("channel_number"),
                "group_name": (c.get("group") or {}).get("name") or (e or {}).get("group-title") or "",
                "effective_tvc_guide_stationid": (e or {}).get("tvc-guide-stationid") or "",
                "in_output": e is not None,
            })
        missing = sum(1 for r in rows if not r["in_output"])
        log.info("m3u-editor playlist %s: %d channels, %d m3u entries, %d not matched to an entry",
                 self.playlist["name"], len(rows), len(entries), missing)
        return rows


PLUGIN_STEPS = """Saved: {path}

In m3u-editor (as an admin), once:
1. Extensions > Plugin Installs > Upload Plugin Archive, and choose that zip.
2. Open the review, run the scan, then Install and Trust.
3. Extensions > Extensions > Gracenote Station IDs > Enable.

Installing a newer version later works the same way and replaces the old one."""

APPLY_STEPS = """{n} station IDs are on your clipboard (a copy is saved in {saved}).

In m3u-editor:
1. Extensions > Extensions > Gracenote Station IDs.
2. Actions > Apply Station IDs.
3. Playlist: {playlist}. Paste into "Station IDs from the matcher", then confirm.

It runs in the background and only touches channels in that playlist. When the run shows as
completed under Run History, come back here and press "Verify in m3u-editor"."""


def station_id_lines(playlist, rows):
    """The text pasted into the plugin's Apply Station IDs action."""
    out = [f"# m3u-editor playlist: {playlist.get('name')}"
           + (f" (#{playlist['id']})" if playlist.get("id") else ""),
           f"# {len(rows)} channels. Format: channel_id=station_id (empty removes the ID)"]
    for r in rows:
        name = re.sub(r"[\r\n]+", " ", r["channel"].get("effective_name") or "")
        out.append(f"{r['channel']['id']}={'' if r.get('remove') else r['new_id']}    # {name}")
    return "\n".join(out) + "\n"


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

LEFT = "Left as is"   # shown for a channel you looked at and left without a pick
ATTENTION = (REVIEW, UNMATCHED, ERROR, CHANGE)   # a current ID outside your lineups is fine (streaming)
FILTERS = (("all", "All channels"), ("attention", "Needs attention"), ("ticked", "Ticked to apply"),
           (MATCHED, MATCHED), (REVIEW, REVIEW), (UNMATCHED, UNMATCHED), (NOT_IN_LINEUP, NOT_IN_LINEUP),
           (CHANGE, CHANGE), (REMOVE, REMOVE), (VERIFIED, VERIFIED), (LEFT, LEFT), (DELETED, DELETED))
ROW_COLORS = {MATCHED: "#d9f2d9", REVIEW: "#fff2c2", UNMATCHED: "#f8d4d4", NOT_IN_LINEUP: "#e8e8e8",
              VERIFIED: "#e8e8e8", ERROR: "#f4b6b6", DELETED: "#cccccc", CHANGE: "#ffd8a8",
              REMOVE: "#e0c8f0", LEFT: "#efefef"}
ON, OFF = "☑", "☐"
COLS = ("write", "num", "channel", "group", "current", "status", "new", "gname", "call", "why")
HEADS = ("Apply", "Ch #", "m3u-editor channel", "Group", "Current ID", "Status", "Station ID",
         "Gracenote name", "Call sign", "Reason")
WIDTHS = (52, 60, 240, 130, 80, 150, 80, 210, 90, 220)
STATION_COLS = ("name", "call", "type", "where", "id")   # name on the left, station ID on the right
STATION_HEADS = ("Gracenote name", "Call sign", "Type", "Where", "Station ID")
STATION_WIDTHS = (240, 80, 90, 170, 90)
HELP = """\
1. Connect. Your Channels DVR server reads the Gracenote lineups for your \
countries, and every channel of the m3u-editor playlist is matched against \
them. Exact, unique matches are ticked for you (green, Matched).

2. Work through "Needs attention". Click a channel, then click the right \
station in the list under it (or press 1-9). If it isn't there, type in the \
search box above the list: the list then shows the search results (tick "All \
of Gracenote" to look outside your lineups; Esc brings the suggestions back). \
You can also type a station ID. Press Return (or "Done, next") to go to the \
next channel. A channel you leave without a pick becomes "Left as is".

3. Press "Copy N IDs for m3u-editor". Nothing changes in m3u-editor yet. The \
window that opens lists every replacement and removal, and the steps: in \
m3u-editor run Gracenote Station IDs > Apply Station IDs, pick the playlist, \
paste and confirm. Then press "Verify in m3u-editor".

The Gracenote Station IDs plugin is installed in m3u-editor once: File > \
Export m3u-editor plugin.

Select several channels (Shift-click or {key}-click) and press Tick selected \
or Untick selected to include them in the next copy or leave them out. Your \
picks are autosaved, so closing the app loses nothing."""


def _fmt_num(num):
    if isinstance(num, float) and num.is_integer():
        return int(num)
    return "" if num is None else num


def _num_key(num):
    try:
        return (0, float(num))
    except (TypeError, ValueError):
        return (1, 0.0)


def can_apply(r):
    """True when the row has something the plugin would change."""
    return bool((r.get("remove") and r["current"]) or (r["new_id"] and r["new_id"] != r["current"]))


def to_apply(r):
    return bool(r["write"] and can_apply(r))


def display_status(r):
    if r.get("reviewed") and not r["write"] and r["status"] in ATTENTION:
        return LEFT
    return r["status"]


def run_gui():
    import tkinter as tk
    import webbrowser
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
        for seq in (("<Button-2>", "<Control-Button-1>") if aqua else ("<Button-3>",)):
            widget.bind(seq, fn)

    def scrolled_tree(parent, cols, heads, widths, fill_col, **kw):
        """Treeview with both scrollbars. Columns never steal width from each other, so any
        column can be dragged as wide as you like; the fill column takes up spare room."""
        box = ttk.Frame(parent)
        tree = ttk.Treeview(box, columns=cols, show="headings", **kw)
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h, anchor="w")
            tree.column(c, width=w, minwidth=30, anchor="w", stretch=False)
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
            if not self.w.winfo_exists():
                return
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

    class LiveSearch:
        """Re-run a station search as the user types.

        Local lineup results appear first; Gracenote-wide results follow when the local ones
        are weak (or when wide() is on). The work runs in a background thread and reaches Tk
        through the app's queue. Results from an older keystroke are dropped.
        """

        def __init__(self, app, var, render, wide=lambda: False, alive=lambda: True, delay=250):
            self.app, self.var, self.render, self.wide, self.alive, self.delay = (
                app, var, render, wide, alive, delay)
            self.gen, self.job, self.mute, self.typed = 0, None, False, False
            var.trace_add("write", self.changed)

        def changed(self, *_):
            if self.mute:
                return
            self.typed = True
            self.schedule()

        def schedule(self, delay=None):
            if self.job:
                self.app.root.after_cancel(self.job)
            self.job = self.app.root.after(self.delay if delay is None else delay, self.run)

        def stop(self):
            if self.job:
                self.app.root.after_cancel(self.job)
                self.job = None
            self.gen += 1

        def set_quiet(self, value):
            """Set the search box without starting a search."""
            self.stop()
            self.typed = False
            self.mute = True
            try:
                self.var.set(value)
            finally:
                self.mute = False

        def run(self):
            self.job = None
            self.gen += 1
            gen, text = self.gen, self.var.get().strip()
            app, matcher, cdvr = self.app, self.app.matcher, self.app.cdvr
            if not self.alive():
                return
            if not text or not matcher:
                self.render(None, text, "")
                return
            wide = bool(self.wide())

            def post(fn):
                app.q.put(("call", (None, lambda: gen == self.gen and self.alive() and fn())))

            def work():
                try:
                    local = merge_candidates(matcher, text, matcher.search(text), matcher.suggest(text))
                except Exception as e:  # a bad search must never break the window
                    log.exception("Search %r failed", text)
                    msg = f"Search failed: {e}"
                    post(lambda: self.render([], text, msg))
                    return
                best = name_score(text, local[0]["name"], _bare_call(local[0])) if local else 0.0
                more = bool(cdvr) and len(text) >= 2 and (wide or best < GOOD_LOCAL)
                post(lambda: self.render(local, text, "looking in all of Gracenote…" if more else ""))
                if not more:
                    return
                try:
                    extra = app.wide_search(cdvr, matcher, text)
                except Exception as e:  # only the extra suggestions are lost
                    log.debug("Live Gracenote search %r failed: %s", text, e)
                    msg = f"Gracenote-wide search failed: {e}"
                    post(lambda: self.render(local, text, msg))
                    return
                merged = merge_candidates(matcher, text, local, extra, limit=30)
                post(lambda: self.render(merged, text, ""))
            threading.Thread(target=work, name="live-search", daemon=True).start()

    class App:
        def __init__(self, root):
            self.root = root
            self.q = queue.Queue()   # worker threads never touch Tk; they post here
            self.rows = []           # one dict per channel
            self.matcher = None
            self.cdvr = None
            self.m3ue = None
            self.copied = []         # rows handed to the plugin, awaiting Verify
            self.busy = False
            self.job_id = 0
            self.job_kind = ""
            self.job_quiet = False
            self.cancel_evt = None
            self.source = ""
            self._save_after = None
            self.find_job = None
            self.loaded_once = False
            self.shown_i = None
            self.cand_st = {}        # station ID -> station, for the suggestions list
            self.cand_ids = []       # suggestions in order, for the 1-9 keys
            self.wide_cache = {}
            self.apply_win = None
            self.sort_col, self.sort_rev = "num", False
            self.filter_idx = 0
            root.title("Channels DVR - Gracenote Station ID Matcher for m3u-editor")
            self.v = {k: tk.StringVar() for k in ("cdvr", "playlist", "token", "countries")}
            self.include_existing = tk.BooleanVar(value=True)
            self.ui = read_json(UI_FILE)
            self.place_window()
            self._build()
            self._load_settings()
            prev = root.report_callback_exception

            def report(*exc):
                log.critical("Unhandled error in the window", exc_info=exc)
                try:
                    self.note(f"Something went wrong: {exc[1]}. Details are in the log.", warn=True)
                except tk.TclError:
                    pass
                prev(*exc)
            root.report_callback_exception = report
            root.after(60, self._poll)
            log.info("Window opened")
            if self._can_autoconnect():
                root.after(150, lambda: self.start(False, startup=True))
            else:
                self.note("Fill in your servers and press Connect & Match.")

        # ------------------------------------------------------------ window
        def place_window(self):
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            geo = self.ui.get("geometry")
            m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo or "")
            if m and int(m.group(1)) <= sw and int(m.group(2)) <= sh:
                self.root.geometry(geo)
            else:
                self.root.geometry(f"{min(1280, sw - 60)}x{min(820, sh - 90)}")
            self.root.minsize(min(1080, sw - 40), min(640, sh - 80))

        def save_ui(self):
            try:
                ui = {"geometry": self.root.geometry(), "vsash": self.vpane.sash_coord(0)[1]}
                os.makedirs(CONFIG_DIR, exist_ok=True)
                write_private_json(UI_FILE, ui)
            except (OSError, tk.TclError) as e:
                log.debug("Could not save the window layout: %s", e)

        def place_sashes(self):
            self.root.update_idletasks()
            h = self.vpane.winfo_height()
            if h > 400:
                y = self.ui.get("vsash")
                self.vpane.sash_place(0, 0, y if isinstance(y, int) and 140 < y < h - 240 else int(h * 0.52))

        # ------------------------------------------------------------ layout
        def _build(self):
            self.build_menu()
            bold = tkfont.nametofont("TkDefaultFont").copy()
            bold.configure(weight="bold", size=bold.cget("size") + 2)
            self.bold = bold
            pad = {"padx": 4, "pady": 3}

            # servers: full form until connected, then a one-line summary
            self.top_holder = ttk.Frame(self.root)
            self.top_holder.pack(side="top", fill="x", padx=10, pady=(8, 2))
            top = self.conn = ttk.LabelFrame(self.top_holder, text="Servers", padding=(8, 4))
            ttk.Label(top, text="Channels DVR").grid(row=0, column=0, sticky="e", **pad)
            e_cdvr = ttk.Entry(top, textvariable=self.v["cdvr"], width=22)
            e_cdvr.grid(row=0, column=1, sticky="we", **pad)
            Tip(e_cdvr, "Address of your Channels DVR server, e.g. 192.168.1.10 (port 8089 is assumed).")
            ttk.Label(top, text="Countries").grid(row=0, column=2, sticky="e", **pad)
            cf = ttk.Frame(top)
            cf.grid(row=0, column=3, sticky="we", **pad)
            ttk.Button(cf, text="Pick…", command=self.pick_countries).pack(side="right", padx=(4, 0))
            e_ctry = ttk.Entry(cf, textvariable=self.v["countries"], width=24)
            e_ctry.pack(side="left", fill="x", expand=True)
            Tip(e_ctry, "3-letter country codes with a postal code, e.g. USA:10001, CAN, GBR:SW1A. "
                        "Your postal code decides which local stations count.")
            ttk.Label(top, text="m3u-editor playlist URL").grid(row=1, column=0, sticky="e", **pad)
            e_pl = ttk.Entry(top, textvariable=self.v["playlist"], width=52)
            e_pl.grid(row=1, column=1, columnspan=3, sticky="we", **pad)
            Tip(e_pl, "The playlist URL you gave Channels DVR, e.g. "
                      "http://192.168.1.10:36400/<playlist id>/playlist.m3u. Use the playlist that "
                      "imports from your provider.")
            ttk.Label(top, text="API token").grid(row=2, column=0, sticky="e", **pad)
            tf = ttk.Frame(top)
            tf.grid(row=2, column=1, sticky="we", **pad)
            self.token_entry = ttk.Entry(tf, textvariable=self.v["token"], show="•", width=22)
            self.show_token = tk.BooleanVar(value=False)
            ttk.Checkbutton(tf, text="Show", variable=self.show_token,
                            command=lambda: self.token_entry.configure(show="" if self.show_token.get() else "•")
                            ).pack(side="right", padx=(4, 0))
            self.token_entry.pack(side="left", fill="x", expand=True)
            ttk.Checkbutton(top, text="Include channels that already have an ID (to check or fix them)",
                            variable=self.include_existing).grid(row=2, column=2, columnspan=2, sticky="w", **pad)
            ttk.Label(top, text="m3u-editor: profile menu > API Tokens (View permission)",
                      foreground="gray").grid(row=3, column=1, sticky="w", **pad)
            ttk.Label(top, text="e.g. USA:10001, CAN, GBR (code:postal; your postal code decides local stations)",
                      foreground="gray").grid(row=3, column=2, columnspan=2, sticky="w", **pad)
            btns = ttk.Frame(top)
            btns.grid(row=0, column=4, rowspan=4, sticky="ne", padx=(10, 0))
            self.run_btn = ttk.Button(btns, text="Connect & Match", default="active",
                                      command=lambda: self.start(False))
            self.run_btn.pack(fill="x", pady=2)
            self.rebuild_btn = ttk.Button(btns, text="Rebuild lineup index", command=lambda: self.start(True))
            self.rebuild_btn.pack(fill="x", pady=2)
            Tip(self.rebuild_btn, f"Read the Gracenote lineups again instead of using the copy cached for "
                                  f"{CACHE_DAYS} days, then match.")
            b = ttk.Button(btns, text="Export m3u-editor plugin…", command=self.export_plugin)
            b.pack(fill="x", pady=2)
            Tip(b, "Save the Gracenote Station IDs plugin. Install it in m3u-editor once; its "
                   "Apply Station IDs action writes the IDs this app copies.")
            top.columnconfigure(1, weight=1)
            top.columnconfigure(3, weight=1)
            for e in (e_cdvr, e_ctry, e_pl, self.token_entry):
                e.bind("<Return>", lambda ev: self.start(False))

            self.conn_bar = ttk.Frame(self.top_holder)
            self.reload_btn = ttk.Button(self.conn_bar, text="Reload", command=lambda: self.start(False))
            self.reload_btn.pack(side="right")
            Tip(self.reload_btn, "Read the playlist again and re-match (Connect & Match). "
                                 "Your picks are kept.")
            ttk.Button(self.conn_bar, text="Connection…", command=self.show_conn).pack(side="right", padx=4)
            b = ttk.Button(self.conn_bar, text="Export plugin…", command=self.export_plugin)
            b.pack(side="right")
            Tip(b, "Save the Gracenote Station IDs plugin for m3u-editor (install it once).")
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
            self.group_filter = tk.StringVar(value="All groups")
            self.group_box = ttk.Combobox(bar, textvariable=self.group_filter, state="readonly", width=24,
                                          values=["All groups"])
            self.group_box.pack(side="left", padx=(4, 12))
            self.group_box.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
            ttk.Label(bar, text="Find").pack(side="left")
            self.name_filter = tk.StringVar()
            self.find_entry = ttk.Entry(bar, textvariable=self.name_filter, width=22)
            self.find_entry.pack(side="left", padx=(4, 0))
            Tip(self.find_entry, "Channel name, station ID, group or Gracenote name. A number finds "
                                 "that channel number; 500-600 finds a range. Click a heading to sort.")
            self.name_filter.trace_add("write", lambda *a: self.schedule_find())
            self.find_entry.bind("<Escape>", lambda e: (self.name_filter.set(""), "break")[1])
            self.find_entry.bind("<Down>", lambda e: (self.focus_table(), "break")[1])
            self.find_entry.bind("<Return>", lambda e: (self.focus_table(), "break")[1])
            ttk.Button(bar, text="✕", width=2, command=self.clear_filters).pack(side="left", padx=(2, 0))
            self.shown_lbl = ttk.Label(bar, text="", foreground="gray")
            self.shown_lbl.pack(side="left", padx=12)

            # bottom: status line, then the action buttons above it (packed before the panes so a
            # small window shrinks the table, never the buttons)
            sbar = ttk.Frame(self.root)
            sbar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
            self.busy_box = ttk.Frame(sbar)
            self.busy_box.pack(side="right")
            self.prog = ttk.Progressbar(self.busy_box, length=180, maximum=100)
            self.prog.pack(side="left", padx=(8, 4))
            self.cancel_btn = ttk.Button(self.busy_box, text="Cancel (Esc)", command=self.cancel)
            self.cancel_btn.pack(side="left")
            self.status = ttk.Label(sbar, text="", anchor="w", width=1)
            self.status.pack(side="left", fill="x", expand=True)
            self.busy_box.pack_forget()
            actions = ttk.Frame(self.root)
            actions.pack(side="bottom", fill="x", padx=10, pady=(2, 4))
            self.copy_btn = ttk.Button(actions, text="Copy 0 IDs for m3u-editor…", command=self.write)
            self.copy_btn.pack(side="right")
            Tip(self.copy_btn, "Copy every ticked station ID for the plugin's Apply Station IDs action and "
                               "show the steps. Nothing changes in m3u-editor until you paste them there. "
                               f"Shortcut: {KEY}S")
            self.verify_btn = ttk.Button(actions, text="Verify in m3u-editor", command=self.verify)
            self.verify_btn.pack(side="right", padx=6)
            Tip(self.verify_btn, "After Apply Station IDs has run in m3u-editor: read the playlist again "
                                 "and check that every copied ID is there.")
            for text, fn, tip in (
                    ("Tick selected", lambda: self.tick_selected(True),
                     "Include the selected channels in the next copy. Select several with Shift-click "
                     f"or {KEY}-click ({KEY}A selects every channel shown)."),
                    ("Untick selected", lambda: self.tick_selected(False),
                     "Leave the selected channels out of the next copy without losing their picks."),
                    ("Mark reviewed", lambda: self.mark_reviewed(),
                     "Keep the selected channels as they are and take them out of Needs attention."),
                    ("Match unmatched one by one…", self.walkthrough,
                     "Step through the channels that still need a station, one at a time.")):
                b = ttk.Button(actions, text=text, command=fn)
                b.pack(side="left", padx=(0, 6))
                Tip(b, tip)
            self.action_buttons = list(actions.winfo_children())

            # table above, channel detail below; drag the divider to resize
            self.vpane = tk.PanedWindow(self.root, orient="vertical", sashwidth=8, sashrelief="raised",
                                        bd=0, opaqueresize=True)
            self.vpane.pack(side="top", fill="both", expand=True, padx=10, pady=4)
            tbox, self.tree = scrolled_tree(self.vpane, COLS, HEADS, WIDTHS, "why", selectmode="extended")
            for c in COLS:
                self.tree.heading(c, command=lambda c=c: self.sort_by(c))
            self.tree.column("write", anchor="center")
            self.tree.heading("write", anchor="center")
            for tag, color in ROW_COLORS.items():
                self.tree.tag_configure(tag, background=color, foreground="black")
            self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
            self.tree.bind("<Button-1>", self.table_click)
            self.tree.bind("<Double-1>", self.table_double)
            self.tree.bind("<space>", lambda e: (self.toggle(), "break")[1])
            self.tree.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind(f"<{MOD}-a>", lambda e: (self.select_all(), "break")[1])
            for k in range(1, 10):
                self.tree.bind(str(k), lambda e, k=k: (self.pick_number(k), "break")[1])
            allow_toggle_click(self.tree)
            bind_right_click(self.tree, self.table_menu)
            self.vpane.add(tbox, minsize=140, stretch="always")
            self.update_headings()

            detail = ttk.Frame(self.vpane, padding=(0, 6, 0, 0))
            self.vpane.add(detail, minsize=240, stretch="never", height=340)
            head = ttk.Frame(detail)
            head.pack(side="top", fill="x")
            self.d_title = ttk.Label(head, text="Select a channel above.", font=bold)
            self.d_title.pack(side="top", anchor="w")
            self.d_sub = ttk.Label(head, text="", foreground="gray", justify="left")
            self.d_sub.pack(side="top", anchor="w", fill="x")
            head.bind("<Configure>", lambda e: self.d_sub.configure(wraplength=max(200, e.width - 10)))

            # one station list for the selected channel: the matcher's suggestions, or the results of
            # the search box above it while there is text in it
            self.list_box = ttk.LabelFrame(detail, text="Suggestions", padding=4)
            self.list_box.pack(side="top", fill="both", expand=True, pady=(4, 0))
            srow = ttk.Frame(self.list_box)
            srow.pack(side="top", fill="x", pady=(0, 4))
            ttk.Label(srow, text="Search").pack(side="left", padx=(0, 4))
            self.wide_v = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(srow, text="All of Gracenote", variable=self.wide_v)
            cb.pack(side="right")
            Tip(cb, "Also search stations outside your lineups. Without this, Gracenote-wide results are "
                    "added only when nothing in your lineups is a good match.")
            ttk.Button(srow, text="✕", width=2, command=lambda: self.search_v.set("")).pack(side="right",
                                                                                          padx=(2, 8))
            self.search_v = tk.StringVar()
            self.search_entry = ttk.Entry(srow, textvariable=self.search_v)
            self.search_entry.pack(side="left", fill="x", expand=True)
            Tip(self.search_entry, "Type to search Gracenote; the list below shows the results. Esc or ✕ "
                                   f"brings back the suggestions. Shortcut: {KEY}L")
            self.wide_v.trace_add("write", lambda *a: self.search_v.get().strip() and self.live.schedule(0))
            self.search_entry.bind("<Down>", lambda e: (self.focus_list(), "break")[1])
            self.search_entry.bind("<Return>", lambda e: (self.live.schedule(0), self.focus_list(), "break")[2])
            self.search_entry.bind("<KP_Enter>", lambda e: (self.live.schedule(0), self.focus_list(), "break")[2])
            self.search_entry.bind("<Escape>", lambda e: (self.search_v.set(""), "break")[1])
            lb = ttk.Frame(self.list_box)
            lb.pack(side="bottom", fill="x", pady=(4, 0))
            self.done_btn = ttk.Button(lb, text="Done, next  ⏎", command=self.done_next)
            self.done_btn.pack(side="right")
            Tip(self.done_btn, "Keep this channel as shown and go to the next one (Return). "
                               "Without a pick it becomes Left as is.")
            self.d_buttons = [self.done_btn]
            for text, fn, tip in (
                    ("Clear choice", lambda: self.clear_choice(),
                     "No new station ID for this channel (a current ID is kept)."),
                    ("Remove ID", lambda: self.remove_id(),
                     "Clear the station ID this channel has in m3u-editor."),
                    ("Reset", lambda: self.reset_rows(),
                     "Go back to what the matcher found for this channel.")):
                b = ttk.Button(lb, text=text, command=fn)
                b.pack(side="left", padx=(0, 6))
                Tip(b, tip)
                self.d_buttons.append(b)
            ttk.Label(lb, text="Or a station ID").pack(side="left", padx=(12, 0))
            self.manual_v = tk.StringVar()
            e_man = ttk.Entry(lb, textvariable=self.manual_v, width=10)
            e_man.pack(side="left", padx=4)
            e_man.bind("<Return>", lambda e: (self.set_manual(), "break")[1])
            self.use_id_btn = ttk.Button(lb, text="Use ID", command=self.set_manual)
            self.use_id_btn.pack(side="left")
            Tip(self.use_id_btn, "Use a Gracenote station ID you already know.")
            self.d_buttons.append(self.use_id_btn)
            self.list_info = ttk.Label(self.list_box, text="", foreground="gray", width=1)
            self.list_info.pack(side="bottom", fill="x", pady=(4, 0))
            cbox, self.cand = scrolled_tree(self.list_box, ("n", "mark") + STATION_COLS, ("#", "") + STATION_HEADS,
                                            (30, 30) + STATION_WIDTHS, "id", selectmode="browse", height=6)
            cbox.pack(side="top", fill="both", expand=True)
            self.cand.column("n", anchor="center")
            self.cand.column("mark", anchor="center")
            for tag, bg, fg in (("on", "#d6f0d6", "black"), ("cur", "#e3eaf6", "black"),
                                ("outside", "", "#a04000")):
                self.cand.tag_configure(tag, background=bg, foreground=fg)
            self.cand.bind("<Button-1>", self.cand_click)
            self.cand.bind("<space>", lambda e: (self.use_cand(self.cand.focus()), "break")[1])
            self.cand.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.cand.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            for k in range(1, 10):
                self.cand.bind(str(k), lambda e, k=k: (self.pick_number(k), "break")[1])
            self.list_mode, self.results, self.results_text, self.results_note = "suggest", [], "", ""
            self.live = LiveSearch(self, self.search_v, self.show_results, wide=self.wide_v.get)
            self.set_detail_enabled(False)

            # keyboard shortcuts that work anywhere in the window
            self.root.bind("<Escape>", lambda e: self.cancel())
            self.root.bind(f"<{MOD}-s>", lambda e: (self.write(), "break")[1])
            self.root.bind(f"<{MOD}-r>", lambda e: (self.start(False), "break")[1])
            self.root.bind(f"<{MOD}-f>", lambda e: (self.find_entry_focus(), "break")[1])
            self.root.bind(f"<{MOD}-l>", lambda e: (self.search_focus(), "break")[1])
            self.root.bind(f"<{MOD}-Return>", lambda e: (self.done_next(), "break")[1])
            self.root.after(80, self.place_sashes)

        def build_menu(self):
            mb = tk.Menu(self.root)
            fm = tk.Menu(mb, tearoff=0)
            fm.add_command(label="Connect & Match / Reload", accelerator=f"{KEY}R",
                           command=lambda: self.start(False))
            fm.add_command(label="Rebuild lineup index and match", command=lambda: self.start(True))
            fm.add_command(label="Connection settings…", command=self.show_conn)
            fm.add_separator()
            fm.add_command(label="Copy ticked IDs for m3u-editor…", accelerator=f"{KEY}S", command=self.write)
            fm.add_command(label="Verify in m3u-editor", command=self.verify)
            fm.add_command(label="Export m3u-editor plugin…", command=self.export_plugin)
            fm.add_separator()
            fm.add_command(label="Export unmatched CSV…", command=self.export)
            fm.add_command(label="Open log", command=self.open_log)
            fm.add_command(label="Open data folder", command=lambda: self.open_path(CONFIG_DIR))
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
            cm.add_command(label="Search Gracenote", accelerator=f"{KEY}L", command=self.search_focus)
            mb.add_cascade(label="Channels", menu=cm)
            hm = tk.Menu(mb, tearoff=0)
            hm.add_command(label="How it works", command=lambda: messagebox.showinfo(
                "How it works", HELP.format(key=KEY.rstrip("+")), parent=self.root))
            hm.add_command(label="Keyboard shortcuts", command=self.show_keys)
            hm.add_command(label="Open log", command=self.open_log)
            mb.add_cascade(label="Help", menu=hm)
            self.root.configure(menu=mb)

        def fill_channel_menu(self, m):
            m.add_command(label="Done, next", accelerator="Return", command=self.done_next)
            m.add_command(label="Tick selected", command=lambda: self.tick_selected(True))
            m.add_command(label="Untick selected", command=lambda: self.tick_selected(False))
            m.add_command(label="Tick / untick", accelerator="Space", command=self.toggle)
            m.add_command(label="Mark reviewed", command=lambda: self.mark_reviewed())
            m.add_separator()
            m.add_command(label="Clear choice", command=lambda: self.clear_choice())
            m.add_command(label="Remove ID…", command=lambda: self.remove_id())
            m.add_command(label="Reset to the matcher's result", command=lambda: self.reset_rows())

        def show_keys(self):
            messagebox.showinfo("Keyboard shortcuts", "\n".join((
                "Channel table",
                "   Up / Down: next or previous channel",
                "   Return: done with this channel, go to the next",
                "   1-9: use that station and go to the next",
                "   Space: tick or untick the selected channels",
                f"   {KEY}A: select every channel shown",
                "   Shift-click / " + ("⌘" if aqua else "Ctrl-") + "click: select several",
                "",
                "Station list (suggestions or search results)",
                "   Click: use that station (click it again to clear)",
                "   1-9: use that station and go to the next",
                "   Space: use the selected station",
                "   Return: done, next",
                "",
                "Search box",
                f"   {KEY}L: jump to the search box",
                "   Typing: the list shows the results",
                "   Return or Down: search now and move into the list",
                "   Esc: clear it and show the suggestions again",
                "",
                "Anywhere",
                f"   {KEY}F: find a channel      {KEY}R: reload",
                f"   {KEY}S: copy for m3u-editor   {KEY}Return: done, next",
                "   Esc: cancel the running job")), parent=self.root)

        def show_conn(self):
            self.conn_bar.pack_forget()
            self.conn.pack(fill="x")

        def hide_conn(self):
            self.conn.pack_forget()
            self.conn_bar.pack(fill="x")

        def find_entry_focus(self):
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")

        def search_focus(self):
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")

        def focus_table(self):
            kids = self.tree.get_children()
            if kids and not self.tree.selection():
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])
            self.tree.focus_set()

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
                                f"({KEY.rstrip('+')}-click for several).").pack(anchor="w", padx=10, pady=(10, 4))
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
            lb.bind("<Double-1>", lambda e: ok())
            win.bind("<Escape>", lambda e: win.destroy())
            win.grab_set()

        def _load_settings(self):
            s = read_json(SETTINGS_FILE)
            for k in ("cdvr", "playlist", "countries", "token"):
                if s.get(k):
                    self.v[k].set(str(s[k]))
            if "include_existing" in s:
                self.include_existing.set(bool(s["include_existing"]))
            if not self.v["countries"].get():
                old = re.search(r"\(([A-Z]{3})\)$", str(s.get("country") or ""))  # older settings format
                code = old.group(1) if old else "USA"
                self.v["countries"].set(f"{code}:{s.get('postal') or COUNTRY_POSTAL.get(code, '10001')}")

        def _save_settings(self, **extra):
            # The API token is saved too, so it doesn't have to be pasted every time. The file
            # is readable only by your user account; delete it to forget the API token.
            data = read_json(SETTINGS_FILE)
            data.update({k: self.v[k].get() for k in ("cdvr", "playlist", "countries", "token")})
            data["include_existing"] = bool(self.include_existing.get())
            data.update(extra)
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                write_private_json(SETTINGS_FILE, data)
            except OSError as e:
                log.warning("Could not save settings: %s", e)

        def _can_autoconnect(self):
            """Connect at startup only when every saved setting is complete and valid."""
            v = {k: var.get().strip() for k, var in self.v.items()}
            if not all(v.values()):
                return False
            try:
                validate_address(v["cdvr"], "Channels DVR", 8089)
                parse_playlist_url(v["playlist"])
                pairs = parse_countries(v["countries"])
            except ValueError:
                return False
            return all(c in COUNTRY_NAMES for c, _ in pairs)

        # ------------------------------------------------------------ session autosave
        def _session_data(self):
            picks = {}
            for r in self.rows:
                if r["why"] in USER_PICK or r.get("remove") or r["status"] in (ERROR, DELETED) \
                        or not r["write"] or r.get("reviewed"):
                    picks[str(r["channel"]["id"])] = {
                        "name": r["channel"].get("effective_name") or r["channel"].get("name"),
                        "new_id": r["new_id"], "write": r["write"], "why": r["why"],
                        "status": r["status"], "best": r["best"], "remove": bool(r.get("remove")),
                        "reviewed": bool(r.get("reviewed"))}
            return {"saved": time.time(), "source": self.source, "picks": picks,
                    "copied": [r["channel"]["id"] for r in self.copied]}

        def save_session(self, now=False):
            """Autosave picks shortly after every change (debounced)."""
            if not self.rows:
                return
            if not now:
                if self._save_after:
                    self.root.after_cancel(self._save_after)
                self._save_after = self.root.after(800, lambda: self.save_session(True))
                return
            if self._save_after:
                self.root.after_cancel(self._save_after)
            self._save_after = None
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                with open(SESSION_FILE + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(self._session_data(), f)
                os.replace(SESSION_FILE + ".tmp", SESSION_FILE)
            except (OSError, TypeError, ValueError) as e:
                log.warning("Could not autosave picks: %s", e)

        def _restore_session(self, ask):
            """Re-apply work saved from an earlier run against the same playlist: hand picks,
            removals, unticked matches and reviews. Asks first after a fresh start."""
            data = read_json(SESSION_FILE)
            picks = data.get("picks")
            if data.get("source") != self.source or not isinstance(picks, dict):
                return 0
            todo = []
            for r in self.rows:
                pk = picks.get(str(r["channel"]["id"]))
                if not isinstance(pk, dict):
                    continue
                kind = None
                if pk.get("why") in USER_PICK and pk.get("new_id") and pk["new_id"] != r["current"]:
                    kind = "pick"
                elif pk.get("remove") and pk.get("write") and r["current"]:
                    kind = "remove"
                elif pk.get("write") is False and r["write"] and pk.get("new_id") == r["new_id"]:
                    kind = "untick"
                if kind or (pk.get("reviewed") and not r.get("reviewed")):
                    todo.append((r, pk, kind))
            if not todo:
                return 0
            if ask:
                age = time.strftime("%b %d %H:%M", time.localtime(data.get("saved") or 0))
                if not messagebox.askyesno(
                        "Restore your picks?",
                        f"Found work from {age} on {len(todo)} channels that is not in m3u-editor yet "
                        "(station IDs you picked, removals, unticked matches and reviews).\n\n"
                        "Restore it?", parent=self.root):
                    return 0
            for r, pk, kind in todo:
                if kind == "remove":
                    self._set_remove(r)
                elif kind == "pick":
                    best = pk.get("best") if isinstance(pk.get("best"), dict) else None
                    r.update(new_id=str(pk["new_id"]), write=bool(pk.get("write", True)), why=pk["why"],
                             status=MATCHED, best=best or self.matcher.by_id.get(pk["new_id"]))
                    self._mark_change(r)
                elif kind == "untick":
                    r["write"] = False
                if pk.get("reviewed"):
                    r["reviewed"] = True
            ids = data.get("copied")
            if isinstance(ids, list):
                ids = set(ids)
                self.copied = [r for r in self.rows if r["channel"]["id"] in ids and to_apply(r)]
            log.info("Restored saved work on %d channels", len(todo))
            return len(todo)

        def open_path(self, path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # noqa: S606 - opens the log in the default viewer
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except OSError as e:
                messagebox.showinfo("Open", f"Could not open {path}. The log is at:\n{LOG_FILE}\n\n({e})", parent=self.root)

        def open_log(self):
            self.open_path(LOG_FILE if os.path.exists(LOG_FILE) else CONFIG_DIR)

        def on_close(self):
            if self.busy and not messagebox.askyesno(
                    "Still working", "A job is still running. Quit anyway?", parent=self.root):
                return
            try:
                if self.cancel_evt:
                    self.cancel_evt.set()
                self.save_session(True)
                self.save_ui()
            finally:
                log.info("Window closed")
                self.root.destroy()

        # ------------------------------------------------------------ worker plumbing
        def _poll(self):
            try:
                while True:
                    kind, (jid, payload) = self.q.get_nowait()
                    if kind == "call":
                        payload()
                        continue
                    if jid != self.job_id:
                        continue  # result of a canceled job
                    if kind == "progress":
                        text, pct = payload
                        self.status.config(text=text, foreground="")
                        if pct is not None:
                            self.prog["value"] = pct
                    elif kind == "done":
                        self._set_busy(False)
                        payload()
                    elif kind == "error":
                        self._set_busy(False)
                        first = payload.splitlines()[0] if payload else "unknown error"
                        if self.job_quiet:
                            self.show_conn()
                            self.note(f"Could not connect: {first}  Check the servers and press "
                                      "Connect & Match.", warn=True)
                        else:
                            self.note("Error: " + first, warn=True)
                            messagebox.showerror("Error", payload + f"\n\nDetails are in the log:\n{LOG_FILE}",
                                                 parent=self.root)
            except queue.Empty:
                pass
            except Exception as e:  # never let the poll loop die
                log.exception("Error in the UI loop")
                self.note(f"Error: {e}", warn=True)
            try:
                self.root.after(60, self._poll)
            except tk.TclError:
                pass  # window closed

        def _set_busy(self, busy):
            self.busy = busy
            for b in (self.run_btn, self.rebuild_btn, self.reload_btn, self.verify_btn):
                b.state(["disabled"] if busy else ["!disabled"])
            if self.apply_alive():
                self.apply_verify.state(["disabled"] if busy else ["!disabled"])
            if busy:
                self.prog["value"] = 0
                self.busy_box.pack(side="right", before=self.status)
            else:
                self.busy_box.pack_forget()
            self.update_counts()

        def _worker(self, fn, kind, quiet=False):
            self.job_id += 1
            jid, evt = self.job_id, threading.Event()
            self.cancel_evt, self.job_kind, self.job_quiet = evt, kind, quiet

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
            self.note("Canceled." + (" Fix the details and press Connect & Match again."
                                     if self.job_kind == "match" else ""))

        def wide_search(self, cdvr, matcher, text):
            """Gracenote-wide candidates, remembered per text until the next load (any thread)."""
            key = (id(matcher), text.lower())
            if key not in self.wide_cache:
                self.wide_cache[key] = global_candidates(cdvr, text, matcher, limit=25)
            return self.wide_cache[key]

        # ------------------------------------------------------------ connect and match
        def start(self, rebuild, startup=False):
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            v = {k: var.get().strip() for k, var in self.v.items()}

            def bad(title, msg):
                self.show_conn()
                if startup:
                    self.note(msg, warn=True)
                else:
                    messagebox.showwarning(title, msg, parent=self.root)
            try:
                cdvr = validate_address(v["cdvr"], "Channels DVR", 8089)
                m3ue = M3uEditor(v["playlist"], v["token"])
            except ValueError as e:
                bad("Check the address", str(e))
                return
            try:
                pairs = parse_countries(v["countries"])
            except ValueError as e:
                bad("Check the countries", str(e))
                return
            unknown = [c for c, _ in pairs if c not in COUNTRY_NAMES]
            if unknown and startup:
                return   # startup auto-connect only runs with known countries
            if not v["token"]:
                bad("Missing info", "Enter an m3u-editor API token (profile menu > API "
                                    "Tokens; View permission is enough).")
                return
            self._save_settings()
            self.save_session(True)
            include = self.include_existing.get()
            log.info("Connect & Match: cdvr=%s m3u-editor=%s playlist=%s countries=%s rebuild=%s",
                     cdvr, m3ue.base, m3ue.uuid, format_countries(pairs), rebuild)

            def job(progress):
                progress(f"Connecting to Channels DVR at {_host_of(cdvr)}...", 1)
                try:
                    st = http("GET", f"{cdvr}/status", timeout=PROBE_TIMEOUT)
                except RuntimeError as e:
                    raise RuntimeError(f"Channels DVR: {e}") from None
                if not (isinstance(st, dict) and "channels dvr" in str(st.get("description", "")).lower()):
                    raise RuntimeError(f"Channels DVR: {_host_of(cdvr)} answered but is not a Channels DVR "
                                       "server. Check the address and port (default 8089).")
                progress(f"Reading channels from m3u-editor at {_host_of(m3ue.base)}...", 2)
                try:
                    chans = m3ue.load(timeout=PROBE_TIMEOUT, retries=0)
                except RuntimeError as e:
                    raise RuntimeError(f"m3u-editor: {e}") from None
                if not chans:
                    raise RuntimeError(f"m3u-editor: playlist '{m3ue.playlist['name']}' has no enabled "
                                       "live channels.")
                idx = load_indexes(cdvr, pairs, progress, rebuild)
                matcher = Matcher(idx)
                progress("Matching channels...", 97)
                rows = []
                for ch in sorted(chans, key=lambda c: _num_key(c.get("effective_channel_number")
                                                               or c.get("channel_number") or 0)):
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
                                 "write": status == MATCHED and not current})
                    if status == NOT_IN_LINEUP and not best:
                        rows[-1]["new_id"] = ""

                counts = {}
                for r in rows:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                log.info("Matched %d channels: %s", len(rows), counts)
                hidden = sum(1 for c in chans if not c["in_output"])
                return lambda: self.matched(cdvr, m3ue, matcher, rows, chans, idx, pairs, hidden, startup)

            self.note("Connecting…")
            self._worker(job, "match", quiet=startup)

        def matched(self, cdvr, m3ue, matcher, rows, chans, idx, pairs, hidden, quiet):
            """Runs on the Tk thread with the result of Connect & Match."""
            first = not self.loaded_once
            self.save_session(True)   # picks made while loading are kept
            self.cdvr, self.m3ue, self.matcher, self.rows = cdvr, m3ue, matcher, rows
            self.source, self.copied = f"{m3ue.base}/{m3ue.uuid}", []
            self.wide_cache = {}
            self.loaded_once = True
            self.shown_i = None
            for r in rows:
                r["group"] = r["channel"].get("group_name") or "(no group)"
                r["reviewed"] = False
                r["auto"] = {k: r[k] for k in ("status", "best", "new_id", "write", "why")}
            names = sorted({r["group"] for r in rows}, key=str.lower)
            gcount = {n: sum(1 for r in rows if r["group"] == n) for n in names}
            self.group_box["values"] = ["All groups"] + [f"{n}  ({gcount[n]})" for n in names]
            if self.group_filter.get() not in self.group_box["values"]:
                self.group_filter.set("All groups")
            self.hide_conn()
            self.conn_lbl.configure(text=f"{m3ue.playlist['name']} on {_host_of(m3ue.base)}   ·   "
                                         f"{len(rows)} channels   ·   {format_countries(pairs)}   ·   "
                                         f"loaded {time.strftime('%H:%M')}")
            notes = []
            if idx.get("problems"):
                notes.append("Some countries had problems: " + " ".join(idx["problems"]))
                log.warning("Country problems: %s", idx["problems"])
            if hidden:
                notes.append(f"{hidden} channels are not in the playlist output (hidden by a filter?), so "
                             "their current ID shows as empty; picking one still works.")
            restored = self._restore_session(ask=first)
            att = sum(1 for r in rows if self.in_filter(r, "attention"))
            if first:
                self.filter_idx = 1 if att else 0
            self.rebuild(keep_view=not first)
            if first or not self.tree.selection():
                self.focus_table()
            ticked = sum(1 for r in rows if to_apply(r))
            msg = (f"{m3ue.playlist['name']}: {len(chans)} channels, {len(rows)} checked against "
                   f"{idx['lineups']} lineups / {len(idx['stations'])} stations. {ticked} ticked to apply. "
                   + (f"{att} need{'s' if att == 1 else ''} attention: pick a station below, then press "
                      "Return for the next one." if att else "Nothing needs attention."))
            if restored:
                msg = f"Restored your work on {restored} channel{'' if restored == 1 else 's'}. " + msg
            self.note(" ".join([msg] + notes), warn=bool(notes) and quiet)
            self.prog["value"] = 100

        # ------------------------------------------------------------ table
        def in_filter(self, r, key, tag=None):
            tag = tag or display_status(r)
            if key == "all":
                return True
            if key == "attention":
                return tag in ATTENTION and not r["write"]
            if key == "ticked":
                return to_apply(r)
            return tag == key

        def matches(self, r, tag, q):
            g = self.group_filter.get()
            if g != "All groups" and re.sub(r"\s+\(\d+\)$", "", g) != r.get("group"):
                return False
            if q:
                ch = r["channel"]
                num = ch.get("effective_channel_number") or ch.get("channel_number")
                hay = " ".join(str(x) for x in (ch.get("effective_name") or ch.get("name"), r["current"],
                                                r["new_id"], _fmt_num(num), r.get("group", ""),
                                                (r["best"] or {}).get("name", ""))).lower()
                for w in q.split():
                    rng = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", w)
                    if rng:
                        k = _num_key(num)
                        if num is None or k[0] or not float(rng.group(1)) <= k[1] <= float(rng.group(2)):
                            return False
                    elif w not in hay:
                        return False
            return self.in_filter(r, FILTERS[self.filter_idx][0], tag)

        def row_values(self, r):
            tag = display_status(r)
            ch, st = r["channel"], r["best"] or {}
            num = _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number"))
            mark = (ON if r["write"] else OFF) if can_apply(r) else ""
            return tag, (mark, num, ch.get("effective_name") or ch.get("name"), r.get("group", ""),
                         r["current"], tag, "(remove)" if r.get("remove") else r["new_id"],
                         st.get("name", ""), st.get("callSign", ""), r["why"])

        def _sort_key(self, r, tag):
            ch, st = r["channel"], r["best"] or {}
            num = ch.get("effective_channel_number") or ch.get("channel_number")
            col = self.sort_col
            if col == "num":
                return (num is None,) + _num_key(num) + ("",)
            if col in ("current", "new"):
                v = r["current"] if col == "current" else r["new_id"]
                return (not v, int(v) if str(v).isdigit() else 0, "")
            val = {"write": "0" if to_apply(r) else "1" if can_apply(r) else "2",
                   "channel": ch.get("effective_name") or ch.get("name"),
                   "group": r.get("group"), "status": tag, "gname": st.get("name"),
                   "call": st.get("callSign"), "why": r["why"]}.get(col)
            return (not val, 0, str(val or "").lower()) + _num_key(num)

        def sort_by(self, col):
            self.sort_rev = not self.sort_rev if self.sort_col == col else False
            self.sort_col = col
            self.update_headings()
            self.rebuild()

        def update_headings(self):
            for c, h in zip(COLS, HEADS):
                arrow = (" ▼" if self.sort_rev else " ▲") if c == self.sort_col else ""
                self.tree.heading(c, text=h + arrow)

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
            self.group_filter.set("All groups")
            if self.name_filter.get():
                self.name_filter.set("")  # rebuilds via schedule_find
            else:
                self.rebuild()

        def rebuild(self, keep_view=True):
            """Refill the table from the filters. Edits never call this; they update rows in place,
            so the table doesn't jump and an edited row stays put until you change the filters."""
            self.find_job = None
            sel = set(self.tree.selection())
            top = self.tree.yview()[0]
            self.tree.delete(*self.tree.get_children())
            q = self.name_filter.get().strip().lower()
            items = []
            for i, r in enumerate(self.rows):
                tag, vals = self.row_values(r)
                if self.matches(r, tag, q):
                    items.append((self._sort_key(r, tag), i, tag, vals))
            items.sort(key=lambda x: x[0], reverse=self.sort_rev)
            for _, i, tag, vals in items:
                self.tree.insert("", "end", iid=str(i), tags=(tag,), values=vals)
            keep = [s for s in sel if self.tree.exists(s)]
            if keep:
                self.tree.selection_set(keep)
            if keep_view:
                self.tree.yview_moveto(top)
            if keep:
                self.tree.see(keep[0])
            self.update_counts()
            self.show_detail()

        def update_rows(self, idxs):
            for i in idxs:
                if self.tree.exists(str(i)):
                    tag, vals = self.row_values(self.rows[i])
                    self.tree.item(str(i), values=vals, tags=(tag,))
            self.update_counts()
            self.save_session()

        def update_counts(self):
            counts, att, ticked = {}, 0, 0
            for r in self.rows:
                t = display_status(r)
                counts[t] = counts.get(t, 0) + 1
                att += self.in_filter(r, "attention", t)
                ticked += to_apply(r)
            labels = []
            for key, label in FILTERS:
                n = len(self.rows) if key == "all" else att if key == "attention" else \
                    ticked if key == "ticked" else counts.get(key, 0)
                labels.append(f"{label} ({n})")
            self.show_box.configure(values=labels)
            self.show_v.set(labels[self.filter_idx])
            self.copy_btn.configure(text=f"Copy {ticked} ID{'' if ticked == 1 else 's'} for m3u-editor…")
            self.copy_btn.state(["disabled"] if self.busy or not ticked else ["!disabled"])
            self.verify_btn.state(["disabled"] if self.busy or not self.m3ue else ["!disabled"])
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
            """A click in the Apply column ticks or unticks that channel."""
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
            if self.tree.identify_region(e.x, e.y) != "cell" or self.tree.identify_column(e.x) == "#1":
                return None   # headings, dividers and the Apply column (one click there is enough)
            iid = self.tree.identify_row(e.y)
            if iid:
                self.tree.selection_set(iid)
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

        # ------------------------------------------------------------ edits (all update rows in place)
        def after_edit(self, idxs):
            for i in idxs:
                self.rows[i]["reviewed"] = True
            self.update_rows(idxs)
            self.show_detail()

        def _apply(self, i, st, sid, why):
            r = self.rows[i]
            r["remove"] = False
            r["best"], r["new_id"], r["write"], r["why"] = st, sid, True, why
            if not r["current"]:
                r["status"] = MATCHED   # same as a pick in the walkthrough
            if r["current"] and sid != r["current"]:
                r["status"], r["why"] = CHANGE, f"{why}: replaces {r['current']}"
            elif r["current"] and sid == r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"
            log.info("Picked %s for channel %s (%s)", sid, r["channel"]["id"], why)
            self.after_edit([i])
            name = r["channel"].get("effective_name") or ""
            self.note(f"{name}: {sid} {(st or {}).get('name', '')}. Press Return for the next channel.")

        def tick_selected(self, on):
            rows = self.targets()
            if not rows:
                return
            done = [(i, r) for i, r in rows if can_apply(r)]
            skipped = len(rows) - len(done)
            for _, r in done:
                r["write"] = on
            if done:
                self.after_edit([i for i, _ in done])
            what = f"{'Ticked' if on else 'Unticked'} {len(done)} channel{'' if len(done) == 1 else 's'}"
            if skipped:
                what += f"; {skipped} {'has' if skipped == 1 else 'have'} no pick yet"
            self.note(what + ".", warn=not done)

        def toggle(self):
            """Space or a click in the Apply column: tick the selection, or untick it if all are ticked."""
            rows = self.targets()
            if not rows:
                return
            ready = [r for _, r in rows if can_apply(r)]
            if not ready:
                self.note("Nothing to tick: pick a station for " + (
                    "this channel" if len(rows) == 1 else "those channels") + " first.", warn=True)
                return
            self.tick_selected(not all(r["write"] for r in ready))

        def mark_reviewed(self, all_shown=False):
            rows = self.targets(all_shown)
            if rows:
                self.after_edit([i for i, _ in rows])
                self.note(f"Marked {len(rows)} channel{'s' if len(rows) > 1 else ''} reviewed.")

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
            q = self.name_filter.get().strip().lower()
            for i, r in rows:   # handled rows leave a filtered view such as Needs attention
                if not self.matches(r, display_status(r), q):
                    self.tree.delete(str(i))
            self.update_counts()
            if len(rows) == 1:
                r = rows[0][1]
                state = ("will be applied" if to_apply(r) else display_status(r).lower())
                self.note(f"{r['channel'].get('effective_name')}: {state}. "
                          + ("" if nxt else "That was the last channel shown. Press Copy when you're ready."))
            if nxt and self.tree.exists(nxt):
                self.tree.selection_set(nxt)
                self.tree.focus(nxt)
                self.tree.see(nxt)
            else:
                self.tree.selection_set(())
            self.tree.focus_set()

        def _kept_current(self, r):
            st = self.matcher.by_id.get(r["current"]) if self.matcher else None
            r.update(new_id=r["current"] if st else "", best=st, status=VERIFIED if st else NOT_IN_LINEUP,
                     why="kept the current ID")

        def clear_choice(self, idxs=None):
            rows = [(i, self.rows[i]) for i in idxs] if idxs is not None else self.targets()
            for _, r in rows:
                was = r.get("remove") or r["status"] in (CHANGE, REMOVE)
                r["write"], r["new_id"], r["best"], r["remove"] = False, "", None, False
                if r["current"]:
                    if was or r["status"] == VERIFIED:
                        self._kept_current(r)
                elif r["status"] in (MATCHED, VERIFIED, REMOVE, CHANGE):
                    r["status"], r["why"] = (REVIEW if r["cands"] else UNMATCHED), "no ID chosen"
            if rows:
                self.after_edit([i for i, _ in rows])
                self.note("Cleared. " + ("That channel" if len(rows) == 1 else f"Those {len(rows)} channels")
                          + " won't change.")

        def _set_remove(self, r):
            """Mark a row so Copy clears its station ID. Returns False if there is nothing to remove."""
            if not r["current"]:
                return False
            r.update(remove=True, new_id="", best=None, write=True, status=REMOVE,
                     why=f"you chose to remove {r['current']}")
            log.info("Marked channel %s to remove ID %s", r["channel"]["id"], r["current"])
            return True

        def remove_id(self):
            rows = self.targets()
            if not rows:
                return
            have = [(i, r) for i, r in rows if r["current"]]
            if not have:
                self.note(("This channel has" if len(rows) == 1 else "Those channels have")
                          + " no station ID to remove.", warn=True)
                return
            for _, r in have:
                self._set_remove(r)
            self.after_edit([i for i, _ in have])
            self.note(f"{len(have)} station ID{' is' if len(have) == 1 else 's are'} set to be removed.")

        def reset_rows(self):
            rows = self.targets()
            for _, r in rows:
                r.update(r["auto"])
                r.update(remove=False, reviewed=False)
            if rows:
                self.update_rows([i for i, _ in rows])
                self.show_detail()
                self.note("Back to the matcher's result.")

        @staticmethod
        def _mark_change(r):
            if r["current"] and r["new_id"] != r["current"]:
                r["status"], r["why"] = CHANGE, f"{r['why']}: replaces {r['current']}"
            elif r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"

        # ------------------------------------------------------------ channel detail
        def set_detail_enabled(self, on):
            for b in self.d_buttons:
                b.state(["!disabled"] if on else ["disabled"])

        def where(self, st):
            if self.matcher and st.get("stationId") in self.matcher.by_id:
                n = len(self.matcher.by_id[st["stationId"]].get("lineups", []))
                return f"in {n} lineup{'' if n == 1 else 's'}"
            return "not in your lineups"

        def show_detail(self):
            i, r = self.current()
            same = i is not None and i == self.shown_i
            self.shown_i = i
            if r is None:
                n = len(self.tree.selection())
                self.cand.delete(*self.cand.get_children())
                self.cand_ids, self.cand_st = [], {}
                self.list_box.configure(text="Suggestions")
                self.list_info.configure(text="")
                self.set_detail_enabled(False)
                if n:
                    self.d_title.configure(text=f"{n} channels selected")
                    self.d_sub.configure(text="Use Tick selected, Untick selected or Mark reviewed below "
                                              "(or right-click) to change them all at once.")
                else:
                    self.d_title.configure(text="Select a channel above." if self.rows else "")
                    self.d_sub.configure(text="")
                return
            self.set_detail_enabled(True)
            ch = r["channel"]
            name = ch.get("effective_name") or ch.get("name") or ""
            num = _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number"))
            self.d_title.configure(text=f"{str(num) + '   ' if num != '' else ''}{name}"
                                        + (f"   ·   {r['group']}" if r.get("group") else ""))
            st = r["best"] or {}
            if r.get("remove"):
                plan = f"Will remove the current ID {r['current']}."
            elif can_apply(r):
                plan = (f"Will {'replace ' + r['current'] + ' with' if r['current'] else 'apply'} "
                        f"{r['new_id']} {st.get('name', '')}" + ("" if r["write"] else " (unticked)") + ".")
            else:
                plan = "Nothing to apply."
            self.d_sub.configure(text=f"Current ID: {r['current'] or '-'}   ·   tvg-id: "
                                      f"{ch.get('effective_tvg_id') or '-'}   ·   {r['why']}   ·   {plan}")
            if not same:
                self.list_mode, self.results = "suggest", []
                self.live.set_quiet(search_phrase(name))
            self.fill_list(r, same)

        def fill_list(self, r, same):
            """The station list: suggestions for the channel, or search results while searching."""
            top = self.cand.yview()[0] if same else 0.0
            self.cand.delete(*self.cand.get_children())
            self.cand_ids, self.cand_st = [], {}
            name = r["channel"].get("effective_name") or r["channel"].get("name") or ""
            items = []
            if self.list_mode == "search":
                items = self.results
                self.list_box.configure(text=f"Search results for '{self.results_text}'  —  click a line to "
                                             "use it (or press 1-9)")
            else:
                if r["current"]:
                    st = self.matcher.by_id.get(r["current"]) if self.matcher else None
                    items.append(st or {"stationId": r["current"], "name": "", "callSign": "", "type": ""})
                if r["best"]:
                    items.append(r["best"])
                items += r["cands"]
                self.list_box.configure(text=f"Suggestions for {name}  —  click a line to use it (or press 1-9)")
            for st in items:
                sid = str(st.get("stationId") or "")
                if not sid or sid in self.cand_st:
                    continue
                self.cand_st[sid] = st
                self.cand_ids.append(sid)
                n = len(self.cand_ids)
                picked = sid == r["new_id"] and not r.get("remove")
                mark = (ON if r["write"] or sid == r["current"] else OFF) if picked else ""
                where = self.where(st)
                if sid == r["current"]:
                    where = ("current ID, to be removed" if r.get("remove") else "current ID") + " · " + where
                tags = ("on",) if picked else ("cur",) if sid == r["current"] else \
                    ("outside",) if where.startswith("not in") else ()
                self.cand.insert("", "end", iid=sid, tags=tags, values=(
                    n if n <= 9 else "", mark, st.get("name") or "", st.get("callSign") or "",
                    st.get("type") or "", where, sid))
            text = self.search_v.get().strip()
            if self.list_mode == "search":
                info = f"{len(self.cand_ids)} found" + (f"  ·  {self.results_note}" if self.results_note else "")
                info += "  ·  Esc or ✕ shows the suggestions again"
                empty = "Nothing found. Try other words, tick All of Gracenote, or type a station ID below."
            else:
                info = (f"Press Return in the search box to search for '{text}', or type something else."
                        if text else "Type in the search box to look for other stations.")
                empty = "No suggestions. Use the search box above, or type a station ID below."
            self.list_info.configure(text=info)
            if not self.cand_ids:
                self.cand.insert("", "end", iid="none", values=("", "", empty, "", "", "", ""))
            if same:
                self.cand.yview_moveto(top)

        def show_results(self, cands, text, note):
            """LiveSearch result: a list of stations, or None when the search box was cleared."""
            if cands is None:
                self.list_mode, self.results = "suggest", []
            else:
                self.list_mode, self.results, self.results_text, self.results_note = "search", cands, text, note
            i, r = self.current()
            if r is not None:
                self.fill_list(r, False)

        def focus_list(self):
            kids = [k for k in self.cand.get_children() if k != "none"]
            if kids:
                self.cand.focus_set()
                self.cand.selection_set(kids[0])
                self.cand.focus(kids[0])
                self.cand.see(kids[0])

        def cand_click(self, e):
            if self.cand.identify_region(e.x, e.y) not in ("cell", "tree"):
                return None   # headings and column dividers work as usual
            iid = self.cand.identify_row(e.y)
            if not iid or iid == "none":
                return "break"
            self.cand.selection_set(iid)
            self.cand.focus(iid)
            self.cand.focus_set()
            self.use_cand(iid)
            return "break"

        def pick_number(self, k):
            """1-9: use that station and go to the next channel."""
            i, r = self.current()
            if r is None:
                self.note("Select one channel to pick a station for it.", warn=True)
                return
            if k > len(self.cand_ids):
                self.note(f"There is no station {k} in the list.", warn=True)
                return
            sid = self.cand_ids[k - 1]
            if not (sid == r["new_id"] and r["write"] and not r.get("remove")):   # never clears a pick
                self.use_cand(sid)
            self.done_next()

        def use_cand(self, sid):
            i, r = self.current()
            if r is None or not sid or sid not in self.cand_st:
                return
            if sid == r["new_id"] and r["write"] and not r.get("remove") and sid != r["current"]:
                self.clear_choice([i])   # clicking the picked station again clears it
                return
            self._apply(i, self.cand_st[sid], sid, "picked by you")
            if self.cand.exists(sid):
                self.cand.selection_set(sid)
                self.cand.see(sid)

        def set_manual(self):
            i, r = self.current()
            sid = self.manual_v.get().strip()
            if r is None:
                self.note("Select one channel in the table first.", warn=True)
                return
            if not sid:
                self.note("Type a station ID first.", warn=True)
                return
            if not sid.isdigit():
                self.note("Gracenote station IDs are numbers only.", warn=True)
                return
            st = self.matcher.by_id.get(sid)
            self.manual_v.set("")
            self._apply(i, st, sid, "entered by you")

        # ------------------------------------------------------------ walkthrough
        def walkthrough(self):
            """Step through every channel without a chosen ID and pick from options."""
            if not self.matcher:
                self.note("Connect first.", warn=True)
                return
            shown = [int(x) for x in self.tree.get_children()]
            todo = [i for i, r in enumerate(self.rows) if not r["write"] and not r.get("reviewed")
                    and r["status"] in (REVIEW, UNMATCHED, ERROR)]
            if self.name_filter.get().strip():
                # Find active: step through exactly what the table shows, even channels that have an ID
                todo = shown
            elif self.group_filter.get() != "All groups":
                # group only: the unmatched channels in that group, in table order
                want = set(todo)
                todo = [i for i in shown if i in want]
            if not todo:
                self.note("No unmatched channels left.")
                return

            win = tk.Toplevel(self.root)
            win.title("Match unmatched channels")
            win.geometry("900x560")
            win.minsize(640, 420)
            win.transient(self.root)
            state = {"pos": 0, "cands": []}
            outer = ttk.Frame(win, padding=10)
            outer.pack(fill="both", expand=True)

            head = ttk.Label(outer, font=self.bold)
            head.pack(side="top", anchor="w")
            sub = ttk.Label(outer, foreground="gray")
            sub.pack(side="top", anchor="w")
            ttk.Label(outer, text="Pick the right station (click, Return, or press 1-9). "
                                  "Esc = skip. Type in Search for other stations.").pack(
                side="top", anchor="w", pady=(8, 2))

            brow = ttk.Frame(outer)
            brow.pack(side="bottom", fill="x", pady=(8, 0))
            prog = ttk.Label(brow, width=1)
            irow = ttk.Frame(outer)
            irow.pack(side="bottom", fill="x", pady=(4, 0))
            srow = ttk.Frame(outer)
            srow.pack(side="bottom", fill="x", pady=(6, 0))
            info = ttk.Label(outer, foreground="gray", width=1)
            info.pack(side="bottom", fill="x", pady=(4, 0))
            box, lb = scrolled_tree(outer, ("n",) + STATION_COLS, ("#",) + STATION_HEADS,
                                    (30,) + STATION_WIDTHS, "id", selectmode="browse", height=12)
            box.pack(side="top", fill="both", expand=True)
            lb.column("n", anchor="center")
            lb.tag_configure("outside", foreground="#a04000")

            ttk.Label(srow, text="Search:").pack(side="left")
            sv = tk.StringVar()
            se = ttk.Entry(srow, textvariable=sv, width=30)
            outside = tk.BooleanVar(value=False)
            ttk.Checkbutton(srow, text="search all of Gracenote (not just my lineups)",
                            variable=outside).pack(side="right", padx=(6, 0))
            ttk.Button(srow, text="Search", command=lambda: live.schedule(0)).pack(side="right")
            se.pack(side="left", padx=4, fill="x", expand=True)
            ttk.Label(irow, text="Or a station ID:").pack(side="left")
            iv = tk.StringVar()
            ie = ttk.Entry(irow, textvariable=iv, width=10)
            ie.pack(side="left", padx=4)

            def alive():
                return bool(win.winfo_exists())

            def fill(cands, note="", focus=True):
                state["cands"] = cands
                lb.delete(*lb.get_children())
                for n, s in enumerate(cands, 1):
                    where = self.where(s)
                    lb.insert("", "end", iid=str(n - 1), tags=("outside",) if where.startswith("not in") else (),
                              values=(n if n <= 9 else "", s.get("name") or "?", s.get("callSign") or "",
                                      s.get("type") or "", where, s.get("stationId")))
                info.configure(text=note if not cands or note else f"{len(cands)} options")
                if not cands:
                    lb.insert("", "end", iid="none", values=(
                        "", note or "No options. Try a search, or type a station ID.", "", "", "", ""))
                else:
                    lb.selection_set("0")
                    lb.focus("0")
                if focus:
                    lb.focus_set()

            def close():
                self.rebuild()
                win.destroy()
                self.tree.focus_set()

            def show():
                if state["pos"] >= len(todo):
                    left = sum(1 for i in todo if not self.rows[i]["write"])
                    n = sum(1 for r in self.rows if to_apply(r))
                    close()
                    self.note(f"Finished: {len(todo) - left} picked, {left} skipped. "
                              f"Press Copy {n} IDs for m3u-editor to apply them.")
                    return
                r = self.rows[todo[state["pos"]]]
                ch = r["channel"]
                name = ch.get("effective_name") or ch.get("name") or ""
                head.config(text=name)
                sub.config(text=f"tvg-id: {ch.get('effective_tvg_id') or '-'}    "
                                f"current ID: {r['current'] or '-'}    {r['why']}")
                prog.config(text=f"Channel {state['pos'] + 1} of {len(todo)}")
                cands = merge_candidates(self.matcher, name, r["cands"], self.matcher.suggest(name))
                live.set_quiet(search_phrase(name))
                iv.set("")
                best = name_score(name, cands[0]["name"], _bare_call(cands[0])) if cands else 0
                wide = best < 0.9 and self.cdvr
                fill(cands, "Looking wider in Gracenote…" if wide else "")
                if wide:
                    pos, cdvr, matcher = state["pos"], self.cdvr, self.matcher

                    def post(fn):
                        self.q.put(("call", (None, lambda: alive() and state["pos"] == pos and fn())))

                    def bg():
                        try:
                            extra = global_candidates(cdvr, name, matcher)
                        except Exception as e:  # network trouble only loses the extra suggestions
                            log.warning("Background Gracenote search for %r failed: %s", name, e)
                            post(lambda: info.configure(text="Gracenote search failed - press Search to retry"))
                            return
                        post(lambda: live.typed or fill(merge_candidates(matcher, name, cands, extra),
                                                        "" if extra else "Nothing more in Gracenote.",
                                                        focus=False))
                    threading.Thread(target=bg, name="suggest", daemon=True).start()

            def advance():
                state["pos"] += 1
                show()

            def take(i, st, sid, why):
                r = self.rows[i]
                r.update(best=st, new_id=sid, write=True, status=MATCHED, why=why, remove=False, reviewed=True)
                self._mark_change(r)
                log.info("Walkthrough: %s %s for channel %s", why, sid, r["channel"]["id"])
                self.update_rows([i])
                advance()

            def pick(_e=None, index=None):
                sel = [index] if index is not None else [int(k) for k in lb.selection() if k != "none"]
                if not sel or sel[0] >= len(state["cands"]):
                    return "break"
                st = state["cands"][sel[0]]
                take(todo[state["pos"]], st, st["stationId"], "picked by you")
                return "break"

            def use_id(_e=None):
                sid = iv.get().strip()
                if not sid.isdigit():
                    self.note("Station IDs are numbers only.", warn=True)
                    return "break"
                st = self.matcher.by_id.get(sid)
                take(todo[state["pos"]], st, sid, "entered by you")
                return "break"

            def back():
                if state["pos"] > 0:
                    state["pos"] -= 1
                    show()

            def remove():
                i = todo[state["pos"]]
                if not self._set_remove(self.rows[i]):
                    self.note("This channel has no station ID to remove.", warn=True)
                    return
                self.rows[i]["reviewed"] = True
                self.update_rows([i])
                advance()

            ttk.Button(irow, text="Use ID", command=use_id).pack(side="left")
            ttk.Button(brow, text="Stop", command=close).pack(side="right")
            ttk.Button(brow, text="Skip (Esc)", command=advance).pack(side="right", padx=4)
            ttk.Button(brow, text="Remove ID", command=remove).pack(side="right", padx=4)
            ttk.Button(brow, text="Back", command=back).pack(side="right", padx=4)
            ttk.Button(brow, text="Use selected  ⏎", default="active", command=pick).pack(side="right", padx=4)
            prog.pack(side="left", fill="x", expand=True)
            live = LiveSearch(self, sv, lambda c, t, n: fill(
                c or [], n or ("" if c else "No matches yet. Keep typing, or tick 'search all of Gracenote'."),
                focus=False), wide=outside.get, alive=alive)
            outside.trace_add("write", lambda *a: live.schedule(0))
            se.bind("<Return>", lambda e: (live.schedule(0), "break")[1])
            se.bind("<FocusIn>", lambda e: se.after_idle(lambda: se.select_range(0, "end")))
            se.bind("<Down>", lambda e: (lb.focus_set(), "break")[1])
            ie.bind("<Return>", use_id)
            win.bind("/", lambda e: None if e.widget in (se, ie) else (se.focus_set(), "break")[1])
            lb.bind("<Double-1>", lambda e: pick() if lb.identify_region(e.x, e.y) == "cell" else None)
            lb.bind("<Return>", pick)
            lb.bind("<KP_Enter>", pick)
            win.bind("<Escape>", lambda e: advance())
            for k in range(1, 10):
                lb.bind(str(k), lambda e, k=k: pick(index=k - 1))
            win.protocol("WM_DELETE_WINDOW", close)
            show()

        # ------------------------------------------------------------ export, copy, verify
        def export(self):
            rows = [r for r in self.rows if not r["write"] and r["status"] != VERIFIED]
            if not rows:
                self.note("Nothing to export: every channel has an ID ticked or verified.", warn=True)
                return
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv",
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

        def export_plugin(self, parent=None):
            parent = parent or self.root
            path = filedialog.asksaveasfilename(parent=parent, defaultextension=".zip", initialfile=PLUGIN_ZIP,
                                                filetypes=[("Zip archive", "*.zip")])
            if not path:
                return
            try:
                export_plugin(path)
            except OSError as e:
                messagebox.showerror("Export failed", str(e), parent=parent)
                return
            self._save_settings(plugin_exported=True)
            self.note(f"Saved the m3u-editor plugin to {path}")
            messagebox.showinfo("Install the plugin in m3u-editor (once)", PLUGIN_STEPS.format(path=path),
                                parent=parent)

        def write(self):
            """Copy every ticked change for the plugin's Apply Station IDs action."""
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            todo = [r for r in self.rows if to_apply(r)]
            if not todo:
                self.note("Nothing to copy: no ticked channel has a new station ID.", warn=True)
                return
            removals = [r for r in todo if r.get("remove")]
            changes = [r for r in todo if r["current"] and not r.get("remove")]
            msg = f"Copy {len(todo)} station ID change{'s' if len(todo) != 1 else ''} for m3u-editor?"
            if removals:
                lines = [f"  {r['channel'].get('effective_name')}: {r['current']} -> (no ID)" for r in removals[:12]]
                if len(removals) > 12:
                    lines.append(f"  ...and {len(removals) - 12} more")
                msg += f"\n\n{len(removals)} of these REMOVE the ID:\n" + "\n".join(lines)
            if changes:
                lines = [f"  {r['channel'].get('effective_name')}: {r['current']} -> {r['new_id']}"
                         for r in changes[:12]]
                if len(changes) > 12:
                    lines.append(f"  ...and {len(changes) - 12} more")
                msg += f"\n\n{len(changes)} of these REPLACE an existing ID:\n" + "\n".join(lines)
            if not messagebox.askyesno("Copy for m3u-editor", msg, parent=self.root):
                return
            text = station_id_lines(self.m3ue.playlist, todo)
            saved = os.path.join(CONFIG_DIR, "station_ids_to_apply.txt")
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                with open(saved, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                log.warning("Could not save %s: %s", saved, e)
                saved = "(could not save a copy)"
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self.copied = todo
            log.info("Copied %d IDs for m3u-editor, %d replacing existing, %d removing: %s", len(todo),
                     len(changes), len(removals),
                     ", ".join(f"{r['channel']['id']}:{r['current']}->{r['new_id'] or '(none)'}" for r in todo))
            self.save_session(True)
            self.note(f"Copied {len(todo)} station IDs. Apply them in m3u-editor, then press Verify.")
            self.show_apply(todo, text, saved)

        def apply_alive(self):
            try:
                return bool(self.apply_win and self.apply_win.winfo_exists())
            except tk.TclError:
                return False

        def show_apply(self, todo, text, saved):
            """One window for the whole apply step: what changes, where to paste it, and Verify."""
            if self.apply_alive():
                self.apply_win.destroy()
            top = self.apply_win = tk.Toplevel(self.root)
            top.title("Apply in m3u-editor")
            top.transient(self.root)
            top.geometry("820x600")
            top.minsize(620, 460)
            frm = ttk.Frame(top, padding=12)
            frm.pack(fill="both", expand=True)
            removals = [r for r in todo if r.get("remove")]
            changes = [r for r in todo if r["current"] and not r.get("remove")]
            new = len(todo) - len(removals) - len(changes)
            ttk.Label(frm, text=f"{len(todo)} station ID{' is' if len(todo) == 1 else 's are'} on your "
                                "clipboard", font=self.bold).pack(side="top", anchor="w")
            parts = [f"{new} new"] if new else []
            if changes:
                parts.append(f"{len(changes)} REPLACE an existing ID")
            if removals:
                parts.append(f"{len(removals)} REMOVE an ID")
            ttk.Label(frm, text=", ".join(parts) + ". Nothing changes in m3u-editor until you paste them there "
                                "and confirm.", wraplength=780, justify="left").pack(side="top", anchor="w")

            steps = ttk.LabelFrame(frm, text="In m3u-editor", padding=8)
            steps.pack(side="top", fill="x", pady=(8, 6))
            pl = self.m3ue.playlist
            for n, (line, btn) in enumerate((
                    ("Extensions > Extensions > Gracenote Station IDs > Actions > Apply Station IDs.",
                     ("Open m3u-editor", lambda: webbrowser.open(self.m3ue.base))),
                    (f"Playlist: {pl['name']}" + (f" (#{pl['id']})" if pl.get("id") else "")
                     + ". Paste into \"Station IDs from the matcher\", then confirm.",
                     ("Copy again", lambda: self._copy_again(text))),
                    ("When the run shows as completed under Run History, press Verify below.", None)), 1):
                ttk.Label(steps, text=f"{n}.").grid(row=n, column=0, sticky="nw", padx=(0, 4), pady=2)
                ttk.Label(steps, text=line, wraplength=560, justify="left").grid(row=n, column=1, sticky="w",
                                                                                 pady=2)
                if btn:
                    ttk.Button(steps, text=btn[0], command=btn[1]).grid(row=n, column=2, sticky="e", padx=(8, 0))
            steps.columnconfigure(1, weight=1)
            ttk.Label(steps, text=f"It runs in the background and only touches channels in that playlist. "
                                  f"A copy of the IDs is saved in {saved}.", foreground="gray",
                      wraplength=740, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

            btns = ttk.Frame(frm)
            btns.pack(side="bottom", fill="x", pady=(8, 0))
            ttk.Button(btns, text="Close", command=top.destroy).pack(side="right")
            self.apply_verify = ttk.Button(btns, text="Verify in m3u-editor", default="active", command=self.verify)
            self.apply_verify.pack(side="right", padx=6)
            if self.busy:
                self.apply_verify.state(["disabled"])
            b = ttk.Button(btns, text="Export plugin…", command=lambda: self.export_plugin(parent=top))
            b.pack(side="left")
            Tip(b, "First time? Install the Gracenote Station IDs plugin in m3u-editor once.")
            self.apply_msg = ttk.Label(frm, text="" if read_json(SETTINGS_FILE).get("plugin_exported") else
                                       "First time? Install the plugin once: press Export plugin… and "
                                       "follow the steps.", foreground="gray", width=1)
            self.apply_msg.pack(side="bottom", fill="x", pady=(6, 0))
            self.apply_msg.bind("<Configure>", lambda e: self.apply_msg.configure(wraplength=max(200, e.width - 4)))

            title = "What will change" + ("  —  check the replacements and removals" if changes or removals
                                          else "")
            lf = ttk.LabelFrame(frm, text=title, padding=4)
            lf.pack(side="top", fill="both", expand=True)
            box, tv = scrolled_tree(lf, ("num", "channel", "gname", "current", "new"),
                                    ("Ch #", "Channel", "Gracenote name", "Current ID", "New ID"),
                                    (60, 240, 220, 90, 90), "new", selectmode="browse", height=6)
            box.pack(fill="both", expand=True)
            tv.tag_configure(CHANGE, background=ROW_COLORS[CHANGE], foreground="black")
            tv.tag_configure(REMOVE, background=ROW_COLORS[REMOVE], foreground="black")
            for r in removals + changes + [r for r in todo if r not in removals and r not in changes]:
                ch = r["channel"]
                tag = REMOVE if r.get("remove") else CHANGE if r["current"] else ""
                tv.insert("", "end", tags=(tag,) if tag else (), values=(
                    _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number")),
                    ch.get("effective_name") or ch.get("name") or "", (r["best"] or {}).get("name", ""),
                    r["current"] or "-", "(remove)" if r.get("remove") else r["new_id"]))
            top.bind("<Escape>", lambda e: top.destroy())
            self.apply_verify.focus_set()

        def _copy_again(self, text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            if self.apply_alive():
                self.apply_msg.configure(text="Copied again.", foreground="")

        def verify(self):
            """Re-read the playlist and confirm every copied ID is now in m3u-editor."""
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            if not self.m3ue:
                self.note("Connect first.", warn=True)
                return
            todo = self.copied or [r for r in self.rows if r["write"]]
            if not todo:
                self.note("Nothing to verify: copy some station IDs for m3u-editor first.", warn=True)
                return
            m3ue = self.m3ue
            if self.apply_alive():
                self.apply_msg.configure(text="Checking m3u-editor…", foreground="")

            def job(progress):
                progress("Reading the playlist from m3u-editor...", 30)
                live = {c["id"]: c for c in m3ue.load()}
                return lambda: self.verified(todo, live)

            self._worker(job, "verify")

        def verified(self, todo, live):
            """Runs on the Tk thread with the playlist read back from m3u-editor."""
            ok, deleted, pending = 0, 0, []
            for r in todo:
                ch = live.get(r["channel"]["id"])
                want = "" if r.get("remove") else r["new_id"]
                if ch is None:
                    r.update(status=DELETED, write=False, why="channel no longer in this playlist")
                    deleted += 1
                elif (ch.get("effective_tvc_guide_stationid") or "") == want:
                    if want:
                        r.update(current=want, status=VERIFIED, write=False, why="verified in m3u-editor")
                    else:
                        r.update(current="", status=UNMATCHED, write=False, remove=False, why="ID removed",
                                 reviewed=True)
                    r["auto"] = {k: r[k] for k in ("status", "best", "new_id", "write", "why")}  # Reset = now
                    ok += 1
                elif not ch.get("in_output"):
                    r.update(why="not in the playlist output, so it cannot be checked")
                    pending.append(r)
                else:
                    r.update(why=f"m3u-editor still shows '{ch.get('effective_tvc_guide_stationid') or 'nothing'}'")
                    pending.append(r)
            self.copied = [r for r in self.copied if r in pending]
            self.prog["value"] = 100
            msg = (f"Verified {ok} of {len(todo)} station IDs."
                   + (f" {deleted} no longer in m3u-editor." if deleted else "")
                   + (f" {len(pending)} not applied yet (still ticked): check that the plugin's Apply "
                      "Station IDs run finished (Extensions > Gracenote Station IDs > Run History), then "
                      "Verify again." if pending else ""))
            self.note(msg, warn=bool(pending))
            ids = {id(r) for r in todo}
            self.update_rows([i for i, r in enumerate(self.rows) if id(r) in ids])
            self.show_detail()
            self.save_session(True)
            if self.apply_alive():
                self.apply_msg.configure(text=msg + ("" if pending else "  All done; you can close this window."),
                                         foreground="#c00000" if pending else "#006000")

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    if aqua:
        root.createcommand("::tk::mac::Quit", app.on_close)
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


def main():
    setup_logging()
    log.info("Starting %s on %s, Python %s", os.path.basename(__file__), sys.platform,
             sys.version.split()[0])
    ensure_dependencies(assume_yes="--yes" in sys.argv, check_only="--check-deps" in sys.argv)
    if "--check-deps" in sys.argv:
        return
    run_gui()


if __name__ == "__main__":
    main()
