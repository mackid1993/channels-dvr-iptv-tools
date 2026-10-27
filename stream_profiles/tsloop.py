# ======================================================================
# SETTINGS: fill these in at the bottom of this block, then save.
#
# Use double quotes, never single quotes: a single quote breaks the
# profile.
#
# TO UPDATE THIS PROFILE LATER: keep everything down to the END OF
# SETTINGS line, delete everything below it, and paste the new CODE
# block from the README in its place. Your settings stay as they are.
#
# Leave a setting empty and the channel still plays; the features that
# need it switch off and the Dispatcharr log says why.
#
# TRANSCODE: re-encode every stream to one format, or pass it through.
#
#   TRANSCODE="off"       pass through untouched (default, no extra CPU/GPU)
#   TRANSCODE="1080p60"   1080p, 59.94 fps, 6 Mbps
#   TRANSCODE="1080p30"   1080p, 29.97 fps, 4.5 Mbps
#   TRANSCODE="720p60"    720p, 59.94 fps, 4.5 Mbps
#   TRANSCODE="720p30"    720p, 29.97 fps, 3 Mbps
#   50 Hz countries (Europe, UK, Australia and others): use
#   "1080p50", "1080p25", "720p50" or "720p25" instead.
#
# Which frame rate? Pick the highest one your channels use.
#   60: sports, news and most US broadcast channels. Smooth motion.
#       A 30 fps channel simply shows each frame twice, so nothing is lost.
#   30: fine if your channels are mostly movies and entertainment, and
#       easier on weak hardware, but 60 fps sports will look choppier.
#   50 or 25: the same choice for 50 Hz countries. Do not use 60 or 30
#       there, it makes motion judder.
# Which size? 1080p if you have a GPU. 720p for weak hardware or when
#   encoding on the CPU. The bitrates match what IPTV providers send
#   (about 3 to 6 Mbps), so quality holds without bloating the stream.
#
# Why transcode? Every channel leaves Dispatcharr in the same format, so
# a backup with a different frame rate never upsets the player, and the
# video plays at its real speed, except while the reserve refills: if a
# provider problem keeps the reserve below half for 60 seconds, video and
# audio play 3% slower together (pitch unchanged) until it is back to
# three quarters. Costs a few seconds more delay
# and some GPU time. Give the Dispatcharr container a GPU
# (/dev/dri for Intel or AMD, the NVIDIA runtime for NVIDIA); the README
# shows how. Without one it falls back to the CPU.
# ======================================================================
#
# ------------------------------------------------------------------------
# 1. DISPATCHARR_API_KEY (needed for failover, shared viewers, recordings)
#    In Dispatcharr open Users, edit your user, press Generate API Key,
#    and paste the key between the quotes. This is an admin-level key:
#    anyone who sees it can manage Dispatcharr. To replace it, generate a
#    new one in Users and paste it here.
#    Example: DISPATCHARR_API_KEY = "abc123..."
# ------------------------------------------------------------------------
DISPATCHARR_API_KEY = ""  # <- paste your Dispatcharr API key here
#
# ------------------------------------------------------------------------
# 2. CHANNELS_DVR (needed for shared viewers and recordings)
#    Your Channels DVR server as http://IP:PORT. Find the IP in Channels
#    DVR under Settings; the port is 8089 unless you changed it.
#    Example: CHANNELS_DVR = "http://192.168.1.50:8089"
# ------------------------------------------------------------------------
CHANNELS_DVR = ""  # <- your Channels DVR address, e.g. "http://192.168.1.50:8089"
#
# ------------------------------------------------------------------------
# 3. TRANSCODE (optional, see the TRANSCODE help above)
#    "off" passes streams through untouched. With a GPU, "1080p60" is
#    the usual choice. 50 Hz countries use "1080p50", "1080p25",
#    "720p50" or "720p25".
#    Example: TRANSCODE = "1080p60"
# ------------------------------------------------------------------------
TRANSCODE = "off"  # <- "off", "1080p60", "1080p30", "720p60" or "720p30"
#
# ------------------------------------------------------------------------
# 4. DISPATCHARR_URL (advanced, almost everyone leaves this empty)
#    The address the profile uses to reach the Dispatcharr API. The
#    profile runs inside Dispatcharr, so when this is empty it uses
#    http://127.0.0.1:9191, which is Dispatcharr itself. Only fill it in
#    if you changed the Dispatcharr web port, for example
#    "http://127.0.0.1:9292".
# ------------------------------------------------------------------------
DISPATCHARR_URL = ""  # <- leave empty unless you changed the Dispatcharr port
#
# ========================== END OF SETTINGS ===========================
TCP = {"1080p60": (1080, 59.94, "6M"), "1080p30": (1080, 29.97, "4.5M"), "720p60": (720, 59.94, "4.5M"), "720p30": (720, 29.97, "3M"), "1080p50": (1080, 50, "6M"), "1080p25": (1080, 25, "4.5M"), "720p50": (720, 50, "4.5M"), "720p25": (720, 25, "3M")}
TC_HEIGHT, TC_FPS, TC_VBITRATE = TCP.get(str(TRANSCODE).lower().strip(), (0, 0, ""))
TRANSCODE = TC_HEIGHT > 0
TC_LEAD = 8.0
import os, sys, time, json, signal, select, shutil, hashlib, threading, collections, subprocess as sp, re, urllib.parse as up
try:
    import requests
except ImportError:
    print("[tsloop stream] python requests module not found", file=sys.stderr)
    sys.exit(1)
src, ua = (sys.argv[1], sys.argv[2])
chid = sys.argv[3] if len(sys.argv) > 3 else ""
cdvr = CHANNELS_DVR.strip().rstrip("/")
if cdvr and "://" not in cdvr:
    cdvr = "http://" + cdvr
DURL = (DISPATCHARR_URL.strip() or "http://127.0.0.1:%s" % os.environ.get("DISPATCHARR_PORT", "9191")).rstrip("/")
if "://" not in DURL:
    DURL = "http://" + DURL
AKEY = DISPATCHARR_API_KEY.strip()

def dapi(path):
    if not AKEY:
        raise ValueError("DISPATCHARR_API_KEY is empty at the top of the profile")
    r = requests.get(DURL + path, headers={"X-API-Key": AKEY, "Accept": "application/json"}, timeout=5)
    if r.status_code in (401, 403):
        raise ValueError("Dispatcharr refused the API key (HTTP %d); check DISPATCHARR_API_KEY" % r.status_code)
    r.raise_for_status()
    return r.json()
FF = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
s = requests.Session()
s.headers["User-Agent"] = ua
mono = time.monotonic
BUSY = (429, 503, 509)
run = 1
WR = 95443.717688888
CL = 3.0
JB = 5.0
xb = 0.0
vpk = 1
vlj = 0.0
xo = 0.0
dmx = 0.0
STALL = 8.0
FAILT = 60.0
QUIET = 4.0
SIL = 6.0
RLOW = 4.0
RES = 20.0
QMAX = 60.0
le = {}
pd = 0.04
fq = collections.deque()
fql = threading.Lock()
shut = {}
wb = time.monotonic()
rb = wb
skf = False
rstk = False
sl0 = 0.0
sl1 = 0.0
PM = 45.0
avok = None
RAMP = 0.02
RXL = 1.5
RXR = 0.15
nrx = [0]
hc = {"r": 0, "x": 0, "f": 0, "k": 0.0}

def log(m):
    for a, b in (("stream error", "r"), ("restarting remuxer", "x"), ("switching to stream", "f")):
        if m.startswith(a) or "; " + a in m:
            hc[b] += 1
    try:
        sys.stderr.write("[tsloop stream] %s\n" % m)
        sys.stderr.flush()
    except Exception:
        pass

def dif(a, b):
    return (a - b + WR / 2) % WR - WR / 2
ff = None
off = 0.0
tout = 0.0
HW = None
STL = collections.deque()

def hwpick():
    global HW
    if HW:
        return HW
    hf = "/tmp/tsloop-hw.txt"
    try:
        if time.time() - os.path.getmtime(hf) < 3600:
            HW = open(hf).read().strip()
    except Exception:
        pass
    if not HW:
        HW = "cpu"
        t_ = ["-f", "lavfi", "-i", "color=s=320x240:d=0.3", "-frames:v", "5", "-f", "null", "-"]
        try:
            if os.path.exists("/dev/dri/renderD128") and sp.run([FF, "-hide_banner", "-loglevel", "error", "-vaapi_device", "/dev/dri/renderD128"] + t_[:4] + ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"] + t_[4:], capture_output=True, timeout=15).returncode == 0:
                HW = "vaapi"
            elif sp.run([FF, "-hide_banner", "-loglevel", "error"] + t_[:4] + ["-c:v", "h264_nvenc"] + t_[4:], capture_output=True, timeout=15).returncode == 0:
                HW = "nvenc"
        except Exception:
            pass
        try:
            open(hf, "w").write(HW)
        except Exception:
            pass
    log("transcoding with " + {"vaapi": "Intel/AMD graphics (VAAPI)", "nvenc": "NVIDIA graphics (NVENC)", "cpu": "the CPU (no hardware encoder found)"}[HW])
    return HW

def frs(f):
    for a, b in ((59.94, "60000/1001"), (29.97, "30000/1001"), (23.976, "24000/1001"), (47.952, "48000/1001")):
        if abs(f - a) < 0.01:
            return b
    return "%g" % f
SLOW = 1.03
SPD = 1.0
SPA = 1.0
ADJ = [0.0]
RP = [None, 0.0]
RQ = collections.deque(maxlen=6000)
lowt = None
ltk = -1000000000.0

def rstep(now, qs, lo, hi, win):
    global SPD, lowt, ltk
    while RQ and now - RQ[0][0] > win:
        RQ.popleft()
    qm = min((q for _, q in RQ))
    if SPD == 1.0:
        if qm >= lo:
            lowt = None
            return
        if lowt is None:
            lowt = now
        if now - lowt < 60 or now - ltk < 60:
            return
        SPD = SLOW
        lowt = None
        ltk = now
        RP[1] = now
        RP[0] = "reserve down to %.0fs; playing 3 percent slower, pitch unchanged, until it is back to %.0fs" % (qm, hi)
    elif qm >= hi:
        SPD = 1.0
        ltk = now
        RP[1] = now
        RP[0] = "reserve back to %.0fs; back to real speed" % qm

def kfo(d, a, b):
    for j in range(a, b - 187, 188):
        p = d[j:j + 188]
        if p[0] == 71 and p[1] & 64:
            q = pests(p)
            if q and 224 <= q[0] <= 239 and iskey(p):
                return j
    return None

def avlead(d, k):
    q = pests(d[k:k + 188])
    vk = q[1] if q else None
    if vk is None:
        return 0.0
    for j in range(k, min(len(d), k + 188 * 3000) - 187, 188):
        p = d[j:j + 188]
        if p[0] == 71 and p[1] & 64:
            q = pests(p)
            if q and (192 <= q[0] <= 223 or q[0] == 189):
                return min(max(dif(vk, q[1]), 0.0), 1.0)
    return 0.0

def ffcmd():
    b = [FF, "-hide_banner", "-loglevel", "error", "-fflags", "+discardcorrupt", "-probesize", "1000000", "-analyzeduration", "1000000"] + (["-probesize", "5000000", "-analyzeduration", "3000000"] if TRANSCODE else [])
    if not TRANSCODE:
        return b + ["-i", "pipe:0", "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-copyts"] + (["-output_ts_offset", "%.3f" % off] if off else []) + ["-f", "mpegts", "pipe:1"]
    h = hwpick()
    fr = frs(TC_FPS)
    g = str(int(round(2 * TC_FPS)))
    vf = "bwdif=mode=send_frame:deint=interlaced," + ("setpts=(PTS-STARTPTS)*%.4f+STARTPTS," % SPA if SPA != 1.0 else "") + "fps=%s,scale=-2:%d:flags=fast_bilinear" % (fr, TC_HEIGHT)
    rc = ["-b:v", TC_VBITRATE, "-maxrate", TC_VBITRATE, "-bufsize", TC_VBITRATE, "-g", g]
    if h == "vaapi":
        b += ["-vaapi_device", "/dev/dri/renderD128"]
        vc = ["-vf", vf + ",format=nv12,hwupload", "-c:v", "h264_vaapi"] + rc + ["-bf", "0"]
    elif h == "nvenc":
        vc = ["-vf", vf + ",format=yuv420p", "-c:v", "h264_nvenc", "-preset", "p4"] + rc + ["-bf", "0"]
    else:
        vc = ["-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "veryfast"] + rc
    af = "aresample=async=1000" + (",atempo=%.6f" % (1 / SPA) if SPA != 1.0 else "")
    return b + ["-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?"] + vc + ["-af", af, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-output_ts_offset", "%.3f" % (tout + 1.4 - ADJ[0]), "-f", "mpegts", "pipe:1"]

def rtgt():
    return RES

def remux(why=None):
    global ff, SPA
    if why:
        log("restarting remuxer: " + why)
    old = ff
    SPA = SPD
    RP[0] = None
    ff = sp.Popen(ffcmd(), stdin=sp.PIPE, stdout=sp.PIPE)
    ADJ[0] = 0.0
    with fql:
        fq.append(ff)
    if old:
        shut[old.pid] = mono()
        try:
            old.stdin.close()
        except Exception:
            pass
NUL = (b"G\x1f\xff\x10" + b"\xff" * 184) * 7

def pump():
    global skf
    ob = 0
    lw = mono()
    kas = None
    kag = False
    while True:
        with fql:
            p = fq[0] if fq else None
            more = len(fq) > 1
        if p is None:
            time.sleep(0.05)
            continue
        r, _, _ = select.select([p.stdout], [], [], 0.5)
        if skf and mono() - rb < 25:
            skf = False
        if not r and mono() - lw > 3 and (not ob % 188) and (mono() - wb < 10) and (mono() - rb < 25):
            if kas is None:
                kas = lw
                hc["ks"] = kas
                log("output: no video to send for 3s, sending keepalive packets so the stream stays open")
            try:
                os.write(1, NUL)
                lw = mono()
            except OSError:
                bye()
        elif not r and mono() - lw > 3 and (not kag) and ob % 188:
            kag = True
            log("output: stopped for %.0fs partway through a packet; no keepalive" % (mono() - lw))
        elif not r and mono() - lw > 3 and (not kag) and (mono() - wb >= 10 or mono() - rb >= 25):
            kag = True
            skf = mono() - rb >= 25
            log("output: stopped for %.0fs and the %s is stuck; no keepalive, so Dispatcharr can restart the stream" % (mono() - lw, "writer" if mono() - wb >= 10 else "reader"))
        if not r:
            if more and mono() - shut.get(p.pid, mono()) > 5:
                try:
                    p.kill()
                except Exception:
                    pass
            elif more or p.poll() is None:
                continue
        b = os.read(p.stdout.fileno(), 65536) if r else b""
        if not b:
            if more:
                with fql:
                    fq.popleft()
                try:
                    p.wait(1)
                except Exception:
                    pass
                shut.pop(p.pid, None)
                b = b"\xff" * ((188 - ob % 188) % 188)
            elif p.poll() is not None:
                time.sleep(0.05)
            if not b:
                continue
        try:
            while b:
                n = os.write(1, b)
                b = b[n:]
                ob += n
            lw = mono()
            kag = False
            if kas is not None:
                hc["k"] += lw - kas
                hc["ks"] = None
                log("output: video flowing again after %.1fs without video" % (lw - kas))
                kas = None
        except OSError:
            bye()

def gone():
    try:
        pl = select.poll()
        pl.register(1, select.POLLOUT)
        return any((e & (select.POLLERR | select.POLLHUP) for _, e in pl.poll(0)))
    except Exception:
        return False

def put(b):
    try:
        ff.stdin.write(b)
        ff.stdin.flush()
    except Exception as e:
        if gone():
            bye()
        remux("remuxer rejected data (%s)" % e)
        try:
            ff.stdin.write(b)
            ff.stdin.flush()
        except Exception:
            pass

def pests(p):
    if not p[1] & 64:
        return None
    af = p[3] >> 4 & 3
    if not af & 1:
        return None
    o = 5 + p[4] if af == 3 else 4
    if o + 14 > 188 or p[o:o + 3] != b"\x00\x00\x01":
        return None
    sid = p[o + 3]
    if not (192 <= sid <= 239 or sid == 189):
        return None
    fl = p[o + 7] & 192
    if not fl & 128:
        return None
    b = p[o + 14:o + 19] if fl == 192 and o + 19 <= 188 else p[o + 9:o + 14]
    return (sid, ((b[0] >> 1 & 7) << 30 | b[1] << 22 | b[2] >> 1 << 15 | b[3] << 7 | b[4] >> 1) / 90000.0)

def iskey(p):
    af = p[3] >> 4 & 3
    if af == 3 and p[4] and p[5] & 64:
        return True
    o = 5 + p[4] if af == 3 else 4
    b = bytes(p[o + 9 + p[o + 8]:])
    i = b.find(b"\x00\x00\x01")
    while 0 <= i < len(b) - 3:
        n = b[i + 3]
        if not n & 128 and (i + 4 < len(b) and b[i + 4] == 1 and (n >> 1 & 63 in (19, 20, 21, 32, 33)) or n & 31 in (5, 7)):
            return True
        i = b.find(b"\x00\x00\x01", i + 3)
    return False
st = "/tmp/tsloop-" + hashlib.md5(("ch" + chid if chid.isdigit() else src).encode()).hexdigest() + ".json"
last = {}
dl = {}
rcur = None
wlead = 0.0
prim = None
sl = threading.Lock()
resumed = False
rg = 0.0
try:
    j = json.load(open(st))
    if time.time() - j["t"] < 4 and j.get("src") == src:
        dl = {int(k): v for k, v in j["dl"].items()}
        last = dict(dl)
        off = j.get("off", 0.0)
        rcur = j.get("cur")
        prim = j.get("prim")
        xb = float(j.get("xb", 0.0))
        vpk = max(1, int(j.get("pk", 1)))
        rstk = bool(j.get("stk"))
        vlj = mono() if xb > JB else 0.0
        wlead = max(j.get("lead", 0.0) - (time.time() - j["t"]), 0.0)
        resumed = True
        rg = min(max(j.get("gage", 0.0), 0.0), FAILT)
        log("resuming after Dispatcharr restart")
except Exception:
    pass
cur = rcur or src

def save():
    try:
        with sl:
            with open(st + ".tmp", "w") as f:
                json.dump({"dl": {str(k): v for k, v in dl.items()}, "gage": mono() - got, "off": off, "cur": wcur, "src": src, "prim": prim, "lead": wlead, "xb": xb, "pk": vpk, "res": round(Qs[0], 2), "stk": skf, "to": tout, "t": time.time()}, f)
            os.replace(st + ".tmp", st)
    except Exception:
        pass
wcur = cur

def status(h0):
    try:
        fl_ = feeds(True) if chid.isdigit() else [src]
        ks = hc.get("ks")
        log("status: stream %s of %d, reserve %.0fs, client lead %.0fs, extra lead for shared viewers %.0fs, last 10 min: %d stream retries, %d remuxes, %d failovers, %.0fs keepalive%s" % (str(fl_.index(wcur) + 1) if wcur in fl_ else "?", len(fl_), Qs[0], max(wlead, 0.0), xb, hc["r"] - h0["r"], hc["x"] - h0["x"], hc["f"] - h0["f"], hc["k"] - h0["k"], ", keepalive running for %.0fs" % (mono() - ks) if ks else ""))
    except Exception:
        pass

def beat():
    n = 0
    h0 = dict(hc)
    while run:
        save()
        time.sleep(1)
        n += 1
        if n % 600 == 0:
            threading.Thread(target=status, args=(dict(h0),), daemon=True).start()
            h0 = dict(hc)
threading.Thread(target=beat, daemon=True).start()
XC = re.compile("/(live|hls)/([^/]+)/([^/]+)/")

def feeds(quiet=False):
    if not chid.isdigit():
        return [src]
    try:
        urls = [u for u in (x.get("url") for x in dapi("/api/channels/channels/%d/streams/" % int(chid))) if u]
    except Exception as x:
        if not quiet:
            log("could not read backup streams (%s)" % x)
        return [src]
    m = XC.search(src)
    if m:
        urls = [XC.sub("/%s/%s/%s/" % (mm.group(1), m.group(2), m.group(3)), u, count=1) if (mm := XC.search(u)) and up.urlsplit(u).netloc == up.urlsplit(src).netloc else u for u in urls]
    out = [src]
    for u in urls:
        if u not in out and XC.sub("/", u) != XC.sub("/", src):
            out.append(u)
    return out
stalled = False
pend = False
got = mono() - rg
ost = got
lv = got
gap = None
dlv = False
fbad = {}
lpr = -1000000000.0
prr = None
fst = mono()

def mark(u):
    b = fbad.get(u, [0, 0])
    fbad[u] = [mono() + min(60 * 2 ** b[1], 600), b[1] + 1]
    return fbad[u][0] - mono()

def probe(u):
    r = None
    try:
        r = s.get(u, stream=True, timeout=(2, 3), allow_redirects=True)
        r.raise_for_status()
        ct = (r.headers.get("Content-Type") or "").lower()
        if "html" in ct or "mpegurl" in ct or r.url.rsplit("/", 1)[-1].lower().startswith("black."):
            raise ValueError("not a live MPEG-TS stream")
        b = b""
        t = mono()
        r.it = r.iter_content(65536)
        for x in r.it:
            b += x
            if len(b) >= 188 * 9:
                if any((all((b[i + 188 * k] == 71 for k in range(8))) for i in range(188))):
                    r.pk = b
                    return r
                break
            if mono() - t > 3:
                break
    except Exception:
        pass
    if r is not None:
        r.close()
    return None

def handoff(why):
    global stalled, got, cur, pend, sticky, gap, dlv, lpr, prr, fst, ost
    fl = feeds()
    if len(fl) < 2:
        if not stalled:
            ost = got
            log(why + "; no backup stream on this channel, so staying on this feed")
        stalled = True
        got = mono()
        return
    if stalled and mono() - lpr < 30:
        got = mono()
        return
    lpr = mono()
    i0 = fl.index(cur) if cur in fl else 0
    nxt = None
    pt = mono()
    if not stalled:
        log("stream %d of %d failed; not using it again for %.0fs" % (i0 + 1, len(fl), mark(cur)))
    cand = [fl[(i0 + j) % len(fl)] for j in range(1, len(fl))]
    cand = [u for u in cand if fbad.get(u, [0])[0] <= mono()] or sorted(cand, key=lambda u: fbad.get(u, [0])[0])
    for u in cand:
        if mono() - pt > 6:
            break
        pr = probe(u)
        if pr is not None:
            nxt = u
            break
        log("backup stream %d of %d is not answering; not trying it again for %.0fs" % (fl.index(u) + 1, len(fl), mark(u)))
    if nxt is None:
        if not stalled:
            ost = got
            log(why + "; no backup stream is answering right now, so staying on this feed")
        stalled = True
        got = mono()
        return
    log(why + "; switching to stream %d of %d" % (fl.index(nxt) + 1, len(fl)))
    gap = mono() - ost if stalled else (gap or 0.0) + mono() - got if gap is not None or dlv else None
    cur = nxt
    prr = [nxt, pr]
    pend = True
    stalled = False
    got = mono()
    sticky = None
    dlv = False
    hc["e"] = 0
    fst = mono()

def bye():
    global run
    run = 0
    save()
    killall()
    os._exit(0)

def killall():
    k = fql.acquire(timeout=1)
    ps = list(fq)
    if k:
        fql.release()
    for p in ps:
        try:
            p.kill()
        except Exception:
            pass
for g in (15, 2, 1):
    signal.signal(g, lambda *a: (killall(), os._exit(0)))
Q = collections.deque()
Qs = [0.0]
ql = threading.Lock()
moff = off
qend = None
wput = mono()
if prim is not None and prim in dl:
    qend = dl[prim] + off

def enq(it):
    with ql:
        Q.append(it)
        Qs[0] += it[2]
        nrx[0] += it[0] == "remux"

def clcap():
    return max(CL, dmx - STALL - RLOW)

def watch():
    global xb, vpk, vlj, xo
    if not chid.isdigit():
        return
    e = os.environ.get
    w = requests.Session()
    base = cdvr
    if not base:
        log("viewer buffer: off (CHANNELS_DVR is empty at the top of the profile)")
        return
    dev = None
    num = None
    tmap = -1000000000.0
    seen = None
    t0 = mono()
    fails = 0
    said = set()
    tj = -1000000000.0
    jerr = set()
    jfail = False
    og = None
    jt = None
    JF = ("/data" if os.access("/data", os.W_OK) else "/tmp") + "/tsloop-jobs-%s.json" % chid
    try:
        for f_ in os.listdir(os.path.dirname(JF)):
            p_ = os.path.join(os.path.dirname(JF), f_)
            if f_.startswith(("hlsloop-jobs-", "tsloop-jobs-")) and time.time() - os.path.getmtime(p_) > 86400:
                os.remove(p_)
    except Exception:
        pass

    def say(k, m):
        if k not in said:
            said.add(k)
            log("viewer buffer: " + m)

    def ext(rj):
        nonlocal og, jfail
        jw = requests.Session()
        try:
            with ql:
                rv = min(Qs[0] + max(wlead, 0.0), rtgt() + 15.0)
            ad = min(int(rv + PM), 180)
            now_ = time.time()
            if og is None:
                try:
                    og = json.load(open(JF))
                    og = {a: b for a, b in og.items() if isinstance(b, list) and len(b) == 2}
                except Exception:
                    og = {}

            def wog():
                nonlocal og
                og = {a: b for a, b in og.items() if time.time() - b[1] < 86400}
                tf = JF + ".%d.tmp" % os.getpid()
                with open(tf, "w") as f:
                    json.dump(og, f)
                os.replace(tf, JF)
            for jb in jw.get(base + "/dvr/jobs", timeout=5).json() or []:
                jid = str(jb.get("ID") or "")
                if jid not in rj:
                    continue
                t1 = float(jb.get("Time") or 0)
                du = float(jb.get("Duration") or 0)
                if jid not in og:
                    og[jid] = [du, now_]
                    wog()
                nd = int(og[jid][0] + ad)
                if now_ < t1 + du - 30 and nd > du + 10:
                    try:
                        jb["Duration"] = nd
                        jw.put(base + "/dvr/jobs/" + up.quote(jid), json=jb, timeout=5).raise_for_status()
                        log("recording: %s on ch %s extended by %ds on top of its own padding (%ds for this stream running behind, %ds for the provider running behind the broadcast)" % (jb.get("Name") or jid, num, nd - int(og[jid][0]), int(rv), int(PM)))
                    except Exception as x:
                        if jid not in jerr:
                            jerr.add(jid)
                            log("recording: could not extend %s (%s)" % (jb.get("Name") or jid, str(x)[:80]))
            jfail = False
        except Exception as x:
            if not jfail:
                jfail = True
                log("recording: could not check or extend recordings (%s); will keep trying quietly" % str(x)[:80])
    try:
        uid = str(dapi("/api/channels/channels/%d/" % int(chid))["uuid"])
    except Exception as x:
        say("db", "off (could not read this channel from Dispatcharr: %s)" % x)
        return

    def nm(l):
        x = re.sub("\\\"[^\\\"]*\\\"", "", l)
        return x.split(",", 1)[1].strip() if "," in x else ""
    while run:
        try:
            if mono() - tmap > 300:
                tmap = mono()
                num = dev = None
                st = w.get(base + "/settings", timeout=5).json()
                for k, v in st.items():
                    mm = re.fullmatch("m3u\\.src\\.(.+)\\.url", k)
                    if not mm or "/output/m3u" not in str(v):
                        continue
                    t = w.get(v, timeout=20).text.splitlines()
                    ent = [(nm(t[i]), t[i + 1].strip()) for i in range(len(t) - 1) if t[i].startswith("#EXTINF")]
                    hit = [i for i, (n, x) in enumerate(ent) if uid in x]
                    if not hit:
                        continue
                    d = "M3U-" + mm.group(1)
                    for tr in range(6):
                        cl = w.get(base + "/devices/%s/channels" % d, timeout=10).json()
                        if len(cl) == len(ent) and all((str(c.get("GuideName", "")).strip() == n for c, (n, x) in zip(cl, ent))):
                            break
                        if tr == 0 and "post" + d not in said:
                            said.add("post" + d)
                            log("viewer buffer: Channels DVR source %s is out of date, refreshing it" % d)
                            w.post(base + "/providers/m3u/sources/%s/refresh" % up.quote(mm.group(1)), timeout=30)
                        time.sleep(1)
                    else:
                        say("drift" + d, "Channels DVR source %s still does not match the playlist after a refresh" % d)
                        continue
                    dev = d
                    num = str(cl[hit[0]]["GuideNumber"])
                    say("map" + dev + num, "this stream is Channels DVR %s ch %s" % (dev, num))
                    break
                if num is None:
                    say("nomap", "off for now (this channel is not in a Channels DVR source that matches the playlist)")
            if num is None:
                time.sleep(30)
                continue
            k = 0
            nr = 0
            rj = set()
            act = w.get(base + "/dvr/activity", timeout=3).json()
            for x in act.get("streams") or []:
                tu = x.get("tuner") or {}
                if tu.get("device_id") == dev and str(tu.get("channel")) == num:
                    k = max(k, int(tu.get("readers") or 1))
            for x in act.get("recordings") or []:
                tu = x.get("tuner") or {}
                if tu.get("device_id") == dev and str(tu.get("channel")) == num and (x.get("state") == "recording"):
                    nr += 1
                    rj.add(str(x.get("job_id") or ""))
            rj.discard("")
            if rj and mono() - tj > 30 and (not (jt and jt.is_alive())):
                tj = mono()
                jt = threading.Thread(target=ext, args=(set(rj),), daemon=True)
                jt.start()
            if k or nr:
                seen = mono()
            elif seen is None and mono() - t0 > 90:
                say("unseen", "waiting (ch %s not playing in Channels DVR yet)" % num)
                time.sleep(30)
                continue
            if k and k > vpk:
                room = min(max(clcap() - CL - xb, 0.0), max(Qs[0] - STALL - RLOW, 0.0))
                add = min(JB * (k - vpk), room)
                xb += add
                vlj = mono()
                xo = JB * (k - vpk) - add
                log("viewer buffer: %d viewers on Channels DVR ch %s, sent the new viewer a %.0fs head start (lead now %.0fs, reserve %.0fs)" % (k, num, add, CL + xb, max(Qs[0] - add, 0.0)) + ("" if xo <= 0 else ", %.0fs more as the reserve allows" % xo))
            elif xo > 0:
                if mono() - vlj > 30 or not k or k < vpk:
                    log("viewer buffer: %.0fs of the head start dropped (reserve did not grow in time or the viewer left)" % xo)
                    xo = 0.0
                else:
                    add = min(xo, max(clcap() - CL - xb, 0.0), max(Qs[0] - STALL - RLOW, 0.0))
                    xb += add
                    xo -= add
                    if xo <= 0.01:
                        xo = 0.0
                        log("viewer buffer: rest of the head start sent (lead now %.0fs)" % (CL + xb))
            if k:
                vpk = k
            if xb > JB and mono() - vlj > 120 and (Qs[0] < RES):
                xb = max(JB, xb - 0.02)
                if xb == JB:
                    log("viewer buffer: reserve rebuilt to %.0fs" % Qs[0])
            fails = 0
        except Exception as x:
            fails += 1
            if fails == 3:
                log("viewer buffer: Channels DVR not reachable (%s)" % str(x)[:80])
            if not base and mono() - t0 > 120:
                time.sleep(28)
        time.sleep(2)

def writer():
    global dl, off, wlead, wcur, wput, wb, dmx, tout
    lead = wlead
    t = mono()
    sv = 0.0
    remp = False
    dh = collections.deque()
    dt = 0.0
    ce = max(CL, min(wlead, CL + xb))
    tc = mono()
    rxt = -1000000000.0
    pxb = xb
    while run:
        try:
            now = mono()
            wb = now
            lead = max(lead - (now - t), 0.0)
            t = now
            wlead = lead
            if ff.poll() is not None:
                remux("remuxer exited")
                time.sleep(1 if mono() - rxt < 10 else 0.1)
                rxt = mono()
                continue
            with ql:
                it = Q[0] if Q else None
                qs = Qs[0]
                if TRANSCODE:
                    RQ.append((now, qs))
                if it is None:
                    Qs[0] = 0.0
            if now - dt >= 1:
                dt = now
                dh.append((now, qs + max(lead, 0.0)))
                while dh[0][0] < now - 120:
                    dh.popleft()
                dmx = max((v for _, v in dh))
            if it is None:
                if not remp and hc.get("s") and (lead <= 0.2) and (dmx >= RLOW + 3):
                    remp = True
                    log("reserve empty: nothing queued and the client lead is used up, playback will pause until video arrives")
                time.sleep(0.05)
                continue
            if remp and qs >= RLOW:
                remp = False
                log("reserve refilled to %.0fs" % qs)
            if it[0] == "remux":
                with ql:
                    Q.popleft()
                    nrx[0] -= 1
                off = it[3]
                remux(it[1])
                continue
            d = it[1]
            ix = it[5]
            i = it[6]
            if i < len(ix):
                en, ca, sn = ix[i]
            else:
                en, ca, sn = (len(d), it[2], it[3])
            cd = max(ca - it[8], 0.0)
            rx = nrx[0] > 0 and qs > 4.0
            ce = min(max(min(CL + xb + (RXL if rx else 0.0), clcap(), max(ce, lead + qs - STALL - RLOW)), ce - 0.01 * (now - tc)), ce + (RXR if rx else RAMP) * (now - tc) + max(xb - pxb, 0.0))
            tc = now
            pxb = xb
            if cd > 0 and lead >= ce and (qs <= QMAX + 30):
                time.sleep(0.02)
                continue
            if TRANSCODE and i == 0 and (mono() - fst > 90):
                rstep(now, qs, RES * 0.5, RES * 0.8, 3.0)
            if RP[0] and it[7] < en:
                k_ = kfo(d, it[7], en) if now - RP[1] < 10 else it[7]
                if k_ is not None:
                    if k_ > it[7]:
                        put(d[it[7]:k_])
                    it[7] = k_
                    w_ = RP[0]
                    RP[0] = None
                    ADJ[0] = avlead(d, k_)
                    remux(w_)
            put(d[it[7]:en])
            it[6] = i + 1
            it[7] = en
            it[8] = max(ca, it[8])
            wput = mono()
            lead = min(lead + cd * SPA, ce + 10.0)
            tout += cd * SPA
            dl = sn
            wcur = it[4]
            with ql:
                Qs[0] -= cd
                if en >= len(d):
                    Q.popleft()
                    Qs[0] -= it[2] - it[8]
            if en >= len(d):
                dl = it[3]
            if en >= len(d) or mono() - sv > 0.2:
                save()
                sv = mono()
        except Exception as e:
            log("writer error (%s)" % e)
            time.sleep(1)
try:
    j_ = json.load(open(st))
    if time.time() - j_["t"] < 4 and j_.get("src") == src:
        tout = float(j_.get("to", 0.0))
except Exception:
    pass
remux()
threading.Thread(target=pump, daemon=True).start()
threading.Thread(target=writer, daemon=True).start()
threading.Thread(target=watch, daemon=True).start()
err = 0
e0 = None
ph = 0
short = 0
lrf = -1000000000.0
warned = set()
cc = {}
sticky = None
usest = False

def budget():
    now = mono()
    qs = Qs[0]
    return max(qs + min(max(wlead, 0.0), SIL - (now - wput if qs <= 0 else 0)) - 1.0, 3.0)

def conn(r):
    global last, moff, qend, got, prim, pend, resumed, stalled, ph, short, le, lrf, avok
    rem = b""
    pv = True
    rest = []
    mh = collections.deque()
    ta = 0.0
    lc = None
    mode = None
    pre = []
    sync = set()
    t0 = mono()
    media = 0.0
    hold = {}
    seen = set()
    exact = set()
    kwait = set()
    gate = None
    kt = mono()
    jump = None
    nowait = False
    beh = None
    bs = []
    hw = None
    tw = None
    hs = []
    fin = False
    c0 = mono()
    lb = {}
    lmin = None

    def emit(pk):
        global last, qend, got, prim, stalled, ph, gap, dlv, lv
        nonlocal media, gate, kt, jump, beh, pv, rest
        out = bytearray()
        adv = 0.0
        ix = []

        def fix(p):
            pid = (p[1] & 31) << 8 | p[2]
            if p[3] & 16:
                c = cc[pid] + 1 & 15 if pid in cc else p[3] & 15
                cc[pid] = c
                p[3] = p[3] & 240 | c
            elif pid in cc:
                p[3] = p[3] & 240 | cc[pid]
            return p

        def commit(h):
            global qend, prim, pd
            nonlocal adv, jump
            pid, sid, ts, buf = h
            if prim is None and 224 <= sid <= 239:
                prim = pid
            if pid == prim:
                if pid in last:
                    a = dif(ts, last[pid])
                    if not -1 < a < 5:
                        jump = a
                        return
                    if 0 < a < 5:
                        adv += a
                        pd = a
                    if a > 0:
                        last[pid] = ts
                else:
                    last[pid] = ts
                qend = last[pid] + moff
            else:
                if prim in last and dif(ts, last[prim]) >= 5:
                    return False
                e = le.get(pid, last.get(pid))
                if e is not None and -2 < dif(ts, e) < -0.001:
                    return False
                if pid not in last or dif(ts, last[pid]) > 0:
                    last[pid] = ts
            for i in range(0, len(buf), 188):
                out.extend(fix(bytearray(buf[i:i + 188])))
            if pid == prim:
                ix.append((len(out), adv, dict(last)))
            return True
        for n_, p in enumerate(pk):
            pid = (p[1] & 31) << 8 | p[2]
            if pid == 8191:
                continue
            if not p[3] & 16:
                if pid in hold:
                    if hold[pid] is not None:
                        hold[pid][3] += p
                else:
                    out += fix(p)
                continue
            if p[1] & 64:
                seen.add(pid)
            elif pid not in seen:
                continue
            x = pests(p)
            if x:
                h = hold.pop(pid, None)
                if h and commit(h) and (pid != prim):
                    le[pid] = x[1]
                if jump is not None and -1 < dif(x[1], last[pid]) < 5:
                    jump = None
                if jump is not None:
                    rest = ([bytearray(h[3][i:i + 188]) for i in range(0, len(h[3]), 188)] if h else []) + pk[n_:]
                    break
                sid, ts = x
                if pid in sync:
                    d = dif(ts, last.get(pid, ts - 1))
                    if d <= 0.001:
                        if pid == prim:
                            beh = -d
                        if d >= -0.001:
                            exact.add(pid)
                        hold[pid] = None
                        continue
                    sync.discard(pid)
                    if pid == prim and pid not in exact and pv:
                        kwait.add(pid)
                        kt = mono()
                        log("reconnect did not line up with what was sent; waiting for a keyframe")
                if pid in kwait:
                    if not iskey(p) and mono() - kt < 3:
                        hold[pid] = None
                        continue
                    kwait.discard(pid)
                if gate is not None and pid != prim and (dif(ts, gate) < 0):
                    hold[pid] = None
                    continue
                hold[pid] = [pid, sid, ts, bytearray(p)]
            elif pid in hold:
                if hold[pid] is not None:
                    hold[pid][3] += p
            else:
                out += fix(p)
        if adv > 0 and (not hc.get("s")):
            hc["s"] = 1
            log("input: first video received from %s, building the reserve" % up.urlsplit(cur).netloc)
        if adv > 0 and hc.get("e"):
            log("input: video back after %d reconnects (reserve %.1fs)" % (hc["e"], Qs[0]))
            hc["e"] = 0
        if adv > 0:
            got = mono()
            lv = got
            media += adv
            stalled = False
            ph = 0
            gap = None
            dlv = True
            if cur in fbad and got - fst > 120:
                fbad.pop(cur, None)
        if out:
            enq(["seg", bytes(out), adv, dict(last), cur, ix, 0, 0, 0.0])
        if jump is not None and jump < 0:
            raise ValueError("timeline jumped %.0fs inside the stream" % jump)

    def rdch():
        nonlocal fin
        it = getattr(r, "it", None) or r.iter_content(65536)
        if getattr(r, "pk", b""):
            yield r.pk
        while True:
            try:
                c = next(it)
            except StopIteration:
                if mode is None and pre and pend:
                    fin = True
                    yield b""
                return
            except Exception as x:
                if mode is None and pre and pend:
                    fin = True
                    yield b""
                raise x
            yield c
    global rb, rto
    for chunk in rdch():
        rb = mono()
        ta += min(rb - lc, 2.0) if lc else 0.0
        lc = rb
        if not run or gone():
            break
        pw = mode is None and pre and pend
        nr = min(rto, 2.5) if pw else max(4.0, min(STALL, Qs[0] - RLOW))
        if nr > rto + 0.5 or (pw and nr < rto):
            rto = nr
            try:
                r.raw._fp.fp.raw._sock.settimeout(nr)
            except Exception:
                pass
        d = rem + chunk
        if d and d[0] != 71:
            i = d.find(b"G")
            while i >= 0 and (not (i + 188 < len(d) and d[i + 188] == 71)):
                i = d.find(b"G", i + 1)
            d = d[i:] if i >= 0 else b""
        k = len(d) // 188 * 188
        rem = d[k:]
        pk = [bytearray(d[i:i + 188]) for i in range(0, k, 188)]
        if mode is None:
            pre += pk
            dec = None
            for p in pre:
                x = pests(p)
                if x and (224 <= x[0] <= 239 or (prim is not None and (p[1] & 31) << 8 | p[2] == prim)):
                    dec = ((p[1] & 31) << 8 | p[2], x[1])
                    break
            if dec is None and (len(pre) > 8000 or (prim is None and mono() - t0 > 3)):
                for au in (1, 0):
                    for p in pre:
                        x = pests(p)
                        if x and (192 <= x[0] <= 223 or not au):
                            dec = ((p[1] & 31) << 8 | p[2], x[1])
                            break
                    if dec:
                        break
            if dec is None:
                if mono() - t0 > STALL + 2:
                    raise ValueError("no decodable video timestamps")
                continue
            dp, ts = dec
            va = [x for x in (pests(p) for p in pre) if x]
            fv_ = next((x[1] for x in va if 224 <= x[0] <= 239), None)
            fa_ = next((x[1] for x in va if 192 <= x[0] <= 223), None)
            if fv_ is not None and fa_ is not None and (3 < abs(dif(fv_, fa_)) < WR - 60) and (not (stalled and mono() - lv > 30)) and (avok != cur):
                raise ValueError("audio and video are %.0fs apart in this feed" % abs(dif(fv_, fa_)))
            if fv_ is not None and fa_ is not None and (3 < abs(dif(fv_, fa_)) < WR - 60):
                avok = cur
            elif fv_ is not None and fa_ is not None:
                avok = None
            if prim is None or dp != prim:
                prim = dp
            pv = any((pests(p) and 224 <= pests(p)[0] <= 239 for p in pre if (p[1] & 31) << 8 | p[2] == prim))
            ref = last.get(prim)
            if ref is not None and (not pend) and (not nowait) and (-120 <= dif(ts, ref) < -1):
                for p in pk if hw is not None else pre:
                    x = pests(p) if (p[1] & 31) << 8 | p[2] == prim else None
                    if x and (hw is None or dif(x[1], hw) > 0):
                        hw = x[1]
                if tw is None:
                    tw = mono()
                if dif(hw, ref) < -1 and mono() - tw < 1.5:
                    continue
                if -dif(hw, ref) > budget():
                    nowait = True
                    log("reconnected %.1fs behind what was sent and the provider burst only reaches %.1fs of it; replaying instead of stalling" % (-dif(ts, ref), dif(hw, ts)))
            if pend and gap is not None and (qend is not None):
                for p in pk if hw is not None else pre:
                    x = pests(p) if (p[1] & 31) << 8 | p[2] == prim else None
                    if x and (hw is None or dif(x[1], hw) > 0):
                        hw = x[1]
                if tw is None:
                    tw = mono()
                hs.append((mono(), hw))
                hs = [x for x in hs if x[0] > mono() - 0.5]
                if not fin and mono() - tw < 2.0 and (mono() - tw < 0.4 or dif(hw, hs[0][1]) > 0.8 * (mono() - hs[0][0]) + 0.3):
                    continue
            if ref is not None and (not pend) and (not nowait) and (-120 <= dif(ts, ref) <= 1.0):
                mode = "dedupe"
                sync = set(last)
                beh = max(-dif(ts, ref), 0.0)
                bs = []
                if resumed:
                    log("resuming after Dispatcharr restart, %.1fs of video already sent" % -min(dif(ts, ref), 0))
                elif dif(ts, ref) < 0:
                    log("reconnected, dropping %.1fs already sent (reserve %.1fs)" % (-dif(ts, ref), Qs[0]))
            else:
                ks = [i for i, p in enumerate(pre) if (p[1] & 31) << 8 | p[2] == prim and pests(p) and (iskey(p) or not pv)]
                ki = ks[0] if ks else None
                if ki is None:
                    if not fin and len(pre) < 30000 and (mono() - t0 < STALL + 2):
                        continue
                    ki = 0
                tt = None
                if nowait and hw is not None:
                    tt = hw - max(min(RES - Qs[0] - max(wlead, 0.0), 10.0), 3.0)
                elif pend and gap is not None and (hw is not None):
                    tt = hw - gap - (mono() - got) - 0.5
                if tt is not None and len(ks) > 1:
                    ki = next((i for i in reversed(ks) if dif(pests(pre[i])[1], tt) <= 0), ks[0]) if pend else next((i for i in ks if dif(pests(pre[i])[1], tt) >= 0), ks[-1])
                    pp = {(p[1] & 31) << 8 | p[2] for p in pre if pests(p)}
                    pre = [p for p in pre[:ki] if (p[1] & 31) << 8 | p[2] not in pp] + pre[ki:]
                    ki = next((i for i, p in enumerate(pre) if (p[1] & 31) << 8 | p[2] == prim and pests(p)))
                ts = pests(pre[ki])[1]
                gate = ts
                kt = mono()
                mode = "splice"
                if pv:
                    kwait.add(prim)
                if qend is not None:
                    om = moff
                    moff = qend + max(0.1, pd + 0.02) - ts
                    why = "joining the backup stream (offset %.3fs, %.1fs behind its live edge)" % (qend + max(0.1, pd + 0.02) - ts, dif(hw, ts) if hw is not None else -1) if pend else "replaying %.0fs to keep playback going, keeping timestamps continuous" % -dif(ts, ref) if nowait and ref is not None else "provider skipped %.0fs, keeping timestamps continuous" % dif(ts, ref) if ref is not None and 0 < dif(ts, ref) < 600 else "stream timeline changed, keeping timestamps continuous"
                    enq(["remux", why, 0, moff])
                    sh = om - moff
                    le = {k: v + sh for k, v in le.items() if k != prim}
                    last = {}
                else:
                    last = {}
                    le = {}
            pend = False
            resumed = False
            pk = pre
            pre = []
        emit(pk)
        if mode is not None and rto < 4.0:
            rto = max(4.0, min(STALL, Qs[0] - RLOW))
            try:
                r.raw._fp.fp.raw._sock.settimeout(rto)
            except Exception:
                pass
        if jump is not None:
            log("timeline jumped %.0fs inside the stream, re-syncing on this connection" % jump)
            jump = None
            mode = None
            nowait = False
            sync = set()
            exact = set()
            kwait = set()
            hold = {}
            seen = set()
            gate = None
            t0 = mono()
            lb = {}
            lmin = None
            pre = rest
            rest = []
            pk = []
            if pre:
                rem = b"".join((bytes(p) for p in pre)) + rem
                pre = []
            continue
        if Qs[0] > QMAX:
            if QMAX not in warned:
                warned.add(QMAX)
                log("input stream is arriving faster than real time (more than %.0fs queued); reading it at playback speed" % QMAX)
            while run and Qs[0] > QMAX - 5 and (not gone()):
                rb = mono()
                time.sleep(0.2)
            got = mono()
        dr = mode == "dedupe" and prim in sync and (beh is not None)
        if dr:
            now = mono()
            bs.append((now, beh))
            bs = [x for x in bs if x[0] > now - 1.5]
            o = bs[0]
            if now - o[0] >= 0.8 and beh / max((o[1] - beh) / (now - o[0]), 1.0) > budget():
                log("still %.1fs behind what was sent and arriving at real time; waiting would stall playback" % beh)
                mode = None
                nowait = True
                sync = set()
                exact = set()
                kwait = set()
                hold = {}
                seen = set()
                gate = None
                t0 = mono()
                continue
        if mono() - got > STALL + 2 and mono() - t0 > STALL + 2 and (not dr):
            raise ValueError("no new video for %ds" % (mono() - got))
        if media > 0 and mode is not None and (not pend):
            nm = mono()
            lg = nm - c0 - media
            bk = int(nm / 10)
            lb[bk] = min(lb.get(bk, lg), lg)
            lmin = lg if lmin is None else min(lmin, lg)
            if len(lb) > 7:
                lb.pop(min(lb))
            cu = Qs[0] + max(wlead - CL, 0.0)
            if cu < RES / 2 and nm - c0 > 180 and (nm - lrf > 300 or cu < RLOW + 2) and (not stalled) and (len(lb) >= 6):
                dg = min((v for k_, v in lb.items() if k_ >= bk - 5)) - lmin
                if dg > 4:
                    lrf = nm
                    raise ValueError("provider fell %.0fs behind live on this connection and the reserve is down to %.1fs; reconnecting for a fresh burst" % (dg, cu))
        if media > 0:
            mh.append((mono(), media, ta))
            while mh[0][0] < mono() - 20:
                mh.popleft()
            if Qs[0] < RLOW and mono() - mh[0][0] > 19 and (ta - mh[0][2] > 15) and (media - mh[0][1] < 0.85 * (ta - mh[0][2])):
                raise ValueError("feed slower than real time (%.1fs of video in %.0fs)" % (media - mh[0][1], ta - mh[0][2]))
    return (mono() - t0, media)
if rstk:
    handoff("the previous process got stuck reading this feed")
while not gone():
    rb = mono()
    try:
        url = sticky if usest and sticky else cur
        usest = False
        rto = STALL if stalled and Qs[0] <= RLOW or Qs[0] <= 0 else max(4.0, min(STALL, Qs[0] - RLOW))
        if prr and prr[0] == url:
            r = prr[1]
            prr = None
            try:
                r.raw._fp.fp.raw._sock.settimeout(rto)
            except Exception:
                pass
        else:
            if prr:
                prr[1].close()
                prr = None
            try:
                r = s.get(url, stream=True, timeout=(3, rto), allow_redirects=True)
                r.raise_for_status()
            except Exception as x:
                if url != cur:
                    sticky = None
                rs = getattr(x, "response", None)
                if rs is not None:
                    rs.close()
                raise
        ct = (r.headers.get("Content-Type") or "").lower()
        if "mpegurl" in ct:
            r.close()
            if cur not in warned:
                log("this stream URL is HLS; use the HLS profile for it")
                warned.add(cur)
            raise ValueError("stream URL is HLS, not MPEG-TS")
        if r.url.rsplit("/", 1)[-1].lower().startswith("black."):
            r.close()
            ph += 1
            handoff("provider is serving an offline placeholder")
            dl_ = 5 if stalled else 0
            rb = mono() + dl_
            time.sleep(dl_)
            continue
        if err > 0:
            hc["e"] = hc.get("e", 0) + err
        err = 0
        e0 = None
        sticky = r.url if url == cur and r.url != cur else None
        try:
            dur, media = conn(r)
        finally:
            r.close()
        if gone():
            break
        if media < 15 and dur < 60:
            short += 1
        else:
            short = 0
        if short >= 3:
            short = 0
            ph += 1
            handoff("stream keeps ending after a few seconds (offline placeholder?)")
            dl_ = 5 if stalled else 0
            rb = mono() + dl_
            time.sleep(dl_)
            continue
        raise ValueError("stream ended")
    except Exception as e:
        if gone():
            break
        usest = sticky is not None and err == 0
        if e0 is None:
            e0 = mono()
        if "fresh burst" in str(e):
            log("input: " + str(e))
            e0 = None
            rb = mono()
            time.sleep(0.2)
            continue
        ng = mono() - got
        if "slower than real time" in str(e):
            if not sl0 or mono() - sl1 > 120:
                sl0 = mono()
            sl1 = mono()
        if ng > FAILT or (ng > STALL and Qs[0] + max(wlead, 0.0) < RLOW + 3.0) or (sl0 and mono() - sl0 > 300 and (mono() - sl1 < 120)):
            if sl0 and mono() - sl0 > 300:
                e = ValueError("feed has been slower than real time for 5 minutes")
                gap = max(gap or 0.0, RES) if pend else gap
            sl0 = 0.0
            ph += 1
            handoff("no new video for %ds (%s)" % (mono() - got, str(e)[:80]))
            if not stalled:
                err = 0
                e0 = None
                continue
        err += 1
        rs = getattr(e, "response", None)
        c = getattr(rs, "status_code", 0)
        ra = rs.headers.get("Retry-After", "") if rs is not None else ""
        dl_ = min(int(ra), 30) if ra.isdigit() else min(2 * err, 10) if c in BUSY else 0.2 if err == 1 else min(err - 1, 5)
        if stalled and Qs[0] <= RLOW and (mono() - lv > FAILT):
            dl_ = max(dl_, 1.0 if "slower than real time" in str(e) else 5.0)
        if not stalled or Qs[0] > RLOW:
            dl_ = max(0.2, min(dl_, max(FAILT - ng, 0.2), max(STALL - ng, Qs[0] - RLOW)))
        if not stalled and mono() - wput > QUIET:
            dl_ = min(dl_, 2)
        if ph > 1:
            dl_ = max(dl_, min(ph - 1, 10))
        log("stream error (%s); reconnect %d in %.1fs" % (str(e)[:120], err, dl_))
        rb = mono() + dl_
        time.sleep(dl_)
bye()
