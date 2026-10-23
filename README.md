# Channels DVR Station ID Matcher

Tools for feeding an IPTV provider into [Channels DVR](https://getchannels.com) through
[Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) or
[m3u-editor](https://github.com/m3ue/m3u-editor):

- **Station ID matcher apps.** Two small desktop apps, one for Dispatcharr and one for m3u-editor,
  that fill in the **Gracenote station ID** (`tvc-guide-stationid`) on your IPTV channels using the
  Gracenote data your own Channels DVR server already has access to. Once your channels carry the
  right station IDs, Channels DVR uses its Gracenote guide for them (two weeks of listings,
  artwork and logos) instead of your provider's EPG.
- **A Dispatcharr stream profile for HLS providers** that keeps live channels playing through
  provider hiccups, stalls and channel restarts. It is copied and pasted into Dispatcharr from
  [this README](#dispatcharr-stream-profile-for-hls-providers); there is nothing to install.

## What's in this repository

| Item | What it is | Use it with |
|---|---|---|
| [`dispatcharr_stationid_matcher.py`](dispatcharr_stationid_matcher.py) | Desktop app. Matches your channels to Gracenote station IDs and writes them straight to Dispatcharr through its API. | Dispatcharr |
| [`m3u_editor_stationid_matcher.py`](m3u_editor_stationid_matcher.py) | Desktop app. Same matching; applies the IDs through a small m3u-editor plugin that the app exports for you. | m3u-editor |
| [`plugin/`](plugin) | Source of that m3u-editor plugin, for reading. The app builds the installable zip. | m3u-editor |
| [Stream profile](#dispatcharr-stream-profile-for-hls-providers) (in this README) | A Dispatcharr stream profile that buffers, resumes and retries HLS streams. | Dispatcharr |

The two apps match channels the same way. They differ only in where the channels come from and
how the IDs are saved. Use the app for whichever manager you run.

> **Renamed:** the Dispatcharr app used to be called `channels_dvr_stationid_matcher.py`. It is now
> `dispatcharr_stationid_matcher.py`. Its settings folder moves to the new name automatically the
> first time you run it.

## How it fits together

```
 IPTV provider (Xtream)
        |  HLS or MPEG-TS
        v
 Dispatcharr  or  m3u-editor          <- your channels, groups, numbers, station IDs
        |  M3U playlist (with tvc-guide-stationid) + streams
        v
 Channels DVR  (Custom Channels source)  -->  your TVs, phones, computers
        ^
        |  lineups and station lookups
        |
 Matcher app  -- reads channels / saves IDs -->  Dispatcharr or m3u-editor
```

The matcher apps run on your computer. They talk to your Channels DVR server for Gracenote lineup
data, and to your Dispatcharr or m3u-editor server for the channel list. Nothing goes anywhere
else. The stream profile runs inside Dispatcharr and only talks to your provider.

## Dispatcharr or m3u-editor?

If you already run one, use it; both work with Channels DVR. If you're choosing:

| | Dispatcharr | m3u-editor |
|---|---|---|
| Saving Gracenote IDs from the matcher | Written directly through Dispatcharr's API | Pasted into a plugin action (m3u-editor's API can't set them) |
| HLS provider hiccups | The built-in stream profiles give up after 3 quick reconnects. Use the [stream profile](#dispatcharr-stream-profile-for-hls-providers) in this README. | Passes the provider's HLS through; Channels DVR fetches the next segment |
| Provider that only sends MPEG-TS | Built-in proxy buffers and sends keepalives through stalls | Works, with no special TS buffering |
| Backup streams per channel | Automatic switching | Failover channels, set by hand or with auto-merge |
| Several logins for one provider | M3U account profiles | Provider profiles |

## Station ID matcher apps

### What the apps do

1. Read your channels from Dispatcharr, or from one m3u-editor playlist.
2. Download the real cable, satellite and over-the-air lineups Gracenote lists for the
   countries and postal codes you choose (through your Channels DVR server) and build a local
   index of every station in them.
3. Match each channel against that index and show the results in a table:
   - **Matched** (green, pre-ticked): exact and unambiguous only. An explicit call sign such as
     `(WABC)`, or a channel name that equals the Gracenote name after removing words like
     HD/East/Channel.
   - **Review** (yellow): several stations share the name, or the name is only similar. Never
     ticked for you.
   - **Unmatched** (red): nothing similar in your lineups.
4. Let you fix the rest by hand, one channel at a time, with numbered suggestions.
5. Save the IDs you ticked after you confirm, then read them back to verify every one.

Fuzzy matches are never saved automatically. Anything uncertain needs your pick.

### Features

- **Multiple countries at once** (`USA:10001, CAN, GBR:SW1A`), for lists that mix them. Lineups
  from all of them are merged into one index.
- **Guided walkthrough** for unmatched channels: press 1-9 to pick, Esc to skip, Back, search
  your lineups or all of Gracenote, or type an ID.
- **Word-order independent matching** that handles names like "Nat Geo Wild HD", "C-SPAN 2",
  "QVC 2", run-together names like "SHOXBET", and provider prefixes like `US|`.
- **Gracenote-wide search** when nothing in your lineups is a good match, clearly labelled
  "NOT in your lineups".
- **Fix or remove existing IDs**: channels that already have an ID are included, a different pick
  shows as "Will replace ID", and **Remove ID** clears one. The confirmation lists every
  replacement and removal.
- **Filters**: group dropdown, Find box (name, ID, group, or a channel-number range like
  `500-600`), sortable columns.
- **Your work is autosaved** and offered back after a restart or crash.
- **Your servers and API key are remembered** between runs (see
  [Where data is stored](#where-data-is-stored)).
- **Cancel button**, clear error messages for wrong addresses or credentials, and a log file.
- **CSV export** of whatever is still unmatched.

### Requirements

- **Python 3.8 or newer.**
- **Tk (tkinter)** for the window. It is part of Python's standard library but is often a
  separate install:

  | Platform | Install Tk |
  |---|---|
  | Windows | The python.org installer includes it by default (keep "tcl/tk and IDLE" ticked). The Microsoft Store Python also includes it. |
  | macOS, Homebrew Python | `brew install python-tk@3.13` (match your Python version) |
  | macOS, Apple's `/usr/bin/python3` | Included |
  | Debian / Ubuntu | `sudo apt install python3-tk` |
  | Fedora | `sudo dnf install python3-tkinter` |
  | Arch | `sudo pacman -S tk` |
  | openSUSE | `sudo zypper install python3-tk` |

  To check, run `python3 -c "import tkinter"`. It should print nothing.
- A Channels DVR server you can reach on your network.
- **Dispatcharr app:** a Dispatcharr instance and an admin account or API key.
- **m3u-editor app:** m3u-editor 0.13 or newer, an admin account (to install the plugin once)
  and an API token.

Nothing else is needed: no `pip install` and no other packages.

When an app starts it checks for Tk. If it is missing it offers to install it with the commands
above (Homebrew, winget, apt, dnf, pacman or zypper), or switches to another Python on the
machine that already has it. To only see what it would do, add `--check-deps`; add `--yes` to
install without being asked.

### Running an app

```
# macOS / Linux
python3 dispatcharr_stationid_matcher.py       # Dispatcharr
python3 m3u_editor_stationid_matcher.py        # m3u-editor

# Windows (or double-click the file)
py dispatcharr_stationid_matcher.py
py m3u_editor_stationid_matcher.py
```

In both apps, enter your **Channels DVR** address (e.g. `192.168.1.10`; port 8089 is assumed) and
your **countries** with a postal code, e.g. `USA:10001`, or use **Pick…**. The postal code
decides which local stations count, so use your own area's. The first run for a country takes
about a minute while lineups are read; after that they are cached for 7 days.

### Using the Dispatcharr app

#### Get a Dispatcharr API key

1. In Dispatcharr, open **Users** and edit your user.
2. Press **Generate API Key** and copy it. A username and password also work instead of a key.

#### Match and write

1. Run `dispatcharr_stationid_matcher.py`.
2. Enter the **Dispatcharr** address (port 9191 is assumed) and the **API key**, or a username
   and password.
3. Press **Connect & Match**.
4. Review the table, use **Match unmatched one by one…** for the rest, then
   **Write ticked IDs to Dispatcharr**.

For channels Dispatcharr created from an M3U (auto-sync), the station ID is written as a channel
**override**; otherwise the next M3U refresh would overwrite it with whatever the provider sends.
Removing an ID on such a channel writes a blank override. Manually created channels get the
normal field.

Point Channels DVR at Dispatcharr's M3U output; Dispatcharr includes `tvc-guide-stationid` there
automatically.

### Using the m3u-editor app

#### Why there is a plugin

m3u-editor's API can list channels but cannot set a station ID, and plugin actions can only be
started from its web page. So the app ships a small m3u-editor plugin, **Gracenote Station IDs**,
inside the script, and you install it once. The plugin has one action, **Apply Station IDs**:
you pick the playlist and paste the lines the app copied (`channel_id=station_id`; an empty ID
removes it). It:

- rejects the whole paste if any line is malformed or a channel is listed twice with different
  IDs, so nothing is half-applied
- only touches channels that belong to the playlist you pick
- reads every value back before reporting success, and lists any channel it skipped

m3u-editor keeps station IDs when it re-syncs the playlist from your provider, so they only need
to be applied once. Its playlist output includes them as `tvc-guide-stationid`. The plugin's
source is in [`plugin/`](plugin) if you want to read it before installing.

#### Install the plugin (once)

1. Run `m3u_editor_stationid_matcher.py`, press **Export m3u-editor plugin…** and save the zip.
2. In m3u-editor, signed in as an admin: **Extensions > Plugin Installs > Upload Plugin Archive**,
   and choose the zip.
3. Open the review, run the scan, then **Install and Trust**.
4. **Extensions > Extensions > Gracenote Station IDs > Enable**.

A newer version installs the same way and replaces the old one.

If you also have m3u-editor's built-in **Channels DVR** plugin enabled, turn it off or turn off
its **Overwrite existing** setting. It runs after every playlist sync and can replace the IDs you
applied.

#### Get an m3u-editor API token

1. In m3u-editor, open **Tools > API Tokens** and create a token.
2. Give it the **View** permission (that's all the app needs) and copy it. m3u-editor shows the
   token only once.

#### Match and apply

1. Paste the **m3u-editor playlist URL**: the same `http://<host>:36400/<id>/playlist.m3u` URL
   you gave Channels DVR. Use the playlist that imports from your provider, because station IDs
   live on its channels. Custom and merged playlists are refused; they pick up the IDs from that
   playlist automatically.
2. Paste the **API token** and press **Connect & Match**.
3. Review the table, use **Match unmatched one by one…** for the rest, then press
   **Copy ticked IDs for m3u-editor**.
4. In m3u-editor open **Extensions > Extensions > Gracenote Station IDs > Actions > Apply Station
   IDs**, pick the playlist, paste, and confirm.
5. When the run shows as completed under Run History, press **Verify in m3u-editor** in the app.
   Anything not applied yet stays ticked.

The app reads each channel's current station ID from the playlist output, because the API doesn't
return it. A channel hidden from the output (for example disabled) shows no current ID; the app
warns when that happens. Picking an ID for it still works.

#### Channels DVR source settings for m3u-editor

- **Source:** the m3u-editor playlist URL.
- **Stream Format:** **HLS** if your m3u-editor playlist outputs HLS (Xtream playlists: **Output:
  HLS**), otherwise MPEG-TS. Use the format the URLs end in (`.m3u8` is HLS).
- **Stream limit:** no more than your provider's connection limit.

### Where data is stored

Each app keeps its own folder in your home directory:

| App | Folder |
|---|---|
| Dispatcharr | `~/.dispatcharr_stationid_matcher/` (Windows: `%USERPROFILE%\.dispatcharr_stationid_matcher\`) |
| m3u-editor | `~/.m3u_editor_stationid_matcher/` (Windows: `%USERPROFILE%\.m3u_editor_stationid_matcher\`) |

The folder name starts with a dot, so it is hidden by default on macOS and Linux. Open it with
`open ~/.<folder>` (macOS) or `xdg-open ~/.<folder>` (Linux), or press Cmd-Shift-. in Finder. On
Windows, paste the path into Explorer's address bar. The **Open log** button opens the log
directly.

| File | Contents |
|---|---|
| `settings.json` | Server addresses, countries, and your API key or token (Dispatcharr: also the username, never the password). Readable only by your user account. |
| `session.json` | Autosaved picks, used for the restore prompt. |
| `station_ids_to_apply.txt` | m3u-editor only: the last set of IDs copied for the plugin. |
| `index_<COUNTRY><POSTAL>.json` | Cached lineup data, refreshed after 7 days. |
| `logs/matcher.log` | Rotating log. Passwords, API keys and tokens are never logged. |

The API key or token is saved so you don't have to paste it every time. To make the app forget
it, delete `settings.json` while the app is closed. Deleting the whole folder resets the app;
nothing in Dispatcharr or m3u-editor is affected.

### Known limitations

- Gracenote only returns lineups for about 36 countries (the Americas, most of Western and
  Northern Europe, Australia and South Korea). Other countries can still be entered; their
  channels only get Gracenote-wide suggestions.
- An unknown postal code doesn't produce an error from Gracenote; it returns only nationwide
  satellite lineups. The app warns when that happens.
- Some channels have no Gracenote station of their own (for example per-team sports feeds), so
  they stay unmatched or need a hand-picked ID.
- m3u-editor app: IDs are applied by pasting into the plugin's action, because m3u-editor has
  no API for it and plugin actions can only be started from its web page.

## Dispatcharr stream profile for HLS providers

### Why

IPTV providers that serve HLS (`.m3u8`) publish live video as a stream of short segments. Now
and then a segment arrives late, the provider stalls for a few seconds, refuses a request, or
restarts the channel. Dispatcharr's built-in stream profiles start playback right at the live
edge and give up after three quick reconnects, so those hiccups show up in Channels DVR as
buffering or a dropped channel.

This profile replaces the stream fetcher with a small Python program that runs inside
Dispatcharr and handles HLS the way a good IPTV player app does, and in some ways better:

- **Starts with a cushion.** It joins 3 segments behind live (about 30 seconds on 10-second
  segments) and downloads them at full speed, so short hiccups never reach the screen.
- **Refills the cushion after a stall.** It remembers the last segment it delivered. When the
  provider comes back, it resumes at the next segment and downloads everything it missed back to
  back. Nothing is replayed and nothing is skipped, as long as the missed segments are still in
  the provider's playlist (usually about a minute).
- **Never gives up.** Errors are retried indefinitely with a short backoff. Connection-limit
  responses (503, 509, 429) back off longer and respect `Retry-After` (up to 30 seconds).
- **Catches a frozen playlist.** If no new segment appears for two segment lengths, it requests
  the playlist again from scratch, which can land on a working server.
- **Tells a lagging server from a channel restart.** A server that is slightly behind is waited
  out instead of replayed; a real channel restart is rejoined immediately.
- **Survives Dispatcharr's own restarts.** If Dispatcharr's health check restarts the stream, the
  new copy picks up where the old one stopped instead of starting over.
- **One continuous stream.** A single `ffmpeg -c copy` remuxer joins everything into one MPEG-TS
  stream for Dispatcharr. Nothing is re-encoded, so quality and CPU use are unchanged.
- Also handles AES-128 encrypted streams, fMP4 segments and byte-range playlists.

You watch about 30 seconds behind live, like most IPTV player apps.

### Requirements

- **Dispatcharr 0.32 or newer** (tested on 0.32.0). The profile uses Python, the `requests`
  module and `ffmpeg`, which are all part of Dispatcharr's Docker image. Nothing is installed.
- **Your provider's stream URLs must be HLS**, ending in `.m3u8`. Check under **Streams** in
  Dispatcharr. If they end in `.ts` or have no extension, switch the provider's M3U account to
  HLS output if your provider offers it, and refresh the account. With MPEG-TS URLs every tune
  fails with `response is not an m3u8 playlist` in the Dispatcharr log; keep those channels on a
  built-in profile.

### Create the profile

1. In Dispatcharr, open **Settings > Stream Profiles** and press **Add Stream Profile**.
2. **Name:** anything you like, for example `HLS Resume`.
3. **Command:** type exactly:

   ```
   python
   ```

4. **Parameters:** copy the **whole** block under
   [Profile parameters](#profile-parameters) below (on GitHub, use the copy button in the
   block's top-right corner) and paste it into the Parameters box.
   - It starts with `-u -c '` and ends with `' {streamUrl} {userAgent}`.
   - Paste it as is. Don't edit it, don't join the lines, and don't add or remove quotes. The
     program is wrapped in single quotes and contains none itself, which is what lets it sit in
     the Parameters box safely.
5. Press **Save**.

### Turn it on

- **For all channels:** **Settings > Stream Settings > Default Stream Profile**, choose the new
  profile, and save. Channels that have their own stream profile set keep it.
- **For one channel:** edit the channel and set **Stream Profile** to the new profile.

A profile only applies to streams that start after you save. To switch a channel that is already
playing, change to another channel in Channels DVR, wait about 10 seconds so Dispatcharr stops the
old stream, then tune back.

### Channels DVR settings

In Channels DVR, the Dispatcharr source should use **Stream Format: MPEG-TS**. The profile always
delivers MPEG-TS to Dispatcharr, whatever the provider sends.

### Check that it's working

Tune a channel, then look at Dispatcharr's log (for Docker, `docker logs dispatcharr`). The
running process shows up as `python -u -c import os,sys,...`. Messages from the profile start
with `[hlsloop]`:

| Message | Meaning |
|---|---|
| `catching up N segments` | The provider stalled; the profile downloaded what it missed and refilled the cushion. |
| `playlist stale Ns, re-resolving` | The playlist stopped updating; the profile requested it again. |
| `edge server is behind, waiting for it to catch up` | A provider server was a few segments behind; nothing was replayed. |
| `provider restarted the channel, rejoining` | The provider restarted the channel; the profile rejoined it. |
| `resuming after Dispatcharr restart` | Dispatcharr restarted the stream; the profile picked up where it stopped. |
| `playlist error (...); retry N in Ns` | A request failed and is being retried. |
| `skipped segment N` | A segment could not be downloaded in time and was skipped so playback could continue. |

Dispatcharr also logs `Stream unhealthy - no data for 10s` followed by `Stream health restored`
every half minute or so. That is normal for HLS: segments arrive in bursts about 10 seconds apart,
and Dispatcharr's health check expects a steady flow. It is not an error.

### What it can't fix

- **A provider outage longer than the provider's playlist window** (usually about a minute). The
  picture freezes; when the provider comes back, the profile recovers on its own, but video from
  the outage is lost.
- **A bad feed that keeps running**, for example audio out of sync. The profile can't detect it;
  use another stream for that channel.
- **Several stalls in quick succession** can use up the cushion before it refills, causing a
  short freeze.

### Undo

Set **Default Stream Profile** (or the channel's **Stream Profile**) back to what it was, then
retune. You can delete the profile afterwards.

### Profile parameters

```text
-u -c 'import os,sys,time,json,signal,re,select,shutil,hashlib,threading,collections,subprocess as sp,urllib.parse as up
try:import requests
except ImportError:print("[hlsloop] python requests module not found",file=sys.stderr);sys.exit(1)
src,ua=sys.argv[1],sys.argv[2]
FF=shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
s=requests.Session();s.headers["User-Agent"]=ua
mono=time.monotonic;BUSY=(429,503,509);AV={1,2,3,4,6,15,17,27,36,129,135};run=1
def log(m):print("[hlsloop]",m,file=sys.stderr,flush=True)
ff=None;sig=None;cmap=None
def remux(why=None):
 global ff,sig,cmap
 if why:log("restarting remuxer: "+why)
 if ff and ff.poll() is None:
  try:ff.stdin.close();ff.wait(5)
  except Exception:ff.kill()
 ff=sp.Popen([FF,"-hide_banner","-loglevel","error","-fflags","+discardcorrupt","-i","pipe:0","-map","0:v?","-map","0:a?","-c","copy","-f","mpegts","pipe:1"],stdin=sp.PIPE);sig=None;cmap=None
def gone():
 try:
  pl=select.poll();pl.register(1,select.POLLOUT)
  return any(e&(select.POLLERR|select.POLLHUP) for _,e in pl.poll(0))
 except Exception:return False
def put(b):
 try:ff.stdin.write(b);ff.stdin.flush()
 except BrokenPipeError:
  if gone():bye()
  remux("remuxer died");time.sleep(1);ff.stdin.write(b);ff.stdin.flush()
def attrs(x):return {k:v.strip(chr(34)) for k,v in re.findall(r"([A-Z0-9-]+)=(\"[^\"]*\"|[^,]*)",x.split(":",1)[1])}
def tsig(d):
 try:
  if not d or d[0]!=0x47:return None
  pmt=None
  for i in range(0,min(len(d),188*600),188):
   p=d[i:i+188]
   if len(p)<188 or p[0]!=0x47:return None
   if not p[1]&64:continue
   pid=((p[1]&31)<<8)|p[2];af=(p[3]>>4)&3
   if af==2:continue
   o=5+p[4] if af==3 else 4
   o+=1+p[o];sl=((p[o+1]&15)<<8)|p[o+2];end=min(o+3+sl-4,188)
   if pid==0 and p[o]==0:
    for j in range(o+8,end,4):
     if (p[j]<<8)|p[j+1]:pmt=((p[j+2]&31)<<8)|p[j+3];break
   elif pid==pmt and p[o]==2:
    j=o+12+(((p[o+10]&15)<<8)|p[o+11]);es=[]
    while j+5<=end:es.append((p[j],((p[j+1]&31)<<8)|p[j+2]));j+=5+(((p[j+3]&15)<<8)|p[j+4])
    return tuple(sorted(e for e in es if e[0] in AV))
 except Exception:pass
 return None
keys={};warned=set()
def fetch(q):
 h={"Range":"bytes=%d-%d"%(q["b"][1],q["b"][1]+q["b"][0]-1)} if q["b"] else {}
 r=s.get(q["u"],headers=h,timeout=(3,5));r.raise_for_status();d=r.content;k=q["k"]
 if not k:return d
 if k[0]!="AES-128":
  if k[0] not in warned:log("unsupported encryption "+k[0]+", passing through");warned.add(k[0])
  return d
 if k[1] not in keys:kr=s.get(k[1],timeout=(3,5));kr.raise_for_status();keys[k[1]]=kr.content
 from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
 iv=int(k[2],16).to_bytes(16,"big") if k[2] else q["s"].to_bytes(16,"big")
 c=Cipher(algorithms.AES(keys[k[1]]),modes.CBC(iv)).decryptor();d=c.update(d)+c.finalize()
 return d[:-d[-1]] if d and 0<d[-1]<=16 else d
st="/tmp/hlsloop-"+hashlib.md5(src.encode()).hexdigest()+".json"
last=None;rseen=None;seen=collections.deque(maxlen=30)
try:
 j=json.load(open(st))
 if time.time()-j["t"]<4:last=j["last"] if j["hasseq"] else None;rseen=set(j["seen"]);seen.extend(j["seen"]);log("resuming after Dispatcharr restart")
except Exception:pass
hasseq=True
def beat():
 while run:
  try:
   with open(st+".tmp","w") as f:json.dump({"last":last,"hasseq":hasseq,"seen":list(seen),"t":time.time()},f)
   os.replace(st+".tmp",st)
  except Exception:pass
  time.sleep(1)
bt=threading.Thread(target=beat,daemon=True);bt.start()
def bye():
 global run
 run=0;bt.join(2)
 try:os.remove(st)
 except Exception:pass
 os._exit(0)
for g in (15,2,1):signal.signal(g,lambda*a:(ff and ff.kill(),os._exit(0)))
bw=re.compile(r"(?<![A-Z-])BANDWIDTH=(\d+)")
media=None;err=0;fresh=mono();lag=None;bad=set();ids={};nid=0;dead=0
remux()
while not gone():
 if ff.poll() is not None:dead+=1;remux("remuxer exited");time.sleep(min(dead,5));continue
 try:
  r=s.get(media or src,timeout=(5,10));r.raise_for_status()
  L=[x.strip() for x in r.text.splitlines() if x.strip()]
  if not L or not L[0].startswith("#EXTM3U"):raise ValueError("response is not an m3u8 playlist")
  v=[(int(m.group(1)) if (m:=bw.search(x)) else 0,L[i+1]) for i,x in enumerate(L[:-1]) if x.startswith("#EXT-X-STREAM-INF")]
  if v:media=up.urljoin(r.url,max(v)[1]);continue
  media=r.url;seq=0;hasseq=False;tgt=10;segs=[];key=None;mp=None;br=None;pe={}
  for x in L:
   if x.startswith("#EXT-X-MEDIA-SEQUENCE:"):seq=int(x[22:]);hasseq=True
   elif x.startswith("#EXT-X-TARGETDURATION:"):tgt=float(x[22:])
   elif x.startswith("#EXT-X-KEY:"):a=attrs(x);key=None if a.get("METHOD","NONE")=="NONE" else (a.get("METHOD"),up.urljoin(r.url,a.get("URI","")),a.get("IV"))
   elif x.startswith("#EXT-X-MAP:"):mp=up.urljoin(r.url,attrs(x)["URI"])
   elif x.startswith("#EXT-X-BYTERANGE:"):br=x[17:]
   elif not x.startswith("#"):
    u=up.urljoin(r.url,x);pth=up.urlsplit(u).path;b=None
    if br:ln,_,of=br.partition("@");ln=int(ln);of=int(of) if of else pe.get(pth,0);pe[pth]=of+ln;b=(ln,of)
    segs.append(dict(n=seq,s=seq,u=u,p=pth+("@%d"%b[1] if b else ""),k=key,m=mp,b=b));seq+=1;br=None
  if not segs:raise ValueError("empty playlist")
  if not hasseq:
   for q in segs:
    if q["p"] not in ids:ids[q["p"]]=nid;nid+=1
    q["n"]=ids[q["p"]]
   if len(ids)>300:ids={k:v for k,v in ids.items() if v>nid-100}
   if rseen:last=max([q["n"] for q in segs if q["p"] in rseen],default=None)
  rseen=None;err=0;dead=0;w=min(max(tgt/2,1),5)
  if last is None:new=segs[-3:];lag=None
  elif segs[-1]["n"]<last:
   if not ({q["p"].rsplit("/",1)[-1] for q in segs}&{x.rsplit("/",1)[-1] for x in seen}) or last-segs[-1]["n"]>50 or (lag is not None and mono()-lag>4*tgt):log("provider restarted the channel, rejoining");bad.clear();new=segs[-3:];lag=None
   else:
    if lag is None:lag=mono();log("edge server is behind, waiting for it to catch up")
    new=[];media=None;w=1
  else:new=[q for q in segs if q["n"]>last];lag=None
  if new:fresh=mono()
  elif mono()-fresh>2*tgt:log("playlist stale %ds, re-resolving"%(mono()-fresh));media=None;fresh=mono();continue
  if last is not None and len(new)>1:log("catching up %d segments"%len(new))
  for q in new:
   n=q["n"];d=None
   for t in range(2):
    try:d=fetch(q);break
    except Exception as e:
     d=None;log("segment %d: %s"%(n,e))
     if getattr(getattr(e,"response",None),"status_code",0) in BUSY:time.sleep(2)
   if d is None:
    if n in bad and sum(o["n"]>n for o in new)>=2:log("skipped segment %d"%n);last=n;continue
    bad.add(n);media=None;w=1;break
   if q["m"]!=cmap:
    if cmap is not None:remux("new init segment")
    i=s.get(q["m"],timeout=(3,5));i.raise_for_status();put(i.content);cmap=q["m"]
   g=tsig(d)
   if g and sig and g!=sig:remux("stream layout changed")
   if g:sig=g
   try:put(d)
   except Exception as e:
    if gone():bye()
    log("remuxer rejected segment %d (%s), skipping"%(n,e));remux("rejected segment");last=n;continue
   last=n;bad.discard(n);seen.append(q["p"])
  time.sleep(w)
 except Exception as e:
  err+=1;media=None;rs=getattr(e,"response",None);c=getattr(rs,"status_code",0);ra=rs.headers.get("Retry-After","") if rs is not None else ""
  dl=min(int(ra),30) if ra.isdigit() else (min(2*err,10) if c in BUSY else min(err,5))
  log("playlist error (%s); retry %d in %ds"%(e,err,dl));time.sleep(dl)
bye()' {streamUrl} {userAgent}
```

## Disclaimer

This is an unofficial community project. It is not affiliated with, endorsed by or supported by
Fancy Bits (Channels DVR), Gracenote / Nielsen, the Dispatcharr project or the m3u-editor
project. Channels DVR and Gracenote are trademarks of their respective owners. Guide data is
accessed through your own Channels DVR server and is subject to its terms.

Back up your Dispatcharr or m3u-editor database before bulk changes.

## Contributing

This project does not accept issues or pull requests; both are turned off.

You are welcome to fork, remix and build on it under the terms of the MIT license.

## License

[MIT](LICENSE)
