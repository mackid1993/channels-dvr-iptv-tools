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

class Cancelled(Exception):
    """The user pressed Cancel."""


class NetError(RuntimeError):
    """The server could not be reached."""


class HttpError(RuntimeError):
    """The server answered with an HTTP error status."""

    def __init__(self, msg, code, body=""):
        super().__init__(msg)
        self.code, self.body = code, body


_tls = threading.local()  # per-worker cancel flag


def _cancelled():
    evt = getattr(_tls, "cancel", None)
    return evt is not None and evt.is_set()


def _sleep(seconds):
    """Sleep that wakes up immediately on Cancel."""
    end = time.time() + seconds
    while time.time() < end:
        if _cancelled():
            raise Cancelled()
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
        if _cancelled():
            raise Cancelled() from None
        log.warning("%s %s -> failed after %.2fs: %r", method, path, time.time() - t0, e)
        raise NetError(f"Could not reach {host}: {_reason(e)}") from None
    log.debug("%s %s -> %s, %d bytes in %.2fs", method, path, code, len(raw), time.time() - t0)
    return raw


def http(method, url, headers=None, body=None, timeout=TIMEOUT, retries=2):
    """JSON request with retries on network errors and HTTP 429/5xx."""
    if _cancelled():
        raise Cancelled()
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
    if _cancelled():
        raise Cancelled()
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
            raise RuntimeError(f"Gracenote does not recognise postal code '{postal}' for {country}. "
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
        _tls.cancel = cancel_evt  # pool threads honour Cancel too
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
        except (NetError, Cancelled):
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
            if time.time() - idx.get("built", 0) < CACHE_DAYS * 86400:
                progress(f"Using cached lineup index ({len(idx['stations'])} stations)", 95)
                return idx
        except (OSError, ValueError):
            pass
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

def _fmt_num(num):
    if isinstance(num, float) and num.is_integer():
        return int(num)
    return "" if num is None else num


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App:
        def __init__(self, root):
            self.root = root
            self.q = queue.Queue()
            self.rows = []           # one dict per channel
            self.matcher = None
            self.cdvr = None
            self.m3ue = None
            self.copied = []         # rows handed to the plugin, awaiting Verify
            self.busy = False
            self.job_id = 0
            self.job_kind = ""
            self.cancel_evt = None
            self.source = ""
            self._save_after = None
            root.title("Channels DVR - Gracenote Station ID Matcher for m3u-editor")
            root.geometry("1250x780")
            self._build()
            self._load_settings()
            root.bind("<Escape>", lambda e: self.cancel())
            root.protocol("WM_DELETE_WINDOW", self.on_close)
            root.after(100, self._poll)
            log.info("Window opened")

        # ------------------------------------------------------------ layout
        def _build(self):
            pad = {"padx": 4, "pady": 3}
            top = ttk.LabelFrame(self.root, text="Servers")
            top.pack(fill="x", padx=8, pady=6)
            self.v = {k: tk.StringVar() for k in
                      ("cdvr", "playlist", "token", "countries")}
            self.include_existing = tk.BooleanVar(value=True)
            r = 0
            ttk.Label(top, text="Channels DVR").grid(row=r, column=0, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["cdvr"], width=26).grid(row=r, column=1, **pad)
            ttk.Label(top, text="m3u-editor playlist URL").grid(row=r, column=2, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["playlist"], width=52).grid(row=r, column=3, columnspan=3,
                                                                          sticky="we", **pad)
            r = 1
            ttk.Label(top, text="Countries").grid(row=r, column=0, sticky="e", **pad)
            cf = ttk.Frame(top)
            cf.grid(row=r, column=1, sticky="we", **pad)
            ttk.Entry(cf, textvariable=self.v["countries"], width=30).pack(side="left", fill="x", expand=True)
            ttk.Button(cf, text="Pick…", command=self.pick_countries).pack(side="left", padx=4)
            ttk.Label(top, text="API token").grid(row=r, column=2, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["token"], show="•", width=30).grid(row=r, column=3, sticky="w", **pad)
            ttk.Label(top, text="m3u-editor: profile menu > API Tokens (View permission)",
                      foreground="gray").grid(row=r, column=4, columnspan=2, sticky="w", **pad)
            r = 2
            ttk.Label(top, text="e.g. USA:10001, CAN, GBR  (code:postal; your postal code decides local stations)",
                      foreground="gray").grid(row=r, column=1, columnspan=3, sticky="w", **pad)
            ttk.Checkbutton(top, text="Include channels that already have an ID (to check or fix them)",
                            variable=self.include_existing).grid(row=3, column=1, columnspan=3, sticky="w", **pad)
            btns = ttk.Frame(top)
            btns.grid(row=0, column=6, rowspan=4, padx=10)
            self.run_btn = ttk.Button(btns, text="Connect && Match", command=lambda: self.start(False))
            self.run_btn.pack(fill="x", pady=2)
            self.rebuild_btn = ttk.Button(btns, text="Rebuild lineup index", command=lambda: self.start(True))
            self.rebuild_btn.pack(fill="x", pady=2)
            self.cancel_btn = ttk.Button(btns, text="Cancel (Esc)", command=self.cancel, state="disabled")
            self.cancel_btn.pack(fill="x", pady=2)
            ttk.Button(btns, text="Export m3u-editor plugin…", command=self.export_plugin).pack(fill="x", pady=2)

            mid = ttk.Frame(self.root)
            mid.pack(fill="both", expand=True, padx=8)
            bar = ttk.Frame(mid)
            bar.pack(fill="x")
            ttk.Label(bar, text="Show").pack(side="left")
            self.filter = tk.StringVar(value="All")
            fbox = ttk.Combobox(bar, textvariable=self.filter, state="readonly", width=26,
                                values=["All", "Needs attention", MATCHED, REVIEW, UNMATCHED,
                                        NOT_IN_LINEUP, VERIFIED, CHANGE, REMOVE, ERROR, DELETED,
                                        "Ticked to write"])
            fbox.pack(side="left", padx=4)
            ttk.Label(bar, text="Group").pack(side="left", padx=(10, 0))
            self.group_filter = tk.StringVar(value="All groups")
            self.group_box = ttk.Combobox(bar, textvariable=self.group_filter, state="readonly", width=24,
                                          values=["All groups"])
            self.group_box.pack(side="left", padx=4)
            self.group_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            ttk.Label(bar, text="Find").pack(side="left", padx=(10, 0))
            self.name_filter = tk.StringVar()
            fe = ttk.Entry(bar, textvariable=self.name_filter, width=22)
            fe.pack(side="left", padx=4)
            self.name_filter.trace_add("write", lambda *a: self.refresh())
            ttk.Button(bar, text="Clear", width=6, command=self.clear_filters).pack(side="left")
            self.groups = {}
            fbox.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self.counts = ttk.Label(bar, text="")
            self.counts.pack(side="left", padx=12)
            ttk.Label(bar, text="Find: name, ID, # or range like 500-600.  Click a heading to sort.",
                      foreground="gray").pack(side="right")

            cols = ("write", "num", "channel", "group", "current", "status", "new", "gname", "call", "why")
            heads = ("Write", "Ch #", "m3u-editor channel", "Group", "Current ID", "Status",
                     "Station ID", "Gracenote name", "Call sign", "Reason")
            widths = (50, 60, 250, 140, 80, 150, 80, 210, 90, 200)
            self.sort_col, self.sort_rev = "num", False
            tf = ttk.Frame(mid)
            tf.pack(fill="both", expand=True, pady=4)
            self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
            for c, h, w in zip(cols, heads, widths):
                self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
                self.tree.column(c, width=w, anchor="w", stretch=c in ("channel", "gname", "why"))
            sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            for tag, color in ((MATCHED, "#d9f2d9"), (REVIEW, "#fff2c2"), (UNMATCHED, "#f8d4d4"),
                               (NOT_IN_LINEUP, "#f8d4d4"), (VERIFIED, "#e8e8e8"), (ERROR, "#f4b6b6"),
                               (DELETED, "#cccccc"), (CHANGE, "#ffd8a8"), (REMOVE, "#e0c8f0")):
                self.tree.tag_configure(tag, background=color, foreground="black")
            self.tree.bind("<<TreeviewSelect>>", self._selected)
            self.tree.bind("<Double-1>", lambda e: self.toggle())
            self.tree.bind("<space>", lambda e: self.toggle())

            pick = ttk.LabelFrame(self.root, text="Fix the selected channel")
            pick.pack(fill="x", padx=8, pady=4)
            self.sel_label = ttk.Label(pick, text="Select a row above.")
            self.sel_label.grid(row=0, column=0, columnspan=4, sticky="w", padx=4)
            self.cand = tk.Listbox(pick, height=6, width=100, exportselection=False)
            self.cand.grid(row=1, column=0, rowspan=3, sticky="we", padx=4, pady=3)
            self.cand.bind("<Double-1>", lambda e: self.use_candidate())
            pick.columnconfigure(0, weight=1)
            ttk.Button(pick, text="Use selected", command=self.use_candidate).grid(row=1, column=1, sticky="we", padx=4)
            self.search_v = tk.StringVar()
            se = ttk.Entry(pick, textvariable=self.search_v, width=28)
            se.grid(row=2, column=1, padx=4)
            se.bind("<Return>", lambda e: self.search())
            ttk.Button(pick, text="Search", command=self.search).grid(row=2, column=2, padx=4)
            self.manual_v = tk.StringVar()
            ttk.Entry(pick, textvariable=self.manual_v, width=28).grid(row=3, column=1, padx=4)
            ttk.Button(pick, text="Set ID manually", command=self.set_manual).grid(row=3, column=2, padx=4)
            ttk.Button(pick, text="Clear choice", command=self.clear_choice).grid(row=1, column=2, padx=4)
            ttk.Button(pick, text="Remove ID", command=self.remove_id).grid(row=1, column=3, padx=4)

            bottom = ttk.Frame(self.root)
            bottom.pack(fill="x", padx=8, pady=6)
            self.status = ttk.Label(bottom, text="Fill in your servers and press Connect & Match.")
            self.status.pack(side="left")
            self.verify_btn = ttk.Button(bottom, text="Verify in m3u-editor", command=self.verify)
            self.verify_btn.pack(side="right", padx=4)
            self.write_btn = ttk.Button(bottom, text="Copy ticked IDs for m3u-editor", command=self.write)
            self.write_btn.pack(side="right", padx=4)
            ttk.Button(bottom, text="Export unmatched CSV", command=self.export).pack(side="right", padx=4)
            ttk.Button(bottom, text="Open log", command=self.open_log).pack(side="right", padx=4)
            ttk.Button(bottom, text="Match unmatched one by one…",
                       command=self.walkthrough).pack(side="right", padx=4)
            self.prog = ttk.Progressbar(bottom, length=220, maximum=100)
            self.prog.pack(side="right", padx=10)

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
            row = ttk.Frame(win)
            row.pack(fill="x", padx=10, pady=8)
            ttk.Label(row, text="Edit postal codes in the main window afterwards (CODE:postal).",
                      foreground="gray").pack(side="left")
            ttk.Button(row, text="OK", command=ok).pack(side="right")
            ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=4)
            win.grab_set()

        def _load_settings(self):
            s = {}
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    s = json.load(f)
            except (OSError, ValueError):
                pass
            for k in ("cdvr", "playlist", "countries", "token"):
                if s.get(k):
                    self.v[k].set(s[k])
            if not self.v["countries"].get():
                old = re.search(r"\(([A-Z]{3})\)$", s.get("country") or "")  # older settings format
                code = old.group(1) if old else "USA"
                self.v["countries"].set(f"{code}:{s.get('postal') or COUNTRY_POSTAL.get(code, '10001')}")

        def _save_settings(self):
            # The API token is saved too, so it doesn't have to be pasted every time. The file
            # is readable only by your user account; delete it to forget the API token.
            data = {k: self.v[k].get() for k in ("cdvr", "playlist", "countries", "token")}
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                write_private_json(SETTINGS_FILE, data)
            except OSError as e:
                log.warning("Could not save settings: %s", e)

        # ------------------------------------------------------------ session autosave
        def _session_data(self):
            picks = {}
            for r in self.rows:
                if r["why"] in USER_PICK or r.get("remove") or r["status"] in (ERROR, DELETED) or not r["write"]:
                    picks[str(r["channel"]["id"])] = {
                        "name": r["channel"].get("effective_name") or r["channel"].get("name"),
                        "new_id": r["new_id"], "write": r["write"], "why": r["why"],
                        "status": r["status"], "best": r["best"], "remove": bool(r.get("remove"))}
            return {"saved": time.time(), "source": self.source, "picks": picks}

        def save_session(self, now=False):
            """Autosave picks shortly after every change (debounced)."""
            if not self.rows:
                return
            if not now:
                if self._save_after:
                    self.root.after_cancel(self._save_after)
                self._save_after = self.root.after(800, lambda: self.save_session(True))
                return
            self._save_after = None
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                with open(SESSION_FILE + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(self._session_data(), f)
                os.replace(SESSION_FILE + ".tmp", SESSION_FILE)
            except OSError as e:
                log.warning("Could not autosave picks: %s", e)

        def _restore_session(self):
            """Offer to re-apply hand picks saved from an earlier run against the same playlist."""
            try:
                with open(SESSION_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return
            if data.get("source") != self.source:
                return
            picks = data.get("picks") or {}
            todo = []
            for r in self.rows:
                pk = picks.get(str(r["channel"]["id"]))
                if pk and pk.get("why") in USER_PICK and pk.get("new_id") and pk["new_id"] != r["current"]:
                    todo.append((r, pk))
                elif pk and pk.get("remove") and pk.get("write") and r["current"]:
                    todo.append((r, pk))
            if not todo:
                return
            age = time.strftime("%b %d %H:%M", time.localtime(data.get("saved", 0)))
            if not messagebox.askyesno(
                    "Restore your picks?",
                    f"Found {len(todo)} station IDs you picked by hand on {age} that are not in "
                    "m3u-editor yet.\n\nRestore them?"):
                return
            for r, pk in todo:
                if pk.get("remove"):
                    self._set_remove(r)
                    continue
                r.update(new_id=pk["new_id"], write=pk.get("write", True), why=pk["why"],
                         status=MATCHED, best=pk.get("best") or (self.matcher.by_id.get(pk["new_id"])))
                self._mark_change(r)
            log.info("Restored %d hand picks from the saved session", len(todo))
            self.refresh()

        def open_log(self):
            path = LOG_FILE if os.path.exists(LOG_FILE) else CONFIG_DIR
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # noqa: S606 - opens the log in the default viewer
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except OSError as e:
                messagebox.showinfo("Log file", f"The log is at:\n{LOG_FILE}\n\n({e})")

        def on_close(self):
            self.save_session(True)
            log.info("Window closed")
            self.root.destroy()

        # ------------------------------------------------------------ worker plumbing
        def _poll(self):
            try:
                while True:
                    kind, (jid, payload) = self.q.get_nowait()
                    if jid != self.job_id:
                        continue  # result of a cancelled job
                    if kind == "progress":
                        text, pct = payload
                        self.status.config(text=text)
                        if pct is not None:
                            self.prog["value"] = pct
                    elif kind == "done":
                        payload()
                        self._set_busy(False)
                    elif kind == "error":
                        self._set_busy(False)
                        self.prog["value"] = 0
                        self.status.config(text="Error: " + payload.splitlines()[0])
                        messagebox.showerror("Error", payload + f"\n\nDetails are in the log:\n{LOG_FILE}")
            except queue.Empty:
                pass
            except Exception as e:  # never let the poll loop die
                log.exception("Error in the UI loop")
                self._set_busy(False)
                self.status.config(text=f"Error: {e}")
            self.root.after(100, self._poll)

        def _set_busy(self, busy):
            self.busy = busy
            idle = "disabled" if busy else "normal"
            for b in (self.run_btn, self.rebuild_btn, self.write_btn, self.verify_btn):
                b.config(state=idle)
            self.cancel_btn.config(state="normal" if busy else "disabled")

        def _worker(self, fn, kind):
            self.job_id += 1
            jid, evt = self.job_id, threading.Event()
            self.cancel_evt, self.job_kind = evt, kind

            def progress(text, pct=None):
                if evt.is_set():
                    raise Cancelled()
                self.q.put(("progress", (jid, (text, pct))))

            def run():
                _tls.cancel = evt
                try:
                    self.q.put(("done", (jid, fn(progress))))
                except Cancelled:
                    log.info("%s job cancelled", kind)
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
            log.info("User cancelled the %s job", self.job_kind)
            self._set_busy(False)
            self.prog["value"] = 0
            self.status.config(text="Cancelled. Fix the details and press Connect & Match again.")

        # ------------------------------------------------------------ actions
        def start(self, rebuild):
            if self.busy:
                return
            v = {k: var.get().strip() for k, var in self.v.items()}
            try:
                cdvr = validate_address(v["cdvr"], "Channels DVR", 8089)
                m3ue = M3uEditor(v["playlist"], v["token"])
            except ValueError as e:
                messagebox.showwarning("Check the address", str(e))
                return
            try:
                pairs = parse_countries(v["countries"])
            except ValueError as e:
                messagebox.showwarning("Check the countries", str(e))
                return
            unknown = [c for c, _ in pairs if c not in COUNTRY_NAMES]
            if unknown and not messagebox.askyesno(
                    "Countries not in the list",
                    f"{', '.join(unknown)} returned no lineups when this tool was tested. Gracenote "
                    "may have no data there; channels from it will only get labelled suggestions "
                    "from Gracenote's global search.\n\nTry anyway?"):
                return
            if not v["token"]:
                messagebox.showwarning("Missing info", "Enter an m3u-editor API token (profile menu > API "
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
                                 "write": status == MATCHED and not current})
                    if status == NOT_IN_LINEUP and not best:
                        rows[-1]["new_id"] = ""

                counts = {}
                for r in rows:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1
                log.info("Matched %d channels: %s", len(rows), counts)

                def finish():
                    self.cdvr, self.m3ue, self.matcher, self.rows = cdvr, m3ue, matcher, rows
                    self.source, self.copied = f"{m3ue.base}/{m3ue.uuid}", []
                    for r in rows:
                        r["group"] = r["channel"].get("group_name") or "(no group)"
                    names = sorted({r["group"] for r in rows}, key=str.lower)
                    counts = {n: sum(1 for r in rows if r["group"] == n) for n in names}
                    self.group_box["values"] = ["All groups"] + [f"{n}  ({counts[n]})" for n in names]
                    if self.group_filter.get() not in self.group_box["values"]:
                        self.group_filter.set("All groups")
                    self.prog["value"] = 100
                    self.status.config(
                        text=f"{m3ue.playlist['name']}: {len(chans)} channels, {len(rows)} checked against "
                             f"{idx['lineups']} lineups / {len(idx['stations'])} stations "
                             f"in {format_countries(pairs)}.")
                    self.refresh()
                    if idx.get("problems"):
                        messagebox.showwarning("Some countries had problems",
                                               "\n\n".join(idx["problems"]))
                    hidden = sum(1 for c in chans if not c["in_output"])
                    if hidden:
                        messagebox.showwarning(
                            "Some current IDs could not be read",
                            f"{hidden} channels are not in the playlist output (for example they are "
                            "hidden by a filter), so their current station ID shows as empty. "
                            "Picking an ID for them still works.")
                    self._restore_session()
                return finish

            self._worker(job, "match")

        def _visible(self, row):
            g = self.group_filter.get() if hasattr(self, "group_filter") else "All groups"
            if g != "All groups" and re.sub(r"\s+\(\d+\)$", "", g) != row.get("group"):
                return False
            q = self.name_filter.get().strip().lower() if hasattr(self, "name_filter") else ""
            if q:
                ch = row["channel"]
                num = ch.get("effective_channel_number") or ch.get("channel_number")
                hay = " ".join(str(x) for x in (ch.get("effective_name") or ch.get("name"), row["current"],
                                                row["new_id"], _fmt_num(num), row.get("group", ""),
                                                (row["best"] or {}).get("name", ""))).lower()
                for w in q.split():
                    rng = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", w)
                    if rng:
                        if num is None or not float(rng.group(1)) <= float(num) <= float(rng.group(2)):
                            return False
                    elif w not in hay:
                        return False
            f = self.filter.get()
            if f == "All":
                return True
            if f == "Needs attention":
                return row["status"] in (REVIEW, UNMATCHED, NOT_IN_LINEUP, ERROR, CHANGE) and not row["write"]
            if f == "Ticked to write":
                return row["write"]
            return row["status"] == f

        def refresh(self):
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            shown = [(i, r) for i, r in enumerate(self.rows) if self._visible(r)]
            shown.sort(key=lambda x: self._sort_key(x[1]), reverse=self.sort_rev)
            for i, r in shown:
                ch, st = r["channel"], r["best"] or {}
                num = _fmt_num(ch.get("effective_channel_number") or ch.get("channel_number"))
                self.tree.insert("", "end", iid=str(i), tags=(r["status"],), values=(
                    "☑" if r["write"] else "☐", num, ch.get("effective_name") or ch.get("name"),
                    r.get("group", ""), r["current"], r["status"],
                    "(remove)" if r.get("remove") else r["new_id"], st.get("name", ""),
                    st.get("callSign", ""), r["why"]))
            if sel and self.tree.exists(sel[0]):
                self.tree.selection_set(sel[0])
            c = {}
            for r in self.rows:
                c[r["status"]] = c.get(r["status"], 0) + 1
            ticked = sum(r["write"] for r in self.rows)
            self.counts.config(text=f"Showing {len(shown)} of {len(self.rows)}   |   "
                               + "   ".join(f"{k}: {n}" for k, n in c.items()) + f"   |   ticked: {ticked}")

        def _sort_key(self, r):
            ch, st = r["channel"], r["best"] or {}
            num = ch.get("effective_channel_number") or ch.get("channel_number")
            col = self.sort_col
            if col == "num":
                return (num is None, num or 0)
            if col in ("current", "new"):
                v = r["current"] if col == "current" else r["new_id"]
                return (not v, int(v) if str(v).isdigit() else 0)
            val = {"write": "0" if r["write"] else "1", "channel": ch.get("effective_name") or ch.get("name"),
                   "group": r.get("group"), "status": r["status"], "gname": st.get("name"),
                   "call": st.get("callSign"), "why": r["why"]}.get(col)
            return (not val, str(val or "").lower(), num or 0)

        def sort_by(self, col):
            self.sort_rev = not self.sort_rev if self.sort_col == col else False
            self.sort_col = col
            self.refresh()

        def clear_filters(self):
            self.filter.set("All")
            self.group_filter.set("All groups")
            self.name_filter.set("")  # triggers refresh

        def _row(self):
            sel = self.tree.selection()
            return (int(sel[0]), self.rows[int(sel[0])]) if sel else (None, None)

        def _selected(self, _e=None):
            _, r = self._row()
            if not r:
                return
            ch = r["channel"]
            self.sel_label.config(text=f"{ch.get('effective_name') or ch.get('name')}   "
                                       f"(current ID: {r['current'] or '-'}, tvg-id: "
                                       f"{ch.get('effective_tvg_id') or '-'})   {r['why']}")
            self._show_cands(r["cands"])
            self.search_v.set(re.sub(r"\s+", " ", " ".join(sorted(tokens(ch.get("effective_name") or "")))))

        def _show_cands(self, cands, outside=False):
            self.cand_list = cands
            self.cand.delete(0, "end")
            for s in cands:
                where = "NOT in your lineups" if outside and s.get("stationId") not in self.matcher.by_id \
                    else f"in {len(s.get('lineups', []))} lineups"
                self.cand.insert("end", f"{s.get('stationId'):>7}   {s.get('name')}   [{s.get('callSign')}]   "
                                        f"{s.get('type') or ''}   ({where})")
            if not cands:
                self.cand.insert("end", "(no candidates - search, or enter an ID manually)")

        def _apply(self, i, st, sid, why):
            r = self.rows[i]
            r["remove"] = False
            r["best"], r["new_id"], r["write"], r["why"] = st, sid, True, why
            if r["current"] and sid != r["current"]:
                r["status"], r["why"] = CHANGE, f"{why}: replaces {r['current']}"
            elif r["current"] and sid == r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"
            log.info("Picked %s for channel %s (%s)", sid, r["channel"]["id"], why)
            self.refresh()
            self.save_session()

        def use_candidate(self):
            i, r = self._row()
            pick = self.cand.curselection()
            if r is None or not pick or not getattr(self, "cand_list", None):
                return
            st = self.cand_list[pick[0]]
            self._apply(i, st, st["stationId"], "picked by you")

        def search(self):
            if not self.matcher or not self.search_v.get().strip():
                return
            text = self.search_v.get()
            try:
                hits = find_candidates(self.cdvr, self.matcher, text)
            except RuntimeError as e:
                messagebox.showerror("Search failed", str(e))
                return
            self._show_cands(hits, outside=True)

        def set_manual(self):
            i, r = self._row()
            sid = self.manual_v.get().strip()
            if r is None or not sid:
                return
            if not sid.isdigit():
                messagebox.showwarning("Invalid ID", "Gracenote station IDs are numbers only.")
                return
            st = self.matcher.by_id.get(sid)
            if not st and not messagebox.askyesno(
                    "Not in your lineups",
                    f"Station {sid} is not carried in any lineup for this country/postal code.\n"
                    "Use it anyway?"):
                return
            self._apply(i, st, sid, "entered by you")

        def clear_choice(self):
            i, r = self._row()
            if r is None:
                return
            was_remove = r.get("remove")
            r["write"], r["new_id"], r["best"], r["remove"] = False, "", None, False
            if r["status"] in (CHANGE, REMOVE) or was_remove:
                r["status"], r["why"] = VERIFIED, "kept the current ID"
            self.refresh()
            self.save_session()

        def toggle(self):
            i, r = self._row()
            if r is None:
                return
            if not r["new_id"] and not r.get("remove"):
                messagebox.showinfo("No ID", "Pick a candidate or enter an ID first.")
                return
            r["write"] = not r["write"]
            self.refresh()
            self.save_session()

        def _set_remove(self, r):
            """Mark a row so Write clears its station ID. Returns False if there is nothing to remove."""
            if not r["current"]:
                return False
            r.update(remove=True, new_id="", best=None, write=True, status=REMOVE,
                     why=f"you chose to remove {r['current']}")
            log.info("Marked channel %s to remove ID %s", r["channel"]["id"], r["current"])
            return True

        def remove_id(self):
            i, r = self._row()
            if r is None:
                return
            if not self._set_remove(r):
                messagebox.showinfo("No ID", "This channel has no station ID to remove.")
                return
            self.refresh()
            self.save_session()

        @staticmethod
        def _mark_change(r):
            if r["current"] and r["new_id"] != r["current"]:
                r["status"], r["why"] = CHANGE, f"{r['why']}: replaces {r['current']}"
            elif r["current"]:
                r["status"], r["write"], r["why"] = VERIFIED, False, "same as the current ID"

        # ------------------------------------------------------------ walkthrough
        def walkthrough(self):
            """Step through every channel without a chosen ID and pick from options."""
            todo = [i for i, r in enumerate(self.rows)
                    if not r["write"] and r["status"] in (REVIEW, UNMATCHED, NOT_IN_LINEUP, ERROR)]
            visible = {int(x) for x in self.tree.get_children()}
            if self.name_filter.get().strip():
                # Find active: step through exactly what the table shows, even channels that have an ID
                todo = [i for i in range(len(self.rows)) if i in visible]
            elif self.group_filter.get() != "All groups":
                # group only: the unmatched channels in that group
                todo = [i for i in todo if i in visible]
            if not self.matcher:
                messagebox.showinfo("Not connected", "Press Connect & Match first.")
                return
            if not todo:
                messagebox.showinfo("All done", "No unmatched channels left.")
                return

            win = tk.Toplevel(self.root)
            win.title("Match unmatched channels")
            win.geometry("900x560")
            win.transient(self.root)
            state = {"pos": 0, "cands": []}

            head = ttk.Label(win, font=("TkDefaultFont", 14, "bold"))
            head.pack(anchor="w", padx=10, pady=(10, 0))
            sub = ttk.Label(win, foreground="gray")
            sub.pack(anchor="w", padx=10)
            ttk.Label(win, text="Pick the right station (double-click, Enter, or press 1-9). "
                                "Esc = skip, / = new search.").pack(anchor="w", padx=10, pady=(8, 2))
            lb = tk.Listbox(win, height=14, exportselection=False, font=("TkFixedFont", 12))
            lb.pack(fill="both", expand=True, padx=10)

            srow = ttk.Frame(win)
            srow.pack(fill="x", padx=10, pady=6)
            ttk.Label(srow, text="Search:").pack(side="left")
            sv = tk.StringVar()
            se = ttk.Entry(srow, textvariable=sv, width=34)
            se.pack(side="left", padx=4)
            outside = tk.BooleanVar(value=False)
            ttk.Checkbutton(srow, text="search all of Gracenote (not just my lineups)",
                            variable=outside).pack(side="left", padx=6)
            ttk.Label(srow, text="ID:").pack(side="left", padx=(12, 0))
            iv = tk.StringVar()
            ttk.Entry(srow, textvariable=iv, width=10).pack(side="left", padx=4)

            brow = ttk.Frame(win)
            brow.pack(fill="x", padx=10, pady=(0, 10))
            prog = ttk.Label(brow)
            prog.pack(side="left")

            def fill(cands, note=""):
                state["cands"] = cands
                lb.delete(0, "end")
                for n, s in enumerate(cands, 1):
                    where = ("NOT in your lineups (Gracenote-wide)" if s.get("stationId") not in self.matcher.by_id
                             else f"{len(self.matcher.by_id[s['stationId']]['lineups'])} lineups")
                    lb.insert("end", f"{n:>2}. {s.get('stationId'):>7}  {(s.get('name') or '?')[:40]:<40} "
                                     f"{s.get('callSign') or '':<9} {where}")
                if not cands:
                    lb.insert("end", note or "No options. Try a search, or type a station ID.")
                else:
                    lb.selection_set(0)
                lb.focus_set()

            def show():
                if state["pos"] >= len(todo):
                    self.refresh()
                    left = sum(1 for i in todo if not self.rows[i]["write"])
                    messagebox.showinfo("Done", f"Finished. {len(todo) - left} picked, {left} skipped.\n"
                                                "Press 'Copy ticked IDs for m3u-editor' to apply them.", parent=win)
                    win.destroy()
                    return
                r = self.rows[todo[state["pos"]]]
                ch = r["channel"]
                name = ch.get("effective_name") or ch.get("name") or ""
                head.config(text=name)
                sub.config(text=f"tvg-id: {ch.get('effective_tvg_id') or '-'}    "
                                f"current ID: {r['current'] or '-'}    {r['why']}")
                prog.config(text=f"Channel {state['pos'] + 1} of {len(todo)}")
                cands = merge_candidates(self.matcher, name, r["cands"], self.matcher.suggest(name))
                sv.set(search_phrase(name))
                iv.set("")
                fill(cands, "Looking wider in Gracenote…")
                best = name_score(name, cands[0]["name"], _bare_call(cands[0])) if cands else 0
                if best < 0.9 and self.cdvr:
                    pos = state["pos"]

                    def bg():
                        try:
                            extra = global_candidates(self.cdvr, name, self.matcher)
                        except Exception as e:  # network trouble only loses the extra suggestions
                            log.warning("Background Gracenote search for %r failed: %s", name, e)
                            win.after(0, lambda: state["pos"] == pos and win.winfo_exists() and prog.config(
                                text=prog.cget("text") + "   (Gracenote search failed - press Search to retry)"))
                            return
                        if extra:
                            win.after(0, lambda: state["pos"] == pos and win.winfo_exists() and fill(
                                merge_candidates(self.matcher, name, cands, extra)))
                    threading.Thread(target=bg, name="suggest", daemon=True).start()

            def advance():
                state["pos"] += 1
                show()

            def pick(_e=None, index=None):
                sel = [index] if index is not None else lb.curselection()
                if not sel or sel[0] >= len(state["cands"]):
                    return
                st = state["cands"][sel[0]]
                i = todo[state["pos"]]
                if st.get("stationId") not in self.matcher.by_id and not messagebox.askyesno(
                        "Not in your lineups",
                        f"{st.get('name')} ({st.get('stationId')}) is not carried in your lineups.\n"
                        "Use it anyway?", parent=win):
                    return
                self.rows[i].update(best=st, new_id=st["stationId"], write=True,
                                    status=MATCHED, why="picked by you", remove=False)
                self._mark_change(self.rows[i])
                log.info("Walkthrough: picked %s for channel %s", st["stationId"], self.rows[i]["channel"]["id"])
                self.save_session()
                advance()

            def use_id():
                sid = iv.get().strip()
                if not sid.isdigit():
                    messagebox.showwarning("Invalid ID", "Station IDs are numbers only.", parent=win)
                    return
                st = self.matcher.by_id.get(sid)
                if not st and not messagebox.askyesno(
                        "Not in your lineups", f"Station {sid} is not in your lineups. Use it anyway?",
                        parent=win):
                    return
                self.rows[todo[state["pos"]]].update(best=st, new_id=sid, write=True,
                                                     status=MATCHED, why="entered by you", remove=False)
                self._mark_change(self.rows[todo[state["pos"]]])
                log.info("Walkthrough: entered %s for channel %s", sid, self.rows[todo[state["pos"]]]["channel"]["id"])
                self.save_session()
                advance()

            def do_search(_e=None):
                text = sv.get().strip()
                if not text:
                    return
                try:
                    fill(find_candidates(self.cdvr, self.matcher, text, wide=outside.get()),
                         "Gracenote found nothing.")
                except RuntimeError as e:
                    messagebox.showerror("Search failed", str(e), parent=win)

            def back():
                if state["pos"] > 0:
                    state["pos"] -= 1
                    show()

            ttk.Button(srow, text="Search", command=do_search).pack(side="left")
            ttk.Button(srow, text="Use ID", command=use_id).pack(side="left", padx=4)
            ttk.Button(brow, text="Stop", command=lambda: (self.refresh(), win.destroy())).pack(side="right")
            ttk.Button(brow, text="Skip", command=advance).pack(side="right", padx=4)

            def remove():
                r = self.rows[todo[state["pos"]]]
                if not self._set_remove(r):
                    messagebox.showinfo("No ID", "This channel has no station ID to remove.", parent=win)
                    return
                self.save_session()
                advance()
            ttk.Button(brow, text="Remove ID", command=remove).pack(side="right", padx=4)
            ttk.Button(brow, text="Back", command=back).pack(side="right", padx=4)
            ttk.Button(brow, text="Use selected", command=pick).pack(side="right", padx=4)
            se.bind("<Return>", do_search)
            se.bind("<FocusIn>", lambda e: se.after_idle(lambda: se.select_range(0, "end")))
            win.bind("/", lambda e: (se.focus_set(), "break")[1])
            lb.bind("<Double-1>", pick)
            lb.bind("<Return>", pick)
            win.bind("<Escape>", lambda e: advance())
            for k in range(1, 10):
                lb.bind(str(k), lambda e, k=k: pick(index=k - 1))
            win.protocol("WM_DELETE_WINDOW", lambda: (self.refresh(), win.destroy()))
            show()

        def export(self):
            rows = [r for r in self.rows if not r["write"] and r["status"] != VERIFIED]
            if not rows:
                messagebox.showinfo("Nothing to export", "Every channel has an ID ticked or verified.")
                return
            path = filedialog.asksaveasfilename(defaultextension=".csv",
                                                initialfile="unmatched_stationids.csv")
            if not path:
                return
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
            self.status.config(text=f"Saved {len(rows)} unmatched channels to {path}")

        def export_plugin(self):
            path = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=PLUGIN_ZIP,
                                                filetypes=[("Zip archive", "*.zip")])
            if not path:
                return
            try:
                export_plugin(path)
            except OSError as e:
                messagebox.showerror("Export failed", str(e))
                return
            self.status.config(text=f"Saved the m3u-editor plugin to {path}")
            messagebox.showinfo("Install the plugin in m3u-editor (once)", PLUGIN_STEPS.format(path=path))

        def write(self):
            todo = [r for r in self.rows if r["write"] and (
                (r.get("remove") and r["current"]) or (r["new_id"] and r["new_id"] != r["current"]))]
            if not todo:
                messagebox.showinfo("Nothing to copy", "No ticked rows with a new station ID.")
                return
            removals = [r for r in todo if r.get("remove")]
            changes = [r for r in todo if r["current"] and not r.get("remove")]
            msg = f"Copy {len(todo)} station ID changes for m3u-editor?"
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
            if not messagebox.askyesno("Copy for m3u-editor", msg):
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
            self.root.update()  # keep the clipboard after the dialog closes on X11
            self.copied = todo
            log.info("Copied %d IDs for m3u-editor, %d replacing existing, %d removing: %s", len(todo),
                     len(changes), len(removals),
                     ", ".join(f"{r['channel']['id']}:{r['current']}->{r['new_id'] or '(none)'}" for r in todo))
            self.save_session(True)
            self.status.config(text=f"Copied {len(todo)} station IDs. Apply them in m3u-editor, then press Verify.")
            messagebox.showinfo("Apply in m3u-editor", APPLY_STEPS.format(
                n=len(todo), playlist=self.m3ue.playlist["name"], saved=saved))

        def verify(self):
            """Re-read the playlist and confirm every copied ID is now in m3u-editor."""
            if not self.m3ue:
                messagebox.showinfo("Not connected", "Press Connect & Match first.")
                return
            todo = self.copied or [r for r in self.rows if r["write"]]
            if not todo:
                messagebox.showinfo("Nothing to verify", "Copy some station IDs for m3u-editor first.")
                return

            def job(progress):
                progress("Reading the playlist from m3u-editor...", 30)
                live = {c["id"]: c for c in self.m3ue.load()}

                def finish():
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
                                r.update(current="", status=UNMATCHED, write=False, remove=False, why="ID removed")
                            ok += 1
                        elif not ch.get("in_output"):
                            r.update(why="not in the playlist output, so it cannot be checked")
                            pending.append(r)
                        else:
                            r.update(why=f"m3u-editor still shows '{ch.get('effective_tvc_guide_stationid') or 'nothing'}'")
                            pending.append(r)
                    self.copied = [r for r in self.copied if r in pending]
                    self.prog["value"] = 100
                    self.status.config(text=f"Verified {ok} of {len(todo)} station IDs."
                                            + (f" {deleted} no longer in m3u-editor." if deleted else "")
                                            + (f" {len(pending)} not applied yet." if pending else ""))
                    self.refresh()
                    self.save_session(True)
                    if pending:
                        names = "\n  ".join(r["channel"].get("effective_name") or "" for r in pending[:8])
                        more = f"\n  ...and {len(pending) - 8} more" if len(pending) > 8 else ""
                        messagebox.showwarning(
                            "Not applied yet",
                            f"{len(pending)} station IDs are not in m3u-editor yet. They are still ticked.\n  "
                            f"{names}{more}\n\nCheck that the Apply Station IDs run finished (Extensions > "
                            "Gracenote Station IDs > Run History), then press Verify again.")
                return finish

            self._worker(job, "verify")

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
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
