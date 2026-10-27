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
# provider problem keeps the reserve below half for 75 seconds, video and
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
TC_LEAD = 4.0
import os, sys, time, json, signal, re, select, shutil, hashlib, threading, collections, subprocess as sp, urllib.parse as up
try:
    import requests
except ImportError:
    print("[hlsloop] python requests module not found", file=sys.stderr)
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
HSX = re.compile("(/live/[^/?]+/[^/?]+/[0-9]+)\\.ts(?=$|\\?)")

def hsu(u):
    return HSX.sub("\\1.m3u8", u or "", count=1)
if HSX.search(src):
    src = hsu(src)
    print("[hlsloop stream] input: provider stream URL is MPEG-TS; using its HLS form", file=sys.stderr, flush=True)
FF = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
s = requests.Session()
s.headers["User-Agent"] = ua
mono = time.monotonic
BUSY = (429, 503, 509)
AV = {1, 2, 3, 4, 6, 15, 17, 27, 36, 129, 135}
run = 1
CL = 4.0
JB = 5.0
xb = 0.0
pk = 1
lj = 0.0
RF = 30.0
WR = 95443.717688888
fq = collections.deque()
fql = threading.Lock()
shut = {}
wb = time.monotonic()
rb = time.monotonic()
RBT = 45.0
skf = False
rstk = False
DEADT = 60.0
PM = 45.0
LG = threading.Lock()
CNT = collections.Counter()
CK = threading.Lock()
ns = 0

def inc(k, v=1):
    with CK:
        CNT[k] += v

def log(m):
    try:
        with LG:
            sys.stderr.write("[hlsloop stream] " + str(m) + "\n")
            sys.stderr.flush()
    except Exception:
        pass
ff = None
sig = None
cmap = None
off = 0.0
tout = 0.0
HW = None
STL = collections.deque()

def hwpick():
    global HW
    if HW:
        return HW
    hf = "/tmp/hlsloop-hw.txt"
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
RP = [None, 0.0]
ADJ = [0.0]
OFS = [0.0]
EI = {}
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
        if now - lowt < 75 or now - ltk < 60:
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

def dif(a, b):
    return (a - b + WR / 2) % WR - WR / 2

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

def note(b):
    e = EI.get(ff.pid) if ff else None
    if not e or (e[3] is not None and e[4] is not None):
        return
    for j in range(0, len(b) - 187, 188):
        p = b[j:j + 188]
        if p[0] != 71 or not p[1] & 64:
            continue
        q = pests(p)
        if not q:
            continue
        if e[2] is None or dif(q[1], e[2]) < 0:
            e[2] = q[1]
        if 224 <= q[0] <= 239:
            if e[3] is None and iskey(p):
                e[3] = q[1]
        elif e[4] is None:
            e[4] = q[1]
        if e[3] is not None and e[4] is not None:
            return

def eofs(vk, av):
    e = EI.get(ff.pid) if ff else None
    if not e or e[2] is None or e[3] is None:
        return None
    return e[0] + dif(e[3], e[2]) + dif(vk, e[3]) * e[1] - av

def ffcmd():
    b = [FF, "-hide_banner", "-loglevel", "error", "-fflags", "+discardcorrupt", "-probesize", "1000000", "-analyzeduration", "1000000"]
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
    return b + ["-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?"] + vc + ["-af", af, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-output_ts_offset", "%.3f" % OFS[0], "-f", "mpegts", "pipe:1"]

def rtgt():
    return dep or 3 * tgt

def remux(why=None, ofs=None):
    global ff, SPA
    if why:
        log("restarting remuxer: " + why)
    old = ff
    SPA = SPD
    RP[0] = None
    OFS[0] = ofs if ofs is not None else tout + 1.4 - ADJ[0]
    if old:
        EI.pop(old.pid, None)
    ff = sp.Popen(ffcmd(), stdin=sp.PIPE, stdout=sp.PIPE)
    ADJ[0] = 0.0
    EI[ff.pid] = [OFS[0], SPA, None, None, None]
    with fql:
        fq.append(ff)
    if old:
        shut[old.pid] = mono()
        try:
            old.stdin.close()
        except Exception:
            pass
OWT = [mono()]
NUL = (b"G\x1f\xff\x10" + b"\xff" * 184) * 7

def pump():
    global skf
    ob = 0
    lw = mono()
    ka = None
    stk = False
    while True:
        with fql:
            p = fq[0] if fq else None
            more = len(fq) > 1
        if p is None:
            time.sleep(0.05)
            continue
        r, _, _ = select.select([p.stdout], [], [], 0.25)
        if not r and mono() - rb >= RBT and (not stk):
            stk = True
            skf = True
            log("input: fetch loop stuck for %.0fs, stopping keepalive so Dispatcharr restarts the stream" % (mono() - rb))
        elif stk and mono() - rb < RBT:
            stk = False
            skf = False
        if not r and mono() - lw > 2 and (not ob % 188) and (mono() - wb < 10) and (mono() - rb < RBT):
            try:
                os.write(1, NUL)
                lw2 = lw
                lw = mono()
            except OSError:
                bye()
            if ka is None:
                ka = lw2
                log("output: no video to send for 2s, sending keepalive packets so the stream stays open")
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
                OWT[0] = mono()
            if ka is not None:
                inc("ka", mono() - ka)
                log("output: video flowing again after %.1fs without video" % (mono() - ka))
                ka = None
            lw = mono()
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
    if TRANSCODE:
        note(b)
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

def attrs(x):
    return {k: v.strip(chr(34)) for k, v in re.findall("([A-Z0-9-]+)=(\\\"[^\\\"]*\\\"|[^,]*)", x.split(":", 1)[1])}

def tsig(d):
    try:
        if not d or d[0] != 71:
            return None
        pmt = None
        for i in range(0, min(len(d), 188 * 600), 188):
            p = d[i:i + 188]
            if len(p) < 188 or p[0] != 71:
                return None
            if not p[1] & 64:
                continue
            pid = (p[1] & 31) << 8 | p[2]
            af = p[3] >> 4 & 3
            if af == 2:
                continue
            o = 5 + p[4] if af == 3 else 4
            o += 1 + p[o]
            sl = (p[o + 1] & 15) << 8 | p[o + 2]
            end = min(o + 3 + sl - 4, 188)
            if pid == 0 and p[o] == 0:
                for j in range(o + 8, end, 4):
                    if p[j] << 8 | p[j + 1]:
                        pmt = (p[j + 2] & 31) << 8 | p[j + 3]
                        break
            elif pid == pmt and p[o] == 2:
                j = o + 12 + ((p[o + 10] & 15) << 8 | p[o + 11])
                es = []
                while j + 5 <= end:
                    es.append((p[j], (p[j + 1] & 31) << 8 | p[j + 2]))
                    j += 5 + ((p[j + 3] & 15) << 8 | p[j + 4])
                return tuple(sorted((e for e in es if e[0] in AV)))
    except Exception:
        pass
    return None
PSK = None
nsk = 0

def pts0(d):
    global PSK
    fb = None
    fv = None
    PSK = None
    try:
        for i in range(0, min(len(d), 188 * 2000), 188):
            p = d[i:i + 188]
            if len(p) < 188 or p[0] != 71:
                return None
            if not p[1] & 64:
                continue
            af = p[3] >> 4 & 3
            if af == 2:
                continue
            o = 5 + p[4] if af == 3 else 4
            if o + 14 <= 188 and p[o:o + 3] == bytes([0, 0, 1]) and (p[o + 3] >= 192) and (p[o + 3] <= 239) and p[o + 7] & 128:
                b = p[o + 9:o + 14]
                v = ((b[0] >> 1 & 7) << 30 | b[1] << 22 | b[2] >> 1 << 15 | b[3] << 7 | b[4] >> 1) / 90000.0
                if p[o + 3] >= 224:
                    fv = v if fv is None else fv
                elif fb is None:
                    fb = v
                if fv is not None and fb is not None:
                    break
    except Exception:
        pass
    if fv is not None and fb is not None:
        PSK = fv - fb
    if fv is None or fb is None:
        return fv if fv is not None else fb
    return fv if abs(fv - fb) > 5 else min(fv, fb)
keys = {}
warned = set()

def tsok(q, d):
    if not q["m"] and d[:3] != b"ID3" and (not (d[:1] == b"G" and (len(d) < 189 or d[188] == 71))):
        raise ValueError("segment is not MPEG-TS (%d bytes)" % len(d))
    return d

def fetch(q):
    h = {"Range": "bytes=%d-%d" % (q["b"][1], q["b"][1] + q["b"][0] - 1)} if q["b"] else {}
    r = s.get(q["u"], headers=h, timeout=(3, 5))
    r.raise_for_status()
    d = r.content
    k = q["k"]
    if not k:
        return tsok(q, d)
    if k[0] != "AES-128":
        if k[0] not in warned:
            log("unsupported encryption " + k[0] + ", passing through")
            warned.add(k[0])
        return d
    if k[1] not in keys:
        kr = s.get(k[1], timeout=(3, 5))
        kr.raise_for_status()
        keys[k[1]] = kr.content
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    iv = int(k[2], 16).to_bytes(16, "big") if k[2] else q["s"].to_bytes(16, "big")
    c = Cipher(algorithms.AES(keys[k[1]]), modes.CBC(iv)).decryptor()
    d = c.update(d) + c.finalize()
    return tsok(q, d[:-d[-1]] if d and 0 < d[-1] <= 16 else d)
st = "/tmp/hlsloop-" + hashlib.md5(("ch" + chid if chid.isdigit() else src).encode()).hexdigest() + ".json"
last = None
rseen = None
seen = collections.deque(maxlen=30)
stalled = False
oend = None
rcur = None
wlast = None
wpart = None
rpart = None
dep = 0.0
wlead = 0.0
sl = threading.Lock()
try:
    j = json.load(open(st))
    if time.time() - j["t"] < 4 and j.get("src") == src:
        last = j["last"] if j["hasseq"] else None
        rseen = set(j["seen"])
        seen.extend(j["seen"])
        off = j.get("off", 0.0)
        oend = j.get("oend")
        rcur = j.get("cur")
        wlast = last
        rpart = j.get("part")
        dep = j.get("dep") or 0.0
        xb = float(j.get("xb", 0.0))
        pk = max(1, int(j.get("pk", 1)))
        rstk = bool(j.get("stk"))
        wlead = max(j.get("lead", 0.0) - (time.time() - j["t"]), 0.0)
        log("resuming after Dispatcharr restart")
except Exception:
    pass
hasseq = True

def save():
    try:
        with sl:
            with open(st + ".tmp", "w") as f:
                json.dump({"last": wlast, "part": wpart, "hasseq": hasseq, "seen": list(seen), "off": off, "oend": oend, "cur": cur, "src": src, "dep": dep, "lead": wlead, "xb": xb, "pk": pk, "stk": skf, "to": tout, "t": time.time()}, f)
            os.replace(st + ".tmp", st)
    except Exception:
        pass

def beat():
    while run:
        save()
        time.sleep(1)
bt = threading.Thread(target=beat, daemon=True)
bt.start()
XC = re.compile("/(live|hls)/([^/]+)/([^/]+)/")

def feeds():
    if not chid.isdigit():
        return [src]
    try:
        urls = [hsu(u) for u in (x.get("url") for x in dapi("/api/channels/channels/%d/streams/" % int(chid))) if u]
    except Exception as x:
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
cur = src
pend = False
fbad = {}
lpr = -1000000000.0

def mark(u):
    b = fbad.get(u, [0, 0])
    fbad[u] = [mono() + min(60 * 2 ** b[1], 600), b[1] + 1]
    return fbad[u][0] - mono()

def probe(u):
    try:
        r = s.get(u, timeout=(2, 3))
        r.raise_for_status()
        t = r.text
        if "#EXTM3U" not in t:
            return False
        if "#EXT-X-STREAM-INF" in t:
            v = [l.strip() for l in t.splitlines() if l.strip() and (not l.startswith("#"))]
            if not v:
                return False
            r = s.get(up.urljoin(r.url, v[0]), timeout=(2, 3))
            r.raise_for_status()
            t = r.text
        return "#EXTINF" in t and "#EXT-X-ENDLIST" not in t
    except Exception:
        return False

def handoff(why, depth=None, fl=None):
    global stalled, got, cur, media, last, lag, pend, rseen, bad, gap, fst, lsw, lpr, ost
    fl = fl or feeds()
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
        if probe(u):
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
    inc("sw")
    was = stalled
    cur = nxt
    gap = mono() - ost if was else gap + mono() - got if gap is not None else mono() - got if last is not None else None
    media = None
    last = None
    lag = None
    rseen = None
    seen.clear()
    bad.clear()
    pend = True
    stalled = False
    got = mono()
    if depth:
        gap = depth
    dq.clear()
    rq.clear()
    fst = lsw = mono()
    save()

def bye():
    global run
    run = 0
    save()
    killall()
    os._exit(0)

def killall():
    with fql:
        ps = list(fq)
    for p in ps:
        try:
            p.kill()
        except Exception:
            pass
for g in (15, 2, 1):
    signal.signal(g, lambda *a: (killall(), os._exit(0)))
if rcur:
    cur = hsu(rcur)
bw = re.compile("(?<![A-Z-])BANDWIDTH=(\\d+)")
media = None
err = 0
e0 = None
gap = None
fresh = mono()
got = mono()
ost = got
tgt = 10
lag = None
bad = set()
ids = {}
nid = 0
dead = 0
ph = 0
dq = collections.deque()
rq = collections.deque()
fst = mono()
lsw = -1000000000.0
nsl = 0
Q = collections.deque()
Qs = [0.0]
ql = threading.Lock()
moff = off
qend = oend

def join(segs):
    global dep
    n = max(1, min(max(3, int(-(-60 // max(tgt, 1)))), len(segs)))
    dep = n * tgt
    return segs[-n:]

def clcap():
    dp = dep or 3 * tgt
    return max(min(CL, dp * 2 / 3), dp - 2 * tgt)

def cushlim():
    dp = dep or 3 * tgt
    return max(min(3 * tgt, dp - min(CL + xb, clcap())), 2 * tgt)

def watch():
    global xb, pk, lj
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
    JF = ("/data" if os.access("/data", os.W_OK) else "/tmp") + "/hlsloop-jobs-%s.json" % chid
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
                rv = min(Qs[0] + max(wlead, 0.0), rtgt() + tgt)
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
                        if tr == 0:
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
            if k and k > pk:
                room = max(clcap() - CL - xb, 0.0)
                add = min(JB * (k - pk), room)
                xb += add
                lj = mono()
                log("viewer buffer: %d viewers on Channels DVR ch %s, sent the new viewer a %.0fs head start (lead now %.0fs, reserve %.0fs)" % (k, num, add, CL + xb, (dep or 3 * tgt) - CL - xb) + ("" if add >= JB * (k - pk) else ", reserve at its floor"))
            if k:
                pk = k
            dp = dep or 3 * tgt
            if xb > JB and mono() - lj > 120 and (dp - CL - xb < RF):
                xb = max(JB, xb - 0.02)
                if xb == JB or dp - CL - xb >= RF:
                    log("viewer buffer: reserve rebuilt to %.0fs" % (dp - CL - xb))
            fails = 0
        except Exception as x:
            fails += 1
            if fails == 3:
                log("viewer buffer: Channels DVR not reachable (%s)" % str(x)[:80])
            if not base and mono() - t0 > 120:
                time.sleep(28)
        time.sleep(2)

def stat():
    low = False
    t0 = mono()
    while run:
        time.sleep(5)
        try:
            with ql:
                qs = Qs[0]
            dp = dep or 3 * tgt
            if ns and (not low) and (qs < 2):
                low = True
                log("reserve: empty, video now arrives only as fast as the provider publishes it")
            elif low and qs >= dp / 2:
                low = False
                log("reserve: refilled to %.0fs" % qs)
            if mono() - t0 >= 600:
                t0 = mono()
                with CK:
                    c = dict(CNT)
                    CNT.clear()
                log("status: %s stream, reserve %.0fs, client lead %.1fs, extra lead for shared viewers %.0fs, viewers %d; last 10 min: %d playlist retries, %d catch-ups, %d switches, %.0fs keepalive" % ("main" if cur == src else "backup", qs, wlead, xb, pk, c.get("pe", 0), c.get("cu", 0), c.get("sw", 0), c.get("ka", 0)))
        except Exception:
            pass

def tix(d):
    out = []
    b = None
    m = 0.0
    for i in range(0, len(d) - 187, 188):
        p = d[i:i + 188]
        if p[0] != 71 or not p[1] & 64:
            continue
        af = p[3] >> 4 & 3
        if af == 2:
            continue
        o = 5 + p[4] if af == 3 else 4
        if o + 14 <= 188 and p[o:o + 3] == bytes([0, 0, 1]) and (224 <= p[o + 3] <= 239) and p[o + 7] & 128:
            q = p[o + 9:o + 14]
            t = ((q[0] >> 1 & 7) << 30 | q[1] << 22 | q[2] >> 1 << 15 | q[3] << 7 | q[4] >> 1) / 90000.0
            if b is None:
                b = t
            m = max(m, (t - b + WR / 2) % WR - WR / 2)
            out.append((i, m))
    return out

def tpos(ix, x, dur):
    if x >= ix[-1][0] if ix else True:
        return dur if not ix or x >= ix[-1][0] + 188 else min(ix[-1][1], dur)
    lo, hi = (0, len(ix) - 1)
    while lo < hi:
        md = (lo + hi + 1) // 2
        if ix[md][0] <= x:
            lo = md
        else:
            hi = md - 1
    return min(ix[lo][1], dur) if ix[lo][0] <= x else 0.0

def enq(it):
    with ql:
        Q.append(it)
        Qs[0] += it[2] if it[0] == "seg" else 0

def writer():
    global wlast, wpart, oend, off, wlead, wb, tout
    lead = wlead
    t = mono()
    cpr = None
    cpt = mono()
    cbv = None
    cbt = mono()
    while run:
        try:
            now = mono()
            wb = now
            lead = max(lead - (now - t), 0.0)
            t = now
            wlead = lead
            if ff.poll() is not None:
                remux("remuxer exited")
                time.sleep(1)
                continue
            with ql:
                it = Q[0] if Q else None
                qs = Qs[0]
                if TRANSCODE:
                    RQ.append((now, qs))
                if it is None:
                    Qs[0] = 0.0
            if it is None:
                time.sleep(0.05)
                continue
            if it[0] != "seg":
                with ql:
                    Q.popleft()
                if it[0] == "remux":
                    if it[3] is not None:
                        off = it[3]
                    remux(it[1])
                else:
                    put(it[1])
                continue
            dp = rtgt()
            cb = max(CL, 255868 * it[2] / max(len(it[1]), 1) + 0.5)
            cbv = cb if cbv is None else min(cb, cbv + 0.02 * (now - cbt))
            cbt = now
            cl = min(max(cbv, TC_LEAD if TRANSCODE else 0.0) + xb, clcap())
            with ql:
                rmx = any((x[0] == "remux" for x in list(Q)[1:5]))
            if rmx and qs > 4.0:
                cl = max(cl, min(4.0, clcap()))
            if cpr is not None and cl < cpr:
                cl = max(cl, cpr - 0.1 * (now - cpt))
            cpr = cl
            cpt = now
            if lead >= cl and (qs <= dp - cl + tgt or (TRANSCODE and lead >= cl)) and (not (TRANSCODE and qs > 2 and (mono() - OWT[0] > 0.7) and (lead < clcap()))):
                time.sleep(0.02)
                continue
            if TRANSCODE and mono() - fst > 90:
                dp_ = dep or 3 * tgt
                rstep(now, qs, dp_ * 0.5, dp_ * 0.75, 1.5 * tgt)
            d = it[1]
            if len(it) < 7:
                it.append(tix(d))
            k = min(65424, max(len(d) // 100 // 188 * 188, 188), len(d) - it[5])
            e = it[5] + k
            cd = (it[2] if e >= len(d) else tpos(it[6], e, it[2])) - tpos(it[6], it[5], it[2]) if it[6] else it[2] * k / max(len(d), 1)
            cd = max(cd, 0.0)
            if RP[0]:
                k_ = kfo(d, it[5], e) if now - RP[1] < 10 else it[5]
                if k_ is not None:
                    if k_ > it[5]:
                        put(d[it[5]:k_])
                    w_ = RP[0]
                    av_ = avlead(d, k_) if now - RP[1] < 10 else 0.0
                    q_ = pests(d[k_:k_ + 188])
                    o_ = eofs(q_[1], av_) if q_ and 224 <= q_[0] <= 239 else None
                    if o_ is not None and abs(o_ - (tout + 1.4 - av_)) > 3:
                        o_ = None
                    if o_ is not None:
                        cp_ = max(tpos(it[6], k_, it[2]) - tpos(it[6], it[5], it[2]) if it[6] else it[2] * (k_ - it[5]) / max(len(d), 1), 0.0)
                        tout = o_ + av_ - 1.4 - cp_ * SPD
                        remux(w_, o_)
                    else:
                        ADJ[0] = av_
                        remux(w_)
                    put(d[k_:e])
                else:
                    put(d[it[5]:e])
            else:
                put(d[it[5]:e])
            it[5] += k
            wpart = [it[3], it[5]]
            lead += cd * SPA
            tout += cd * SPA
            with ql:
                Qs[0] -= cd
            if it[5] >= len(d):
                with ql:
                    Q.popleft()
                wlast = it[3]
                wpart = None
                if it[4] is not None:
                    oend = it[4] + off + it[2]
            save()
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
threading.Thread(target=stat, daemon=True).start()
if rstk:
    handoff("the previous process got stuck reading this feed")
while not gone():
    rb = mono()
    try:
        r = s.get(media or cur, timeout=(5, 10))
        r.raise_for_status()
        L = [x.strip() for x in r.text.splitlines() if x.strip()]
        if not L or not L[0].startswith("#EXTM3U"):
            raise ValueError("response is not an m3u8 playlist")
        v = [(int(m.group(1)) if (m := bw.search(x)) else 0, L[i + 1]) for i, x in enumerate(L[:-1]) if x.startswith("#EXT-X-STREAM-INF")]
        if v:
            media = up.urljoin(r.url, max(v)[1])
            continue
        if "#EXT-X-ENDLIST" in L:
            ph += 1
            handoff("provider is serving an offline placeholder")
            z = 5 if stalled else 0
            rb = mono() + z
            time.sleep(z)
            continue
        media = r.url
        seq = 0
        hasseq = False
        tgt = 10
        segs = []
        key = None
        mp = None
        br = None
        pe = {}
        dur = 0.0
        for x in L:
            if x.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                seq = int(x[22:])
                hasseq = True
            elif x.startswith("#EXT-X-TARGETDURATION:"):
                tgt = max(float(x[22:]), 1)
            elif x.startswith("#EXT-X-KEY:"):
                a = attrs(x)
                key = None if a.get("METHOD", "NONE") == "NONE" else (a.get("METHOD"), up.urljoin(r.url, a.get("URI", "")), a.get("IV"))
            elif x.startswith("#EXT-X-MAP:"):
                mp = up.urljoin(r.url, attrs(x)["URI"])
            elif x.startswith("#EXT-X-BYTERANGE:"):
                br = x[17:]
            elif x.startswith("#EXTINF:"):
                try:
                    dur = float(x[8:].split(",")[0])
                except ValueError:
                    dur = 0.0
            elif not x.startswith("#"):
                u = up.urljoin(r.url, x)
                pth = up.urlsplit(u).path
                b = None
                if br:
                    ln, _, of = br.partition("@")
                    ln = int(ln)
                    of = int(of) if of else pe.get(pth, 0)
                    pe[pth] = of + ln
                    b = (ln, of)
                segs.append(dict(n=seq, s=seq, u=u, p=pth + ("@%d" % b[1] if b else ""), k=key, m=mp, b=b, d=dur))
                seq += 1
                br = None
                dur = 0.0
        if not segs:
            raise ValueError("empty playlist")
        if not hasseq:
            for q in segs:
                if q["p"] not in ids:
                    ids[q["p"]] = nid
                    nid += 1
                q["n"] = ids[q["p"]]
            if len(ids) > 300:
                ids = {k: v for k, v in ids.items() if v > nid - 100}
            if rseen:
                last = max([q["n"] for q in segs if q["p"] in rseen], default=None)
        rseen = None
        err = 0
        e0 = None
        dead = 0
        w = min(max(tgt / 2, 1), 5)
        if last is None:
            new = join(segs) if gap is None else segs[-max(1, min(len(segs), int(-(-gap // max(tgt, 1))))):]
            lag = None
        elif segs[-1]["n"] < last:
            if not {q["p"].rsplit("/", 1)[-1] for q in segs} & {x.rsplit("/", 1)[-1] for x in seen} or last - segs[-1]["n"] > 50 or (lag is not None and mono() - lag > 4 * tgt):
                log("provider restarted the channel, rejoining at the live edge")
                bad.clear()
                k_ = [i for i, q in enumerate(segs) if q["p"] in seen]
                new = segs[k_[-1] + 1:] if k_ else segs[-max(2, min(len(segs), int(-(-(mono() - got) // max(tgt, 1))))):]
                lag = None
                pend = True
                last = last if new else segs[-1]["n"]
            else:
                if lag is None:
                    lag = mono()
                    log("edge server is behind, waiting for it to catch up")
                new = []
                media = None
                w = 1
        else:
            new = [q for q in segs if q["n"] > last]
            lag = None
            gap = None
        lim = max(cushlim(), DEADT)
        if new:
            fresh = mono()
        if mono() - got > lim or (mono() - got > cushlim() and Qs[0] + max(wlead, 0.0) < 10.0):
            handoff("no new video for %ds" % (mono() - got))
            if not stalled:
                continue
        elif not new and mono() - fresh > 0.6 * cushlim():
            log("playlist stale %ds, re-resolving" % (mono() - fresh))
            media = None
            fresh = mono()
            continue
        if not new and mono() - fresh > 0.8 * tgt:
            w = min(w, max(tgt / 10, 0.5))
        if last is not None and len(new) > 1:
            log("catching up %d segments" % len(new))
            inc("cu")
        for q in new:
            n = q["n"]
            d = None
            rb = mono()
            for t in range(2):
                try:
                    d = fetch(q)
                    break
                except Exception as e:
                    d = None
                    log("segment %d: %s" % (n, e))
                    if getattr(getattr(e, "response", None), "status_code", 0) in BUSY:
                        rb = mono() + 2
                        time.sleep(2)
            if d is None:
                if n in bad and sum((o["n"] > n for o in new)) >= 2:
                    log("skipped segment %d" % n)
                    last = n
                    continue
                bad.add(n)
                media = None
                w = 1
                break
            if q["m"] != cmap:
                if cmap is not None:
                    enq(["remux", "new init segment", 0, None])
                    sig = None
                i = s.get(q["m"], timeout=(3, 5))
                i.raise_for_status()
                enq(["init", i.content, 0])
                cmap = q["m"]
            p0 = pts0(d)
            if PSK is not None and 3 < abs(PSK) < WR - 60:
                nsk += 1
                if nsk == 1:
                    log("audio and video are %.1fs apart in this feed; %s" % (PSK, "holding it back" if qend is not None else "passing it through (nothing playing yet)"))
                if nsk >= 2 and mono() - lpr >= 30:
                    handoff("audio and video are %.0fs apart in this feed" % abs(PSK))
                    if not stalled:
                        nsk = 0
                        break
                if nsk * (q["d"] or tgt) < (30 if qend is not None else 0.1):
                    last = n
                    bad.discard(n)
                    seen.append(q["p"])
                    continue
            elif nsk:
                nsk = 0
                pend = True
                log("audio and video back in step; rejoining smoothly")
            if pend and p0 is not None:
                pend = False
                if qend is not None:
                    moff = qend - p0
                    enq(["remux", "continuing on the new stream timeline seamlessly (offset %.1fs)" % moff, 0, moff])
                    sig = None
                    cmap = None
            g = tsig(d)
            if g and sig and (g != sig):
                enq(["remux", "stream layout changed", 0, None])
                cmap = None
            if g:
                sig = g
            o = 0
            if rpart and rpart[0] == n and (rpart[1] < len(d)):
                o = rpart[1] - rpart[1] % 188
                rpart = None
            enq(["seg", d, q["d"] or tgt, n, p0, o])
            dq.append((mono(), q["d"] or tgt))
            ns += 1
            last = n
            bad.discard(n)
            seen.append(q["p"])
            gap = None
            got = mono()
            stalled = False
            ph = 0
            if p0 is not None:
                qend = p0 + moff + (q["d"] or tgt)
        now = mono()
        SW = max(300.0, 30 * tgt)
        if cur in fbad and last is not None and (not stalled) and (now - fst > 120) and (now - got < 2 * tgt):
            fbad.pop(cur, None)
        with ql:
            qs = Qs[0]
        rq.append((now, qs + max(wlead, 0.0)))
        while dq and now - dq[0][0] > SW:
            dq.popleft()
        while rq and now - rq[0][0] > SW:
            rq.popleft()
        if not stalled and last is not None and (now - fst > SW) and rq:
            dp = dep or 3 * tgt
            rt = (sum((x[1] for x in dq)) + tgt) / SW
            lost = dp - max(wlead, 0.0) - qs
            drop = max((x[1] for x in rq)) - (qs + max(wlead, 0.0))
            if rt >= 1.0 and drop < 0.05 * SW:
                nsl = 0
            if rt < 0.75 and lost > cushlim() and (drop >= 0.1 * SW) and (now - lsw > 300 * 2 ** nsl):
                lsw = now
                nsl = min(nsl + 1, 4)
                why = "provider is delivering %.2fx real time and the reserve fell %.0fs to %.0fs" % (rt, drop, qs)
                fl = feeds()
                if len(fl) > 1:
                    handoff(why, lost, fl)
                    continue
                log(why + "; no backup stream on this channel, so staying on this feed")
        rb = mono() + w
        time.sleep(w)
    except Exception as e:
        if e0 is None:
            e0 = mono()
        if mono() - got > max(cushlim(), DEADT) or (mono() - got > cushlim() and Qs[0] + max(wlead, 0.0) < 10.0):
            handoff("feed unreachable for %ds" % (mono() - e0))
            if not stalled:
                err = 0
                e0 = None
                continue
        err += 1
        media = None
        rs = getattr(e, "response", None)
        c = getattr(rs, "status_code", 0)
        ra = rs.headers.get("Retry-After", "") if rs is not None else ""
        dl = min(int(ra), 30) if ra.isdigit() else min(2 * err, 10) if c in BUSY else min(err, 5)
        if not stalled:
            dl = max(0.5, min(dl, max(cushlim(), DEADT) - (mono() - got)))
        log("playlist error (%s); retry %d in %ds" % (e, err, dl))
        inc("pe")
        rb = mono() + dl
        time.sleep(dl)
bye()
