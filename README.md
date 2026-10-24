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
  provider hiccups, stalls and channel restarts, and fails over to a channel's backup streams
  without stalling. It is copied and pasted into Dispatcharr from
  [this README](#dispatcharr-stream-profile-for-hls-providers); there is nothing to install.
- **A backup streams app** that finds other feeds of each channel and adds them to your Dispatcharr
  channels as backups in bulk, so the stream profile has somewhere to fail over to.

## What's in this repository

| Item | What it is | Use it with |
|---|---|---|
| [`dispatcharr_stationid_matcher.py`](dispatcharr_stationid_matcher.py) | Desktop app. Matches your channels to Gracenote station IDs and writes them straight to Dispatcharr through its API. | Dispatcharr |
| [`m3u_editor_stationid_matcher.py`](m3u_editor_stationid_matcher.py) | Desktop app. Same matching; applies the IDs through a small m3u-editor plugin that the app exports for you. | m3u-editor |
| [`plugin/`](plugin) | Source of that m3u-editor plugin, for reading. The app builds the installable zip. | m3u-editor |
| [`dispatcharr_backup_streams.py`](dispatcharr_backup_streams.py) | Desktop app. Finds other feeds of each channel and adds them as backup streams through Dispatcharr's API. | Dispatcharr |
| [Stream profile](#dispatcharr-stream-profile-for-hls-providers) (in this README) | A Dispatcharr stream profile that buffers, resumes, retries and fails over HLS streams. | Dispatcharr |

The two station ID apps match channels the same way. They differ only in where the channels come from and
how the IDs are saved. Use the app for whichever manager you run.

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
| Adding backup streams in bulk | The [backup streams app](#backup-streams-app) | By hand, or m3u-editor's auto-merge |
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
and then a segment arrives late, the provider stalls, refuses a request, restarts the channel, or
replaces a dead channel with a black "offline" placeholder. Dispatcharr's built-in stream profiles
start playback right at the live edge and give up after three quick reconnects, so those problems
show up in Channels DVR as buffering, a black screen or a dropped channel.

This profile replaces the stream fetcher with a small Python program that runs inside Dispatcharr:

- **Starts with a cushion and a hidden reserve, worked out from the stream.** It joins about 45
  seconds behind live (counted in the stream's own segment length, never deeper than the
  provider's playlist holds), hands about 30 seconds of that to Dispatcharr straight away, and
  keeps the rest as a private reserve that it releases at real-time speed. When the provider
  publishes a segment late, the reserve covers the wait, so the output never stops and the
  client's cushion doesn't drain. When the provider catches up, the reserve refills at download
  speed. There are no settings: segment length and timings come from each playlist.
- **Refills the cushion after a stall.** It remembers the last segment it delivered. When the
  provider comes back, it resumes at the next segment and downloads everything it missed back to
  back. Nothing is replayed and nothing is skipped, as long as the missed segments are still in
  the provider's playlist (usually about a minute).
- **Fails over to the channel's backup streams on its own, without rewinding.** It switches to
  the next stream on the channel at once if the feed turns into the provider's offline
  placeholder, and when the feed has delivered no new video for about as long as its hidden
  reserve can cover (this includes a playlist that keeps updating while its segments fail) (about 20 seconds on 10-second segments; brief errors are ridden out on the same feed,
  and a stuck playlist is re-checked first). The backup is joined only as far back as the outage
  lasted, so the program continues instead of replaying; if the first backup is dead too, the
  outage keeps counting until a working stream is reached. If the main feed is already dead
  when you tune in, the backup gets the normal full cushion. The connection to Dispatcharr and
  Channels DVR stays open and timestamps continue where the old feed stopped. Add backup streams
  with [`dispatcharr_backup_streams.py`](#backup-streams-app).
- **Never kills a channel that has no backup.** Without a backup it keeps retrying the same feed
  (and never plays the black placeholder) until the provider comes back.
- **Tells a lagging server from a channel restart.** A server that is slightly behind is waited
  out instead of replayed; a real channel restart is rejoined without the timeline jumping back.
- **Survives Dispatcharr's own restarts.** If Dispatcharr's health check restarts the stream, the
  new copy picks up at the exact point the old one stopped sending, with its reserve intact (it
  remembers the position per channel, so this works even when Dispatcharr reconnects on a
  different provider login).
- **Backs off politely.** Connection-limit responses (503, 509, 429) wait longer and respect
  `Retry-After` (up to 30 seconds).
- **One continuous stream, no re-encoding.** A single `ffmpeg -c copy` remuxer delivers MPEG-TS to
  Dispatcharr, so quality and CPU use are unchanged. AES-128 encrypted streams, fMP4 segments and
  byte-range playlists are supported too.

You watch about 45 seconds behind live on providers with 10-second segments (less on providers whose playlists are short).

### Requirements

- **Dispatcharr 0.32 or newer** (tested on 0.32.0). The profile uses Python, the `requests` and
  `psycopg` modules and `ffmpeg`, which are all part of Dispatcharr's Docker image. Nothing is
  installed. It reads each channel's backup stream list from Dispatcharr's own database, using the
  database settings Dispatcharr already passes to its stream processes.
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
   - It starts with `-u -c '` and ends with `' {streamUrl} {userAgent} {channelId}`.
   - Paste it as is. Don't edit it, don't join the lines, and don't add or remove quotes. The
     program is wrapped in single quotes and contains none itself, which is what lets it sit in
     the Parameters box safely.
5. Press **Save**.

If you used an earlier version of this profile, replace its Parameters with the new block. The new
one ends with `{channelId}`; without it, failover to backup streams can't work.

### Turn it on

- **For all channels:** **Settings > Stream Settings > Default Stream Profile**, choose the new
  profile, and save. Channels that have their own stream profile set keep it.
- **For one channel:** edit the channel and set **Stream Profile** to the new profile.

A profile only applies to streams that start after you save. To switch a channel that is already
playing, change to another channel in Channels DVR, wait about 10 seconds so Dispatcharr stops the
old stream, then tune back.

### Recommended settings

- **Dispatcharr:** **Settings > Proxy Settings > Buffering Timeout** of at least **30** seconds, so
  Dispatcharr's own stream switching doesn't step in before the profile's failover.
- **Channels DVR:** the Dispatcharr source should use **Stream Format: MPEG-TS**. The profile always
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
| `...; switching to stream N of M` | The feed died or went to the offline placeholder; the profile moved to the next backup stream. |
| `joining the backup stream seamlessly (offset Xs)` | The backup's timestamps were shifted to continue the old feed's timeline. |
| `...; no backup stream on this channel, so staying on this feed` | The feed is down and the channel has no backup; the profile keeps waiting for it. |
| `resuming after Dispatcharr restart` | Dispatcharr restarted the stream; the profile picked up where it stopped. |
| `could not read backup streams (...)` | The profile couldn't read the channel's streams from Dispatcharr's database; failover is off for that stream. |
| `playlist error (...); retry N in Ns` | A request failed and is being retried. |
| `skipped segment N` | A segment could not be downloaded in time and was skipped so playback could continue. |

Because the profile releases video at a steady real-time pace, Dispatcharr's
`Stream unhealthy - no data for 10s` warning should no longer appear for streams using it. If it
does, the provider went quiet for longer than the profile's reserve.

### What it can't fix

- **The program can skip or repeat up to one segment when it fails over.** Two feeds of the same
  channel are separate encodes that run at different delays behind live, and providers don't send
  the wall-clock time needed to line them up. The switch itself is smooth (no stall or rebuffer),
  but the program continues from wherever the backup feed is.
- **Feeds with a different resolution or codec** can cause a brief glitch on the client at a
  failover while its decoder resets.
- **A late publish longer than about 25 seconds** on a 10-second-segment provider uses up the
  reserve and then the client's cushion. Providers with short playlists or short segments get a
  smaller reserve.
- **A provider outage longer than the playlist window** (usually about a minute) on a channel with
  no backup. The picture freezes; when the provider comes back, the profile recovers on its own,
  but video from the outage is lost.
- **A bad feed that keeps running**, for example audio out of sync. The profile can't detect it;
  use another stream for that channel.
- **Connection limits.** Providers often count a stream as active for a short time after the last
  request, so a failover can briefly look like two connections. If every connection on your account
  is busy at that moment, the switch may have to wait.

### Undo

Set **Default Stream Profile** (or the channel's **Stream Profile**) back to what it was, then
retune. You can delete the profile afterwards.

### Profile parameters

```text
-u -c 'import os,sys,time,json,signal,re,select,shutil,hashlib,threading,collections,subprocess as sp,urllib.parse as up
try:import requests
except ImportError:print("[hlsloop] python requests module not found",file=sys.stderr);sys.exit(1)
src,ua=sys.argv[1],sys.argv[2];chid=sys.argv[3] if len(sys.argv)>3 else ""
FF=shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
s=requests.Session();s.headers["User-Agent"]=ua
mono=time.monotonic;BUSY=(429,503,509);AV={1,2,3,4,6,15,17,27,36,129,135};run=1
def log(m):print("[hlsloop]",m,file=sys.stderr,flush=True)
ff=None;sig=None;cmap=None;off=0.0
def remux(why=None):
 global ff
 if why:log("restarting remuxer: "+why)
 if ff and ff.poll() is None:
  try:ff.stdin.close();ff.wait(5)
  except Exception:ff.kill()
 ff=sp.Popen([FF,"-hide_banner","-loglevel","error","-fflags","+discardcorrupt","-probesize","1000000","-analyzeduration","1000000","-i","pipe:0","-map","0:v?","-map","0:a?","-c","copy","-copyts"]+(["-output_ts_offset","%.3f"%off] if off else [])+["-f","mpegts","pipe:1"],stdin=sp.PIPE)
def gone():
 try:
  pl=select.poll();pl.register(1,select.POLLOUT)
  return any(e&(select.POLLERR|select.POLLHUP) for _,e in pl.poll(0))
 except Exception:return False
def put(b):
 try:ff.stdin.write(b);ff.stdin.flush()
 except Exception as e:
  if gone():bye()
  remux("remuxer rejected data (%s)"%e)
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
def pts0(d):
 try:
  for i in range(0,min(len(d),188*2000),188):
   p=d[i:i+188]
   if len(p)<188 or p[0]!=0x47:return None
   if not p[1]&64:continue
   af=(p[3]>>4)&3
   if af==2:continue
   o=5+p[4] if af==3 else 4
   if p[o:o+3]==bytes([0,0,1]) and p[o+3]>=0xC0 and p[o+3]<=0xEF and p[o+7]&0x80:
    b=p[o+9:o+14];return (((b[0]>>1)&7)<<30|b[1]<<22|(b[2]>>1)<<15|b[3]<<7|b[4]>>1)/90000.0
 except Exception:pass
 return None
keys={};warned=set()
def tsok(q,d):
 if not q["m"] and d[:3]!=b"ID3" and not (d[:1]==b"\x47" and (len(d)<189 or d[188]==0x47)):raise ValueError("segment is not MPEG-TS (%d bytes)"%len(d))
 return d
def fetch(q):
 h={"Range":"bytes=%d-%d"%(q["b"][1],q["b"][1]+q["b"][0]-1)} if q["b"] else {}
 r=s.get(q["u"],headers=h,timeout=(3,5));r.raise_for_status();d=r.content;k=q["k"]
 if not k:return tsok(q,d)
 if k[0]!="AES-128":
  if k[0] not in warned:log("unsupported encryption "+k[0]+", passing through");warned.add(k[0])
  return d
 if k[1] not in keys:kr=s.get(k[1],timeout=(3,5));kr.raise_for_status();keys[k[1]]=kr.content
 from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
 iv=int(k[2],16).to_bytes(16,"big") if k[2] else q["s"].to_bytes(16,"big")
 c=Cipher(algorithms.AES(keys[k[1]]),modes.CBC(iv)).decryptor();d=c.update(d)+c.finalize()
 return tsok(q,d[:-d[-1]] if d and 0<d[-1]<=16 else d)
st="/tmp/hlsloop-"+hashlib.md5((("ch"+chid) if chid.isdigit() else src).encode()).hexdigest()+".json"
last=None;rseen=None;seen=collections.deque(maxlen=30);stalled=False;oend=None;rcur=None;wlast=None;wpart=None;rpart=None;dep=0.0;wlead=0.0;sl=threading.Lock()
try:
 j=json.load(open(st))
 if time.time()-j["t"]<4:last=j["last"] if j["hasseq"] else None;rseen=set(j["seen"]);seen.extend(j["seen"]);off=j.get("off",0.0);oend=j.get("oend");rcur=j.get("cur");wlast=last;rpart=j.get("part");dep=j.get("dep") or 0.0;wlead=max(j.get("lead",0.0)-(time.time()-j["t"]),0.0);log("resuming after Dispatcharr restart")
except Exception:pass
hasseq=True
def save():
 try:
  with sl:
   with open(st+".tmp","w") as f:json.dump({"last":wlast,"part":wpart,"hasseq":hasseq,"seen":list(seen),"off":off,"oend":oend,"cur":cur,"dep":dep,"lead":wlead,"t":time.time()},f)
   os.replace(st+".tmp",st)
 except Exception:pass
def beat():
 while run:
  save();time.sleep(1)
bt=threading.Thread(target=beat,daemon=True);bt.start()
XC=re.compile(r"/(live|hls)/([^/]+)/([^/]+)/")
def feeds():
 if not chid.isdigit():return [src]
 try:
  import psycopg
  e=os.environ.get
  with psycopg.connect(dbname=e("POSTGRES_DB","dispatcharr"),user=e("POSTGRES_USER","dispatch"),password=e("POSTGRES_PASSWORD",""),host=e("POSTGRES_HOST","localhost"),port=int(e("POSTGRES_PORT","5432")),connect_timeout=3) as c:
   urls=[u for (u,) in c.execute("select s.url from dispatcharr_channels_channelstream cs join dispatcharr_channels_stream s on s.id=cs.stream_id where cs.channel_id=%s order by cs.order",(int(chid),)).fetchall() if u]
 except Exception as x:log("could not read backup streams (%s)"%x);return [src]
 m=XC.search(src)
 if m:urls=[XC.sub("/%s/%s/%s/"%(mm.group(1),m.group(2),m.group(3)),u,count=1) if (mm:=XC.search(u)) else u for u in urls]
 out=[src]
 for u in urls:
  if u not in out and XC.sub("/",u)!=XC.sub("/",src):out.append(u)
 return out
cur=src;pend=False
def handoff(why):
 global stalled,got,cur,media,last,lag,pend,rseen,bad,gap
 fl=feeds()
 if len(fl)<2:
  if not stalled:log(why+"; no backup stream on this channel, so staying on this feed")
  stalled=True;got=mono();return
 nxt=fl[(fl.index(cur)+1)%len(fl)] if cur in fl else fl[1]
 log(why+"; switching to stream %d of %d"%(fl.index(nxt)+1,len(fl)))
 cur=nxt;gap=(gap+mono()-got) if gap is not None else ((mono()-got) if last is not None else None);media=None;last=None;lag=None;rseen=None;seen.clear();bad.clear();pend=True;stalled=False;got=mono();save()
def bye():
 global run
 run=0;save();os._exit(0)
for g in (15,2,1):signal.signal(g,lambda*a:(ff and ff.kill(),os._exit(0)))
if rcur:cur=rcur
bw=re.compile(r"(?<![A-Z-])BANDWIDTH=(\d+)")
media=None;err=0;e0=None;gap=None;fresh=mono();got=mono();tgt=10;lag=None;bad=set();ids={};nid=0;dead=0;ph=0
Q=collections.deque();Qs=[0.0];ql=threading.Lock();moff=off;qend=oend
def join(segs):
 global dep
 n=max(1,min(max(3,int(-(-45//max(tgt,1)))),len(segs)-1));dep=n*tgt;return segs[-n:]
def cushlim():
 dp=dep or 3*tgt
 return max(min(3*tgt,dp-min(30,dp*2/3)),2*tgt)
def enq(it):
 with ql:Q.append(it);Qs[0]+=it[2] if it[0]=="seg" else 0
def writer():
 global wlast,wpart,oend,off,wlead
 lead=wlead;t=mono()
 while run:
  try:
   now=mono();lead=max(lead-(now-t),0.0);t=now;wlead=lead
   if ff.poll() is not None:remux("remuxer exited");time.sleep(1);continue
   with ql:
    it=Q[0] if Q else None;qs=Qs[0]
    if it is None:Qs[0]=0.0
   if it is None:time.sleep(0.05);continue
   if it[0]!="seg":
    with ql:Q.popleft()
    if it[0]=="remux":
     if it[3] is not None:off=it[3]
     remux(it[1])
    else:put(it[1])
    continue
   dp=dep or 3*tgt;cl=min(30,dp*2/3)
   if lead>=cl and qs<=dp-cl+tgt:time.sleep(0.02);continue
   d=it[1];k=min(65424,max(len(d)//40,188),len(d)-it[5]);cd=it[2]*k/max(len(d),1)
   put(d[it[5]:it[5]+k]);it[5]+=k;wpart=[it[3],it[5]];lead+=cd
   with ql:Qs[0]-=cd
   if it[5]>=len(d):
    with ql:Q.popleft()
    wlast=it[3];wpart=None
    if it[4] is not None:oend=it[4]+off+it[2]
   save()
  except Exception as e:log("writer error (%s)"%e);time.sleep(1)
remux()
threading.Thread(target=writer,daemon=True).start()
while not gone():
 try:
  r=s.get(media or cur,timeout=(5,10));r.raise_for_status()
  L=[x.strip() for x in r.text.splitlines() if x.strip()]
  if not L or not L[0].startswith("#EXTM3U"):raise ValueError("response is not an m3u8 playlist")
  v=[(int(m.group(1)) if (m:=bw.search(x)) else 0,L[i+1]) for i,x in enumerate(L[:-1]) if x.startswith("#EXT-X-STREAM-INF")]
  if v:media=up.urljoin(r.url,max(v)[1]);continue
  if "#EXT-X-ENDLIST" in L:
   ph+=1;handoff("provider is serving an offline placeholder")
   time.sleep(5 if stalled else min(ph,10))
   continue
  media=r.url;seq=0;hasseq=False;tgt=10;segs=[];key=None;mp=None;br=None;pe={};dur=0.0
  for x in L:
   if x.startswith("#EXT-X-MEDIA-SEQUENCE:"):seq=int(x[22:]);hasseq=True
   elif x.startswith("#EXT-X-TARGETDURATION:"):tgt=max(float(x[22:]),1)
   elif x.startswith("#EXT-X-KEY:"):a=attrs(x);key=None if a.get("METHOD","NONE")=="NONE" else (a.get("METHOD"),up.urljoin(r.url,a.get("URI","")),a.get("IV"))
   elif x.startswith("#EXT-X-MAP:"):mp=up.urljoin(r.url,attrs(x)["URI"])
   elif x.startswith("#EXT-X-BYTERANGE:"):br=x[17:]
   elif x.startswith("#EXTINF:"):
    try:dur=float(x[8:].split(",")[0])
    except ValueError:dur=0.0
   elif not x.startswith("#"):
    u=up.urljoin(r.url,x);pth=up.urlsplit(u).path;b=None
    if br:ln,_,of=br.partition("@");ln=int(ln);of=int(of) if of else pe.get(pth,0);pe[pth]=of+ln;b=(ln,of)
    segs.append(dict(n=seq,s=seq,u=u,p=pth+("@%d"%b[1] if b else ""),k=key,m=mp,b=b,d=dur));seq+=1;br=None;dur=0.0
  if not segs:raise ValueError("empty playlist")
  if not hasseq:
   for q in segs:
    if q["p"] not in ids:ids[q["p"]]=nid;nid+=1
    q["n"]=ids[q["p"]]
   if len(ids)>300:ids={k:v for k,v in ids.items() if v>nid-100}
   if rseen:last=max([q["n"] for q in segs if q["p"] in rseen],default=None)
  rseen=None;err=0;e0=None;dead=0;w=min(max(tgt/2,1),5)
  if last is None:new=join(segs) if gap is None else segs[-max(1,min(len(segs),int(-(-gap//max(tgt,1))))):];lag=None
  elif segs[-1]["n"]<last:
   if not ({q["p"].rsplit("/",1)[-1] for q in segs}&{x.rsplit("/",1)[-1] for x in seen}) or last-segs[-1]["n"]>50 or (lag is not None and mono()-lag>4*tgt):log("provider restarted the channel, rejoining");bad.clear();new=join(segs);lag=None;pend=True
   else:
    if lag is None:lag=mono();log("edge server is behind, waiting for it to catch up")
    new=[];media=None;w=1
  else:new=[q for q in segs if q["n"]>last];lag=None;gap=None
  lim=cushlim()
  if new:fresh=mono()
  if mono()-got>lim:
   handoff("no new video for %ds"%(mono()-got))
   if not stalled:continue
  elif not new and mono()-fresh>0.6*lim:log("playlist stale %ds, re-resolving"%(mono()-fresh));media=None;fresh=mono();continue
  if not new and mono()-fresh>0.8*tgt:w=min(w,max(tgt/10,0.5))
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
    if cmap is not None:enq(["remux","new init segment",0,None]);sig=None
    i=s.get(q["m"],timeout=(3,5));i.raise_for_status();enq(["init",i.content,0]);cmap=q["m"]
   p0=pts0(d)
   if pend:
    pend=False
    if p0 is not None and qend is not None:moff=qend-p0;enq(["remux","joining the backup stream seamlessly (offset %.1fs)"%moff,0,moff]);sig=None;cmap=None
   g=tsig(d)
   if g and sig and g!=sig:enq(["remux","stream layout changed",0,None]);cmap=None
   if g:sig=g
   o=0
   if rpart and rpart[0]==n and rpart[1]<len(d):o=rpart[1]-rpart[1]%188;rpart=None
   enq(["seg",d,q["d"] or tgt,n,p0,o])
   last=n;bad.discard(n);seen.append(q["p"]);gap=None;got=mono();stalled=False;ph=0
   if p0 is not None:qend=p0+moff+(q["d"] or tgt)
  time.sleep(w)
 except Exception as e:
  if e0 is None:e0=mono()
  if mono()-got>cushlim():
   handoff("feed unreachable for %ds"%(mono()-e0))
   if not stalled:err=0;e0=None;continue
  err+=1;media=None;rs=getattr(e,"response",None);c=getattr(rs,"status_code",0);ra=rs.headers.get("Retry-After","") if rs is not None else ""
  dl=min(int(ra),30) if ra.isdigit() else (min(2*err,10) if c in BUSY else min(err,5))
  if not stalled:dl=max(0.5,min(dl,cushlim()-(mono()-got)))
  log("playlist error (%s); retry %d in %ds"%(e,err,dl));time.sleep(dl)
bye()' {streamUrl} {userAgent} {channelId}
```

## Backup streams app

`dispatcharr_backup_streams.py` adds backup streams to your Dispatcharr channels in bulk, so the
[stream profile](#dispatcharr-stream-profile-for-hls-providers) (or Dispatcharr's own stream
switching) has another feed to move to when one dies. It finds other feeds of the same channel
among all your streams, shows them in a table for you to review, and writes the ones you approve
through Dispatcharr's API.

### How it matches

Nothing is specific to one provider or one channel name; it works with any M3U or Xtream source.

- **Pre-ticked ("Backups found")** only on strong evidence: the same channel name once provider
  prefixes (`US|`, `U.S|`, `TV|`) and quality words (HD, FHD, 4K, RAW) are ignored, the same call
  sign (for example `(WNBC)`), or the same provider guide ID (`tvg-id`). The guide ID is what
  catches renamed channels, such as an old "MSNBC" feed and a new "MS NOW" feed that the provider
  still tags with the same ID.
- **"Check suggestions"** are similar names the app isn't sure about. They are never ticked for
  you.
- **East and West/Pacific/Mountain/Central feeds are never mixed.** A name without a region counts
  as the East feed. A channel with a call sign in its name only matches that call sign.
- **Channels that already have backups** show as "Already has backups" and are left alone; nothing
  extra is pre-ticked on them.
- The **live search** box finds any stream as you type, so you can add feeds the app didn't find.

### Run it

```
# macOS / Linux
python3 dispatcharr_backup_streams.py

# Windows (or double-click the file)
py dispatcharr_backup_streams.py
```

It needs Python 3.8+ with Tk, like the matcher apps (see [Requirements](#requirements)). Enter the
Dispatcharr address (port 9191 is assumed) and an API key, or a username and password, then press
**Connect & Match**. The API key is the same one the Dispatcharr matcher uses
([how to get it](#get-a-dispatcharr-api-key)).

### Review and write

- **Show** filter: **Needs attention** (check suggestions, nothing found and errors), one entry per
  status (Backups found, Check suggestions, Nothing found, Already has backups, Written, Error), and
  Ticked to write.
  Plus a **Group** filter, a **Find** box (name, stream, channel number or a range like
  `500-600`) and sortable columns.
- Select a channel to see its backups in the panel below:
  - Cmd-click (Ctrl-click on Windows and Linux) or Shift-click to select several lines, then
    **Tick selected** or **Untick selected**. Double-click toggles one line.
  - The channel's existing backups are listed too. Untick one to mark it for removal; it is
    removed on the next Write. The main stream can never be removed.
  - Search on the right, select one or more streams and press **Add selected streams as
    backups**. Streams already on the channel are hidden from the results.
  - **Confirm this channel** accepts it and moves to the next one. Channels under Needs attention
    stay there until you confirm them. **Un-confirm** puts one back.
- **Approve selected** and **Approve all shown** tick every suggestion on the selected channels (or
  every channel shown) and confirm them in one step. Select rows with Cmd/Ctrl-click, Shift-click
  or Cmd/Ctrl-A. A handy pass: filter to **Check suggestions**, review, then Approve all shown.
- **Write ticked backups to Dispatcharr** saves everything that is ticked and confirmed. The main
  stream always stays first. Every channel is read back afterwards to confirm the change.

Anything the app can't do (nothing selected, everything already on the channel, and so on) is shown
in red in the status line instead of silently doing nothing.

### Autosave and undo

- **Autosave:** every tick, add, removal and confirmation is saved as you make it, and again when
  you close the window. The next Connect & Match offers to restore unsaved work. Written channels
  drop out of the saved work.
- **Undo:** before each Write, the original stream lists of the channels being changed are saved
  to an `undo_<date>.json` file. **Undo a previous run…** puts them back.

Files are kept in `~/.dispatcharr_backup_streams/` (Windows:
`%USERPROFILE%\.dispatcharr_backup_streams\`): `settings.json` (address and API key, readable only
by your user account), `session.json` (autosaved work), `undo_*.json` and `logs/backup_streams.log`.

### Adding a backup by hand

In Dispatcharr, open **Channels**, edit the channel, search for the other feed in the **Streams**
list and add it, then drag it below the current stream and save. The app does the same thing for
many channels at once.

## Removing provider prefixes from channel names

Many IPTV providers put a country or category prefix in front of every channel name, for example
`US| CNN HD`, `U.S| MSNBC HD` or `TV| MS NOW`. Dispatcharr can strip it with a regex find/replace,
so Channels DVR shows `CNN HD` and you can sort channels alphabetically.

**Find (Regex):**

```
^[^|]*\|\s*
```

**Replace:** leave it empty.

It removes everything from the start of the name up to and including the first `|`, plus any
spaces after it. Names without a `|` are left alone.

| Before | After |
|---|---|
| `US| CNN HD` | `CNN HD` |
| `U.S| MSNBC HD` | `MSNBC HD` |
| `TV| MS NOW` | `MS NOW` |
| `CNN HD` | `CNN HD` |

Where you set it depends on how your channels were created.

### Channels created by Auto Channel Sync

Set it on the group, so it is applied on every sync. If you only rename these channels by hand,
the next sync renames them back to the provider's name.

1. Open **M3U & EPG Manager** and edit your provider's M3U account.
2. Open **Groups & Auto Sync Settings**.
3. On each group that has **Auto Channel Sync** turned on, press the gear icon
   (**Configure advanced options for this group**).
4. Under **Name Transforms**, put the pattern in **Find (Regex)** and leave **Replace** empty.
   The preview shows how real stream names in that group will change.
5. Save, then refresh the M3U account so the sync runs and renames the channels.

Repeat for every auto-synced group.

### Renaming every channel at once (bulk edit)

Use this for channels you created yourself, or to rename everything in one pass. (Channels made
by Auto Channel Sync are renamed back on the next sync, so set the group's Name Transforms above
for those as well.)

1. Open **Channels**.
2. Select all the channels:
   - **If they fit on one page:** set **Page Size** at the bottom of the table to its largest value
     (500), then tick the checkbox in the table header to select every channel shown.
   - **If they span several pages:** tick the checkbox of the first channel, press the button that
     jumps to the last page, then **Shift-click** the checkbox of the last channel. Shift-click
     selects every channel in between.
3. Press **Edit** above the table. The bulk edit opens and shows **Selection: N channels**. Check
   that N is the number of channels you meant to rename before going on.
4. Under **Channel Name**, put `^[^|]*\|\s*` in **Find (Regex)** and leave **Replace** empty.
5. Save. Dispatcharr renames all the selected channels in one go. Channels without a `|` in their
   name are left as they are, so it is safe to include them in the selection.

Renaming only changes the name shown in Channels DVR. Gracenote station IDs, channel numbers,
streams and backup streams are not affected, and the station ID matcher and backup streams apps
ignore these prefixes when matching, so you can rename before or after using them.

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
