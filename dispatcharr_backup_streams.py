#!/usr/bin/env python3
"""Dispatcharr Backup Streams (GUI).

Finds other feeds of the same channel among your Dispatcharr streams and adds
them to each channel as backup streams, so Dispatcharr can switch to another
feed when one goes dark. Talks to Dispatcharr only through its API.

How to use it:
  1. Connect. Channels with sure backups are ticked for you (green).
  2. Work through "Needs attention" (yellow and red rows). Click a channel,
     click the streams you want in the list below it (or double-click a search
     result to add any stream), then press Return / "Done, next" to move on.
  3. Press "Write N channels to Dispatcharr". Every write saves the original
     stream lists first, so any run can be undone from File > Undo a write.

Matching (no channel names are built in; it works with any provider):
  * Provider prefixes such as "US|" and "TV|" and quality words such as HD,
    FHD, 4K and RAW are ignored, so "US| CNN FHD" matches "CNN HD".
  * East and West/Pacific/Mountain/Central feeds are never mixed. A name
    without a region counts as the East feed.
  * A channel with a call sign in its name, such as "(WNBC)", only matches
    streams that contain that same call sign.
  * Pre-ticked ("sure") only on strong evidence: the same name, the same call
    sign, or the same provider guide ID (tvg-id). The guide ID is what catches
    renamed channels, e.g. an old "MSNBC" feed and a new "MS NOW" feed that the
    provider still tags with the same guide ID.
  * Similar names are offered unticked for you to decide. Anything else can
    be found with the live search box.
  * Your main stream always stays first. Backups you tick are added; backups
    you untick (including ones already written) are removed on the next Write.

Runs on Windows, macOS and Linux with Python 3.8+ and Tk (tkinter).

    python3 dispatcharr_backup_streams.py        (macOS / Linux)
    py dispatcharr_backup_streams.py             (Windows, or double-click)
"""
import glob
import json
import queue
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".dispatcharr_backup_streams")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")        # autosaved work in progress
UI_FILE = os.path.join(CONFIG_DIR, "window.json")              # window size and layout
LOG_FILE = os.path.join(CONFIG_DIR, "logs", "backup_streams.log")
DEFAULT_MAX = 3

READY = "Ready to write"          # has changes that the next Write sends
CHECK = "Suggestions to check"    # only uncertain matches; nothing ticked
NONE = "Nothing found"
ERROR = "Error"
HAS_BACKUPS = "Already has backups"
LEFT = "Left as is"               # you looked at it and chose no changes
SKIPPED = "Skipped"               # has changes, but left out of Write
WRITTEN = "Written"
ATTENTION = {CHECK, NONE, ERROR}

log = logging.getLogger("dbs")


# ----------------------------------------------------------------------
# files and logging
# ----------------------------------------------------------------------

def write_private_json(path, data):
    """Write JSON readable only by the current user (it can hold an API key)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def open_folder(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # noqa: S606 - opens a folder in Explorer
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        log.warning("Could not open %s: %s", path, e)


# ----------------------------------------------------------------------
# Dispatcharr API
# ----------------------------------------------------------------------

class NotFound(RuntimeError):
    pass


def normalize_base(text):
    text = text.strip().rstrip("/")
    if not text:
        raise ValueError("Enter the Dispatcharr address.")
    if "://" not in text:
        text = "http://" + text
    p = urllib.parse.urlparse(text)
    if not p.hostname:
        raise ValueError(f"'{text}' is not a valid address.")
    port = p.port or 9191
    return f"{p.scheme}://{p.hostname}:{port}"


class Dispatcharr:
    def __init__(self, base, api_key="", username="", password=""):
        self.base, self.api_key, self.username, self.password = base, api_key, username, password
        self.access = None

    def _request(self, method, path, body=None, auth=True):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        if auth:
            if self.api_key:
                hdrs["X-API-Key"] = self.api_key
            elif self.access:
                hdrs["Authorization"] = f"Bearer {self.access}"
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode(errors="replace")[:300]
            log.warning("%s %s -> HTTP %s: %s", method, path, e.code, body_txt)
            if e.code in (401, 403):
                raise RuntimeError("Dispatcharr rejected the login. Check the API key or username and password.")
            if e.code == 404:
                raise NotFound(f"Dispatcharr has no {path} (it may have been deleted). Press Reload.")
            raise RuntimeError(f"Dispatcharr returned HTTP {e.code} for {path}: {body_txt}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Could not reach {self.base}: {getattr(e, 'reason', e)}")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise RuntimeError(f"{self.base}{path} did not answer with JSON. Is this the Dispatcharr address?")

    def login(self):
        if self.api_key:
            return
        tok = self._request("POST", "/api/accounts/token/",
                            {"username": self.username, "password": self.password}, auth=False)
        if not isinstance(tok, dict) or not tok.get("access"):
            raise RuntimeError("Dispatcharr did not return a login token. Is this the right address?")
        self.access = tok["access"]

    def api(self, method, path, body=None):
        try:
            return self._request(method, path, body)
        except RuntimeError as e:
            if "rejected the login" in str(e) and not self.api_key and self.username:
                self.login()  # access token expired
                return self._request(method, path, body)
            raise

    def _all(self, path):
        sep = "&" if "?" in path else "?"
        page, out = 1, []
        while True:
            data = self.api("GET", f"{path}{sep}page={page}&page_size=5000")
            if isinstance(data, list):
                return data
            out += data.get("results", [])
            if not data.get("next"):
                return out
            page += 1

    def channels(self):
        return self._all("/api/channels/channels/")

    def streams(self):
        return self._all("/api/channels/streams/")

    def groups(self):
        try:
            data = self.api("GET", "/api/channels/groups/")
        except RuntimeError:
            return {}
        data = data.get("results", []) if isinstance(data, dict) else data
        return {g["id"]: g.get("name", "") for g in data or []}

    def accounts(self):
        try:
            data = self.api("GET", "/api/m3u/accounts/")
        except RuntimeError:
            return {}
        data = data.get("results", []) if isinstance(data, dict) else data
        return {a["id"]: a.get("name", "") for a in data or []}

    def set_streams(self, channel_id, stream_ids):
        self.api("PATCH", f"/api/channels/channels/{channel_id}/", {"streams": stream_ids})
        got = self.api("GET", f"/api/channels/channels/{channel_id}/")
        return [s if isinstance(s, int) else s.get("id") for s in (got or {}).get("streams", [])]


# ----------------------------------------------------------------------
# matching
# ----------------------------------------------------------------------
# Nothing here is specific to one provider or one channel. A backup is
# "sure" (pre-ticked) only on strong evidence; anything weaker is offered as
# "check" for the user to decide, and the rest can be found with the search box.

QUALITY = {"HD", "FHD", "UHD", "4K", "8K", "SD", "RAW", "HEVC", "H265", "H264", "HDR", "1080P", "720P",
           "2160P", "1080", "720", "60FPS", "50FPS", "30FPS", "FPS", "BACKUP", "ALT", "VIP", "LIVE",
           "FEED", "MULTI", "AUDIO", "SDR"}
REGIONS = {"EAST": "EAST", "EASTERN": "EAST", "WEST": "WEST", "WESTERN": "WEST", "PACIFIC": "WEST",
           "MOUNTAIN": "MOUNTAIN", "CENTRAL": "CENTRAL"}
# words that don't change which channel it is ("FOX NEWS CHANNEL" = "FOX NEWS")
FILLER = {"CHANNEL", "NETWORK", "TV", "THE"}
PREFIX = re.compile(r"^(?:[^|]{0,20}\||\s*[A-Za-z]{2,4}(?:\.[A-Za-z])?\s*:)\s*")
PAREN_CODE = re.compile(r"\(([A-Z][A-Z0-9]{2,7})(?:-[A-Z0-9]{1,4})?\)")
SIMILAR = 0.82        # name similarity needed to offer a "check" candidate
SIMILAR_SHORT = 0.9   # stricter for short names (CNN vs CNNI)


def clean(name):
    """Upper-case name without a provider prefix like "US|"; folds superscripts (ᴿᴬᵂ -> RAW)."""
    s = unicodedata.normalize("NFKC", name or "")
    return PREFIX.sub("", s).upper()


def describe(name):
    s = clean(name)
    code = None
    for m in PAREN_CODE.finditer(s):
        if m.group(1) not in QUALITY and m.group(1) not in REGIONS:
            code = m.group(1)
            break
    words = re.findall(r"[A-Z0-9]+", s.replace("&", " AND ").replace("+", " PLUS "))
    region, key = "EAST", []
    for w in words:
        if w in REGIONS:
            region = REGIONS[w]
        elif w not in QUALITY:
            key.append(w)
    core = [w for w in key if w not in FILLER] or key
    q = 3
    if "FHD" in words or "1080P" in words or "1080" in words:
        q = 0
    elif "HD" in words:
        q = 1
    elif "UHD" in words or "4K" in words:
        q = 2
    if "RAW" in words:
        q = 4
    if "SD" in words:
        q = 5
    compact = "".join(core)
    return dict(key=tuple(sorted(core)), compact=compact, region=region, code=code, q=q,
                words=set(words), nums=tuple(re.findall(r"\d+", compact)))


def tvg_key(t):
    """Provider guide ID without country tags and punctuation: "CNN.us", "US-CNN" -> "cnn"."""
    t = unicodedata.normalize("NFKC", t or "").strip().lower()
    t = re.sub(r"\.[a-z]{2,3}$", "", t)
    t = re.sub(r"^[a-z]{2,3}\s*[-|:]\s*", "", t)
    t = re.sub(r"[^a-z0-9]", "", t)
    return t if len(t) >= 3 else ""


def similarity(a, b):
    """Best of character similarity and word overlap between two described names."""
    if not a["compact"] or not b["compact"]:
        return 0.0
    import difflib
    sm = difflib.SequenceMatcher(None, a["compact"], b["compact"])
    r = sm.ratio() if sm.real_quick_ratio() >= SIMILAR and sm.quick_ratio() >= SIMILAR else 0.0
    ka, kb = set(a["key"]), set(b["key"])
    j = len(ka & kb) / len(ka | kb) if ka | kb else 0.0
    return max(r, j)


def find_backups(ref_names, ref_tvgs, current_ids, streams):
    """[(stream, reason, sure)] for other feeds of this channel, best first.

    ref_names: the channel's name and the names of its current streams.
    ref_tvgs:  normalized guide IDs of the channel and its current streams.
    """
    refs = [describe(n) for n in ref_names if n]
    if not refs:
        return []
    region = refs[0]["region"]
    codes = {r["code"] for r in refs if r["code"]}
    nums = {r["nums"] for r in refs}
    out = []
    for st in streams:
        if st["id"] in current_ids:
            continue
        d = st["_d"]
        if d["region"] != region:          # never mix East and West feeds
            continue
        same_nums = d["nums"] in nums
        why, sure = None, False
        if codes:
            # channels with a call sign in their name only match that call sign
            hit = next((c for c in codes if c in d["words"]), None)
            if hit:
                why, sure = f"same call sign {hit}", True
        elif d["key"] and any(d["key"] == r["key"] or d["compact"] == r["compact"] for r in refs):
            why, sure = "same name", True
        elif st["_tvg"] and st["_tvg"] in ref_tvgs:
            why, sure = ("same guide ID", True) if same_nums else ("same guide ID, different number", False)
        elif same_nums:
            best = max(similarity(r, d) for r in refs)
            need = SIMILAR_SHORT if min(len(d["compact"]), len(refs[0]["compact"])) < 6 else SIMILAR
            if best >= need:
                why = f"similar name ({int(best * 100)}%)"
        if why:
            out.append((not sure, d["q"], st["name"], st, why, sure))
    out.sort(key=lambda x: x[:3])
    return [(st, why, sure) for _, _, _, st, why, sure in out]



# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

FILTERS = (("all", "All channels"), ("attention", "Needs attention"), (READY, READY), (CHECK, CHECK),
           (NONE, NONE), (ERROR, "Errors"), (HAS_BACKUPS, HAS_BACKUPS), (LEFT, LEFT), (SKIPPED, SKIPPED),
           (WRITTEN, WRITTEN))
ROW_COLORS = {READY: "#cdeccd", CHECK: "#fff0b8", NONE: "#f8d4d4", ERROR: "#f0a8a8", HAS_BACKUPS: "#f4f4f4",
              LEFT: "#ececec", SKIPPED: "#e2e2e2", WRITTEN: "#dbe7f7"}
ON, OFF = "☑", "☐"
COLS = ("write", "num", "channel", "group", "status", "changes", "current")
HEADS = ("Write", "Ch #", "Channel", "Group", "Status", "Changes on Write", "Current streams (main first)")
WIDTHS = (52, 60, 200, 130, 190, 300, 300)
HELP = """\
1. Connect. Channels with sure backups (same name, call sign or guide ID) are \
ticked for you and turn green: "Ready to write".

2. Work through "Needs attention". Click a channel, then click streams in the \
list under it to tick or untick them. Yellow lines are only similar names, so \
check them. To add any other stream, type in the search box and double-click \
a result. Press Return (or "Done, next") to go to the next channel. A channel \
you look at and leave without changes becomes "Left as is".

3. Press "Write N channels to Dispatcharr". The main stream always stays \
first. Ticked backups are added and unticked current backups are removed. \
The original stream lists are saved before anything is written, so File > \
Undo a write can put them back.

Click the Write column (or press Space) to leave a channel out of the next \
Write without losing its picks. To do many at once, Shift-click or \
Cmd/Ctrl-click channels and press Tick selected or Untick selected. Your picks are autosaved, so closing the app \
loses nothing."""


def run_gui():
    import tkinter as tk
    from tkinter import font as tkfont, messagebox, ttk

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
            self.disp = None
            self.rows = []
            self.streams = []
            self.by_id = {}
            self.busy = False
            self.loaded_once = False
            self.q = queue.Queue()   # worker threads never touch Tk; they post here
            self.shown_i = None
            self.cand_order = []
            self.hit_ids = []
            self.sort_col, self.sort_rev = "num", False
            self.filter_idx = 0
            self.maxn = DEFAULT_MAX
            self.load_note = ""      # shown once the next load finishes (e.g. the result of an undo)
            self.save_job = self.find_job = self.search_job = None
            self.quiet_search = False
            root.title("Dispatcharr Backup Streams")
            self.v = {k: tk.StringVar() for k in ("disp", "apikey", "user", "password", "max")}
            self.v["max"].set(str(DEFAULT_MAX))
            s = read_json(SETTINGS_FILE)
            for k in ("disp", "apikey", "user", "max"):
                if s.get(k):
                    self.v[k].set(str(s[k]))
            self.ui = read_json(UI_FILE)
            self.place_window()
            self.build()
            self.pump()
            if self.v["disp"].get().strip() and self.v["apikey"].get().strip():
                root.after(150, lambda: self.connect(startup=True))
            else:
                self.note("Enter your Dispatcharr address and an API key (or username and password), "
                          "then press Connect.")

        # ---------------- window and settings
        def place_window(self):
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            geo = self.ui.get("geometry")
            m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo or "")
            if m and int(m.group(1)) <= sw and int(m.group(2)) <= sh:
                self.root.geometry(geo)
            else:
                self.root.geometry(f"{min(1320, sw - 60)}x{min(880, sh - 90)}")
            self.root.minsize(min(1020, sw - 40), min(620, sh - 80))

        def save_settings(self):
            data = read_json(SETTINGS_FILE)    # keep any keys this version doesn't use
            data.update({k: self.v[k].get() for k in ("disp", "apikey", "user", "max")})
            write_private_json(SETTINGS_FILE, data)

        def save_ui(self):
            try:
                ui = {"geometry": self.root.geometry()}
                ui["vsash"] = self.vpane.sash_coord(0)[1]
                ui["hsash"] = self.hpane.sash_coord(0)[0]
                write_private_json(UI_FILE, ui)
            except (OSError, tk.TclError):
                pass

        # ---------------- layout
        def build(self):
            self.build_menu()
            bold = tkfont.nametofont("TkDefaultFont").copy()
            bold.configure(weight="bold", size=bold.cget("size") + 2)
            self.bold = bold

            # connection: full form until connected, then a one-line summary
            self.top_holder = ttk.Frame(self.root)
            self.top_holder.pack(side="top", fill="x", padx=10, pady=(8, 2))
            self.conn = ttk.LabelFrame(self.top_holder, text="Connect to Dispatcharr", padding=(8, 4))
            pad = dict(padx=4, pady=3)
            ttk.Label(self.conn, text="Address").grid(row=0, column=0, sticky="e", **pad)
            e_addr = ttk.Entry(self.conn, textvariable=self.v["disp"], width=30)
            e_addr.grid(row=0, column=1, sticky="ew", **pad)
            ttk.Label(self.conn, text="API key").grid(row=0, column=2, sticky="e", **pad)
            self.key_entry = ttk.Entry(self.conn, textvariable=self.v["apikey"], show="•", width=30)
            self.key_entry.grid(row=0, column=3, sticky="ew", **pad)
            self.show_key = tk.BooleanVar(value=False)
            ttk.Checkbutton(self.conn, text="Show", variable=self.show_key,
                            command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "•")
                            ).grid(row=0, column=4, sticky="w", **pad)
            ttk.Label(self.conn, text="Username").grid(row=1, column=0, sticky="e", **pad)
            e_user = ttk.Entry(self.conn, textvariable=self.v["user"], width=30)
            e_user.grid(row=1, column=1, sticky="ew", **pad)
            ttk.Label(self.conn, text="Password").grid(row=1, column=2, sticky="e", **pad)
            e_pw = ttk.Entry(self.conn, textvariable=self.v["password"], show="•", width=30)
            e_pw.grid(row=1, column=3, sticky="ew", **pad)
            ttk.Label(self.conn, text="(only without an API key)", foreground="gray").grid(
                row=1, column=4, sticky="w", **pad)
            ttk.Label(self.conn, text="Max backups").grid(row=2, column=0, sticky="e", **pad)
            mrow = ttk.Frame(self.conn)
            mrow.grid(row=2, column=1, columnspan=2, sticky="w")
            ttk.Spinbox(mrow, from_=1, to=10, textvariable=self.v["max"], width=4).pack(side="left", padx=4)
            ttk.Label(mrow, text="per channel, ticked automatically when matching",
                      foreground="gray").pack(side="left")
            self.go = ttk.Button(self.conn, text="Connect", default="active", command=self.connect)
            self.go.grid(row=2, column=3, columnspan=2, sticky="e", **pad)
            self.conn.columnconfigure(1, weight=1)
            self.conn.columnconfigure(3, weight=1)
            for e in (e_addr, self.key_entry, e_user, e_pw):
                e.bind("<Return>", lambda ev: self.connect())

            self.conn_bar = ttk.Frame(self.top_holder)
            self.conn_lbl = ttk.Label(self.conn_bar, text="")
            self.reload_btn = ttk.Button(self.conn_bar, text="Reload", command=lambda: self.connect(reload=True))
            self.reload_btn.pack(side="right")
            ttk.Button(self.conn_bar, text="Connection…", command=self.show_conn).pack(side="right", padx=4)
            self.conn_lbl.pack(side="left", fill="x", expand=True)
            Tip(self.reload_btn, "Read channels and streams again and re-match. Your unwritten picks are kept.")
            self.conn.pack(fill="x")

            # filters
            bar = ttk.Frame(self.root)
            bar.pack(side="top", fill="x", padx=10, pady=(4, 2))
            ttk.Label(bar, text="Show").pack(side="left")
            self.show_v = tk.StringVar()
            self.show_box = ttk.Combobox(bar, textvariable=self.show_v, state="readonly", width=27,
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
            Tip(self.find_entry, "Channel name, group or stream name. A number finds that channel "
                                 "number; 500-600 finds a range.")
            self.find_v.trace_add("write", lambda *a: self.schedule_find())
            self.find_entry.bind("<Escape>", lambda e: self.find_v.set(""))
            self.find_entry.bind("<Down>", lambda e: (self.focus_table(), "break")[1])
            ttk.Button(bar, text="✕", width=2, command=self.clear_filters).pack(side="left", padx=(2, 0))
            self.shown_lbl = ttk.Label(bar, text="", foreground="gray")
            self.shown_lbl.pack(side="left", padx=12)

            # bottom: status line, then the action buttons above it (packed before the panes so a
            # small window shrinks the table, never the buttons)
            sbar = ttk.Frame(self.root)
            sbar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
            self.progress = ttk.Progressbar(sbar, length=160, mode="determinate", maximum=1.0)
            self.status = ttk.Label(sbar, text="", anchor="w")
            self.status.pack(side="left", fill="x", expand=True)
            actions = ttk.Frame(self.root)
            actions.pack(side="bottom", fill="x", padx=10, pady=(2, 4))
            self.write_btn = ttk.Button(actions, text="Write 0 channels to Dispatcharr…", command=self.write)
            self.write_btn.pack(side="right")
            Tip(self.write_btn, f"Send every green ({READY}) channel to Dispatcharr. The original stream "
                                f"lists are saved first, so this can be undone. Shortcut: {KEY}S")
            self.undo_btn = ttk.Button(actions, text="Undo a write…", command=self.undo)
            self.undo_btn.pack(side="right", padx=8)
            b = ttk.Button(actions, text="Tick all suggestions", command=lambda: self.tick_suggestions())
            b.pack(side="left")
            Tip(b, "For the selected channels: tick every suggested backup, including the uncertain "
                   "(yellow) ones. Select several with Shift-click or " + KEY + "A.")
            b = ttk.Button(actions, text="Mark reviewed", command=lambda: self.mark_reviewed())
            b.pack(side="left", padx=6)
            Tip(b, "For the selected channels: keep them exactly as they are now and take them out of "
                   "Needs attention.")
            b = ttk.Button(actions, text="Tick selected", command=lambda: self.set_ticked(True))
            b.pack(side="left")
            Tip(b, "Tick every selected channel in the Write column, so the next Write sends it. "
                   "Select several with Shift-click or " + ("⌘" if aqua else "Ctrl-") + "click.")
            b = ttk.Button(actions, text="Untick selected", command=lambda: self.set_ticked(False))
            b.pack(side="left", padx=6)
            Tip(b, "Leave the selected channels out of the next Write without losing their picks. "
                   "Clicking the Write column or pressing Space toggles one.")

            # table above, channel detail below; drag the divider to resize
            self.vpane = tk.PanedWindow(self.root, orient="vertical", sashwidth=8, sashrelief="raised",
                                        bd=0, opaqueresize=True)
            self.vpane.pack(side="top", fill="both", expand=True, padx=10, pady=4)
            tbox, self.tree = scrolled_tree(self.vpane, COLS, HEADS, WIDTHS, "current", selectmode="extended")
            for c in COLS:
                self.tree.heading(c, command=lambda c=c: self.sort_by(c))
            self.tree.column("write", anchor="center")
            self.tree.heading("write", anchor="center")
            for tag, color in ROW_COLORS.items():
                self.tree.tag_configure(tag, background=color, foreground="black")
            self.tree.tag_configure(SKIPPED, foreground="#666666")
            self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
            self.tree.bind("<Button-1>", self.table_click)
            self.tree.bind("<space>", lambda e: (self.toggle_skip(), "break")[1])
            self.tree.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            self.tree.bind(f"<{MOD}-a>", lambda e: (self.select_all(), "break")[1])
            allow_toggle_click(self.tree)
            bind_right_click(self.tree, self.table_menu)
            self.vpane.add(tbox, minsize=140, stretch="always")
            self.update_headings()

            detail = ttk.Frame(self.vpane, padding=(0, 6, 0, 0))
            self.vpane.add(detail, minsize=230, stretch="never", height=330)
            head = ttk.Frame(detail)
            head.pack(side="top", fill="x")
            self.d_title = ttk.Label(head, text="Select a channel above.", font=bold)
            self.d_title.pack(side="top", anchor="w")
            self.d_sub = ttk.Label(head, text="", foreground="gray", justify="left")
            self.d_sub.pack(side="top", anchor="w", fill="x")
            head.bind("<Configure>", lambda e: self.d_sub.configure(wraplength=max(200, e.width - 10)))

            self.hpane = tk.PanedWindow(detail, orient="horizontal", sashwidth=8, sashrelief="raised", bd=0,
                                        opaqueresize=True)
            self.hpane.pack(side="top", fill="both", expand=True, pady=(4, 0))
            left = ttk.LabelFrame(self.hpane, text="Streams for this channel  —  click a line to tick or "
                                                   "untick it", padding=4)
            lb = ttk.Frame(left)
            lb.pack(side="bottom", fill="x", pady=(4, 0))
            self.done_btn = ttk.Button(lb, text="Done, next  ⏎", command=self.done_next)
            self.done_btn.pack(side="right")
            Tip(self.done_btn, "Keep this channel as shown and go to the next one (Return). "
                               "With nothing ticked it becomes Left as is.")
            self.d_buttons = [self.done_btn]
            for text, fn, tip in (
                    ("Tick all", lambda: self.tick_suggestions(single=True),
                     "Tick every suggested backup for this channel."),
                    ("Clear changes", lambda: self.clear_changes(single=True),
                     "Untick everything new and keep all current backups: nothing changes on Write."),
                    ("Reset", lambda: self.reset_rows(single=True),
                     "Go back to what the matcher ticked for this channel.")):
                b = ttk.Button(lb, text=text, command=fn)
                b.pack(side="left", padx=(0, 6))
                Tip(b, tip)
                self.d_buttons.append(b)
            cbox, self.cand = scrolled_tree(left, ("tick", "stream", "group", "provider", "why"),
                                            ("", "Stream", "Group", "Provider", "Why"),
                                            (34, 260, 150, 120, 210), "why", selectmode="extended", height=6)
            cbox.pack(side="top", fill="both", expand=True)
            self.cand.column("tick", anchor="center", minwidth=30)
            for tag, bg, fg in (("main", "#e3eaf6", "black"), ("on", "#d6f0d6", "black"),
                                ("check", "#fff6d5", "black"), ("remove", "#fbe0e0", "#a00000")):
                self.cand.tag_configure(tag, background=bg, foreground=fg)
            self.cand.bind("<Button-1>", self.cand_click)
            self.cand.bind("<space>", lambda e: (self.toggle_cands(self.cand.selection()), "break")[1])
            self.cand.bind("<Return>", lambda e: (self.done_next(), "break")[1])
            self.cand.bind("<KP_Enter>", lambda e: (self.done_next(), "break")[1])
            self.hpane.add(left, minsize=320, stretch="always", width=self.ui.get("hsash", 640))

            right = ttk.LabelFrame(self.hpane, text="Search all streams  —  double-click to add", padding=4)
            srow = ttk.Frame(right)
            srow.pack(side="top", fill="x", pady=(0, 4))
            self.search_v = tk.StringVar()
            self.search_entry = ttk.Entry(srow, textvariable=self.search_v)
            self.search_entry.pack(side="left", fill="x", expand=True)
            ttk.Button(srow, text="✕", width=2, command=lambda: self.search_v.set("")).pack(side="left",
                                                                                          padx=(2, 0))
            self.search_v.trace_add("write", lambda *a: self.schedule_search())
            self.search_entry.bind("<Down>", lambda e: (self.focus_hits(), "break")[1])
            self.search_entry.bind("<Return>", lambda e: (self.focus_hits(), "break")[1])
            self.search_entry.bind("<Escape>", lambda e: self.search_v.set(""))
            rb = ttk.Frame(right)
            rb.pack(side="bottom", fill="x", pady=(4, 0))
            self.add_btn = ttk.Button(rb, text="◀ Add as backup", command=self.add_hits)
            self.add_btn.pack(side="right")
            self.hits_info = ttk.Label(rb, text="", foreground="gray")
            self.hits_info.pack(side="left", fill="x", expand=True)
            self.d_buttons.append(self.add_btn)
            hbox, self.hits = scrolled_tree(right, ("stream", "group", "provider"), ("Stream", "Group", "Provider"),
                                            (260, 150, 120), "provider", selectmode="extended", height=6)
            hbox.pack(side="top", fill="both", expand=True)
            self.hits.bind("<Double-1>", self.hit_double)
            self.hits.bind("<Return>", lambda e: (self.add_hits(), "break")[1])
            self.hits.bind("<KP_Enter>", lambda e: (self.add_hits(), "break")[1])
            self.hits.bind(f"<{MOD}-a>", lambda e: (self.hits.selection_set(self.hits.get_children()),
                                                    "break")[1])
            allow_toggle_click(self.hits)
            self.hpane.add(right, minsize=300, stretch="always")
            self.set_detail_enabled(False)

            # keyboard shortcuts that work anywhere in the window
            self.root.bind(f"<{MOD}-s>", lambda e: (self.write(), "break")[1])
            self.root.bind(f"<{MOD}-r>", lambda e: (self.connect(reload=True), "break")[1])
            self.root.bind(f"<{MOD}-f>", lambda e: (self.find_entry.focus_set(),
                                                    self.find_entry.select_range(0, "end"), "break")[2])
            self.root.bind(f"<{MOD}-l>", lambda e: (self.search_entry.focus_set(),
                                                    self.search_entry.select_range(0, "end"), "break")[2])
            self.root.bind(f"<{MOD}-Return>", lambda e: (self.done_next(), "break")[1])
            self.root.after(80, self.place_sashes)

        def place_sashes(self):
            self.root.update_idletasks()
            h = self.vpane.winfo_height()
            if h > 400:
                y = self.ui.get("vsash")
                self.vpane.sash_place(0, 0, y if isinstance(y, int) and 140 < y < h - 230 else int(h * 0.55))
            x = self.ui.get("hsash")
            if isinstance(x, int) and 320 < x < self.hpane.winfo_width() - 300:
                self.hpane.sash_place(0, x, 0)

        def build_menu(self):
            mb = tk.Menu(self.root)
            fm = tk.Menu(mb, tearoff=0)
            fm.add_command(label="Reload from Dispatcharr", accelerator=f"{KEY}R",
                           command=lambda: self.connect(reload=True))
            fm.add_command(label="Connection settings…", command=self.show_conn)
            fm.add_separator()
            fm.add_command(label="Write ticked channels…", accelerator=f"{KEY}S", command=self.write)
            fm.add_command(label="Undo a write…", command=self.undo)
            fm.add_separator()
            fm.add_command(label="Open data and log folder", command=lambda: open_folder(CONFIG_DIR))
            if not aqua:
                fm.add_separator()
                fm.add_command(label="Quit", command=self.close)
            mb.add_cascade(label="File", menu=fm)
            cm = tk.Menu(mb, tearoff=0)
            self.fill_channel_menu(cm)
            cm.add_separator()
            cm.add_command(label="Tick all suggestions on every channel shown…",
                           command=lambda: self.tick_suggestions(all_shown=True))
            cm.add_command(label="Tick sure matches on every channel shown…",
                           command=lambda: self.tick_suggestions(sure_only=True, all_shown=True))
            cm.add_command(label="Mark every channel shown as reviewed",
                           command=lambda: self.mark_reviewed(all_shown=True))
            cm.add_separator()
            cm.add_command(label="Select all shown", accelerator=f"{KEY}A", command=self.select_all)
            cm.add_command(label="Find a channel", accelerator=f"{KEY}F", command=self.find_entry_focus)
            cm.add_command(label="Search streams", accelerator=f"{KEY}L",
                           command=lambda: self.search_entry.focus_set())
            mb.add_cascade(label="Channels", menu=cm)
            hm = tk.Menu(mb, tearoff=0)
            hm.add_command(label="How it works", command=lambda: messagebox.showinfo(
                "How it works", HELP, parent=self.root))
            hm.add_command(label="Keyboard shortcuts", command=self.show_keys)
            mb.add_cascade(label="Help", menu=hm)
            self.root.configure(menu=mb)

        def fill_channel_menu(self, m):
            m.add_command(label="Done, next", accelerator="Return", command=self.done_next)
            m.add_command(label="Tick all suggestions", command=lambda: self.tick_suggestions())
            m.add_command(label="Tick sure matches only", command=lambda: self.tick_suggestions(sure_only=True))
            m.add_command(label="Clear changes", command=lambda: self.clear_changes())
            m.add_command(label="Reset to the matcher's picks", command=lambda: self.reset_rows())
            m.add_command(label="Tick selected", command=lambda: self.set_ticked(True))
            m.add_command(label="Untick selected", command=lambda: self.set_ticked(False))
            m.add_command(label="Toggle tick", accelerator="Space", command=lambda: self.toggle_skip())
            m.add_command(label="Mark reviewed", command=lambda: self.mark_reviewed())

        def find_entry_focus(self):
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")

        def show_keys(self):
            messagebox.showinfo("Keyboard shortcuts", "\n".join((
                "Channel table",
                "   Up / Down: next or previous channel",
                "   Return: done with this channel, go to the next",
                "   Space: tick or untick in the Write column",
                f"   {KEY}A: select every channel shown",
                "   Shift-click / " + ("⌘" if aqua else "Ctrl-") + "click: select several",
                "",
                "Streams for this channel",
                "   Click: tick or untick a line",
                "   Space: tick or untick the selected lines",
                "",
                "Search",
                f"   {KEY}L: jump to the search box",
                "   Down or Return: move into the results",
                "   Double-click or Return: add as a backup",
                "",
                "Anywhere",
                f"   {KEY}F: find a channel      {KEY}R: reload",
                f"   {KEY}S: write               {KEY}Return: done, next")), parent=self.root)

        def show_conn(self):
            self.conn_bar.pack_forget()
            self.conn.pack(fill="x")

        def hide_conn(self):
            self.conn.pack_forget()
            self.conn_bar.pack(fill="x")

        def focus_table(self):
            kids = self.tree.get_children()
            if kids and not self.tree.selection():
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])
            self.tree.focus_set()

        def focus_hits(self):
            kids = self.hits.get_children()
            if kids:
                self.hits.focus_set()
                self.hits.selection_set(kids[0])
                self.hits.focus(kids[0])
                self.hits.see(kids[0])

        def select_all(self):
            kids = self.tree.get_children()
            self.tree.selection_set(kids)
            self.tree.focus_set()

        # ---------------- background work
        def bg(self, fn, done, quiet=False):
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            self.set_busy(True)

            def run():
                try:
                    res, err = fn(), None
                except Exception as e:  # shown to the user
                    log.exception("Background task failed")
                    res, err = None, e
                self.q.put(lambda: self._bg_done(done, res, err, quiet))
            threading.Thread(target=run, daemon=True).start()

        def _bg_done(self, done, res, err, quiet):
            self.set_busy(False)
            if err:
                if quiet:
                    self.show_conn()
                    self.note(f"Could not connect: {err}", warn=True)
                else:
                    self.note(f"Error: {err}", warn=True)
                    messagebox.showerror("Error", str(err), parent=self.root)
            else:
                done(res)

        def set_busy(self, on):
            self.busy = on
            for b in (self.go, self.reload_btn, self.undo_btn):
                b.state(["disabled"] if on else ["!disabled"])
            if on:
                self.progress.configure(value=0, mode="indeterminate")
                self.progress.pack(side="right", padx=(8, 0))
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()
            self.update_counts()

        def say(self, text, frac=None):
            self.q.put(lambda: self._progress(text, frac))

        def _progress(self, text, frac):
            self.note(text)
            if frac is not None:
                if str(self.progress.cget("mode")) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress.configure(value=frac)

        def note(self, text, warn=False):
            """Status line; warnings are red and ring the bell so a no-op is never silent."""
            self.status.config(text=text, foreground="#c00000" if warn else "")
            if warn:
                self.root.bell()

        def pump(self):
            try:
                while True:
                    self.q.get_nowait()()
            except queue.Empty:
                pass
            self.root.after(60, self.pump)

        # ---------------- connect and match
        def connect(self, reload=False, startup=False):
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            try:
                base = normalize_base(self.v["disp"].get())
            except ValueError as e:
                self.show_conn()
                messagebox.showerror("Check the settings", str(e), parent=self.root)
                return
            try:
                maxn = max(1, int(self.v["max"].get() or DEFAULT_MAX))
            except ValueError:
                self.show_conn()
                messagebox.showerror("Check the settings", "Max backups must be a number.", parent=self.root)
                return
            key, user, pw = self.v["apikey"].get().strip(), self.v["user"].get().strip(), self.v["password"].get()
            if not key and not (user and pw):
                self.show_conn()
                messagebox.showerror("Login needed", "Enter an API key, or a username and password.",
                                     parent=self.root)
                return
            try:
                self.save_settings()
            except OSError as e:
                log.warning("Could not save settings: %s", e)
            self.flush_save()   # unwritten picks survive a reload

            def work():
                d = Dispatcharr(base, key, user, pw)
                d.login()
                self.say("Reading channels…")
                chans = d.channels()
                self.say(f"Reading streams… ({len(chans)} channels)")
                streams = d.streams()
                self.say(f"Matching {len(chans)} channels against {len(streams)} streams…")
                groups, accts = d.groups(), d.accounts()
                for st in streams:
                    st["name"] = st.get("name") or ""
                    st["_d"] = describe(st["name"])
                    st["_tvg"] = tvg_key(st.get("tvg_id"))
                    st["_group"] = groups.get(st.get("channel_group"), "") or ""
                    st["_acct"] = accts.get(st.get("m3u_account"), "") or ""
                    name = unicodedata.normalize("NFKC", st["name"]).lower()
                    st["_name_hay"] = name
                    st["_hay"] = " ".join((name, st["_group"].lower(), st["_acct"].lower()))
                by_id = {st["id"]: st for st in streams}
                rows = []
                for ch in sorted(chans, key=lambda c: (c.get("channel_number") or 0, c.get("name") or "")):
                    cur = [s if isinstance(s, int) else s.get("id") for s in ch.get("streams") or []]
                    name = ch.get("effective_name") or ch.get("name") or ""
                    refs = [name] + [by_id[s]["name"] for s in cur if s in by_id]
                    tvgs = {t for t in [tvg_key(ch.get("effective_tvg_id") or ch.get("tvg_id"))] +
                            [by_id[s]["_tvg"] for s in cur if s in by_id] if t}
                    cands = find_backups(refs, tvgs, set(cur), streams)
                    room = max(0, maxn - (len(cur) - 1))
                    # channels you already gave backups are left as they are; suggestions stay unticked
                    auto = [] if len(cur) > 1 else [st["id"] for st, _, sure in cands if sure][:room]
                    rows.append(dict(ch=ch, name=name, cur=cur, cands=cands, auto=auto, picked=list(auto),
                                     drop=[], added=[], skip=False, reviewed=False, state="", msg="",
                                     region=describe(name)["region"],
                                     group=groups.get(ch.get("effective_channel_group_id")
                                                      or ch.get("channel_group_id"), "") or ""))
                return d, streams, by_id, rows, maxn

            self.note("Reloading…" if reload else "Connecting…")
            self.bg(work, self.matched, quiet=startup)

        def matched(self, res):
            first = not self.loaded_once
            self.disp, self.streams, self.by_id, self.rows, self.maxn = res
            self.loaded_once = True
            self.shown_i = None
            restored = self.restore_session(ask=first)
            self.hide_conn()
            self.conn_lbl.configure(text=f"Connected to {self.disp.base}   ·   {len(self.rows)} channels, "
                                         f"{len(self.streams)} streams   ·   loaded {time.strftime('%H:%M')}")
            self.group_box.configure(values=["All groups"] + sorted({r["group"] for r in self.rows if r["group"]}))
            if self.group_v.get() not in self.group_box.cget("values"):
                self.group_v.set("All groups")
            log.info("Loaded %d channels, %d streams", len(self.rows), len(self.streams))
            if first:
                att = sum(1 for r in self.rows if self.status_of(r)[0] in ATTENTION)
                self.filter_idx = 1 if att else 0
            self.rebuild(keep_view=not first)
            if first or not self.tree.selection():
                self.focus_table()
            ready = sum(1 for r in self.rows if self.will_write(r))
            att = sum(1 for r in self.rows if self.status_of(r)[0] in ATTENTION)
            msg = (f"{ready} channel{' is' if ready == 1 else 's are'} ready with backups. "
                   + (f"{att} need{'s' if att == 1 else ''} attention: pick backups below, then press Return "
                      "for the next one. " if att else "Nothing needs attention. ")
                   + "Press Write when you're done.")
            if restored:
                msg = f"Kept your unwritten changes on {restored} channel{'' if restored == 1 else 's'}. " + msg
            warn = self.load_note.startswith("!")
            self.note(self.load_note.lstrip("!") + msg, warn=warn)
            self.load_note = ""

        # ---------------- row state
        @staticmethod
        def pending(r):
            return bool(r["picked"] or r["drop"])

        def will_write(self, r):
            return self.pending(r) and not r["skip"]

        def unpicked(self, r):
            return [st for st, _, _ in r["cands"] if st["id"] not in r["cur"] and st["id"] not in r["picked"]]

        def status_of(self, r):
            pend = self.pending(r)
            if pend:
                parts = []
                if r["picked"]:
                    parts.append(f"add {len(r['picked'])}")
                if r["drop"]:
                    parts.append(f"remove {len(r['drop'])}")
                what = ", ".join(parts)
                if r["skip"]:
                    return SKIPPED, f"skipped ({what})"
                if r["state"] == "error":
                    return ERROR, f"{what}; last try failed: {r['msg']}"
                return READY, what
            if r["state"] == "error":
                return ERROR, r["msg"]
            if r["state"] == "done":
                return WRITTEN, r["msg"]
            if r["reviewed"]:
                return LEFT, "left as is"
            nb, more = len(r["cur"]) - 1, len(self.unpicked(r))
            if nb > 0:
                return HAS_BACKUPS, f"{nb} backup{'s' if nb > 1 else ''}" + (f", {more} more found" if more else "")
            if more:
                return CHECK, f"{more} to check below"
            return NONE, "nothing found: search below"

        def label(self, sid):
            st = self.by_id.get(sid)
            return st["name"] if st else f"#{sid}"

        @staticmethod
        def _num(r):
            n = r["ch"].get("channel_number")
            return int(n) if isinstance(n, float) and n.is_integer() else n

        def row_values(self, r):
            tag, status = self.status_of(r)
            mark = ON if tag in (READY, ERROR) and self.pending(r) else OFF if tag == SKIPPED else ""
            changes = ", ".join(["+ " + self.label(s) for s in r["picked"]] + ["− " + self.label(s) for s in r["drop"]])
            return tag, (mark, self._num(r) or "", r["name"], r["group"], status, changes,
                         ", ".join(self.label(s) for s in r["cur"]))

        # ---------------- table
        def matches(self, r, tag, find):
            key = FILTERS[self.filter_idx][0]
            if key == "attention" and tag not in ATTENTION:
                return False
            if key not in ("all", "attention") and tag != key:
                return False
            grp = self.group_v.get()
            if grp != "All groups" and r["group"] != grp:
                return False
            if not find:
                return True
            m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", find)
            num = r["ch"].get("channel_number") or 0
            if m:
                return float(m.group(1)) <= float(num) <= float(m.group(2))
            if find == str(self._num(r)):
                return True
            hay = " ".join([r["name"], r["group"]] + [self.label(s) for s in r["cur"] + r["picked"]]).lower()
            return all(w in hay for w in find.split())

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
            self.find_job = None
            sel = set(self.tree.selection())
            top = self.tree.yview()[0]
            self.tree.delete(*self.tree.get_children())
            find = self.find_v.get().strip().lower()
            items = []
            for i, r in enumerate(self.rows):
                tag, vals = self.row_values(r)
                if self.matches(r, tag, find):
                    items.append((i, tag, vals))
            col = COLS.index(self.sort_col)

            def key(it):
                v = it[2][col]
                if col == 1:
                    try:
                        return (0, float(v), "")
                    except (TypeError, ValueError):
                        return (1, 0.0, str(v))
                return (0 if v != "" else 1, 0.0, str(v).lower())
            items.sort(key=key, reverse=self.sort_rev)
            for i, tag, vals in items:
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
            self.schedule_save()

        def update_counts(self):
            counts = {}
            for r in self.rows:
                t = self.status_of(r)[0]
                counts[t] = counts.get(t, 0) + 1
            att = sum(counts.get(t, 0) for t in ATTENTION)
            labels = []
            for key, label in FILTERS:
                n = len(self.rows) if key == "all" else att if key == "attention" else counts.get(key, 0)
                labels.append(f"{label} ({n})")
            self.show_box.configure(values=labels)
            self.show_v.set(labels[self.filter_idx])
            nw = sum(1 for r in self.rows if self.will_write(r))
            self.write_btn.configure(text=f"Write {nw} channel{'' if nw == 1 else 's'} to Dispatcharr…")
            self.write_btn.state(["disabled"] if self.busy or not nw else ["!disabled"])
            shown = len(self.tree.get_children())
            self.shown_lbl.configure(text=f"{shown} of {len(self.rows)} channels shown" if self.rows else "")

        def selected(self):
            """[(index, row)] for the selected channels, in table order."""
            sel = set(self.tree.selection())
            return [(int(k), self.rows[int(k)]) for k in self.tree.get_children() if k in sel]

        def targets(self, all_shown=False, single=False):
            if all_shown:
                rows = [(int(k), self.rows[int(k)]) for k in self.tree.get_children()]
            else:
                rows = self.selected()
            if single and len(rows) != 1:
                rows = []
            if not rows:
                self.note("Select a channel in the table first." if single or not all_shown
                          else "No channels are shown.", warn=True)
            return rows

        def table_click(self, e):
            """A click in the Write column skips or includes that channel."""
            if self.tree.identify_region(e.x, e.y) != "cell" or self.tree.identify_column(e.x) != "#1":
                return None
            iid = self.tree.identify_row(e.y)
            if not iid:
                return None
            if iid not in self.tree.selection() or len(self.tree.selection()) == 1:
                self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.focus_set()
            self.toggle_skip()
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

        # ---------------- edits (all update rows in place)
        def after_edit(self, idxs):
            for i in idxs:
                r = self.rows[i]
                r["reviewed"] = True
                if self.pending(r):
                    r["skip"] = False
            self.update_rows(idxs)
            self.show_detail()

        def toggle_skip(self):
            rows = self.targets()
            if not rows:
                return
            pend = [(i, r) for i, r in rows if self.pending(r)]
            if not pend:
                self.note("Nothing to write on " + (rows[0][1]["name"] if len(rows) == 1 else "those channels")
                          + " yet: tick a backup in the list below first.", warn=True)
                return
            skip = any(not r["skip"] for _, r in pend)
            for _, r in pend:
                r["skip"] = skip
            self.update_rows([i for i, _ in pend])
            self.show_detail()
            what = f"{len(pend)} channel{'s' if len(pend) > 1 else ''}"
            self.note(f"Leaving {what} out of the next Write." if skip else f"Including {what} in the next Write.")

        def set_ticked(self, on):
            """Tick (or untick) the selected channels themselves in the Write column."""
            rows = self.targets()
            if not rows:
                return
            pend = [(i, r) for i, r in rows if self.pending(r)]
            for _, r in pend:
                r["skip"] = not on
            if pend:
                self.update_rows([i for i, _ in pend])
                self.show_detail()
            rest = len(rows) - len(pend)
            what = f"{'Ticked' if on else 'Unticked'} {len(pend)} channel{'' if len(pend) == 1 else 's'}"
            if rest:
                what += (f"; {rest} {'has' if rest == 1 else 'have'} no backups picked yet, so there is "
                         "nothing to write for " + ("it" if rest == 1 else "them"))
            self.note(what + ".", warn=not pend)

        def tick_suggestions(self, sure_only=False, all_shown=False, single=False):
            rows = self.targets(all_shown, single)
            if not rows:
                return
            plan = []
            for i, r in rows:
                if sure_only:
                    have = len(r["cur"]) - 1 - len(r["drop"]) + len(r["picked"])
                    new = [st["id"] for st, _, sure in r["cands"] if sure and st["id"] not in r["cur"]
                           and st["id"] not in r["picked"]][:max(0, self.maxn - have)]
                else:
                    new = [st["id"] for st in self.unpicked(r)]
                if new or (not sure_only and r["drop"]):
                    plan.append((i, r, new))
            if not plan:
                self.note("Nothing to tick: " + ("that channel has" if len(rows) == 1 else "those channels have")
                          + " no more suggestions. Use the search box to add a stream.", warn=True)
                return
            n = sum(len(new) for _, _, new in plan)
            for _, r, new in plan:
                r["picked"] += new
                if not sure_only:
                    r["drop"] = []
                self.sort_picked(r)
            self.after_edit([i for i, _, _ in plan])
            self.note(f"Ticked {n} backup{'s' if n != 1 else ''} on {len(plan)} channel{'s' if len(plan) > 1 else ''}."
                      " Press Write when you're done.")

        def clear_changes(self, single=False):
            rows = self.targets(single=single)
            for _, r in rows:
                r["picked"], r["drop"] = [], []
            if rows:
                self.after_edit([i for i, _ in rows])
                self.note("Cleared. " + ("That channel" if len(rows) == 1 else f"Those {len(rows)} channels")
                          + " won't change on Write.")

        def reset_rows(self, single=False):
            rows = self.targets(single=single)
            for i, r in rows:
                r.update(picked=list(r["auto"]), drop=[], skip=False, reviewed=False)
            if rows:
                self.update_rows([i for i, _ in rows])
                self.show_detail()
                self.note("Back to the matcher's picks.")

        def mark_reviewed(self, all_shown=False):
            rows = self.targets(all_shown)
            for _, r in rows:
                r["reviewed"] = True
            if rows:
                self.update_rows([i for i, _ in rows])
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
            find = self.find_v.get().strip().lower()
            for i, r in rows:   # handled rows leave a filtered view such as Needs attention
                if not self.matches(r, self.status_of(r)[0], find):
                    self.tree.delete(str(i))
            self.update_counts()
            if len(rows) == 1:
                r = rows[0][1]
                tag = self.status_of(r)[0]
                self.note(f"{r['name']}: {'will be written' if tag == READY else tag.lower()}. "
                          + ("" if nxt else "That was the last channel shown. Press Write when you're ready."))
            if nxt and self.tree.exists(nxt):
                self.tree.selection_set(nxt)
                self.tree.focus(nxt)
                self.tree.see(nxt)
            else:
                self.tree.selection_set(())
            self.tree.focus_set()

        # ---------------- channel detail
        def set_detail_enabled(self, on):
            for b in self.d_buttons:
                b.state(["!disabled"] if on else ["disabled"])

        def current(self):
            sel = self.tree.selection()
            if len(sel) != 1:
                return None, None
            return int(sel[0]), self.rows[int(sel[0])]

        def show_detail(self):
            i, r = self.current()
            same = i is not None and i == self.shown_i
            self.shown_i = i
            if r is None:
                n = len(self.tree.selection())
                self.cand.delete(*self.cand.get_children())
                self.cand_order = []
                self.set_detail_enabled(False)
                if n:
                    self.d_title.configure(text=f"{n} channels selected")
                    self.d_sub.configure(text="Use Tick selected, Untick selected, Tick all suggestions or Mark reviewed below "
                                              "(or right-click) to change them all at once.")
                else:
                    self.d_title.configure(text="Select a channel above." if self.rows else "")
                    self.d_sub.configure(text="")
                self.run_search()
                return
            self.set_detail_enabled(True)
            num = self._num(r)
            self.d_title.configure(text=f"{str(num) + '   ' if num not in (None, '') else ''}{r['name']}"
                                        + (f"   ·   {r['group']}" if r["group"] else ""))
            final = [s for s in r["cur"] if s not in r["drop"]] + [s for s in r["picked"] if s not in r["cur"]]
            tag = self.status_of(r)[0]
            if not r["cur"] and not final:
                sub = "No streams yet. The first stream you tick becomes the main stream."
            elif self.pending(r):
                sub = ("After Write: " if tag != SKIPPED else "Skipped. If included, it would become: ") \
                      + "  →  ".join(self.label(s) + (" (main)" if k == 0 else "") for k, s in enumerate(final))
            else:
                sub = "No changes. Streams: " + "  →  ".join(
                    self.label(s) + (" (main)" if k == 0 else "") for k, s in enumerate(r["cur"]))
            if r["state"] == "error":
                sub += f"\nLast write failed: {r['msg']}"
            self.d_sub.configure(text=sub)
            self.fill_cands(r, same)
            if not same:
                self.quiet_search = True
                self.search_v.set(" ".join(describe(r["name"])["key"]).lower())
                self.quiet_search = False
            self.run_search()

        def fill_cands(self, r, same):
            top = self.cand.yview()[0] if same else 0.0
            sel = self.cand.selection() if same else ()
            self.cand.delete(*self.cand.get_children())
            self.cand_order = []

            def add(sid, mark, why, tag):
                st = self.by_id.get(sid, {"name": f"#{sid}", "_group": "", "_acct": ""})
                self.cand.insert("", "end", iid=str(sid), tags=(tag,) if tag else (),
                                 values=(mark, st["name"], st["_group"], st["_acct"], why))
                self.cand_order.append(sid)
            if r["cur"]:
                add(r["cur"][0], "★", "main stream, always first", "main")
            for sid in r["cur"][1:]:
                gone = sid in r["drop"]
                add(sid, OFF if gone else ON, "current backup: will be REMOVED" if gone else "current backup",
                    "remove" if gone else "")
            listed = set(r["cur"])
            for st, why, sure in r["cands"]:
                if st["id"] in listed:
                    continue
                listed.add(st["id"])
                on = st["id"] in r["picked"]
                add(st["id"], ON if on else OFF, why if sure else f"check: {why}",
                    "on" if on else "" if sure else "check")
            for sid in r["added"] + r["picked"]:
                if sid not in listed and sid in self.by_id:
                    listed.add(sid)
                    on = sid in r["picked"]
                    add(sid, ON if on else OFF, "added by you", "on" if on else "")
            if len(self.cand_order) <= 1 and not r["picked"]:
                self.cand.insert("", "end", iid="none", values=(
                    "", "No other feeds found. Search on the right and double-click a stream to add it.", "", "", ""))
            keep = [s for s in sel if self.cand.exists(s)]
            if keep:
                self.cand.selection_set(keep)
            self.cand.yview_moveto(top)

        def sort_picked(self, r):
            order = {sid: k for k, sid in enumerate(st["id"] for st, _, _ in r["cands"])}
            n = len(order)
            for k, sid in enumerate(r["added"]):
                order.setdefault(sid, n + k)
            r["picked"].sort(key=lambda x: order.get(x, 10 ** 6))

        def cand_click(self, e):
            region = self.cand.identify_region(e.x, e.y)
            if region not in ("cell", "tree"):
                return None   # headings and column dividers work as usual
            iid = self.cand.identify_row(e.y)
            if not iid or iid == "none":
                return "break"
            if e.state & 0x0001:   # Shift-click selects a range without ticking
                return None
            self.cand.selection_set(iid)
            self.cand.focus(iid)
            self.cand.focus_set()
            self.toggle_cands([iid])
            return "break"

        def toggle_cands(self, iids):
            i, r = self.current()
            sids = [int(k) for k in iids if k != "none"]
            if r is None or not sids:
                return
            if r["cur"] and sids == [r["cur"][0]]:
                self.note("The main stream always stays first and can't be unticked.", warn=True)
                return
            states = []
            for sid in sids:
                if r["cur"] and sid == r["cur"][0]:
                    continue
                states.append(sid not in r["drop"] if sid in r["cur"] else sid in r["picked"])
            on = not all(states)   # a mixed selection gets ticked
            for sid in sids:
                if r["cur"] and sid == r["cur"][0]:
                    continue
                if sid in r["cur"]:
                    if on and sid in r["drop"]:
                        r["drop"].remove(sid)
                    elif not on and sid not in r["drop"]:
                        r["drop"].append(sid)
                elif on and sid not in r["picked"]:
                    r["picked"].append(sid)
                elif not on and sid in r["picked"]:
                    r["picked"].remove(sid)
            self.sort_picked(r)
            self.after_edit([i])

        # ---------------- live search over all streams
        def schedule_search(self):
            if self.quiet_search:
                return
            if self.search_job:
                self.root.after_cancel(self.search_job)
            self.search_job = self.root.after(150, self.run_search)

        def run_search(self):
            """Live search over all streams. Streams already on this channel (main, backup
            or picked) are left out, so every line shown can actually be added."""
            if self.search_job:
                self.root.after_cancel(self.search_job)
            self.search_job = None
            words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", self.search_v.get()).lower())
            sel = set(self.hits.selection())
            top = self.hits.yview()[0]
            self.hits.delete(*self.hits.get_children())
            _, r = self.current()
            if not words:
                self.hits_info.configure(text=f"Type to search all {len(self.streams)} streams." if self.streams
                                         else "")
                return
            have = ({x for x in r["cur"] if x not in r["drop"]} | set(r["picked"])) if r else set()
            region = r["region"] if r else None
            found, hidden = [], 0
            for st in self.streams:
                if not all(w in st["_hay"] for w in words):
                    continue
                if st["id"] in have:
                    hidden += 1
                    continue
                found.append(st)
            found.sort(key=lambda st: (not all(w in st["_name_hay"] for w in words),
                                       st["_d"]["region"] != region, st["_d"]["q"], st["name"].lower()))
            limit = 300
            for st in found[:limit]:
                self.hits.insert("", "end", iid=str(st["id"]), values=(st["name"], st["_group"], st["_acct"]))
            keep = [s for s in sel if self.hits.exists(s)]
            if keep:
                self.hits.selection_set(keep)
                self.hits.yview_moveto(top)
            info = f"{len(found)} match{'es' if len(found) != 1 else ''}"
            if len(found) > limit:
                info += f" (first {limit} shown)"
            if hidden:
                info += f"  ·  {hidden} already on this channel"
            if not r:
                info += "  ·  select one channel to add"
            self.hits_info.configure(text=info)

        def hit_double(self, e):
            iid = self.hits.identify_row(e.y)
            if iid and self.hits.identify_region(e.x, e.y) == "cell":
                self.add_hits([iid])
                return "break"
            return None

        def add_hits(self, iids=None):
            i, r = self.current()
            if r is None:
                self.note("Select one channel in the table first, then add streams to it.", warn=True)
                return
            iids = list(iids or self.hits.selection())
            if not iids:
                self.note("Select one or more streams in the search results first.", warn=True)
                return
            cand_ids = {st["id"] for st, _, _ in r["cands"]}
            names = []
            for k in iids:
                sid = int(k)
                if sid in r["drop"]:
                    r["drop"].remove(sid)
                elif sid not in r["cur"] and sid not in r["picked"]:
                    r["picked"].append(sid)
                    if sid not in cand_ids and sid not in r["added"]:
                        r["added"].append(sid)
                else:
                    continue
                names.append(self.label(sid))
            if not names:
                self.note("Those streams are already on this channel.", warn=True)
                return
            self.sort_picked(r)
            kids = list(self.hits.get_children())
            after = [k for k in kids[kids.index(iids[-1]) + 1:] if k not in iids] if iids[-1] in kids else []
            self.after_edit([i])
            if after and self.hits.exists(after[0]):   # keep the cursor where it was for the next add
                self.hits.selection_set(after[0])
                self.hits.focus(after[0])
                self.hits.see(after[0])
            for sid in (int(k) for k in iids):
                if self.cand.exists(str(sid)):
                    self.cand.see(str(sid))
            self.note(f"Added {', '.join(names[:3])}{'…' if len(names) > 3 else ''} to {r['name']}. "
                      "Press Return in the table (or Done, next) to move on.")

        # ---------------- autosave
        def schedule_save(self):
            if self.save_job:
                self.root.after_cancel(self.save_job)
            self.save_job = self.root.after(700, self.save_session)

        def flush_save(self):
            if self.save_job:
                self.root.after_cancel(self.save_job)
                self.save_session()

        def save_session(self):
            """Autosave every pick, removal and review, so closing the app loses nothing."""
            self.save_job = None
            if not self.disp or not self.rows:
                return
            work = {}
            for r in self.rows:
                if (r["picked"] != r["auto"] or r["drop"] or r["added"] or r["skip"] or r["reviewed"]) \
                        and not (r["state"] == "done" and not self.pending(r)):
                    work[str(r["ch"]["id"])] = {k: r[k] for k in ("picked", "drop", "added", "skip", "reviewed")}
            try:
                write_private_json(SESSION_FILE, {"base": self.disp.base, "saved": time.time(), "rows": work})
            except OSError as e:
                log.warning("Autosave failed: %s", e)
                self.note(f"Autosave failed: {e}", warn=True)

        def restore_session(self, ask):
            data = read_json(SESSION_FILE)
            saved = data.get("rows")
            if data.get("base") != self.disp.base or not isinstance(saved, dict) or not saved:
                return 0
            by_ch = {str(r["ch"]["id"]): r for r in self.rows}
            known = set(self.by_id)
            todo = []
            for k, v in saved.items():
                r = by_ch.get(k)
                if r is None or not isinstance(v, dict):
                    continue
                picked = [x for x in v.get("picked", []) if x in known and x not in r["cur"]]
                drop = [x for x in v.get("drop", []) if x in r["cur"][1:]]
                if picked != r["auto"] or drop or v.get("skip") or v.get("reviewed") or v.get("confirmed"):
                    todo.append((r, v, picked, drop))
            if not todo:
                return 0
            if ask:
                when = time.strftime("%b %d at %H:%M", time.localtime(data.get("saved", 0)))
                if not messagebox.askyesno("Restore your work?",
                                           f"You have unwritten work from {when} on {len(todo)} channels "
                                           "(picks, removals and reviews).\n\nRestore it?", parent=self.root):
                    return 0
            for r, v, picked, drop in todo:
                r["picked"], r["drop"] = picked, drop
                r["added"] = [x for x in v.get("added", []) if x in known and x not in r["cur"]]
                if "skip" in v or "reviewed" in v:
                    r["skip"], r["reviewed"] = bool(v.get("skip")), bool(v.get("reviewed"))
                else:   # saved by an older version: "add" unticked a row, "confirmed" meant reviewed
                    r["skip"] = v.get("add") is False and bool(picked or drop)
                    r["reviewed"] = bool(v.get("confirmed"))
                self.sort_picked(r)
            log.info("Restored autosaved work on %d channels", len(todo))
            return len(todo)

        # ---------------- write and undo
        def write(self):
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            todo = [i for i, r in enumerate(self.rows) if self.will_write(r)]
            if not self.disp or not todo:
                self.note(f"Nothing to write yet: tick backups for a channel and it turns green ({READY}).",
                          warn=True)
                return
            n = sum(len(self.rows[i]["picked"]) for i in todo)
            d = sum(len(self.rows[i]["drop"]) for i in todo)
            skipped = sum(1 for r in self.rows if self.pending(r) and r["skip"])
            if not messagebox.askyesno(
                    "Write to Dispatcharr",
                    f"Update {len(todo)} channel{'s' if len(todo) != 1 else ''} on {self.disp.base}:\n\n"
                    f"    add {n} backup stream{'s' if n != 1 else ''}"
                    + (f"\n    remove {d} backup stream{'s' if d != 1 else ''}" if d else "")
                    + (f"\n\n{skipped} skipped channel{'s are' if skipped != 1 else ' is'} left out." if skipped else "")
                    + "\n\nThe main stream always stays first. The original stream lists are saved first, "
                    "so this can be undone.", parent=self.root):
                return
            jobs = []
            for i in todo:
                r = self.rows[i]
                want = [s for s in r["cur"] if s not in r["drop"]] + [s for s in r["picked"] if s not in r["cur"]]
                jobs.append((i, r["ch"]["id"], r["name"], want))
            stamp = time.strftime("%Y%m%d-%H%M%S")
            undo_file = os.path.join(CONFIG_DIR, f"undo_{stamp}.json")
            try:
                write_private_json(undo_file, {"base": self.disp.base, "channels": {
                    str(self.rows[i]["ch"]["id"]): {"name": self.rows[i]["name"], "streams": list(self.rows[i]["cur"])}
                    for i in todo}})
            except OSError as e:
                messagebox.showerror("Not written", f"Could not save the undo file, so nothing was written:\n\n{e}",
                                     parent=self.root)
                return
            log.info("Saved original stream lists to %s", undo_file)
            disp = self.disp

            def work():
                out = []
                for k, (i, cid, name, want) in enumerate(jobs, 1):
                    self.say(f"Writing {k} of {len(jobs)}: {name}", (k - 1) / len(jobs))
                    try:
                        got, err = disp.set_streams(cid, want), None
                        if got[:len(want)] != want:
                            err = "Dispatcharr saved a different list; press Reload to see it"
                    except NotFound:
                        got, err = None, "channel no longer exists in Dispatcharr; press Reload"
                    except RuntimeError as e:
                        got, err = None, str(e)[:120]
                    log.info("Channel %s %s: %s", cid, name, err or "done")
                    out.append((i, want, got, err))
                return out

            def done(out):
                # applied here on the Tk thread; picks made while writing are kept
                bad = 0
                for i, want, got, err in out:
                    r = self.rows[i]
                    if not err:   # as before: only a verified write updates the row
                        r["cur"] = got
                        r["picked"] = [s for s in r["picked"] if s not in got]
                        r["drop"] = [s for s in r["drop"] if s in got]
                    if err:
                        bad += 1
                        r["state"], r["msg"] = "error", err
                    else:
                        r["state"], r["msg"] = "done", f"{len(got)} stream{'s' if len(got) != 1 else ''}"
                self.update_rows([i for i, _, _, _ in out])
                self.flush_save()
                self.show_detail()
                self.note(f"Done: {len(out) - bad} channel{'s' if len(out) - bad != 1 else ''} updated"
                          + (f", {bad} failed (see the Status column)" if bad else "")
                          + f". Undo file: {os.path.basename(undo_file)}", warn=bool(bad))
                if bad:
                    messagebox.showwarning("Some channels failed", f"{bad} channel{'s were' if bad != 1 else ' was'}"
                                           " not updated. Show Errors to see why; they're tried again on the "
                                           "next Write.", parent=self.root)
            self.bg(work, done)

        def undo(self):
            if self.busy:
                self.note("Still working, one moment…", warn=True)
                return
            if not self.disp:
                self.note("Connect first.", warn=True)
                return
            files = []
            for path in sorted(glob.glob(os.path.join(CONFIG_DIR, "undo_*.json")), reverse=True):
                data = read_json(path)
                chans = data.get("channels")
                if not isinstance(chans, dict):
                    continue
                m = re.search(r"undo_(\d{8})-(\d{6})", path)
                when = (time.strftime("%b %d %Y, %H:%M:%S", time.strptime("".join(m.groups()), "%Y%m%d%H%M%S"))
                        if m else os.path.basename(path))
                files.append((path, when, data.get("base", ""), chans))
            if not files:
                self.note("Nothing to undo yet: an undo file is saved every time you Write.", warn=True)
                return
            top = tk.Toplevel(self.root)
            top.title("Undo a write")
            top.transient(self.root)
            top.geometry("720x380")
            frm = ttk.Frame(top, padding=10)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="Pick a write to undo. Those channels get back the stream lists they had "
                                "just before it.", wraplength=680).pack(side="top", anchor="w", pady=(0, 6))
            btns = ttk.Frame(frm)
            btns.pack(side="bottom", fill="x", pady=(8, 0))
            box, lst = scrolled_tree(frm, ("when", "n", "names", "server"), ("Written", "Channels", "Which",
                                                                             "Server"),
                                     (170, 70, 280, 180), "server", selectmode="browse")
            box.pack(side="top", fill="both", expand=True)
            for k, (path, when, base, chans) in enumerate(files):
                names = ", ".join(str(v.get("name", "")) for v in list(chans.values())[:6])
                lst.insert("", "end", iid=str(k), values=(when, len(chans), names, base))
            lst.selection_set("0")

            def go():
                sel = lst.selection()
                if not sel:
                    return
                path, when, base, chans = files[int(sel[0])]
                if base and base != self.disp.base:
                    messagebox.showerror("Different server", f"That write was to {base}, but you're connected "
                                         f"to {self.disp.base}.", parent=top)
                    return
                if not messagebox.askyesno("Undo", f"Put back the stream lists that {len(chans)} channel"
                                           f"{'s' if len(chans) != 1 else ''} had before the write of {when}?",
                                           parent=top):
                    return
                top.destroy()
                self.run_undo(chans)
            ttk.Button(btns, text="Undo this write", default="active", command=go).pack(side="right")
            ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right", padx=6)
            ttk.Button(btns, text="Open folder", command=lambda: open_folder(CONFIG_DIR)).pack(side="left")
            lst.bind("<Double-1>", lambda e: go())
            top.bind("<Escape>", lambda e: top.destroy())
            try:
                top.wait_visibility()   # X11 refuses a grab on a window that isn't shown yet
                top.grab_set()
            except tk.TclError:
                pass
            lst.focus_set()

        def run_undo(self, chans):
            disp = self.disp

            def work():
                bad, gone = 0, 0
                for k, (cid, v) in enumerate(chans.items(), 1):
                    self.say(f"Restoring {k} of {len(chans)}: {v.get('name')}", (k - 1) / len(chans))
                    want = list(v.get("streams") or [])
                    try:
                        if disp.set_streams(int(cid), want)[:len(want)] != want:
                            bad += 1
                    except NotFound:
                        gone += 1
                    except RuntimeError as e:
                        log.warning("Undo of channel %s failed: %s", cid, e)
                        bad += 1
                log.info("Undo restored %d channels, %d errors, %d gone", len(chans) - bad - gone, bad, gone)
                return bad, gone

            def done(res):
                bad, gone = res
                self.load_note = (("!" if bad else "") + f"Undo finished: {len(chans) - bad - gone} restored"
                                  + (f", {bad} failed" if bad else "") + (f", {gone} no longer exist" if gone else "")
                                  + ". ")
                self.connect(reload=True)
            self.bg(work, done)

        def close(self):
            if self.busy and not messagebox.askyesno("Still working", "Dispatcharr is still being updated. "
                                                     "Quit anyway?", parent=self.root):
                return
            try:
                self.flush_save()
                self.save_ui()
            finally:
                self.root.destroy()

    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    if aqua:
        root.createcommand("::tk::mac::Quit", app.close)
    root.mainloop()


def main():
    setup_logging()
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("This app needs Tk (tkinter). See the README's Requirements section for how to install it.")
    run_gui()


if __name__ == "__main__":
    main()
