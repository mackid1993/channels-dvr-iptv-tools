#!/usr/bin/env python3
"""Dispatcharr Backup Streams (GUI).

Finds other feeds of the same channel among your Dispatcharr streams and adds
them to each channel as backup streams, so Dispatcharr can switch to another
feed when one goes dark. Talks to Dispatcharr only through its API.

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
  * Similar names are offered unticked ("check") for you to decide. Anything
    else can be found with the live search box.
  * Your main stream always stays first. Backups you tick are added; backups
    you untick (including ones already written) are removed on the next Write.
    Original stream lists are saved first, so any run can be undone.

Runs on Windows, macOS and Linux with Python 3.8+ and Tk (tkinter).

    python3 dispatcharr_backup_streams.py        (macOS / Linux)
    py dispatcharr_backup_streams.py             (Windows, or double-click)
"""
import json
import queue
import logging
import logging.handlers
import os
import re
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
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")   # autosaved work in progress
LOG_FILE = os.path.join(CONFIG_DIR, "logs", "backup_streams.log")
DEFAULT_MAX = 3

SURE = "Backups found"            # sure matches, ticked
CHECK = "Check suggestions"       # only uncertain matches; nothing ticked
NONE = "Nothing found"
HAS_BACKUPS = "Already has backups"
WRITTEN = "Written"
ERROR = "Error"
SKIPPED = "Skipped"
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


def setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


# ----------------------------------------------------------------------
# Dispatcharr API
# ----------------------------------------------------------------------

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
            raise RuntimeError(f"Dispatcharr returned HTTP {e.code} for {path}: {body_txt}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Could not reach {self.base}: {getattr(e, 'reason', e)}")
        return json.loads(raw) if raw else None

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
        data = self.api("GET", "/api/channels/channels/")
        return data.get("results", []) if isinstance(data, dict) else (data or [])

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

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    def multi_select(lb):
        """Cmd/Ctrl-click toggles one line, Shift-click selects a range, on every platform."""
        state = {"anchor": None}

        def toggle(e):
            k = lb.nearest(e.y)
            if lb.selection_includes(k):
                lb.selection_clear(k)
            else:
                lb.selection_set(k)
            state["anchor"] = k
            lb.activate(k)
            return "break"

        def extend(e):
            k = lb.nearest(e.y)
            a = state["anchor"] if state["anchor"] is not None else k
            lb.selection_set(min(a, k), max(a, k))
            lb.activate(k)
            return "break"

        def plain(e):
            state["anchor"] = lb.nearest(e.y)

        for seq in ("<Command-Button-1>", "<Control-Button-1>", "<Meta-Button-1>", "<Mod1-Button-1>"):
            try:
                lb.bind(seq, toggle)
            except tk.TclError:
                pass
        lb.bind("<Shift-Button-1>", extend)
        lb.bind("<Button-1>", plain, add="+")

    class App:
        def __init__(self, root):
            self.root = root
            self.disp = None
            self.rows = []
            self.streams = []
            self.by_id = {}
            self.busy = False
            self.q = queue.Queue()   # worker threads never touch Tk; they post here
            root.title("Dispatcharr Backup Streams")
            root.geometry("1250x780")
            self.v = {k: tk.StringVar() for k in ("disp", "apikey", "user", "password", "max")}
            self.v["max"].set(str(DEFAULT_MAX))
            self.load_settings()
            self.build()
            self.pump()

        # ---------------- settings
        def load_settings(self):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    s = json.load(f)
            except (OSError, ValueError):
                return
            for k in ("disp", "apikey", "user", "max"):
                if s.get(k):
                    self.v[k].set(str(s[k]))

        def save_settings(self):
            write_private_json(SETTINGS_FILE, {k: self.v[k].get() for k in ("disp", "apikey", "user", "max")})

        # ---------------- layout
        def build(self):
            pad = dict(padx=4, pady=3)
            top = ttk.Frame(self.root)
            top.pack(fill="x", padx=8, pady=6)
            ttk.Label(top, text="Dispatcharr").grid(row=0, column=0, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["disp"], width=26).grid(row=0, column=1, **pad)
            ttk.Label(top, text="API key").grid(row=0, column=2, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["apikey"], show="•", width=24).grid(row=0, column=3, **pad)
            ttk.Label(top, text="or user / password").grid(row=0, column=4, sticky="e", **pad)
            ttk.Entry(top, textvariable=self.v["user"], width=14).grid(row=0, column=5, **pad)
            ttk.Entry(top, textvariable=self.v["password"], show="•", width=14).grid(row=0, column=6, **pad)
            ttk.Label(top, text="Max backups per channel").grid(row=0, column=7, sticky="e", **pad)
            ttk.Spinbox(top, from_=1, to=10, textvariable=self.v["max"], width=4).grid(row=0, column=8, **pad)
            self.go = ttk.Button(top, text="Connect & Match", command=self.connect)
            self.go.grid(row=0, column=9, **pad)

            bar = ttk.Frame(self.root)
            bar.pack(fill="x", padx=8)
            ttk.Label(bar, text="Show").pack(side="left")
            self.show_v = tk.StringVar(value="All")
            sbox = ttk.Combobox(bar, textvariable=self.show_v, state="readonly", width=20,
                                values=["All", "Needs attention", SURE, CHECK, NONE, HAS_BACKUPS,
                                        WRITTEN, ERROR, "Ticked to write"])
            sbox.pack(side="left", padx=4)
            sbox.bind("<<ComboboxSelected>>", lambda e: self.refilter())
            ttk.Label(bar, text="Group").pack(side="left", padx=(10, 0))
            self.group_v = tk.StringVar(value="All groups")
            self.group_box = ttk.Combobox(bar, textvariable=self.group_v, state="readonly", width=24,
                                          values=["All groups"])
            self.group_box.pack(side="left", padx=4)
            self.group_box.bind("<<ComboboxSelected>>", lambda e: self.refilter())
            ttk.Label(bar, text="Find").pack(side="left", padx=(10, 0))
            self.find_v = tk.StringVar()
            ttk.Entry(bar, textvariable=self.find_v, width=22).pack(side="left", padx=4)
            self.find_v.trace_add("write", lambda *a: self.refilter())
            ttk.Button(bar, text="Clear", width=6, command=self.clear_filters).pack(side="left")
            self.counts = ttk.Label(bar, text="")
            self.counts.pack(side="left", padx=10)
            ttk.Label(bar, text="Find: name, stream or # (500-600).  Click a heading to sort.",
                      foreground="gray").pack(side="right")
            leg = ttk.Frame(self.root)
            leg.pack(fill="x", padx=8)
            for text, color in ((SURE, "#d9f2d9"), (CHECK, "#fff2c2"), (NONE, "#f8d4d4"),
                                (HAS_BACKUPS, "#c6ebc6"), (WRITTEN, "#e8e8e8"), (ERROR, "#f4b6b6")):
                tk.Label(leg, text=" " + text + " ", bg=color, fg="black").pack(side="left", padx=2, pady=2)

            cols = ("add", "num", "channel", "group", "current", "backups", "status")
            heads = ("Add", "Ch #", "Channel", "Group", "Current streams", "Backups to add", "Status")
            widths = (45, 65, 210, 130, 250, 400, 170)
            self.sort_col, self.sort_rev = "num", False
            self.pinned = set()   # rows edited under the current filters stay visible until filters change
            tf = ttk.Frame(self.root)
            tf.pack(fill="both", expand=True, padx=8, pady=4)
            self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
            for c, h, w in zip(cols, heads, widths):
                self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
                self.tree.column(c, width=w, anchor="w", stretch=c in ("channel", "backups"))
            sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            for tag, color in ((SURE, "#d9f2d9"), (CHECK, "#fff2c2"), (NONE, "#f8d4d4"), (HAS_BACKUPS, "#c6ebc6"),
                               (WRITTEN, "#e8e8e8"), (ERROR, "#f4b6b6"), (SKIPPED, "#f2f2f2")):
                self.tree.tag_configure(tag, background=color, foreground="black")
            self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_row())
            self.tree.bind("<Double-1>", lambda e: self.toggle_row())
            self.tree.bind("<space>", lambda e: self.toggle_row())
            self.tree.bind("<Command-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), "break")[1])
            self.tree.bind("<Control-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), "break")[1])

            def tree_toggle(e):
                iid = self.tree.identify_row(e.y)
                if iid:
                    self.tree.selection_toggle(iid)
                return "break"
            for seq in ("<Command-Button-1>", "<Control-Button-1>", "<Meta-Button-1>", "<Mod1-Button-1>"):
                try:
                    self.tree.bind(seq, tree_toggle)
                except tk.TclError:
                    pass

            pick = ttk.LabelFrame(self.root, text="Backups for the selected channel  (Cmd/Ctrl-click or Shift-click to select several, then confirm)")
            pick.pack(fill="x", padx=8, pady=4)
            self.sel = ttk.Label(pick, text="Select a channel above.")
            self.sel.grid(row=0, column=0, columnspan=3, sticky="w", padx=4)
            self.cand = tk.Listbox(pick, height=6, exportselection=False, selectmode="extended",
                                   font=("TkFixedFont", 11))
            self.cand.grid(row=1, column=0, sticky="we", padx=4, pady=3)
            multi_select(self.cand)
            self.cand.bind("<Double-1>", lambda e: self.toggle_clicked())
            self.cand.bind("<Return>", lambda e: self.set_cands(True))
            self.cand.bind("<BackSpace>", lambda e: self.set_cands(False))
            self.cand.bind("<Delete>", lambda e: self.set_cands(False))
            cb = ttk.Frame(pick)
            cb.grid(row=2, column=0, sticky="w", padx=4, pady=(0, 4))
            ttk.Button(cb, text="Tick selected", command=lambda: self.set_cands(True)).pack(side="left")
            ttk.Button(cb, text="Untick selected", command=lambda: self.set_cands(False)).pack(side="left", padx=4)
            ttk.Button(cb, text="Select all", command=lambda: self.cand.selection_set(0, "end")).pack(side="left")
            ttk.Button(cb, text="Confirm this channel ✓", command=self.confirm).pack(side="left", padx=(16, 0))
            ttk.Button(cb, text="Un-confirm", command=self.unconfirm).pack(side="left", padx=4)
            ttk.Label(pick, text="Search all streams (as you type). Cmd/Ctrl/Shift-click several, then Add:").grid(row=0, column=1, sticky="w", padx=4)
            self.search_v = tk.StringVar()
            se = ttk.Entry(pick, textvariable=self.search_v, width=40)
            se.grid(row=0, column=2, sticky="we", padx=4)
            self.hits = tk.Listbox(pick, height=6, exportselection=False, selectmode="extended",
                                   font=("TkFixedFont", 11))
            self.hits.grid(row=1, column=1, columnspan=2, sticky="we", padx=4, pady=3)
            multi_select(self.hits)
            self.hits.bind("<Double-1>", lambda e: self.add_hit())
            self.hits.bind("<Command-a>", lambda e: (self.hits.selection_set(0, "end"), "break")[1])
            self.hits.bind("<Control-a>", lambda e: (self.hits.selection_set(0, "end"), "break")[1])
            self.hits.bind("<Return>", lambda e: self.add_hit())
            se.bind("<Down>", lambda e: (self.hits.focus_set(), self.hits.selection_clear(0, "end"),
                                         self.hits.selection_set(0), "break")[-1])
            ttk.Button(pick, text="Add selected streams as backups", command=self.add_hit).grid(
                row=2, column=2, sticky="e", padx=4, pady=(0, 4))
            pick.columnconfigure(0, weight=3)
            pick.columnconfigure(1, weight=2)
            self.search_after = None
            self.search_v.trace_add("write", lambda *a: self.schedule_search())
            self.hit_list = []

            bottom = ttk.Frame(self.root)
            bottom.pack(fill="x", padx=8, pady=6)
            self.status = ttk.Label(bottom, text="Enter your Dispatcharr address and API key, then press Connect & Match.")
            self.status.pack(side="left")
            ttk.Button(bottom, text="Undo a previous run…", command=self.undo).pack(side="right", padx=4)
            self.write_btn = ttk.Button(bottom, text="Write ticked backups to Dispatcharr", command=self.write)
            self.write_btn.pack(side="right", padx=4)
            ttk.Button(bottom, text="Approve all shown", command=lambda: self.approve(all_shown=True)).pack(side="right", padx=4)
            ttk.Button(bottom, text="Approve selected", command=self.approve).pack(side="right", padx=4)
            ttk.Button(bottom, text="Untick all", command=lambda: self.tick_all(False)).pack(side="right", padx=4)

        # ---------------- background work
        def bg(self, fn, done):
            if self.busy:
                return
            self.busy = True
            self.go.state(["disabled"])
            self.write_btn.state(["disabled"])

            def run():
                try:
                    res, err = fn(), None
                except Exception as e:  # shown to the user
                    log.exception("Background task failed")
                    res, err = None, e
                self.q.put(lambda: self._bg_done(done, res, err))
            threading.Thread(target=run, daemon=True).start()

        def _bg_done(self, done, res, err):
            self.busy = False
            self.go.state(["!disabled"])
            self.write_btn.state(["!disabled"])
            if err:
                self.note("Error.")
                messagebox.showerror("Error", str(err))
            else:
                done(res)

        def say(self, text):
            self.q.put(lambda: self.note(text))

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
            self.root.after(100, self.pump)

        # ---------------- connect and match
        def connect(self):
            try:
                base = normalize_base(self.v["disp"].get())
                maxn = max(1, int(self.v["max"].get() or DEFAULT_MAX))
            except ValueError as e:
                messagebox.showerror("Check the settings", str(e))
                return
            key, user, pw = self.v["apikey"].get().strip(), self.v["user"].get().strip(), self.v["password"].get()
            if not key and not (user and pw):
                messagebox.showerror("Login needed", "Enter an API key, or a username and password.")
                return
            self.save_settings()

            def work():
                d = Dispatcharr(base, key, user, pw)
                d.login()
                self.say("Reading channels…")
                chans = d.channels()
                self.say("Reading streams…")
                streams = d.streams()
                groups, accts = d.groups(), d.accounts()
                self.ch_groups = groups
                self.say("Matching…")
                for st in streams:
                    st["_d"] = describe(st.get("name", ""))
                    st["_tvg"] = tvg_key(st.get("tvg_id"))
                    st["_group"] = groups.get(st.get("channel_group"), "")
                    st["_acct"] = accts.get(st.get("m3u_account"), "")
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
                    picked = [] if len(cur) > 1 else [st["id"] for st, _, sure in cands if sure][:room]
                    rows.append(dict(ch=ch, name=name, cur=cur, cands=cands, picked=picked,
                                     add=bool(picked), state="", msg="", review=not picked and len(cur) < 2, confirmed=False, drop=[],
                                     group=groups.get(ch.get("effective_channel_group_id")
                                                      or ch.get("channel_group_id"), "")))
                return d, streams, by_id, rows

            self.note("Connecting…")
            self.bg(work, self.matched)

        def matched(self, res):
            self.disp, self.streams, self.by_id, self.rows = res
            self.restore_session()
            self._loaded = True
            self.group_box.configure(values=["All groups"] + sorted({r["group"] for r in self.rows if r["group"]}))
            log.info("Loaded %d channels, %d streams", len(self.rows), len(self.streams))
            self.refresh()
            n = sum(1 for r in self.rows if r["picked"])
            chk = sum(1 for r in self.rows if not r["picked"] and r["cands"])
            self.note(f"{len(self.rows)} channels, {len(self.streams)} streams. Sure backups for "
                                    f"{n} channels; {chk} more have candidates to check (yellow). "
                                    "Review, then press Write.")

        # ---------------- table
        def label(self, sid):
            st = self.by_id.get(sid)
            return st["name"] if st else f"#{sid}"

        def status_of(self, r):
            if r["state"] == "done":
                return WRITTEN, r["msg"] or "written"
            if r["state"] == "error":
                return ERROR, r["msg"]
            if r["drop"] and not (r["review"] and not r["confirmed"]):
                extra = f", add {len(r['picked'])}" if r["picked"] else ""
                return SURE, f"remove {len(r['drop'])}{extra}"
            if len(r["cur"]) > 1 and not r["picked"] and not r["drop"] and not (r["review"] and not r["confirmed"]):
                return HAS_BACKUPS, f"{len(r['cur']) - 1} backup{'s' if len(r['cur']) > 2 else ''} already"
            if r["review"] and not r["confirmed"]:
                if r["picked"]:
                    return CHECK, f"{len(r['picked'])} picked - press Confirm"
                return (CHECK, f"{len(r['cands'])} to check") if r["cands"] else (NONE, "nothing found - use search")
            if r["picked"] and not r["add"]:
                return SKIPPED, "unticked"
            if r["picked"]:
                return SURE, f"add {len(r['picked'])}"
            if r["cands"]:
                return CHECK, f"{len(r['cands'])} to check"
            if len(r["cur"]) > 1:
                return HAS_BACKUPS, f"{len(r['cur']) - 1} backups already"
            return NONE, "nothing found - use search"

        @staticmethod
        def _num(r):
            n = r["ch"].get("channel_number")
            return int(n) if isinstance(n, float) and n.is_integer() else n

        def _matches_find(self, r, f):
            if not f:
                return True
            m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", f)
            num = r["ch"].get("channel_number") or 0
            if m:
                return float(m.group(1)) <= float(num) <= float(m.group(2))
            if f == str(self._num(r)):
                return True
            hay = " ".join([r["name"], r["group"]] + [self.label(s) for s in r["cur"] + r["picked"]]).lower()
            return all(w in hay for w in f.split())

        def sort_by(self, col):
            self.sort_rev = not self.sort_rev if self.sort_col == col else False
            self.sort_col = col
            self.refresh()

        def refilter(self):
            self.pinned.clear()
            self.refresh()

        def clear_filters(self):
            self.show_v.set("All")
            self.group_v.set("All groups")
            self.find_v.set("")  # triggers refresh

        def save_session(self):
            """Autosave every pick, removal and confirmation, so closing the app loses nothing."""
            if not self.disp or not self.rows:
                return
            work = {}
            for r in self.rows:
                if r["state"] == "done":
                    continue
                work[str(r["ch"]["id"])] = {"picked": r["picked"], "drop": r["drop"], "add": r["add"],
                                            "confirmed": r["confirmed"], "review": r["review"]}
            try:
                write_private_json(SESSION_FILE, {"base": self.disp.base, "saved": time.time(), "rows": work})
            except OSError as e:
                log.warning("Autosave failed: %s", e)
                self.note(f"Autosave failed: {e}", warn=True)

        def restore_session(self):
            try:
                with open(SESSION_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return
            if data.get("base") != self.disp.base or not data.get("rows"):
                return
            by_ch = {str(r["ch"]["id"]): r for r in self.rows}
            saved = data["rows"]
            changed = [k for k, v in saved.items() if k in by_ch and (
                v.get("confirmed") or v.get("drop") or set(v.get("picked", [])) != set(by_ch[k]["picked"])
                or v.get("add") != by_ch[k]["add"])]
            if not changed:
                return
            when = time.strftime("%b %d %H:%M", time.localtime(data.get("saved", 0)))
            if not messagebox.askyesno("Restore your work?",
                                       f"You have unsaved work from {when} on {len(changed)} channels "
                                       "(picks, removals and confirmations).\n\nRestore it?"):
                return
            for k in changed:
                r, v = by_ch[k], saved[k]
                known = set(self.by_id)
                r["picked"] = [x for x in v.get("picked", []) if x in known and x not in r["cur"]]
                r["drop"] = [x for x in v.get("drop", []) if x in r["cur"][1:]]
                r["add"] = v.get("add", r["add"])
                r["confirmed"] = v.get("confirmed", False)
                r["review"] = v.get("review", r["review"])
            log.info("Restored autosaved work on %d channels", len(changed))
            self.note(f"Restored your work on {len(changed)} channels.")

        def refresh(self):
            if getattr(self, "_loaded", False):
                self.save_session()
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            f = self.find_v.get().strip().lower()
            show, grp = self.show_v.get(), self.group_v.get()
            items = []
            counts = {}
            for i, r in enumerate(self.rows):
                tag, status = self.status_of(r)
                counts[tag] = counts.get(tag, 0) + 1
                if i in self.pinned:
                    pass
                elif show == "Needs attention" and tag not in ATTENTION:
                    continue
                if show == "Ticked to write" and tag != SURE:
                    continue
                if show not in ("All", "Needs attention", "Ticked to write") and tag != show:
                    continue
                if i not in self.pinned and grp != "All groups" and r["group"] != grp:
                    continue
                if i not in self.pinned and not self._matches_find(r, f):
                    continue
                vals = ("✓" if tag == SURE else "", self._num(r) or "", r["name"], r["group"],
                        ", ".join(self.label(s) for s in r["cur"]),
                        ", ".join(self.label(s) for s in r["picked"]), status)
                items.append((i, tag, vals))
            col = ("add", "num", "channel", "group", "current", "backups", "status").index(self.sort_col)

            def key(it):
                v = it[2][col]
                if col == 1:
                    try:
                        return (0, float(v), "")
                    except (TypeError, ValueError):
                        return (1, 0.0, str(v))
                return (0, 0.0, str(v).lower())
            items.sort(key=key, reverse=self.sort_rev)
            for i, tag, vals in items:
                self.tree.insert("", "end", iid=str(i), tags=(tag,), values=vals)
            if sel and self.tree.exists(sel[0]):
                self.tree.selection_set(sel[0])
                self.tree.see(sel[0])
            todo = counts.get(SURE, 0)
            self.counts.config(text=f"{len(items)} shown   |   {todo} to write, "
                                    f"{counts.get(CHECK, 0)} to check, {counts.get(NONE, 0)} nothing found, "
                                    f"{counts.get(WRITTEN, 0)} written")

        def current(self):
            sel = self.tree.selection()
            return (int(sel[0]), self.rows[int(sel[0])]) if sel else (None, None)

        def toggle_row(self):
            i, r = self.current()
            if r and r["picked"]:
                r["add"] = not r["add"]
                self.pinned.add(i)
                self.refresh()

        def tick_all(self, on):
            for r in self.rows:
                if r["picked"]:
                    r["add"] = on
            self.refresh()

        def show_row(self, reset_search=True):
            i, r = self.current()
            if i == getattr(self, "shown", None):
                reset_search = False
            self.shown = i
            if not r:
                return
            self.sel.config(text=f"{r['name']}  —  main stream: {self.label(r['cur'][0]) if r['cur'] else '-'}"
                                 + ("   (confirmed)" if r["confirmed"] else ""))
            self.cand.delete(0, "end")
            self.cand_ids = []
            for sid in r["cur"][1:]:
                st = self.by_id.get(sid, {"name": f"#{sid}", "_group": ""})
                mark = "[-]" if sid in r["drop"] else "[x]"
                note = "WILL BE REMOVED" if sid in r["drop"] else "already a backup"
                self.cand.insert("end", f"{mark} {st['name'][:44]:<44} {st['_group'][:22]:<22} {note}")
                self.cand_ids.append(sid)
            extra = [self.by_id[s] for s in r["picked"] if s not in {st["id"] for st, _, _ in r["cands"]}
                     and s in self.by_id]
            listed = set(self.cand_ids)
            for st, why, sure in r["cands"] + [(x, "added by you", True) for x in extra]:
                if st["id"] in r["cur"] or st["id"] in listed:
                    continue
                listed.add(st["id"])
                mark = "[x]" if st["id"] in r["picked"] else "[ ]"
                hint = why if sure else f"CHECK: {why}"
                self.cand.insert("end", f"{mark} {st['name'][:44]:<44} {st['_group'][:22]:<22} {hint}")
                self.cand_ids.append(st["id"])
            if not self.cand_ids:
                self.cand.insert("end", "No other feeds found. Search on the right to add one by hand.")
            if reset_search:
                self._set_search_quiet(" ".join(describe(r["name"])["key"]).lower())

        def confirm(self):
            """Accept the picked backups for this channel; only then does it leave Needs attention."""
            i, r = self.current()
            if not r:
                self.note("Select a channel in the table first.", warn=True)
                return
            if not r["picked"] and not r["drop"]:
                self.note(f"Nothing to confirm for {r['name']}: tick or add at least one backup first.", warn=True)
                return
            r["confirmed"], r["add"] = True, True
            self.pinned.discard(i)
            nxt = None
            kids = list(self.tree.get_children())
            if str(i) in kids and kids.index(str(i)) + 1 < len(kids):
                nxt = kids[kids.index(str(i)) + 1]
            self.refresh()
            if nxt and self.tree.exists(nxt):
                self.tree.selection_set(nxt)
                self.tree.see(nxt)
            self.note(f"Confirmed {r['name']} ({len(r['picked'])} backups). Press Write when you're done.")

        def toggle_clicked(self):
            sel = [k for k in self.cand.curselection() if k < len(self.cand_ids)]
            if not sel:
                return
            i, r = self.current()
            sid = self.cand_ids[sel[0]]
            on = (sid in r["drop"]) if sid in r["cur"] else (sid not in r["picked"])
            self.set_cands(on)

        def approve(self, all_shown=False):
            """Tick every suggested backup and confirm, for the selected rows or every row shown.
            Cmd/Ctrl-click or Shift-click rows to select several; Cmd/Ctrl-A selects all shown."""
            iids = list(self.tree.get_children()) if all_shown else list(self.tree.selection())
            if not iids:
                self.note("Select one or more channels in the table first (Cmd/Ctrl-click, Shift-click or "
                          "Cmd/Ctrl-A), or use Approve all shown.", warn=True)
                return
            rows = [(int(x), self.rows[int(x)]) for x in iids]
            todo = [(i, r) for i, r in rows if r["state"] != "done" and (r["picked"] or any(
                st["id"] not in r["cur"] for st, _, _ in r["cands"]))]
            if not todo:
                self.note("Nothing to approve: none of those channels have suggestions to tick.", warn=True)
                return
            n = sum(len({st["id"] for st, _, _ in r["cands"] if st["id"] not in r["cur"]} | set(r["picked"]))
                    for _, r in todo)
            if all_shown and not messagebox.askyesno(
                    "Approve all shown", f"Tick every suggested backup ({n} streams) on the {len(todo)} channels "
                    "shown and mark them confirmed?\n\nNothing is written until you press Write."):
                return
            for i, r in todo:
                for st, _, _ in r["cands"]:
                    if st["id"] not in r["cur"] and st["id"] not in r["picked"]:
                        r["picked"].append(st["id"])
                r["confirmed"], r["add"] = True, True
                self.pinned.discard(i)
            self.refresh()
            self.show_row(reset_search=False)
            self.note(f"Approved {len(todo)} channels ({n} backups). Review if you like, then press Write.")

        def unconfirm(self):
            """Put a confirmed channel back into Needs attention to change it."""
            i, r = self.current()
            if not r:
                return
            r["confirmed"], r["review"] = False, True
            if r["state"] == "done":
                r["state"] = ""
            self.pinned.add(i)
            self.refresh()
            self.show_row(reset_search=False)

        def set_cands(self, on):
            """Tick or untick every selected line in the candidate list."""
            i, r = self.current()
            sel = [k for k in self.cand.curselection() if k < len(self.cand_ids)]
            if not r or not sel:
                self.note("Select one or more lines in the backups list first.", warn=True)
                return
            for k in sel:
                sid = self.cand_ids[k]
                if sid in r["cur"]:
                    if on and sid in r["drop"]:
                        r["drop"].remove(sid)
                    elif not on and sid not in r["drop"] and sid != r["cur"][0]:
                        r["drop"].append(sid)
                    continue
                if on and sid not in r["picked"]:
                    r["picked"].append(sid)
                elif not on and sid in r["picked"]:
                    r["picked"].remove(sid)
            r["picked"].sort(key=lambda x: self.cand_ids.index(x) if x in self.cand_ids else 999)
            r["add"] = bool(r["picked"] or r["drop"])
            if r["state"] == "done" and (r["picked"] or r["drop"]):
                r["state"] = ""   # written channel edited again: write it again
            self.pinned.add(i)
            self.refresh()
            self.show_row(reset_search=False)
            self.run_search()
            for k in sel:
                self.cand.selection_set(k)

        # ---------------- live search over all streams
        def _set_search_quiet(self, text):
            self._quiet = True
            self.search_v.set(text)
            self._quiet = False
            self.run_search()

        def schedule_search(self):
            if getattr(self, "_quiet", False):
                return
            if self.search_after:
                self.root.after_cancel(self.search_after)
            self.search_after = self.root.after(200, self.run_search)

        def run_search(self):
            """Live search over all streams. Streams already on this channel (main, backup
            or picked) are left out, so every line shown can actually be added."""
            self.search_after = None
            words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", self.search_v.get()).lower())
            self.hits.delete(0, "end")
            self.hit_list = []
            if not words:
                return
            _, r = self.current()
            have = set()
            if r:
                have = ({x for x in r["cur"] if x not in r["drop"]} | set(r["picked"]))
            hidden = 0
            for st in self.streams:
                hay = unicodedata.normalize("NFKC", st["name"]).lower()
                if not all(w in hay for w in words):
                    continue
                if st["id"] in have:
                    hidden += 1
                    continue
                self.hit_list.append(st)
                if len(self.hit_list) >= 200:
                    break
            for st in self.hit_list:
                self.hits.insert("end", f"{st['name'][:46]:<46} {st['_group'][:24]}")
            if not self.hit_list:
                self.hits.insert("end", "No other streams match." if hidden else "No streams match.")
            if hidden:
                self.hits.insert("end", f"({hidden} matching stream{'s are' if hidden > 1 else ' is'} already on this channel)")
                self.hits.itemconfig("end", foreground="#808080")

        def add_hit(self):
            i, r = self.current()
            if not r:
                self.note("Select a channel in the table first, then add streams to it.", warn=True)
                return
            sel = [k for k in self.hits.curselection() if k < len(self.hit_list)]
            if not sel:
                self.note("Select one or more streams in the search results first "
                          "(Cmd/Ctrl-click or Shift-click for several).", warn=True)
                return
            added, dup, main = [], [], []
            for k in sel:
                st = self.hit_list[k]
                sid = st["id"]
                if r["cur"] and sid == r["cur"][0]:
                    main.append(st["name"])
                elif sid in r["cur"] and sid not in r["drop"]:
                    dup.append(st["name"])
                elif sid in r["drop"]:
                    r["drop"].remove(sid)
                    added.append(st["name"])
                elif sid in r["picked"]:
                    dup.append(st["name"])
                else:
                    r["picked"].append(sid)
                    added.append(st["name"])
            parts = []
            if added:
                parts.append(f"Added {len(added)} to {r['name']}")
            if dup:
                parts.append(f"{len(dup)} already in the list ({', '.join(dup[:3])}{'…' if len(dup) > 3 else ''})")
            if main:
                parts.append(f"{main[0]} is the main stream")
            if added:
                r["add"] = True
                if r["state"] == "done":
                    r["state"] = ""
                self.pinned.add(i)
                self.refresh()
                self.show_row(reset_search=False)
                self.run_search()
                self.note(". ".join(parts) + ". Press Confirm, then Write.")
            else:
                self.note("Nothing added: " + "; ".join(parts) + ".", warn=True)

        # ---------------- write and undo
        def write(self):
            todo = [r for r in self.rows if r["add"] and (r["picked"] or r["drop"]) and r["state"] != "done"
                    and (r["confirmed"] or not r["review"])]
            if not self.disp or not todo:
                messagebox.showinfo("Nothing to write", "No ticked channels have backups to add.")
                return
            n = sum(len(r["picked"]) for r in todo)
            d = sum(len(r["drop"]) for r in todo)
            if not messagebox.askyesno("Write backups", f"Update {len(todo)} channels: add {n} backup streams"
                                       + (f", remove {d}" if d else "") + "?\n\n"
                                       "The main stream always stays first. The original stream lists are "
                                       "saved first, so this can be undone."):
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            undo_file = os.path.join(CONFIG_DIR, f"undo_{stamp}.json")
            write_private_json(undo_file, {"base": self.disp.base, "channels": {
                str(r["ch"]["id"]): {"name": r["name"], "streams": r["cur"]} for r in todo}})
            log.info("Saved original stream lists to %s", undo_file)

            def work():
                for k, r in enumerate(todo, 1):
                    self.say(f"Writing {k} of {len(todo)}: {r['name']}")
                    want = [s for s in r["cur"] if s not in r["drop"]] + [s for s in r["picked"] if s not in r["cur"]]
                    try:
                        got = self.disp.set_streams(r["ch"]["id"], want)
                        if got[:len(want)] == want:
                            r["state"], r["msg"], r["cur"], r["picked"], r["drop"] = "done", f"{len(want)} streams", got, [], []
                        else:
                            r["state"], r["msg"] = "error", "read back differently"
                    except RuntimeError as e:
                        r["state"], r["msg"] = "error", str(e)[:80]
                    log.info("Channel %s %s: %s", r["ch"]["id"], r["name"], r["state"])
                return undo_file

            def done(path):
                bad = sum(1 for r in todo if r["state"] == "error")
                self.refresh()   # also autosaves; written channels drop out of the session
                self.note(f"Done. {len(todo) - bad} channels updated, {bad} errors. "
                                        f"Undo file: {path}")
                if bad:
                    messagebox.showwarning("Some channels failed", f"{bad} channels were not updated. "
                                           "See the Status column and the log.")
            self.bg(work, done)

        def undo(self):
            if not self.disp:
                messagebox.showinfo("Not connected", "Press Connect & Match first.")
                return
            path = filedialog.askopenfilename(initialdir=CONFIG_DIR, title="Pick an undo file",
                                              filetypes=[("Undo files", "undo_*.json")])
            if not path:
                return
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            chans = data.get("channels", {})
            if not messagebox.askyesno("Undo", f"Put back the original stream lists of {len(chans)} channels "
                                       f"from {os.path.basename(path)}?"):
                return

            def work():
                bad = 0
                for k, (cid, v) in enumerate(chans.items(), 1):
                    self.say(f"Restoring {k} of {len(chans)}: {v.get('name')}")
                    try:
                        if self.disp.set_streams(int(cid), v["streams"])[:len(v["streams"])] != v["streams"]:
                            bad += 1
                    except RuntimeError:
                        bad += 1
                return bad

            self.bg(work, lambda bad: (self.status.config(
                text=f"Undo finished, {bad} errors. Press Connect & Match to reload."), None))

    root = tk.Tk()
    app = App(root)

    def on_close():
        try:
            app.save_session()
        finally:
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
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
