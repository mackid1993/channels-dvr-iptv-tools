# Channels DVR Station ID Matcher

Tools for feeding an IPTV provider into [Channels DVR](https://getchannels.com) through
[Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) or
[m3u-editor](https://github.com/m3ue/m3u-editor):

- **Station ID matcher apps.** Two small desktop apps, one for Dispatcharr and one for m3u-editor,
  that fill in the **Gracenote station ID** (`tvc-guide-stationid`) on your IPTV channels using the
  Gracenote data your own Channels DVR server already has access to. Once your channels carry the
  right station IDs, Channels DVR uses its Gracenote guide for them (two weeks of listings,
  artwork and logos) instead of your provider's EPG.
- **Two Dispatcharr stream profiles built specifically for Channels DVR**, one for providers that
  send **HLS** (recommended) and one for providers that only send **MPEG-TS**. Both hold the
  buffer inside Dispatcharr, feed Channels DVR at a steady real-time pace, give every device on a
  shared Channels DVR tuner a head start, and fail over to a channel's backup streams without
  pausing. They are copied and pasted into Dispatcharr from this README; there is nothing to
  install. See [HLS or MPEG-TS profile?](#hls-or-mpeg-ts-profile) to pick one.
- **A backup streams app** that finds other feeds of each channel and adds them to your Dispatcharr
  channels as backups in bulk, so the stream profile has somewhere to fail over to.

## Contents

- [What's in this repository](#whats-in-this-repository)
- [How it fits together](#how-it-fits-together)
- [Dispatcharr or m3u-editor?](#dispatcharr-or-m3u-editor)
- [Station ID matcher apps](#station-id-matcher-apps)
  - [What the apps do](#what-the-apps-do)
  - [Features](#features)
  - [Requirements](#requirements)
  - [Running an app](#running-an-app)
  - [Using the Dispatcharr app](#using-the-dispatcharr-app)
  - [Using the m3u-editor app](#using-the-m3u-editor-app)
  - [Where data is stored](#where-data-is-stored)
  - [Known limitations](#known-limitations)
- [HLS or MPEG-TS profile?](#hls-or-mpeg-ts-profile)
  - [Use an Xtream Codes account](#use-an-xtream-codes-account)
- [Recordings](#recordings)
- [Files the profiles write](#files-the-profiles-write)
- [Transcoding (optional)](#transcoding-optional)
  - [Why 3%](#why-3)
  - [Why turn it on](#why-turn-it-on)
  - [Settings](#settings)
  - [Use a GPU](#use-a-gpu)
- [HLS stream profile for Dispatcharr and Channels DVR](#hls-stream-profile-for-dispatcharr-and-channels-dvr)
  - [Why](#why)
  - [How it works](#how-it-works)
  - [Tuner sharing and the buffer, step by step](#tuner-sharing-and-the-buffer-step-by-step)
  - [Recordings](#recordings-1)
  - [HLS in, MPEG-TS out](#hls-in-mpeg-ts-out)
  - [Requirements](#requirements-1)
  - [What it sends to Channels DVR](#what-it-sends-to-channels-dvr)
  - [Create the profile](#create-the-profile)
  - [Turn it on](#turn-it-on)
  - [Recommended settings](#recommended-settings)
  - [Check that it's working](#check-that-its-working)
  - [What it can't fix](#what-it-cant-fix)
  - [Undo](#undo)
  - [HLS profile parameters](#hls-profile-parameters)
- [MPEG-TS stream profile for Dispatcharr and Channels DVR](#mpeg-ts-stream-profile-for-dispatcharr-and-channels-dvr)
  - [How it differs from the HLS profile](#how-it-differs-from-the-hls-profile)
  - [Requirements](#requirements-2)
  - [Create and turn it on](#create-and-turn-it-on)
  - [Check that it's working](#check-that-its-working-1)
  - [What it can't fix](#what-it-cant-fix-1)
  - [Tested](#tested)
  - [MPEG-TS profile parameters](#mpeg-ts-profile-parameters)
- [Backup streams app](#backup-streams-app)
  - [How it matches](#how-it-matches)
  - [Run it](#run-it)
  - [Review and write](#review-and-write)
  - [Autosave and undo](#autosave-and-undo)
  - [Where data is stored](#where-data-is-stored-1)
  - [Adding a backup by hand](#adding-a-backup-by-hand)
- [Removing provider prefixes from channel names](#removing-provider-prefixes-from-channel-names)
  - [Channels created by Auto Channel Sync](#channels-created-by-auto-channel-sync)
  - [Renaming every channel at once (bulk edit)](#renaming-every-channel-at-once-bulk-edit)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## What's in this repository

| Item | What it is | Use it with |
|---|---|---|
| [`dispatcharr_stationid_matcher.py`](dispatcharr_stationid_matcher.py) | Desktop app. Matches your channels to Gracenote station IDs and writes them straight to Dispatcharr through its API. | Dispatcharr |
| [`m3u_editor_stationid_matcher.py`](m3u_editor_stationid_matcher.py) | Desktop app. Same matching; applies the IDs through a small m3u-editor plugin that the app exports for you. | m3u-editor |
| [`plugin/`](plugin) | Source of that m3u-editor plugin, for reading. The app builds the installable zip. | m3u-editor |
| [`dispatcharr_backup_streams.py`](dispatcharr_backup_streams.py) | Desktop app. Finds other feeds of each channel and adds them as backup streams through Dispatcharr's API. | Dispatcharr |
| [HLS stream profile](#hls-stream-profile-for-dispatcharr-and-channels-dvr) (in this README) | **Recommended.** A Dispatcharr stream profile for Channels DVR, for providers that send HLS (`.m3u8`): holds the buffer, paces the stream, sizes the head start for the devices watching, and fails over. | Dispatcharr + Channels DVR |
| [MPEG-TS stream profile](#mpeg-ts-stream-profile-for-dispatcharr-and-channels-dvr) (in this README) | The same for providers that only send raw MPEG-TS (`.ts`). Use it only when your provider has no HLS. | Dispatcharr + Channels DVR |
| [`stream_profiles/`](stream_profiles) | Readable source of the two stream profiles (`hlsloop.py`, `tsloop.py`). The blocks you paste from this README are generated from these files. | Reading and editing the profiles |
| [`scripts/`](scripts) | `build-profiles.sh` and `minify_profile.py`, which turn the sources into the pasteable blocks. GitHub Actions runs them on every push and commits the updated README. | Maintainers |

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
else. The stream profile runs inside Dispatcharr and talks to your provider and your Channels DVR server.

## Dispatcharr or m3u-editor?

If you already run one, use it; both work with Channels DVR. If you're choosing:

| | Dispatcharr | m3u-editor |
|---|---|---|
| Saving Gracenote IDs from the matcher | Written directly through Dispatcharr's API | Pasted into a plugin action (m3u-editor's API can't set them) |
| HLS provider hiccups | The built-in stream profiles give up after 3 quick reconnects. Use the [HLS stream profile](#hls-stream-profile-for-dispatcharr-and-channels-dvr) in this README. | Passes the provider's HLS through; Channels DVR fetches the next segment |
| Adding backup streams in bulk | The [backup streams app](#backup-streams-app) | By hand, or m3u-editor's auto-merge |
| Provider that only sends MPEG-TS | The [MPEG-TS stream profile](#mpeg-ts-stream-profile-for-dispatcharr-and-channels-dvr) in this README, or the built-in proxy | Works, with no special TS buffering |
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
   - **Current ID not in lineups** (gray): the channel already has an ID that isn't in your
     cable, satellite or OTA lineups, which is normal for streaming channels. It is left alone and
     doesn't count as needing attention.
4. Let you fix the rest by hand: the table starts on **Needs attention**, and each channel shows
   numbered suggestions.
5. Save the IDs you ticked after you confirm, then read them back to verify every one.

Fuzzy matches are never saved automatically. Anything uncertain needs your pick.

### Features

- **Multiple countries at once** (`USA:10001, CAN, GBR:SW1A`), for lists that mix them. Lineups
  from all of them are merged into one index.
- **Fast review**: click a channel, then click a station (or press 1-9 to pick it and jump to the
  next channel). Return moves on without a change. The search box above the station list looks
  through your lineups, or all of Gracenote, and you can also type an ID. Stations outside your
  lineups can be picked like any other.
- **Word-order independent matching** that handles names like "Nat Geo Wild HD", "C-SPAN 2",
  "QVC 2", run-together names like "SHOXBET", and provider prefixes like `US|`.
- **Gracenote-wide search** when nothing in your lineups is a good match, clearly labeled
  "not in your lineups".
- **Fix or remove existing IDs**: channels that already have an ID are included, a different pick
  shows as "Will replace ID", and **Remove ID** clears one. The confirmation lists every
  replacement and removal.
- **Bulk changes**: Shift-click or Cmd/Ctrl-click several channels, then **Tick selected**,
  **Untick selected** or **Mark reviewed**. Right-click a channel for more actions.
- **Filters** with live counts (Needs attention, Ticked, Left as is and every status), a group
  dropdown, a Find box (name, ID, group, or a channel-number range like `500-600`) and sortable
  columns. Columns can be dragged as wide as you like.
- **Your work is autosaved** and offered back after a restart or crash.
- **Your servers and API key are remembered** between runs, and the app connects by itself at
  launch when they are complete (see [Where data is stored](#where-data-is-stored)).
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
3. Press **Connect & Match**. Once connected, the form folds into a one-line bar with
   **Connection…** and **Reload**.
4. Work through **Needs attention**: pick a station for each channel (click, or 1-9 to pick and
   move on), or press Return to leave it as is. **Match unmatched one by one…** jumps to the first
   one.
5. Press **Write N channels to Dispatcharr…** (Cmd/Ctrl-S) and confirm.

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
3. Work through **Needs attention** (click a station, or 1-9 to pick and move on), or use
   **Match unmatched one by one…** for a guided pass. Then press **Copy N IDs for m3u-editor…**
   (Cmd/Ctrl-S) and confirm.
4. The **Apply in m3u-editor** window lists every change and the steps: in m3u-editor open
   **Extensions > Extensions > Gracenote Station IDs > Actions > Apply Station IDs**, pick the
   playlist, paste, and confirm. **Open m3u-editor** and **Copy again** are in that window.
5. When the run shows as completed under Run History, press **Verify in m3u-editor**. Anything not
   applied yet stays ticked.

The app reads each channel's current station ID from the playlist output, because the API doesn't
return it. A channel hidden from the output (for example disabled) shows no current ID; the status
line says when that happens. Picking an ID for it still works.

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
Windows, paste the path into Explorer's address bar. **File > Open log** opens the log
directly.

| File | Contents |
|---|---|
| `settings.json` | Server addresses, countries, and your API key or token (Dispatcharr: also the username, never the password). Readable only by your user account. |
| `session.json` | Autosaved picks, used for the restore prompt. |
| `window.json` | Window size and layout. |
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

## HLS or MPEG-TS profile?

Xtream-style providers usually offer each channel two ways. Which one your provider sends decides
which profile you can use:

- **HLS** (`.m3u8`): the provider publishes a playlist of short video files (segments, usually
  10 seconds each) and keeps about the last minute of them available.
- **MPEG-TS** (`.ts`): the provider sends one continuous stream over a single connection, starting
  from a short burst of recent video.

**Use the HLS profile if your provider offers HLS. It is the recommended profile.** The MPEG-TS
profile exists for providers that don't offer HLS.

| | HLS profile (recommended) | MPEG-TS profile (fallback) |
|---|---|---|
| Best source | **Xtream Codes account** (its `.ts` addresses are read as HLS automatically) | **Xtream Codes account** |
| Provider stream URLs | `.m3u8`, or Xtream `.ts` addresses (switched to `.m3u8` automatically) | `.ts` (or no extension) only |
| Reserve held in Dispatcharr | about 60 s (the provider's whole playlist window) | about 20-26 s (only what the provider bursts on connect) |
| Provider hiccup or late video | Re-fetches the missed segments from the playlist; nothing is lost if it is still listed (usually about a minute) | Only the reserve covers it; video from an outage longer than the reconnect overlap is lost for good |
| Reconnects | Always continue the same timeline | Usually seamless (overlap is removed), but the provider can land on a different timeline (about 1 in 6 on our test provider), which shows as a small skip or repeat |
| Extra head start per extra device | 5 s each, up to about 40 s in total | 5 s each, capped at about 8-14 s so failover keeps its headroom |
| Backup failover (last resort), keepalive, stuck-feed recovery | Yes | Yes |
| CPU per stream | Very low (works on whole segments) | Low, about 1-3 % of one core (handles every 188-byte packet in Python) |
| Optional transcoding (`TRANSCODE="1080p60"` etc.) | Yes | Yes |
| Delay behind live | about 60 s | about 20 s |
| Recordings extended past the scheduled end | about 105 s (delay + 45 s), on top of your padding | about 65-80 s, on top of your padding |
| Log prefix | `[hlsloop stream]` | `[tsloop stream]` |

Both send **MPEG-TS** to Channels DVR. Only what they read from the provider differs.

Whichever you use, turn on [transcoding](#transcoding-optional) if your hardware allows it.

### Use an Xtream Codes account

**If your provider offers Xtream Codes (a server address, username and password), add it to
Dispatcharr as an Xtream Codes account rather than an M3U link.** It's the better choice with both
profiles:

- **Faster and more reliable channel lists.** Xtream Codes loads the channel list from
  `player_api.php`, which is usually far quicker than an M3U download. Dispatcharr gives up on an
  M3U download that sends nothing for 60 seconds, and some providers' `get.php` takes longer than
  that to build the list, so the refresh fails every time.
- **Both profiles handle it properly.** You don't need an HLS option in the provider's settings or
  an `output=` parameter.

On Xtream-style providers every live channel is available as HLS and as MPEG-TS at the same address,
differing only in the ending:

```
http://provider/live/<user>/<pass>/<id>.m3u8   (HLS)
http://provider/live/<user>/<pass>/<id>.ts     (MPEG-TS)
```

Dispatcharr stores every stream of an Xtream Codes account with the `.ts` ending, and has no
setting to change that. That's fine:

- **HLS profile:** it switches each `.ts` Xtream address to its `.m3u8` form before connecting
  (logging `input: provider stream URL is MPEG-TS; using its HLS form` once per tune). It does the
  same for every backup stream and after a Dispatcharr restart. So you get HLS from an Xtream Codes
  account even though Dispatcharr has no HLS option for it. Addresses that are already `.m3u8` are
  left exactly as they are, and only `/live/<user>/<pass>/<number>.ts` addresses are touched.
- **MPEG-TS profile:** it reads the `.ts` addresses as they are. An `.m3u8` address is refused
  with `this stream URL is HLS; use the HLS profile for it`.

**Setting it up:** in Dispatcharr add an M3U account of type **Xtream Codes**, with the server
address only (`http://provider`) and the username and password in their own fields (make sure
they are the same line's pair).

An M3U link still works: `output=m3u8` for the HLS profile, `output=ts` for the MPEG-TS profile.

## Recordings

Both profiles run a little behind the provider's live edge: that's the reserve that rides out late
or missing video (about 60 s for HLS, about 20 s for MPEG-TS). Channels DVR starts and stops
recordings by the clock, so without help the last minute of a program would still be inside the
profile when Channels DVR stops recording, and the ending would be cut off.

The profiles handle this for you:

- **They extend the recording in Channels DVR.** While a recording is running on a channel using
  the profile, the profile lengthens that recording (the same as editing its end time in Channels
  DVR) by the profile's own measured delay **plus 45 seconds** for the provider itself running
  behind the broadcast (IPTV providers usually run about 20-40 s behind). That's about 105 s past
  the end for HLS and about 65-80 s for MPEG-TS, never more than 3 minutes.
- **Your padding is kept.** The extension is added **on top of** whatever end padding the recording
  or its pass already has. If you record a show 30 minutes late because a game runs before it, it
  still gets its 30 minutes, plus the extension.
- **Only recordings made through this profile.** The profile asks Channels DVR which recordings are
  using this exact source and channel. Recordings Channels DVR makes from any other source (an
  antenna, another provider, a TV Everywhere source) are never touched, even if they list the same
  channel.
- **Once per recording.** Each recording's original length is saved in Dispatcharr's `/data`
  folder, so restarting Dispatcharr or its container mid-recording doesn't add the extension twice.
  See [Files the profiles write](#files-the-profiles-write).
- **Watching and recording at the same time is fine.** The stream itself is never changed for a
  recording, so a live viewer on the same channel sees nothing different.

In Dispatcharr's log the profile reports it as `recording: <show> on ch N extended by Ns on top of
its own padding (...)`. Channels DVR shows the later end time on the recording.

What it can't cover: if your provider runs more than about 45 seconds behind the broadcast, add a
little end padding for those channels yourself.

## Files the profiles write

Both profiles keep a few small files inside the Dispatcharr container. Nothing else is
written, and nothing is written outside the container.

| File | Where | What's in it | Why | Cleanup |
|---|---|---|---|---|
| `hlsloop-jobs-<channel>.json`, `tsloop-jobs-<channel>.json` | `/data` (Dispatcharr's persistent folder; `/tmp` if `/data` isn't writable) | The original length of each recording the profile has extended on that channel, with the time it was saved. A few dozen bytes per recording. | So a recording is extended only once, even if Dispatcharr or its container restarts mid-recording. | Written when a recording on that channel is first seen, before the recording is extended. Entries older than 24 hours are dropped whenever the file is updated, and every time a stream starts, the profile deletes any of these files that hasn't changed in 24 hours. A recording never needs its saved length longer than that. |
| `hlsloop-hw.txt`, `tsloop-hw.txt` | `/tmp` | The encoder transcoding picked (`vaapi`, `nvenc` or `cpu`). A few bytes. | So the encoder test runs once, not on every tune. Only written with transcoding on. | Re-checked when it is more than an hour old; `/tmp` is cleared whenever the Dispatcharr container restarts. |
| `hlsloop-<id>.json`, `tsloop-<id>.json` | `/tmp` | The stream's position: last segment sent, timeline offset, current feed, reserve depth and viewer head start. Under 2 KB. | So when Dispatcharr restarts a stream (for example after a stuck feed), the new copy carries on where the old one stopped. It's only used if it's less than 4 seconds old and the stream URL hasn't changed. | Rewritten about once a second while the channel plays, one file per channel. Not deleted by the profile; `/tmp` is cleared whenever the Dispatcharr container restarts. |

To remove everything, delete those files (`rm -f /data/*loop-jobs-*.json /tmp/hlsloop-* /tmp/tsloop-*`
inside the container) while nothing is playing.

## Transcoding (optional)

Both stream profiles can re-encode video instead of passing it through. It's off by default.

**Use transcoding whenever your hardware allows it**, ideally with a GPU (see [Use a GPU](#use-a-gpu)).
It is the only way the profiles can keep one steady format across backup feeds and refill the
reserve after a long outage. Without it, there is little more the profiles can do: a backup with
a different frame rate can upset the player, and a drained reserve only comes back on a retune.

> [!IMPORTANT]
> **With transcoding on, playback runs 3% slower for a while after a provider problem.** If the
> reserve stays below half for 30 seconds (about 30 s on HLS, 10 s on MPEG-TS), video and audio
> play 3% slower together, with the audio pitch unchanged, until the reserve is back to three
> quarters (about 45 s on HLS, 16 s on MPEG-TS). The rest of the time video plays at its real speed.
>
> - **Two clean switches per refill.** The encoder is restarted once when the slowdown starts and
>   once when it ends, with the speed set from the first frame. Nothing is changed in a running
>   encoder, so audio and video can't drift apart. Each restart waits for the next keyframe and
>   continues the timeline from the timestamps already sent, correcting for where the audio sits
>   in the stream, so the picture doesn't pause. This works whether or not the provider's HLS
>   segments start on keyframes.
> - **Refill rate:** about 1.8 s of reserve per minute, so a refill takes about 8 to 17 minutes on
>   HLS and 3 to 5 minutes on MPEG-TS.
> - **What you might see:** while it runs, a 60 fps source repeats one frame about twice a second.
>   On a smooth, fast camera pan that can show as a tiny hitch.
> - **Transcoding only:** without transcoding the audio is copied as-is, and slowing it would
>   mean stretching its timestamps, which causes crackle or drift. On the normal path the reserve
>   only comes back on a retune.
>
> See [Why 3%](#why-3) for the research behind the rate.

### Why 3%

The rate is a trade-off between refilling quickly and staying below what people can notice.

- **Motion.** In lab experiments, people comparing two moving patterns side by side can just tell
  speeds apart when they differ by roughly 5 to 10% (McKee, 1981; De Bruyn and Orban, 1988).
  That's the best case: ideal conditions and direct comparison. Judging one speed on its own, as
  a TV viewer does, is harder. 3% sits below the best-case threshold.
- **Speech.** Listeners notice a change in speaking tempo at about 5%, and slowdowns are harder to
  notice than speedups (Quene, 2007). Pitch is corrected, so voices don't drop in pitch.
- **Broadcast precedent.** For decades, 50 Hz countries played 24 fps films at 25 fps, which is
  4.2% faster, to hundreds of millions of viewers. The faster motion went largely unnoticed; the
  raised pitch was what people noticed, and pitch-corrected versions drew few complaints.
  Broadcast profanity-delay units such as Eventide's BD600 rebuild their delay the same way, at a
  rate their maker describes as subtle and transparent.
- **Where it can show.** Music is less forgiving: tempo changes of around 3% can be noticed, and
  trained musicians notice smaller changes than other listeners. A second screen showing the same channel in real time will
  drift away by about 1.8 s a minute. And someone who knows the slowdown is on and is watching for
  it may believe they see it; the fair test is a blind comparison.

3% stays under the motion and speech thresholds and under both broadcast precedents, and it
refills quickly enough that a refill is usually over within a quarter of an hour. The figures above come from the cited
studies and are approximate.

### Why turn it on

Every feed leaves Dispatcharr in the same format, so a failover to a backup with a different frame
rate or resolution doesn't make Channels DVR (Apple TV in particular) drop frames or drift the
audio. It also lets the reserve refill itself after a long outage (see the note above).

The costs: GPU (or CPU) time, a little picture quality, and a few seconds more delay behind live.

### Settings

At the very top of the profile code, set one line:

```
TRANSCODE="1080p60"
```

| Setting | Output | Bitrate |
|---|---|---|
| `"off"` | passed through untouched (default) | as the provider sends it |
| `"1080p60"` | 1080p, 59.94 fps | 6 Mbps |
| `"1080p30"` | 1080p, 29.97 fps | 4.5 Mbps |
| `"720p60"` | 720p, 59.94 fps | 4.5 Mbps |
| `"720p30"` | 720p, 29.97 fps | 3 Mbps |
| `"1080p50"`, `"1080p25"`, `"720p50"`, `"720p25"` | the same for 50 Hz countries | |

**Which frame rate?** Pick the highest one your channels use. 60 suits sports, news and most US
broadcast channels; a 30 fps channel just shows each frame twice, so nothing is lost. 30 is fine
for mostly movies and entertainment and is easier on weak hardware, but 60 fps sports will look
choppier. In 50 Hz countries (Europe, the UK, Australia and others) use 50 or 25 instead, since
60 or 30 there makes motion judder.

**Which size?** 1080p with a GPU, 720p on weak hardware or when encoding on the CPU. The bitrates
match what IPTV providers typically send, so quality holds without making the stream bigger.

Audio is always stereo AAC at 48 kHz. An unknown setting counts as `"off"`.

### Use a GPU

Encoding 1080p60 video on the CPU takes most of a modern core per stream, and small machines
(Intel N100s, many NAS boxes, ARM) can't keep up at all. A GPU does the encoding in dedicated
hardware: on an Intel iGPU a stream costs roughly a third of one CPU core, mostly for decoding and
scaling.

The profile picks an encoder on its own the first time it transcodes and logs which one it used:

1. **Intel Quick Sync or AMD** (VAAPI), if `/dev/dri/renderD128` exists in the container.
2. **NVIDIA** (NVENC), if the NVIDIA runtime is available.
3. **The CPU** (`libx264`), otherwise. Use a 720p setting if you have to run here.

Dispatcharr's image already includes all three encoders. The container just needs access to the
GPU:

**Intel or AMD, Docker Compose:**
```yaml
services:
  dispatcharr:
    devices:
      - /dev/dri:/dev/dri
```

**Intel or AMD, `docker run`:** add `--device /dev/dri:/dev/dri`.

**Intel or AMD, Unraid template:** add `--device=/dev/dri` to **Extra Parameters**.

**NVIDIA** needs the NVIDIA driver on the host plus the NVIDIA Container Toolkit (on Unraid, the
**Nvidia Driver** plugin provides both).

**NVIDIA, Docker Compose:**
```yaml
services:
  dispatcharr:
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=all
```
(Newer Compose versions also accept `deploy: resources: reservations: devices:` with
`driver: nvidia` and `capabilities: [gpu]` instead of `runtime: nvidia`.)

**NVIDIA, `docker run`:** add `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all`
(or `--gpus all -e NVIDIA_DRIVER_CAPABILITIES=all`).

**NVIDIA, Unraid template:** add `--runtime=nvidia` to **Extra Parameters**, then add two
variables: `NVIDIA_VISIBLE_DEVICES` set to `all` (or the GPU UUID shown on the Nvidia Driver
plugin page) and `NVIDIA_DRIVER_CAPABILITIES` set to `all`.

Recreate the container after the change. To check it, tune a channel and look for
`transcoding with Intel/AMD graphics (VAAPI)` or `transcoding with NVIDIA graphics (NVENC)` in
Dispatcharr's log. `transcoding with the CPU` means the container can't see the GPU.

## HLS stream profile for Dispatcharr and Channels DVR

This is the **HLS profile** (recommended). For providers that only send MPEG-TS, see
[MPEG-TS stream profile for Dispatcharr and Channels DVR](#mpeg-ts-stream-profile-for-dispatcharr-and-channels-dvr).

**This profile is built specifically for Channels DVR watching through Dispatcharr.** It works out
which Channels DVR channel each stream is, asks Channels DVR how many devices are watching it, and
sizes the buffer for that. Other players still get a steady stream, but the viewer buffer only
works with Channels DVR. If you don't use Channels DVR, use one of Dispatcharr's built-in profiles.

### Why

IPTV providers that serve HLS (`.m3u8`) publish live video as a stream of short segments. Now
and then a segment arrives late, the provider stalls, refuses a request, restarts the channel, or
replaces a dead channel with a black "offline" placeholder. Dispatcharr's built-in stream profiles
start playback right at the live edge and give up after three quick reconnects, so those problems
show up in Channels DVR as buffering, a black screen or a dropped channel.

Channels DVR also shares one tuner between every device watching the same channel. A device that
joins later starts at the newest video Channels DVR has, so a cushion built up in the first
player doesn't help the second one.

### How it works

The profile replaces Dispatcharr's stream fetcher with a small Python program that runs inside
Dispatcharr. It holds the cushion itself and feeds Channels DVR at a steady pace, the way an HDMI
encoder does:

- **The reserve lives in the profile, not in the players.** It joins the provider's whole
  playlist window (about 60 seconds behind live on 10-second segments) and keeps almost all of
  it as a private reserve. When the provider publishes late, the reserve keeps video flowing to
  every device at once. When the provider catches up, the reserve refills at download speed.
- **Paced by video timestamps.** Video leaves the profile at exactly real time, timed by each
  frame's timestamp rather than by bytes, so players receive it smoothly and every device on a
  shared tuner gets the same stream.
- **A small head start for the players, sized for Channels DVR.** One viewer gets about 4 seconds
  ahead (more on low-bitrate channels, so Dispatcharr's ~256 KB chunks never leave the player
  short). Every time another Channels DVR device joins the channel, including one rejoining
  after another left, the profile sends a 5-second head start out of its reserve. Channels DVR
  shares one connection per tuner, so every device on it gets the extra cushion, and a device
  that joins late doesn't start empty. A device leaving never takes the cushion back, so the
  others never drain.
- **The reserve has a floor and rebuilds itself.** Head starts stop once the reserve is down to
  about 20 seconds, so late segments from the provider are always covered (a device joining at
  the floor gets only its own startup buffer). When no device has joined for 2 minutes and the
  reserve is under 30 seconds, the profile sends video about 1% slower than real time, which is
  too small to see or hear. The players' extra cushion slowly moves back into the reserve, about
  10 seconds every 17 minutes, without ever pausing the output. Tuning the channel fresh starts
  with the full reserve.
- **Finds the channel in Channels DVR on its own.** It finds your Channels DVR server from
  Dispatcharr's client list (the client whose user agent is Channels DVR), finds the Channels DVR
  source whose URL is a Dispatcharr M3U output (`/output/m3u/...`), and locates this channel in
  that playlist by its Dispatcharr UUID. Channels DVR lists an M3U source's channels in playlist
  order, so the same position gives the Channels DVR channel number. It checks that the counts and
  names line up first. If the source is out of date (you changed channels in Dispatcharr since
  Channels DVR last refreshed it), the profile refreshes that source in Channels DVR, the same
  as the **Refresh** button, and checks again. Playback never waits for this. Every Dispatcharr
  playlist you've added to Channels DVR is handled the same way.
- **Switches to a backup only as a last resort.** Every feed of a channel is at a different point
  in the program (two encodes of the same channel can be minutes apart, and providers don't send
  the clock time needed to line them up), so a switch always means jumping forward or back in the
  show. The profile therefore stays on the feed you're watching through anything it can ride out,
  and you get a freeze that resumes at the same point instead of a jump:
  - **Late segments, stalls, stale playlists, provider restarts, refused or timed-out requests
    (407, 429, 503, read timeouts):** it keeps retrying the same feed. If video comes back within
    about a minute, everything missed is fetched from the provider's playlist, so you continue
    exactly where you were.
  - **It switches only when** the feed serves the provider's offline placeholder (at once), has
    delivered **no new video for 60 seconds** (sooner if the reserve is about to run out, so a
    dead feed is replaced before the picture freezes), sends segments with **audio and video badly
    apart** (two in a row), gets stuck mid-download, or has stayed **far slower than real time (below 0.75x)
    for 5 minutes**. A merely slow feed never triggers a switch.
  - **Before switching** it checks that the backup answers with a real playlist (not the offline
    placeholder). A feed that failed is left alone for a while (1, 2, 4, 8, then 10 minutes), so
    a channel whose feeds are all down waits instead of hopping between dead feeds. Once on a
    backup it stays there as long as it works.
  - **At the switch** the backup is joined as deep as the outage lasted (deep enough to rebuild
    the reserve after a long one), timestamps continue where the old feed stopped, and the new
    remuxer starts before the old one finishes, so the output doesn't pause. A backup on a
    different provider keeps its own login. Add backup streams with
    [`dispatcharr_backup_streams.py`](#backup-streams-app).
- **Keeps pacing smooth through a switch.** The output stays at real time when it changes feeds,
  including to a backup with a different bitrate or frame rate, so players don't speed up or
  stall.
- **Holds back broken segments.** If a segment's audio and video are more than 3 seconds apart
  (seen after provider restarts), it isn't sent, so the player never plays sound from one moment
  over picture from another.
- **Never kills a channel that has no backup.** Without a backup it keeps retrying the same feed
  (and never plays the black placeholder) until the provider comes back.
- **Keeps Dispatcharr from restarting the stream.** Whenever the output would go quiet for 2
  seconds, it sends MPEG-TS null packets, which every player discards, so Dispatcharr's
  "no data" health check doesn't kill the stream. They are sent only while the profile is
  working: if its writer or its provider fetch gets stuck (for example a provider that trickles a
  byte every few seconds, which never trips a read timeout), the keepalive stops, Dispatcharr
  restarts the stream, and the new copy resumes where the old one stopped. After a stuck fetch it
  goes straight to the channel's next backup instead of the same stuck feed.
- **Tells a lagging server from a channel restart**, and **backs off politely** on
  connection-limit responses (503, 509, 429, honoring `Retry-After` up to 30 seconds).
- **Recordings keep their ending.** See [Recordings](#recordings-1).
- **Optional transcoding.** Off by default. See [Transcoding (optional)](#transcoding-optional).
- **No re-encoding by default.** `ffmpeg -c copy` delivers MPEG-TS to Dispatcharr, so quality and CPU use are
  unchanged. AES-128 encrypted streams, fMP4 segments and byte-range playlists are supported.

You watch about 60 seconds behind live on providers with 10-second segments (less on providers
whose playlists are short).

### Tuner sharing and the buffer, step by step

Channels DVR shares one tuner (one connection to Dispatcharr) between every device watching the
same channel. A device that joins later starts at the newest video Channels DVR has, so it can't
use a cushion that built up in the first player. The profile handles this by keeping the cushion
on the server and sizing what it hands out to the devices actually watching:

1. **First tune.** The profile joins the provider's whole playlist window, about 60 seconds of
   video on 10-second segments. It sends about 4 seconds ahead of real time to Channels DVR and
   keeps the other ~56 seconds as its reserve.
2. **Steady state.** Video goes out at exactly real time, paced by its timestamps. When the
   provider is late, the reserve covers the wait; when the provider catches up, the profile
   downloads the missed segments quickly and the reserve refills. Viewers don't see either.
3. **Counting viewers.** Every 2 seconds the profile asks Channels DVR how many devices are
   reading this channel's tuner (`/dvr/activity`).
4. **A device joins.** When the count goes up, the profile immediately sends an extra 5 seconds
   out of the reserve. Channels DVR passes it to every device on the tuner, so the newcomer
   starts with a cushion and the devices already watching gain one too. This happens on every
   join, including a device coming back after leaving.
5. **A device leaves.** Nothing is taken back. The output never slows or pauses, so the devices
   still watching keep their cushion.
6. **The floor.** Head starts stop when the reserve is down to about 20 seconds (two segments on
   10-second segments), which is what's needed to ride out a late segment and fail over. A device
   joining at the floor gets only its own startup buffer, and the log says `reserve at its floor`.
7. **The rebuild.** Once no device has joined for 2 minutes and the reserve is under 30 seconds,
   the profile sends video about 1% slower than real time. The players drain their extra
   cushion a little faster than it's topped up, so it moves back into the reserve: about 10
   seconds every 17 minutes. It stops when the reserve is back to 30 seconds, or when the
   players' lead is down to one join's worth (the single-viewer lead plus 5 seconds, about 9
   seconds). A 1% speed change is too small
   to see or hear, and the output never stops.
8. **Channel stops.** When the last device leaves, Dispatcharr ends the stream. The next tune
   starts fresh with the full reserve.

Example on a channel with 10-second segments:

| What happens | Lead in the players | Reserve in the profile |
|---|---|---|
| TV tunes the channel | 4 s | 56 s |
| Laptop joins | 9 s | 51 s |
| Laptop leaves | 9 s | 51 s |
| Laptop joins again | 14 s | 46 s |
| Phone joins, then more joins over the evening | up to 40 s | down to the 20 s floor |
| No joins for 2 minutes, reserve under 30 s | eases down by about 1% | rebuilds to 30 s |

If the profile can't identify the channel in Channels DVR (see **Finds the channel in Channels DVR on its own** above), it doesn't count viewers
and every device gets the single-viewer 4-second lead. Playback is otherwise unaffected.

### Recordings

The profile extends each recording made through it by its delay plus 45 seconds, on top of your
own padding, so recordings keep the end of the program. Details in [Recordings](#recordings).

### HLS in, MPEG-TS out

This is the part people mix up, so in plain English:

- **From your IPTV provider into Dispatcharr: HLS.** Your provider's stream URLs must be HLS
  (they end in `.m3u8`). That's what the profile reads.
- **From Dispatcharr out to Channels DVR: MPEG-TS.** The profile always turns the stream into
  MPEG-TS. Leave Dispatcharr's output on MPEG-TS, and set the Channels DVR source's
  **Stream Format** to **MPEG-TS**.

```
 IPTV provider  --HLS (.m3u8)-->  Dispatcharr + this profile  --MPEG-TS-->  Channels DVR
```

Dispatcharr 0.33 and newer can also *send* HLS to clients (**Settings > Stream Settings >
Default Output Format**, or a user's **Output Format Override**). **Don't use it with this
profile.** Keep the output format on MPEG-TS:

- The profile is built and tested for MPEG-TS output to Channels DVR. Its tuner-sharing head
  start depends on it.
- Dispatcharr's own release notes say HLS output can't tell when a player has closed, so a
  channel can keep a provider connection busy after everyone stops watching. They recommend
  MPEG-TS unless a client specifically needs HLS.
- Channels DVR plays MPEG-TS from Dispatcharr directly, with the lowest delay.

Channels DVR's own **Stream** playback setting in its apps (HLS between the Channels DVR server
and a device) is a separate thing. It doesn't change what Dispatcharr sends, and you can leave
it at its default.

### Requirements

- **Channels DVR**, with Dispatcharr added as a **Custom Channels** M3U source using Dispatcharr's
  M3U output URL (`http://DISPATCHARR:9191/output/m3u/...`) and **Stream Format: MPEG-TS**, with
  Dispatcharr's output format left on **MPEG-TS** (see [HLS in, MPEG-TS out](#hls-in-mpeg-ts-out)).
  Dispatcharr must be able to reach the Channels DVR server on port 8089.
- **Dispatcharr 0.32 or newer** (tested on 0.32.0). The profile uses Python, the `requests`,
  `psycopg` and `redis` modules and `ffmpeg`, all part of Dispatcharr's Docker image. Nothing is
  installed. It reads each channel's UUID and backup streams from Dispatcharr's own database, and
  its client list from Dispatcharr's Redis, using settings Dispatcharr already passes to its
  stream processes.
- **Your provider must offer HLS** for the channels. **Use an Xtream Codes account if you can**
  (see [Use an Xtream Codes account](#use-an-xtream-codes-account)): its `.ts` addresses are
  switched to their `.m3u8` form automatically. Other providers' stream URLs must end in `.m3u8`. This is only what Dispatcharr receives from the provider; what it
  sends to Channels DVR stays MPEG-TS. Check under **Streams** in
  Dispatcharr. If they end in `.ts` or have no extension, switch the provider's M3U account to
  HLS output if your provider offers it, and refresh the account. With MPEG-TS URLs every tune
  fails with `response is not an m3u8 playlist` in the Dispatcharr log; keep those channels on a
  built-in profile.

### What it sends to Channels DVR

The profile reads from Channels DVR (`/settings`, `/devices/<source>/channels`, `/dvr/activity`
and `/dvr/jobs`). It changes only two things: when a Dispatcharr source in Channels DVR is out of
date it asks Channels DVR to refresh that source, and it lengthens recordings made through it (see
[Recordings](#recordings)).

### Create the profile

1. In Dispatcharr, open **Settings > Stream Profiles** and press **Add Stream Profile**.
2. **Name:** anything you like, for example `Channels DVR HLS`.
3. **Command:** type exactly:

   ```
   python
   ```

4. **Parameters:** copy the **whole** block under
   [HLS profile parameters](#hls-profile-parameters) below (on GitHub, use the copy button in the
   block's top-right corner) and paste it into the Parameters box.
   - It starts with `-u -c '` and ends with `' {streamUrl} {userAgent} {channelId}`.
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

### Recommended settings

- **Dispatcharr:** **Settings > Proxy Settings > Buffering Timeout** of at least **30** seconds, so
  Dispatcharr's own stream switching doesn't step in before the profile's failover.
- **Channels DVR:** keep **tuner sharing** on. The profile is built around it.

### Check that it's working

Tune a channel, then look at Dispatcharr's log (for Docker, `docker logs dispatcharr`). The
running process shows up as `python -u -c import os,sys,...`. Messages from the profile start
with `[hlsloop stream]`:

| Message | Meaning |
|---|---|
| `input: provider stream URL is MPEG-TS; using its HLS form` | Dispatcharr had the Xtream `.ts` address; the profile reads the `.m3u8` form instead. Normal with Xtream Codes accounts. |
| `viewer buffer: this stream is Channels DVR <source> ch N` | The profile found this channel in Channels DVR. |
| `viewer buffer: N viewers on Channels DVR ch N, sent the new viewer a Ns head start (lead now Ns, reserve Ns)` | Another device joined; every device on the tuner got the extra head start. `reserve at its floor` means the reserve was too low to give the full 5 seconds. |
| `viewer buffer: reserve rebuilt to Ns` | The reserve finished refilling after joins used part of it. |
| `viewer buffer: Channels DVR source <source> is out of date, refreshing it` | The Channels DVR source didn't match Dispatcharr's playlist, so the profile refreshed it. |
| `viewer buffer: off ...` / `viewer buffer: waiting ...` | The viewer buffer is off or waiting for Channels DVR on this stream (the reason follows); playback is unaffected and uses the single-viewer head start. It keeps checking, so a recording or viewer that arrives later is still picked up. |
| `catching up N segments` | The provider stalled; the profile downloaded what it missed and refilled the reserve. |
| `playlist stale Ns, re-resolving` | The playlist stopped updating; the profile requested it again. |
| `edge server is behind, waiting for it to catch up` | A provider server was a few segments behind; nothing was replayed. |
| `provider restarted the channel, rejoining` | The provider restarted the channel; the profile rejoined it. |
| `...; switching to stream N of M` | The feed died or went to the offline placeholder; the profile moved to the next backup stream. |
| `stream N of M failed; not using it again for Ns` / `backup stream N of M is not answering; not trying it again for Ns` | A feed failed or a backup didn't answer; it is skipped for that long. |
| `...; no backup stream is answering right now, so staying on this feed` | Every backup is down too; the profile waits and checks them again. |
| `audio and video are Ns apart in this feed; holding it back` / `audio and video back in step; rejoining smoothly` | The provider sent segments with audio and video out of step; they were held back instead of played. |
| `recording: <show> on ch N extended by Ns on top of its own padding (...)` | A recording on this channel was lengthened so it keeps the end of the program. See [Recordings](#recordings). |
| `continuing on the new stream timeline seamlessly (offset Xs)` | After a switch to a backup or a provider channel restart, the new stream's timestamps were shifted to continue where the old ones stopped. |
| `...; no backup stream on this channel, so staying on this feed` | The feed is down and the channel has no backup; the profile keeps waiting for it. |
| `resuming after Dispatcharr restart` | Dispatcharr restarted the stream; the profile picked up where it stopped. |
| `could not read backup streams (...)` | The profile couldn't read the channel's streams from Dispatcharr's database; failover is off for that stream. |
| `playlist error (...); retry N in Ns` | A request failed and is being retried. |
| `skipped segment N` | A segment could not be downloaded in time and was skipped so playback could continue. |
| `provider is delivering 0.NNx real time and the reserve fell Ns to Ns; switching to stream N of M` | The feed stayed far too slow for 5 minutes; the profile moved to the next backup. |
| `output: no video to send for 2s, sending keepalive packets so the stream stays open` / `output: video flowing again after Ns without video` | A gap in the provider's delivery was covered by keepalive packets. Occasional short ones are normal. |
| `reserve: empty, video now arrives only as fast as the provider publishes it` / `reserve: refilled to Ns` | The provider fell behind for longer than the reserve covers. This is the line that matches visible buffering. |
| `input: fetch loop stuck for Ns, stopping keepalive so Dispatcharr restarts the stream` | The provider connection hung; Dispatcharr restarts the stream. |
| `the previous process got stuck reading this feed; switching to stream N of M` | After that restart, the profile moved to the next backup. |
| `restarting remuxer: reserve down to Ns; playing 3 percent slower, pitch unchanged, until it is back to Ns` / `restarting remuxer: reserve back to Ns; back to real speed` | Transcoding only: a refill started or finished (see [Transcoding (optional)](#transcoding-optional)). |
| `status: main stream, reserve Ns, client lead Ns, ... last 10 min: N playlist retries, N catch-ups, N switches, Ns keepalive` | A summary every 10 minutes. A healthy session shows a full reserve and few or no switches. |

Dispatcharr's `Stream unhealthy - no data for 10s` warning should not appear for streams using
this profile. **A healthy session** logs the channel being found, the occasional `catching up`, and
a `status:` line every 10 minutes. **Look closer** if you see `reserve: empty` often, repeated
switches, or `fetch loop stuck`.

### What it can't fix

- **A switch to a backup jumps forward or back in the program.** Two feeds of the same channel are
  separate encodes that can run minutes apart, and providers don't send the clock time needed to
  line them up. That's why the profile switches only as a last resort. A provider that restarts a
  channel by itself can cause the same kind of jump without any switch.
- **Riding out an outage costs reserve.** The profile waits for the feed to come back as long as
  its reserve lasts (up to 60 seconds), then switches just before the picture would freeze.
- **Feeds with a different resolution, frame rate or codec** can cause a brief flicker on the
  client at a failover while its decoder resets, and a different frame rate can make some players
  (Channels DVR on Apple TV) drop frames until you tune again. With transcoding off nothing is
  re-encoded to hide it; turn on [transcoding](#transcoding-optional) if it bothers you.
- **A reserve used up by a long outage** only comes back on a retune, unless
  [transcoding](#transcoding-optional) is on.
- **A provider outage longer than the reserve** on a channel with no backup. The picture freezes;
  when the provider comes back, the profile recovers on its own, but video from the outage is lost.
- **Problems after Dispatcharr**, such as a device's Wi-Fi dropping out for longer than its own
  head start.
- **A bad feed that keeps running**, for example audio out of sync. The profile can't detect it;
  use another stream for that channel.
- **Connection limits.** Providers often count a stream as active for a short time after the last
  request, so a failover can briefly look like two connections. If every connection on your account
  is busy at that moment, the switch may have to wait.

### Undo

Set **Default Stream Profile** (or the channel's **Stream Profile**) back to what it was, then
retune. You can delete the profile afterwards.

### HLS profile parameters

This block is generated from [`stream_profiles/hlsloop.py`](stream_profiles/hlsloop.py) by `scripts/build-profiles.sh`, which GitHub Actions runs on every push. To change the profile, edit the source file, not this block.

<!-- BEGIN hlsloop -->
```text
-u -c '# ======================================================================
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
# provider problem keeps the reserve below half for 30 seconds, video and
# audio play 3% slower together (pitch unchanged) until it is back to
# three quarters. Costs a few seconds more delay
# and some GPU time. Give the Dispatcharr container a GPU
# (/dev/dri for Intel or AMD, the NVIDIA runtime for NVIDIA); the README
# shows how. Without one it falls back to the CPU.
# ======================================================================
TRANSCODE = "off";TCP = {"1080p60": (1080, 59.94, "6M"), "1080p30": (1080, 29.97, "4.5M"), "720p60": (720, 59.94, "4.5M"), "720p30": (720, 29.97, "3M"), "1080p50": (1080, 50, "6M"), "1080p25": (1080, 25, "4.5M"), "720p50": (720, 50, "4.5M"), "720p25": (720, 25, "3M")};TC_HEIGHT, TC_FPS, TC_VBITRATE = TCP.get(str(TRANSCODE).lower().strip(), (0, 0, ""));TRANSCODE = TC_HEIGHT > 0;TC_LEAD = 4.0;import os, sys, time, json, signal, re, select, shutil, hashlib, threading, collections, subprocess as sp, urllib.parse as up
try:import requests
except ImportError:print("[hlsloop] python requests module not found", file=sys.stderr);sys.exit(1)
src, ua = (sys.argv[1], sys.argv[2]);chid = sys.argv[3] if len(sys.argv) > 3 else "";cdvr = (sys.argv[4] if len(sys.argv) > 4 else os.environ.get("CHANNELS_DVR", "")).strip().rstrip("/");HSX = re.compile("(/live/[^/?]+/[^/?]+/[0-9]+)\\.ts(?=$|\\?)")
def hsu(u):return HSX.sub("\\1.m3u8", u or "", count=1)
if HSX.search(src):src = hsu(src);print("[hlsloop stream] input: provider stream URL is MPEG-TS; using its HLS form", file=sys.stderr, flush=True)
FF = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg";s = requests.Session();s.headers["User-Agent"] = ua;mono = time.monotonic;BUSY = (429, 503, 509);AV = {1, 2, 3, 4, 6, 15, 17, 27, 36, 129, 135};run = 1;CL = 4.0;JB = 5.0;xb = 0.0;pk = 1;lj = 0.0;RF = 30.0;WR = 95443.717688888;fq = collections.deque();fql = threading.Lock();shut = {};wb = time.monotonic();rb = time.monotonic();RBT = 45.0;skf = False;rstk = False;DEADT = 60.0;PM = 45.0;LG = threading.Lock();CNT = collections.Counter();CK = threading.Lock();ns = 0
def inc(k, v=1):
 with CK:CNT[k] += v
def log(m):
 try:
  with LG:sys.stderr.write("[hlsloop stream] " + str(m) + "\n");sys.stderr.flush()
 except Exception:pass
ff = None;sig = None;cmap = None;off = 0.0;tout = 0.0;HW = None;STL = collections.deque()
def hwpick():
 global HW
 if HW:return HW
 hf = "/tmp/hlsloop-hw.txt"
 try:
  if time.time() - os.path.getmtime(hf) < 3600:HW = open(hf).read().strip()
 except Exception:pass
 if not HW:
  HW = "cpu";t_ = ["-f", "lavfi", "-i", "color=s=320x240:d=0.3", "-frames:v", "5", "-f", "null", "-"]
  try:
   if os.path.exists("/dev/dri/renderD128") and sp.run([FF, "-hide_banner", "-loglevel", "error", "-vaapi_device", "/dev/dri/renderD128"] + t_[:4] + ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"] + t_[4:], capture_output=True, timeout=15).returncode == 0:HW = "vaapi"
   elif sp.run([FF, "-hide_banner", "-loglevel", "error"] + t_[:4] + ["-c:v", "h264_nvenc"] + t_[4:], capture_output=True, timeout=15).returncode == 0:HW = "nvenc"
  except Exception:pass
  try:open(hf, "w").write(HW)
  except Exception:pass
 log("transcoding with " + {"vaapi": "Intel/AMD graphics (VAAPI)", "nvenc": "NVIDIA graphics (NVENC)", "cpu": "the CPU (no hardware encoder found)"}[HW]);return HW
def frs(f):
 for a, b in ((59.94, "60000/1001"), (29.97, "30000/1001"), (23.976, "24000/1001"), (47.952, "48000/1001")):
  if abs(f - a) < 0.01:return b
 return "%g" % f
SLOW = 1.03;SPD = 1.0;SPA = 1.0;RP = [None, 0.0];ADJ = [0.0];OFS = [0.0];EI = {};RQ = collections.deque(maxlen=6000);lowt = None;ltk = -1000000000.0
def rstep(now, qs, lo, hi, win):
 global SPD, lowt, ltk
 while RQ and now - RQ[0][0] > win:RQ.popleft()
 qm = min((q for _, q in RQ))
 if SPD == 1.0:
  if qm >= lo:lowt = None;return
  if lowt is None:lowt = now
  if now - lowt < 30 or now - ltk < 60:return
  SPD = SLOW;lowt = None;ltk = now;RP[1] = now;RP[0] = "reserve down to %.0fs; playing 3 percent slower, pitch unchanged, until it is back to %.0fs" % (qm, hi)
 elif qm >= hi:SPD = 1.0;ltk = now;RP[1] = now;RP[0] = "reserve back to %.0fs; back to real speed" % qm
def dif(a, b):return (a - b + WR / 2) % WR - WR / 2
def pests(p):
 if not p[1] & 64:return None
 af = p[3] >> 4 & 3
 if not af & 1:return None
 o = 5 + p[4] if af == 3 else 4
 if o + 14 > 188 or p[o:o + 3] != b"\x00\x00\x01":return None
 sid = p[o + 3]
 if not (192 <= sid <= 239 or sid == 189):return None
 fl = p[o + 7] & 192
 if not fl & 128:return None
 b = p[o + 14:o + 19] if fl == 192 and o + 19 <= 188 else p[o + 9:o + 14];return (sid, ((b[0] >> 1 & 7) << 30 | b[1] << 22 | b[2] >> 1 << 15 | b[3] << 7 | b[4] >> 1) / 90000.0)
def iskey(p):
 af = p[3] >> 4 & 3
 if af == 3 and p[4] and p[5] & 64:return True
 o = 5 + p[4] if af == 3 else 4;b = bytes(p[o + 9 + p[o + 8]:]);i = b.find(b"\x00\x00\x01")
 while 0 <= i < len(b) - 3:
  n = b[i + 3]
  if not n & 128 and (i + 4 < len(b) and b[i + 4] == 1 and (n >> 1 & 63 in (19, 20, 21, 32, 33)) or n & 31 in (5, 7)):return True
  i = b.find(b"\x00\x00\x01", i + 3)
 return False
def kfo(d, a, b):
 for j in range(a, b - 187, 188):
  p = d[j:j + 188]
  if p[0] == 71 and p[1] & 64:
   q = pests(p)
   if q and 224 <= q[0] <= 239 and iskey(p):return j
 return None
def avlead(d, k):
 q = pests(d[k:k + 188]);vk = q[1] if q else None
 if vk is None:return 0.0
 for j in range(k, min(len(d), k + 188 * 3000) - 187, 188):
  p = d[j:j + 188]
  if p[0] == 71 and p[1] & 64:
   q = pests(p)
   if q and (192 <= q[0] <= 223 or q[0] == 189):return min(max(dif(vk, q[1]), 0.0), 1.0)
 return 0.0
def note(b):
 e = EI.get(ff.pid) if ff else None
 if not e or (e[3] is not None and e[4] is not None):return
 for j in range(0, len(b) - 187, 188):
  p = b[j:j + 188]
  if p[0] != 71 or not p[1] & 64:continue
  q = pests(p)
  if not q:continue
  if e[2] is None or dif(q[1], e[2]) < 0:e[2] = q[1]
  if 224 <= q[0] <= 239:
   if e[3] is None and iskey(p):e[3] = q[1]
  elif e[4] is None:e[4] = q[1]
  if e[3] is not None and e[4] is not None:return
def eofs(vk, av):
 e = EI.get(ff.pid) if ff else None
 if not e or e[2] is None or e[3] is None:return None
 return e[0] + dif(e[3], e[2]) + dif(vk, e[3]) * e[1] - av
def ffcmd():
 b = [FF, "-hide_banner", "-loglevel", "error", "-fflags", "+discardcorrupt", "-probesize", "1000000", "-analyzeduration", "1000000"]
 if not TRANSCODE:return b + ["-i", "pipe:0", "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-copyts"] + (["-output_ts_offset", "%.3f" % off] if off else []) + ["-f", "mpegts", "pipe:1"]
 h = hwpick();fr = frs(TC_FPS);g = str(int(round(2 * TC_FPS)));vf = "bwdif=mode=send_frame:deint=interlaced," + ("setpts=(PTS-STARTPTS)*%.4f+STARTPTS," % SPA if SPA != 1.0 else "") + "fps=%s,scale=-2:%d:flags=fast_bilinear" % (fr, TC_HEIGHT);rc = ["-b:v", TC_VBITRATE, "-maxrate", TC_VBITRATE, "-bufsize", TC_VBITRATE, "-g", g]
 if h == "vaapi":b += ["-vaapi_device", "/dev/dri/renderD128"];vc = ["-vf", vf + ",format=nv12,hwupload", "-c:v", "h264_vaapi"] + rc + ["-bf", "0"]
 elif h == "nvenc":vc = ["-vf", vf + ",format=yuv420p", "-c:v", "h264_nvenc", "-preset", "p4"] + rc + ["-bf", "0"]
 else:vc = ["-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "veryfast"] + rc
 af = "aresample=async=1000" + (",atempo=%.6f" % (1 / SPA) if SPA != 1.0 else "");return b + ["-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?"] + vc + ["-af", af, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-output_ts_offset", "%.3f" % OFS[0], "-f", "mpegts", "pipe:1"]
def rtgt():return dep or 3 * tgt
def remux(why=None, ofs=None):
 global ff, SPA
 if why:log("restarting remuxer: " + why)
 old = ff;SPA = SPD;RP[0] = None;OFS[0] = ofs if ofs is not None else tout + 1.4 - ADJ[0]
 if old:EI.pop(old.pid, None)
 ff = sp.Popen(ffcmd(), stdin=sp.PIPE, stdout=sp.PIPE);ADJ[0] = 0.0;EI[ff.pid] = [OFS[0], SPA, None, None, None]
 with fql:fq.append(ff)
 if old:
  shut[old.pid] = mono()
  try:old.stdin.close()
  except Exception:pass
OWT = [mono()];NUL = (b"G\x1f\xff\x10" + b"\xff" * 184) * 7
def pump():
 global skf;ob = 0;lw = mono();ka = None;stk = False
 while True:
  with fql:p = fq[0] if fq else None;more = len(fq) > 1
  if p is None:time.sleep(0.05);continue
  r, _, _ = select.select([p.stdout], [], [], 0.25)
  if not r and mono() - rb >= RBT and (not stk):stk = True;skf = True;log("input: fetch loop stuck for %.0fs, stopping keepalive so Dispatcharr restarts the stream" % (mono() - rb))
  elif stk and mono() - rb < RBT:stk = False;skf = False
  if not r and mono() - lw > 2 and (not ob % 188) and (mono() - wb < 10) and (mono() - rb < RBT):
   try:os.write(1, NUL);lw2 = lw;lw = mono()
   except OSError:bye()
   if ka is None:ka = lw2;log("output: no video to send for 2s, sending keepalive packets so the stream stays open")
  if not r:
   if more and mono() - shut.get(p.pid, mono()) > 5:
    try:p.kill()
    except Exception:pass
   elif more or p.poll() is None:continue
  b = os.read(p.stdout.fileno(), 65536) if r else b""
  if not b:
   if more:
    with fql:fq.popleft()
    try:p.wait(1)
    except Exception:pass
    shut.pop(p.pid, None)
   elif p.poll() is not None:time.sleep(0.05)
   continue
  try:
   while b:n = os.write(1, b);b = b[n:];ob += n;OWT[0] = mono()
   if ka is not None:inc("ka", mono() - ka);log("output: video flowing again after %.1fs without video" % (mono() - ka));ka = None
   lw = mono()
  except OSError:bye()
def gone():
 try:pl = select.poll();pl.register(1, select.POLLOUT);return any((e & (select.POLLERR | select.POLLHUP) for _, e in pl.poll(0)))
 except Exception:return False
def put(b):
 if TRANSCODE:note(b)
 try:ff.stdin.write(b);ff.stdin.flush()
 except Exception as e:
  if gone():bye()
  remux("remuxer rejected data (%s)" % e)
def attrs(x):return {k: v.strip(chr(34)) for k, v in re.findall("([A-Z0-9-]+)=(\\\"[^\\\"]*\\\"|[^,]*)", x.split(":", 1)[1])}
def tsig(d):
 try:
  if not d or d[0] != 71:return None
  pmt = None
  for i in range(0, min(len(d), 188 * 600), 188):
   p = d[i:i + 188]
   if len(p) < 188 or p[0] != 71:return None
   if not p[1] & 64:continue
   pid = (p[1] & 31) << 8 | p[2];af = p[3] >> 4 & 3
   if af == 2:continue
   o = 5 + p[4] if af == 3 else 4;o += 1 + p[o];sl = (p[o + 1] & 15) << 8 | p[o + 2];end = min(o + 3 + sl - 4, 188)
   if pid == 0 and p[o] == 0:
    for j in range(o + 8, end, 4):
     if p[j] << 8 | p[j + 1]:pmt = (p[j + 2] & 31) << 8 | p[j + 3];break
   elif pid == pmt and p[o] == 2:
    j = o + 12 + ((p[o + 10] & 15) << 8 | p[o + 11]);es = []
    while j + 5 <= end:es.append((p[j], (p[j + 1] & 31) << 8 | p[j + 2]));j += 5 + ((p[j + 3] & 15) << 8 | p[j + 4])
    return tuple(sorted((e for e in es if e[0] in AV)))
 except Exception:pass
 return None
PSK = None;nsk = 0
def pts0(d):
 global PSK;fb = None;fv = None;PSK = None
 try:
  for i in range(0, min(len(d), 188 * 2000), 188):
   p = d[i:i + 188]
   if len(p) < 188 or p[0] != 71:return None
   if not p[1] & 64:continue
   af = p[3] >> 4 & 3
   if af == 2:continue
   o = 5 + p[4] if af == 3 else 4
   if o + 14 <= 188 and p[o:o + 3] == bytes([0, 0, 1]) and (p[o + 3] >= 192) and (p[o + 3] <= 239) and p[o + 7] & 128:
    b = p[o + 9:o + 14];v = ((b[0] >> 1 & 7) << 30 | b[1] << 22 | b[2] >> 1 << 15 | b[3] << 7 | b[4] >> 1) / 90000.0
    if p[o + 3] >= 224:fv = v if fv is None else fv
    elif fb is None:fb = v
    if fv is not None and fb is not None:break
 except Exception:pass
 if fv is not None and fb is not None:PSK = fv - fb
 if fv is None or fb is None:return fv if fv is not None else fb
 return fv if abs(fv - fb) > 5 else min(fv, fb)
keys = {};warned = set()
def tsok(q, d):
 if not q["m"] and d[:3] != b"ID3" and (not (d[:1] == b"G" and (len(d) < 189 or d[188] == 71))):raise ValueError("segment is not MPEG-TS (%d bytes)" % len(d))
 return d
def fetch(q):
 h = {"Range": "bytes=%d-%d" % (q["b"][1], q["b"][1] + q["b"][0] - 1)} if q["b"] else {};r = s.get(q["u"], headers=h, timeout=(3, 5));r.raise_for_status();d = r.content;k = q["k"]
 if not k:return tsok(q, d)
 if k[0] != "AES-128":
  if k[0] not in warned:log("unsupported encryption " + k[0] + ", passing through");warned.add(k[0])
  return d
 if k[1] not in keys:kr = s.get(k[1], timeout=(3, 5));kr.raise_for_status();keys[k[1]] = kr.content
 from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes;iv = int(k[2], 16).to_bytes(16, "big") if k[2] else q["s"].to_bytes(16, "big");c = Cipher(algorithms.AES(keys[k[1]]), modes.CBC(iv)).decryptor();d = c.update(d) + c.finalize();return tsok(q, d[:-d[-1]] if d and 0 < d[-1] <= 16 else d)
st = "/tmp/hlsloop-" + hashlib.md5(("ch" + chid if chid.isdigit() else src).encode()).hexdigest() + ".json";last = None;rseen = None;seen = collections.deque(maxlen=30);stalled = False;oend = None;rcur = None;wlast = None;wpart = None;rpart = None;dep = 0.0;wlead = 0.0;sl = threading.Lock()
try:
 j = json.load(open(st))
 if time.time() - j["t"] < 4 and j.get("src") == src:last = j["last"] if j["hasseq"] else None;rseen = set(j["seen"]);seen.extend(j["seen"]);off = j.get("off", 0.0);oend = j.get("oend");rcur = j.get("cur");wlast = last;rpart = j.get("part");dep = j.get("dep") or 0.0;xb = float(j.get("xb", 0.0));pk = max(1, int(j.get("pk", 1)));rstk = bool(j.get("stk"));wlead = max(j.get("lead", 0.0) - (time.time() - j["t"]), 0.0);log("resuming after Dispatcharr restart")
except Exception:pass
hasseq = True
def save():
 try:
  with sl:
   with open(st + ".tmp", "w") as f:json.dump({"last": wlast, "part": wpart, "hasseq": hasseq, "seen": list(seen), "off": off, "oend": oend, "cur": cur, "src": src, "dep": dep, "lead": wlead, "xb": xb, "pk": pk, "stk": skf, "to": tout, "t": time.time()}, f)
   os.replace(st + ".tmp", st)
 except Exception:pass
def beat():
 while run:save();time.sleep(1)
bt = threading.Thread(target=beat, daemon=True);bt.start();XC = re.compile("/(live|hls)/([^/]+)/([^/]+)/")
def feeds():
 if not chid.isdigit():return [src]
 try:
  import psycopg;e = os.environ.get
  with psycopg.connect(dbname=e("POSTGRES_DB", "dispatcharr"), user=e("POSTGRES_USER", "dispatch"), password=e("POSTGRES_PASSWORD", ""), host=e("POSTGRES_HOST", "localhost"), port=int(e("POSTGRES_PORT", "5432")), connect_timeout=3) as c:urls = [hsu(u) for u, in c.execute("select s.url from dispatcharr_channels_channelstream cs join dispatcharr_channels_stream s on s.id=cs.stream_id where cs.channel_id=%s order by cs.order", (int(chid),)).fetchall() if u]
 except Exception as x:log("could not read backup streams (%s)" % x);return [src]
 m = XC.search(src)
 if m:urls = [XC.sub("/%s/%s/%s/" % (mm.group(1), m.group(2), m.group(3)), u, count=1) if (mm := XC.search(u)) and up.urlsplit(u).netloc == up.urlsplit(src).netloc else u for u in urls]
 out = [src]
 for u in urls:
  if u not in out and XC.sub("/", u) != XC.sub("/", src):out.append(u)
 return out
cur = src;pend = False;fbad = {};lpr = -1000000000.0
def mark(u):b = fbad.get(u, [0, 0]);fbad[u] = [mono() + min(60 * 2 ** b[1], 600), b[1] + 1];return fbad[u][0] - mono()
def probe(u):
 try:
  r = s.get(u, timeout=(2, 3));r.raise_for_status();t = r.text
  if "#EXTM3U" not in t:return False
  if "#EXT-X-STREAM-INF" in t:
   v = [l.strip() for l in t.splitlines() if l.strip() and (not l.startswith("#"))]
   if not v:return False
   r = s.get(up.urljoin(r.url, v[0]), timeout=(2, 3));r.raise_for_status();t = r.text
  return "#EXTINF" in t and "#EXT-X-ENDLIST" not in t
 except Exception:return False
def handoff(why, depth=None, fl=None):
 global stalled, got, cur, media, last, lag, pend, rseen, bad, gap, fst, lsw, lpr, ost;fl = fl or feeds()
 if len(fl) < 2:
  if not stalled:ost = got;log(why + "; no backup stream on this channel, so staying on this feed")
  stalled = True;got = mono();return
 if stalled and mono() - lpr < 30:got = mono();return
 lpr = mono();i0 = fl.index(cur) if cur in fl else 0;nxt = None;pt = mono()
 if not stalled:log("stream %d of %d failed; not using it again for %.0fs" % (i0 + 1, len(fl), mark(cur)))
 cand = [fl[(i0 + j) % len(fl)] for j in range(1, len(fl))];cand = [u for u in cand if fbad.get(u, [0])[0] <= mono()] or sorted(cand, key=lambda u: fbad.get(u, [0])[0])
 for u in cand:
  if mono() - pt > 6:break
  if probe(u):nxt = u;break
  log("backup stream %d of %d is not answering; not trying it again for %.0fs" % (fl.index(u) + 1, len(fl), mark(u)))
 if nxt is None:
  if not stalled:ost = got;log(why + "; no backup stream is answering right now, so staying on this feed")
  stalled = True;got = mono();return
 log(why + "; switching to stream %d of %d" % (fl.index(nxt) + 1, len(fl)));inc("sw");was = stalled;cur = nxt;gap = mono() - ost if was else gap + mono() - got if gap is not None else mono() - got if last is not None else None;media = None;last = None;lag = None;rseen = None;seen.clear();bad.clear();pend = True;stalled = False;got = mono()
 if depth:gap = depth
 dq.clear();rq.clear();fst = lsw = mono();save()
def bye():global run;run = 0;save();killall();os._exit(0)
def killall():
 with fql:ps = list(fq)
 for p in ps:
  try:p.kill()
  except Exception:pass
for g in (15, 2, 1):signal.signal(g, lambda *a: (killall(), os._exit(0)))
if rcur:cur = hsu(rcur)
bw = re.compile("(?<![A-Z-])BANDWIDTH=(\\d+)");media = None;err = 0;e0 = None;gap = None;fresh = mono();got = mono();ost = got;tgt = 10;lag = None;bad = set();ids = {};nid = 0;dead = 0;ph = 0;dq = collections.deque();rq = collections.deque();fst = mono();lsw = -1000000000.0;nsl = 0;Q = collections.deque();Qs = [0.0];ql = threading.Lock();moff = off;qend = oend
def join(segs):global dep;n = max(1, min(max(3, int(-(-60 // max(tgt, 1)))), len(segs)));dep = n * tgt;return segs[-n:]
def clcap():dp = dep or 3 * tgt;return max(min(CL, dp * 2 / 3), dp - 2 * tgt)
def cushlim():dp = dep or 3 * tgt;return max(min(3 * tgt, dp - min(CL + xb, clcap())), 2 * tgt)
def watch():
 global xb, pk, lj
 if not chid.isdigit():return
 e = os.environ.get;w = requests.Session();base = cdvr;dev = None;num = None;tmap = -1000000000.0;seen = None;t0 = mono();fails = 0;said = set();tj = -1000000000.0;jerr = set();jfail = False;og = None;jt = None;JF = ("/data" if os.access("/data", os.W_OK) else "/tmp") + "/hlsloop-jobs-%s.json" % chid
 try:
  for f_ in os.listdir(os.path.dirname(JF)):
   p_ = os.path.join(os.path.dirname(JF), f_)
   if f_.startswith(("hlsloop-jobs-", "tsloop-jobs-")) and time.time() - os.path.getmtime(p_) > 86400:os.remove(p_)
 except Exception:pass
 def say(k, m):
  if k not in said:said.add(k);log("viewer buffer: " + m)
 def ext(rj):
  nonlocal og, jfail;jw = requests.Session()
  try:
   with ql:rv = min(Qs[0] + max(wlead, 0.0), rtgt() + tgt)
   ad = min(int(rv + PM), 180);now_ = time.time()
   if og is None:
    try:og = json.load(open(JF));og = {a: b for a, b in og.items() if isinstance(b, list) and len(b) == 2}
    except Exception:og = {}
   def wog():
    nonlocal og;og = {a: b for a, b in og.items() if time.time() - b[1] < 86400};tf = JF + ".%d.tmp" % os.getpid()
    with open(tf, "w") as f:json.dump(og, f)
    os.replace(tf, JF)
   for jb in jw.get(base + "/dvr/jobs", timeout=5).json() or []:
    jid = str(jb.get("ID") or "")
    if jid not in rj:continue
    t1 = float(jb.get("Time") or 0);du = float(jb.get("Duration") or 0)
    if jid not in og:og[jid] = [du, now_];wog()
    nd = int(og[jid][0] + ad)
    if now_ < t1 + du - 30 and nd > du + 10:
     try:jb["Duration"] = nd;jw.put(base + "/dvr/jobs/" + up.quote(jid), json=jb, timeout=5).raise_for_status();log("recording: %s on ch %s extended by %ds on top of its own padding (%ds for this stream running behind, %ds for the provider running behind the broadcast)" % (jb.get("Name") or jid, num, nd - int(og[jid][0]), int(rv), int(PM)))
     except Exception as x:
      if jid not in jerr:jerr.add(jid);log("recording: could not extend %s (%s)" % (jb.get("Name") or jid, str(x)[:80]))
   jfail = False
  except Exception as x:
   if not jfail:jfail = True;log("recording: could not check or extend recordings (%s); will keep trying quietly" % str(x)[:80])
 try:
  import psycopg
  with psycopg.connect(dbname=e("POSTGRES_DB", "dispatcharr"), user=e("POSTGRES_USER", "dispatch"), password=e("POSTGRES_PASSWORD", ""), host=e("POSTGRES_HOST", "localhost"), port=int(e("POSTGRES_PORT", "5432")), connect_timeout=3) as c:uid = str(c.execute("select uuid from dispatcharr_channels_channel where id=%s", (int(chid),)).fetchone()[0])
 except Exception as x:say("db", "off (could not read this channel id: %s)" % x);return
 def nm(l):x = re.sub("\\\"[^\\\"]*\\\"", "", l);return x.split(",", 1)[1].strip() if "," in x else ""
 while run:
  try:
   if not base:
    import redis;rd = redis.Redis(host=e("REDIS_HOST", "localhost"), port=int(e("REDIS_PORT", "6379")), db=int(e("REDIS_DB", "0")), password=e("REDIS_PASSWORD") or None, socket_timeout=2)
    for m in rd.smembers("live:channel:%s:clients" % uid):
     h = rd.hgetall("live:channel:%s:clients:%s" % (uid, m.decode()))
     if b"channels-dvr" in h.get(b"user_agent", b"").lower():base = "http://%s:8089" % h[b"ip_address"].decode();break
    if not base:
     if mono() - t0 > 120:say("nocd", "waiting (no Channels DVR client on this stream yet)");time.sleep(30);continue
     time.sleep(5);continue
   if mono() - tmap > 300:
    tmap = mono();num = dev = None;st = w.get(base + "/settings", timeout=5).json()
    for k, v in st.items():
     mm = re.fullmatch("m3u\\.src\\.(.+)\\.url", k)
     if not mm or "/output/m3u" not in str(v):continue
     t = w.get(v, timeout=20).text.splitlines();ent = [(nm(t[i]), t[i + 1].strip()) for i in range(len(t) - 1) if t[i].startswith("#EXTINF")];hit = [i for i, (n, x) in enumerate(ent) if uid in x]
     if not hit:continue
     d = "M3U-" + mm.group(1)
     for tr in range(6):
      cl = w.get(base + "/devices/%s/channels" % d, timeout=10).json()
      if len(cl) == len(ent) and all((str(c.get("GuideName", "")).strip() == n for c, (n, x) in zip(cl, ent))):break
      if tr == 0:log("viewer buffer: Channels DVR source %s is out of date, refreshing it" % d);w.post(base + "/providers/m3u/sources/%s/refresh" % up.quote(mm.group(1)), timeout=30)
      time.sleep(1)
     else:say("drift" + d, "Channels DVR source %s still does not match the playlist after a refresh" % d);continue
     dev = d;num = str(cl[hit[0]]["GuideNumber"]);say("map" + dev + num, "this stream is Channels DVR %s ch %s" % (dev, num));break
    if num is None:say("nomap", "off for now (this channel is not in a Channels DVR source that matches the playlist)")
   if num is None:time.sleep(30);continue
   k = 0;nr = 0;rj = set();act = w.get(base + "/dvr/activity", timeout=3).json()
   for x in act.get("streams") or []:
    tu = x.get("tuner") or {}
    if tu.get("device_id") == dev and str(tu.get("channel")) == num:k = max(k, int(tu.get("readers") or 1))
   for x in act.get("recordings") or []:
    tu = x.get("tuner") or {}
    if tu.get("device_id") == dev and str(tu.get("channel")) == num and (x.get("state") == "recording"):nr += 1;rj.add(str(x.get("job_id") or ""))
   rj.discard("")
   if rj and mono() - tj > 30 and (not (jt and jt.is_alive())):tj = mono();jt = threading.Thread(target=ext, args=(set(rj),), daemon=True);jt.start()
   if k or nr:seen = mono()
   elif seen is None and mono() - t0 > 90:say("unseen", "waiting (ch %s not playing in Channels DVR yet)" % num);time.sleep(30);continue
   if k and k > pk:room = max(clcap() - CL - xb, 0.0);add = min(JB * (k - pk), room);xb += add;lj = mono();log("viewer buffer: %d viewers on Channels DVR ch %s, sent the new viewer a %.0fs head start (lead now %.0fs, reserve %.0fs)" % (k, num, add, CL + xb, (dep or 3 * tgt) - CL - xb) + ("" if add >= JB * (k - pk) else ", reserve at its floor"))
   if k:pk = k
   dp = dep or 3 * tgt
   if xb > JB and mono() - lj > 120 and (dp - CL - xb < RF):
    xb = max(JB, xb - 0.02)
    if xb == JB or dp - CL - xb >= RF:log("viewer buffer: reserve rebuilt to %.0fs" % (dp - CL - xb))
   fails = 0
  except Exception as x:
   fails += 1
   if fails == 3:log("viewer buffer: Channels DVR not reachable (%s)" % str(x)[:80])
   if not base and mono() - t0 > 120:time.sleep(28)
  time.sleep(2)
def stat():
 low = False;t0 = mono()
 while run:
  time.sleep(5)
  try:
   with ql:qs = Qs[0]
   dp = dep or 3 * tgt
   if ns and (not low) and (qs < 2):low = True;log("reserve: empty, video now arrives only as fast as the provider publishes it")
   elif low and qs >= dp / 2:low = False;log("reserve: refilled to %.0fs" % qs)
   if mono() - t0 >= 600:
    t0 = mono()
    with CK:c = dict(CNT);CNT.clear()
    log("status: %s stream, reserve %.0fs, client lead %.1fs, extra lead for shared viewers %.0fs, viewers %d; last 10 min: %d playlist retries, %d catch-ups, %d switches, %.0fs keepalive" % ("main" if cur == src else "backup", qs, wlead, xb, pk, c.get("pe", 0), c.get("cu", 0), c.get("sw", 0), c.get("ka", 0)))
  except Exception:pass
def tix(d):
 out = [];b = None;m = 0.0
 for i in range(0, len(d) - 187, 188):
  p = d[i:i + 188]
  if p[0] != 71 or not p[1] & 64:continue
  af = p[3] >> 4 & 3
  if af == 2:continue
  o = 5 + p[4] if af == 3 else 4
  if o + 14 <= 188 and p[o:o + 3] == bytes([0, 0, 1]) and (224 <= p[o + 3] <= 239) and p[o + 7] & 128:
   q = p[o + 9:o + 14];t = ((q[0] >> 1 & 7) << 30 | q[1] << 22 | q[2] >> 1 << 15 | q[3] << 7 | q[4] >> 1) / 90000.0
   if b is None:b = t
   m = max(m, (t - b + WR / 2) % WR - WR / 2);out.append((i, m))
 return out
def tpos(ix, x, dur):
 if x >= ix[-1][0] if ix else True:return dur if not ix or x >= ix[-1][0] + 188 else min(ix[-1][1], dur)
 lo, hi = (0, len(ix) - 1)
 while lo < hi:
  md = (lo + hi + 1) // 2
  if ix[md][0] <= x:lo = md
  else:hi = md - 1
 return min(ix[lo][1], dur) if ix[lo][0] <= x else 0.0
def enq(it):
 with ql:Q.append(it);Qs[0] += it[2] if it[0] == "seg" else 0
def writer():
 global wlast, wpart, oend, off, wlead, wb, tout;lead = wlead;t = mono();cpr = None;cpt = mono();cbv = None;cbt = mono()
 while run:
  try:
   now = mono();wb = now;lead = max(lead - (now - t), 0.0);t = now;wlead = lead
   if ff.poll() is not None:remux("remuxer exited");time.sleep(1);continue
   with ql:
    it = Q[0] if Q else None;qs = Qs[0]
    if TRANSCODE:RQ.append((now, qs))
    if it is None:Qs[0] = 0.0
   if it is None:time.sleep(0.05);continue
   if it[0] != "seg":
    with ql:Q.popleft()
    if it[0] == "remux":
     if it[3] is not None:off = it[3]
     remux(it[1])
    else:put(it[1])
    continue
   dp = rtgt();cb = max(CL, 255868 * it[2] / max(len(it[1]), 1) + 0.5);cbv = cb if cbv is None else min(cb, cbv + 0.02 * (now - cbt));cbt = now;cl = min(max(cbv, TC_LEAD if TRANSCODE else 0.0) + xb, clcap())
   with ql:rmx = any((x[0] == "remux" for x in list(Q)[1:5]))
   if rmx and qs > 4.0:cl = max(cl, min(4.0, clcap()))
   if cpr is not None and cl < cpr:cl = max(cl, cpr - 0.1 * (now - cpt))
   cpr = cl;cpt = now
   if lead >= cl and (qs <= dp - cl + tgt or (TRANSCODE and lead >= cl)) and (not (TRANSCODE and qs > 2 and (mono() - OWT[0] > 0.7) and (lead < clcap()))):time.sleep(0.02);continue
   if TRANSCODE and mono() - fst > 90:dp_ = dep or 3 * tgt;rstep(now, qs, dp_ * 0.5, dp_ * 0.75, 1.5 * tgt)
   d = it[1]
   if len(it) < 7:it.append(tix(d))
   k = min(65424, max(len(d) // 100 // 188 * 188, 188), len(d) - it[5]);e = it[5] + k;cd = (it[2] if e >= len(d) else tpos(it[6], e, it[2])) - tpos(it[6], it[5], it[2]) if it[6] else it[2] * k / max(len(d), 1);cd = max(cd, 0.0)
   if RP[0]:
    k_ = kfo(d, it[5], e) if now - RP[1] < 10 else it[5]
    if k_ is not None:
     if k_ > it[5]:put(d[it[5]:k_])
     w_ = RP[0];av_ = avlead(d, k_) if now - RP[1] < 10 else 0.0;q_ = pests(d[k_:k_ + 188]);o_ = eofs(q_[1], av_) if q_ and 224 <= q_[0] <= 239 else None
     if o_ is not None and abs(o_ - (tout + 1.4 - av_)) > 3:o_ = None
     if o_ is not None:cp_ = max(tpos(it[6], k_, it[2]) - tpos(it[6], it[5], it[2]) if it[6] else it[2] * (k_ - it[5]) / max(len(d), 1), 0.0);tout = o_ + av_ - 1.4 - cp_ * SPD;remux(w_, o_)
     else:ADJ[0] = av_;remux(w_)
     put(d[k_:e])
    else:put(d[it[5]:e])
   else:put(d[it[5]:e])
   it[5] += k;wpart = [it[3], it[5]];lead += cd * SPA;tout += cd * SPA
   with ql:Qs[0] -= cd
   if it[5] >= len(d):
    with ql:Q.popleft()
    wlast = it[3];wpart = None
    if it[4] is not None:oend = it[4] + off + it[2]
   save()
  except Exception as e:log("writer error (%s)" % e);time.sleep(1)
try:
 j_ = json.load(open(st))
 if time.time() - j_["t"] < 4 and j_.get("src") == src:tout = float(j_.get("to", 0.0))
except Exception:pass
remux();threading.Thread(target=pump, daemon=True).start();threading.Thread(target=writer, daemon=True).start();threading.Thread(target=watch, daemon=True).start();threading.Thread(target=stat, daemon=True).start()
if rstk:handoff("the previous process got stuck reading this feed")
while not gone():
 rb = mono()
 try:
  r = s.get(media or cur, timeout=(5, 10));r.raise_for_status();L = [x.strip() for x in r.text.splitlines() if x.strip()]
  if not L or not L[0].startswith("#EXTM3U"):raise ValueError("response is not an m3u8 playlist")
  v = [(int(m.group(1)) if (m := bw.search(x)) else 0, L[i + 1]) for i, x in enumerate(L[:-1]) if x.startswith("#EXT-X-STREAM-INF")]
  if v:media = up.urljoin(r.url, max(v)[1]);continue
  if "#EXT-X-ENDLIST" in L:ph += 1;handoff("provider is serving an offline placeholder");z = 5 if stalled else 0;rb = mono() + z;time.sleep(z);continue
  media = r.url;seq = 0;hasseq = False;tgt = 10;segs = [];key = None;mp = None;br = None;pe = {};dur = 0.0
  for x in L:
   if x.startswith("#EXT-X-MEDIA-SEQUENCE:"):seq = int(x[22:]);hasseq = True
   elif x.startswith("#EXT-X-TARGETDURATION:"):tgt = max(float(x[22:]), 1)
   elif x.startswith("#EXT-X-KEY:"):a = attrs(x);key = None if a.get("METHOD", "NONE") == "NONE" else (a.get("METHOD"), up.urljoin(r.url, a.get("URI", "")), a.get("IV"))
   elif x.startswith("#EXT-X-MAP:"):mp = up.urljoin(r.url, attrs(x)["URI"])
   elif x.startswith("#EXT-X-BYTERANGE:"):br = x[17:]
   elif x.startswith("#EXTINF:"):
    try:dur = float(x[8:].split(",")[0])
    except ValueError:dur = 0.0
   elif not x.startswith("#"):
    u = up.urljoin(r.url, x);pth = up.urlsplit(u).path;b = None
    if br:ln, _, of = br.partition("@");ln = int(ln);of = int(of) if of else pe.get(pth, 0);pe[pth] = of + ln;b = (ln, of)
    segs.append(dict(n=seq, s=seq, u=u, p=pth + ("@%d" % b[1] if b else ""), k=key, m=mp, b=b, d=dur));seq += 1;br = None;dur = 0.0
  if not segs:raise ValueError("empty playlist")
  if not hasseq:
   for q in segs:
    if q["p"] not in ids:ids[q["p"]] = nid;nid += 1
    q["n"] = ids[q["p"]]
   if len(ids) > 300:ids = {k: v for k, v in ids.items() if v > nid - 100}
   if rseen:last = max([q["n"] for q in segs if q["p"] in rseen], default=None)
  rseen = None;err = 0;e0 = None;dead = 0;w = min(max(tgt / 2, 1), 5)
  if last is None:new = join(segs) if gap is None else segs[-max(1, min(len(segs), int(-(-gap // max(tgt, 1))))):];lag = None
  elif segs[-1]["n"] < last:
   if not {q["p"].rsplit("/", 1)[-1] for q in segs} & {x.rsplit("/", 1)[-1] for x in seen} or last - segs[-1]["n"] > 50 or (lag is not None and mono() - lag > 4 * tgt):log("provider restarted the channel, rejoining at the live edge");bad.clear();k_ = [i for i, q in enumerate(segs) if q["p"] in seen];new = segs[k_[-1] + 1:] if k_ else segs[-max(2, min(len(segs), int(-(-(mono() - got) // max(tgt, 1))))):];lag = None;pend = True;last = last if new else segs[-1]["n"]
   else:
    if lag is None:lag = mono();log("edge server is behind, waiting for it to catch up")
    new = [];media = None;w = 1
  else:new = [q for q in segs if q["n"] > last];lag = None;gap = None
  lim = max(cushlim(), DEADT)
  if new:fresh = mono()
  if mono() - got > lim or (mono() - got > cushlim() and Qs[0] + max(wlead, 0.0) < 10.0):
   handoff("no new video for %ds" % (mono() - got))
   if not stalled:continue
  elif not new and mono() - fresh > 0.6 * cushlim():log("playlist stale %ds, re-resolving" % (mono() - fresh));media = None;fresh = mono();continue
  if not new and mono() - fresh > 0.8 * tgt:w = min(w, max(tgt / 10, 0.5))
  if last is not None and len(new) > 1:log("catching up %d segments" % len(new));inc("cu")
  for q in new:
   n = q["n"];d = None;rb = mono()
   for t in range(2):
    try:d = fetch(q);break
    except Exception as e:
     d = None;log("segment %d: %s" % (n, e))
     if getattr(getattr(e, "response", None), "status_code", 0) in BUSY:rb = mono() + 2;time.sleep(2)
   if d is None:
    if n in bad and sum((o["n"] > n for o in new)) >= 2:log("skipped segment %d" % n);last = n;continue
    bad.add(n);media = None;w = 1;break
   if q["m"] != cmap:
    if cmap is not None:enq(["remux", "new init segment", 0, None]);sig = None
    i = s.get(q["m"], timeout=(3, 5));i.raise_for_status();enq(["init", i.content, 0]);cmap = q["m"]
   p0 = pts0(d)
   if PSK is not None and 3 < abs(PSK) < WR - 60:
    nsk += 1
    if nsk == 1:log("audio and video are %.1fs apart in this feed; %s" % (PSK, "holding it back" if qend is not None else "passing it through (nothing playing yet)"))
    if nsk >= 2 and mono() - lpr >= 30:
     handoff("audio and video are %.0fs apart in this feed" % abs(PSK))
     if not stalled:nsk = 0;break
    if nsk * (q["d"] or tgt) < (30 if qend is not None else 0.1):last = n;bad.discard(n);seen.append(q["p"]);continue
   elif nsk:nsk = 0;pend = True;log("audio and video back in step; rejoining smoothly")
   if pend and p0 is not None:
    pend = False
    if qend is not None:moff = qend - p0;enq(["remux", "continuing on the new stream timeline seamlessly (offset %.1fs)" % moff, 0, moff]);sig = None;cmap = None
   g = tsig(d)
   if g and sig and (g != sig):enq(["remux", "stream layout changed", 0, None]);cmap = None
   if g:sig = g
   o = 0
   if rpart and rpart[0] == n and (rpart[1] < len(d)):o = rpart[1] - rpart[1] % 188;rpart = None
   enq(["seg", d, q["d"] or tgt, n, p0, o]);dq.append((mono(), q["d"] or tgt));ns += 1;last = n;bad.discard(n);seen.append(q["p"]);gap = None;got = mono();stalled = False;ph = 0
   if p0 is not None:qend = p0 + moff + (q["d"] or tgt)
  now = mono();SW = max(300.0, 30 * tgt)
  if cur in fbad and last is not None and (not stalled) and (now - fst > 120) and (now - got < 2 * tgt):fbad.pop(cur, None)
  with ql:qs = Qs[0]
  rq.append((now, qs + max(wlead, 0.0)))
  while dq and now - dq[0][0] > SW:dq.popleft()
  while rq and now - rq[0][0] > SW:rq.popleft()
  if not stalled and last is not None and (now - fst > SW) and rq:
   dp = dep or 3 * tgt;rt = (sum((x[1] for x in dq)) + tgt) / SW;lost = dp - max(wlead, 0.0) - qs;drop = max((x[1] for x in rq)) - (qs + max(wlead, 0.0))
   if rt >= 1.0 and drop < 0.05 * SW:nsl = 0
   if rt < 0.75 and lost > cushlim() and (drop >= 0.1 * SW) and (now - lsw > 300 * 2 ** nsl):
    lsw = now;nsl = min(nsl + 1, 4);why = "provider is delivering %.2fx real time and the reserve fell %.0fs to %.0fs" % (rt, drop, qs);fl = feeds()
    if len(fl) > 1:handoff(why, lost, fl);continue
    log(why + "; no backup stream on this channel, so staying on this feed")
  rb = mono() + w;time.sleep(w)
 except Exception as e:
  if e0 is None:e0 = mono()
  if mono() - got > max(cushlim(), DEADT) or (mono() - got > cushlim() and Qs[0] + max(wlead, 0.0) < 10.0):
   handoff("feed unreachable for %ds" % (mono() - e0))
   if not stalled:err = 0;e0 = None;continue
  err += 1;media = None;rs = getattr(e, "response", None);c = getattr(rs, "status_code", 0);ra = rs.headers.get("Retry-After", "") if rs is not None else "";dl = min(int(ra), 30) if ra.isdigit() else min(2 * err, 10) if c in BUSY else min(err, 5)
  if not stalled:dl = max(0.5, min(dl, max(cushlim(), DEADT) - (mono() - got)))
  log("playlist error (%s); retry %d in %ds" % (e, err, dl));inc("pe");rb = mono() + dl;time.sleep(dl)
bye()' {streamUrl} {userAgent} {channelId}
```
<!-- END hlsloop -->

## MPEG-TS stream profile for Dispatcharr and Channels DVR

**Use this only if your provider has no HLS.** See [HLS or MPEG-TS profile?](#hls-or-mpeg-ts-profile).
It does the same job as the [HLS profile](#hls-stream-profile-for-dispatcharr-and-channels-dvr) for providers
whose stream URLs are raw MPEG-TS (`.ts`), and is built specifically for Channels DVR in the same
way: it counts the devices watching and refreshes an out-of-date Channels DVR source the same way.

### How it differs from the HLS profile

Raw MPEG-TS can't fetch the past. Each connection starts with a burst of recent video (about
20-26 seconds on the provider it was tested with) and then runs at real time. So:

- **The reserve is the connect burst.** The profile keeps it queued, sends about 3 seconds ahead to
  Channels DVR, and paces the rest out at exactly real time on the video clock (per video frame
  group; audio-only channels use the audio clock).
- **Reconnects are stitched, not refetched.** A reconnect re-sends video that was already sent. The
  profile drops the overlap per video frame, keeps continuity counters and timestamps continuous,
  and starts the new connection on a keyframe. When the provider lands on a different timeline,
  it splices so playback continues with a small skip or repeat instead of a stall. When a reconnect
  lands too far behind to catch up in time, it replays rather than freezing.
- **Switching is a last resort, as in the HLS profile.** It switches to the next backup after 60 s
  without new video (sooner if its reserve is about to run out), on a feed whose audio and video
  are more than 3 seconds apart (held back; if no backup works it is shown after about 30 seconds,
  as in the HLS profile), on the offline placeholder, on a feed with no decodable video, or on a feed
  that has stayed slower than real time for 5 minutes. Refused or failed connections are retried
  on the same feed. A backup is joined only as deep as the outage lasted.
- **A provider that drifts behind live** (delivers slightly slower than real time for a long time)
  gets a fresh connection for a new burst, at most once every 5 minutes. This can cause the
  occasional small skip on a drifting channel.
- **Optional transcoding** works as in the HLS profile; see [Transcoding (optional)](#transcoding-optional).
- **The head start for extra devices is capped** at the burst depth minus 12 s (usually 8-14 s in
  total), so a late joiner can never eat the headroom failover needs.
- **Flood protection:** an offline channel that serves a recorded clip far faster than real time is
  read at playback speed, with at most 60 s queued.
- Everything else matches the HLS profile: backups checked before switching, failed feeds left
  alone for a while, smooth pacing through a switch, recording extensions (see
  [Recordings](#recordings)), gap-free remux at a switch, keepalive only while the profile is working, stuck-feed recovery that goes to the next backup, resume after a Dispatcharr
  restart, no hammering on a dead channel (retries at least 5 seconds apart), Retry-After honored.

### Requirements

- Everything under the HLS profile's [Requirements](#requirements-1), except the provider part:
- **Your provider's stream URLs must be MPEG-TS** (end in `.ts` or have no extension). **Use an
  Xtream Codes account if you can** (see [Use an Xtream Codes account](#use-an-xtream-codes-account)),
  or an M3U account with `output=ts`. This profile does no conversion; an `.m3u8`
  URL is refused with `this stream URL is HLS; use the HLS profile for it`.
- Dispatcharr's output and the Channels DVR source's **Stream Format** stay on **MPEG-TS**, as for
  the HLS profile.

### Create and turn it on

Same steps as [Create the profile](#create-the-profile) and [Turn it on](#turn-it-on), with the
block under [MPEG-TS profile parameters](#mpeg-ts-profile-parameters). Name it something like
`Channels DVR TS`. Command `python`.

### Check that it's working

Messages start with `[tsloop stream]`.

| Message | Meaning |
|---|---|
| `input: first video received from <host>, building the reserve` | The stream started. |
| `viewer buffer: ...` | Same as the HLS profile (channel found, head start sent, rebuild). `head start dropped` means the reserve didn't have room. |
| `stream error (...); reconnect N in Ns` then `input: video back after N reconnects (reserve Ns)` | The connection dropped and came back. Normal now and then. |
| `reconnected, dropping Ns already sent (reserve Ns)` | A reconnect overlapped what was sent; the repeat was removed. Normal. |
| `timeline jumped Ns inside the stream, re-syncing on this connection` / `restarting remuxer: ...` | The provider's timestamps jumped; the output timeline was kept continuous. |
| `reconnected Ns behind ... replaying instead of stalling` | A reconnect landed too far behind; a short stretch is shown again instead of freezing. |
| `provider fell Ns behind live ...; reconnecting for a fresh burst` | Drift refresh (see above). |
| `...; switching to stream N of M` | Failover to the next backup. |
| `stream N of M failed; not using it again for Ns` / `backup stream N of M is not answering; not trying it again for Ns` | A feed failed or a backup didn't answer; it is skipped for that long. |
| `recording: <show> on ch N extended by Ns on top of its own padding (...)` | A recording on this channel was lengthened so it keeps the end of the program. See [Recordings](#recordings). |
| `...; no backup stream on this channel, so staying on this feed` | The feed is down and there is no backup; it keeps retrying. |
| `output: no video to send for 3s, sending keepalive packets ...` / `output: video flowing again after Ns` | A gap was covered by keepalive packets. |
| `reserve empty: ... playback will pause until video arrives` / `reserve refilled to Ns` | The provider fell behind for longer than the reserve covers. This matches visible buffering. |
| `output: stopped for Ns and the reader is stuck; no keepalive, so Dispatcharr can restart the stream` (or `the writer is stuck`) | The provider connection hung; Dispatcharr restarts the stream, and the new copy goes to the next backup (`the previous process got stuck reading this feed`). |
| `input stream is arriving faster than real time ...` | Flood protection kicked in (usually an offline channel's placeholder clip). |
| `restarting remuxer: reserve down to Ns; playing 3 percent slower ...` / `... back to real speed` | Transcoding only: a refill started or finished, as in the HLS profile. |
| `status: stream N of M, reserve Ns, client lead Ns, ... last 10 min: N stream retries, N remuxes, N failovers, Ns keepalive` | A summary every 10 minutes. |

**A healthy session** shows the first-video line, the viewer-buffer lines, and a `status:` line every
10 minutes with a reserve around 15-25 s and few errors. **Look closer** if you see `reserve empty`
often, repeated `switching to stream`, or a reader stuck.

### What it can't fix

Everything listed for the HLS profile, plus:

- **Video from an outage longer than the reconnect overlap is lost.** HLS can re-fetch it from the
  playlist; raw TS can't.
- **A reconnect onto a different provider timeline** shows as a small skip or repeat.
- **Smaller cushion:** about 20 s instead of about 60 s, so a long provider stall reaches the
  players sooner.

### Tested

On a real provider, one stream at a time: CNN 70 minutes (including a real 71-minute timeline jump
the provider made mid-stream), ESPN 60 minutes and MSNBC 9 minutes, all with 0 stalls, the longest
output gap under 1 s, and 0 connection-limit errors. A live backup switch (main feed hung) and live
dead channels (offline and nonexistent) were also tested; the switch had 0 stalls and the dead
channels were retried about every 6 seconds with the keepalive covering the output. Through a real Channels DVR server, three devices joining one channel 40 s apart (plus a later join and leave) all played with 0 stalls; each extra device got its head start until the reserve reached the failover floor. Plus 47 offline
scenarios against a mock provider, covering reconnects, timeline jumps, failover, floods, drift,
stuck feeds and the tuner-sharing head start.

### MPEG-TS profile parameters

This block is generated from [`stream_profiles/tsloop.py`](stream_profiles/tsloop.py) by `scripts/build-profiles.sh`, which GitHub Actions runs on every push. To change the profile, edit the source file, not this block.

<!-- BEGIN tsloop -->
```text
-u -c '# ======================================================================
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
# provider problem keeps the reserve below half for 30 seconds, video and
# audio play 3% slower together (pitch unchanged) until it is back to
# three quarters. Costs a few seconds more delay
# and some GPU time. Give the Dispatcharr container a GPU
# (/dev/dri for Intel or AMD, the NVIDIA runtime for NVIDIA); the README
# shows how. Without one it falls back to the CPU.
# ======================================================================
TRANSCODE = "off";TCP = {"1080p60": (1080, 59.94, "6M"), "1080p30": (1080, 29.97, "4.5M"), "720p60": (720, 59.94, "4.5M"), "720p30": (720, 29.97, "3M"), "1080p50": (1080, 50, "6M"), "1080p25": (1080, 25, "4.5M"), "720p50": (720, 50, "4.5M"), "720p25": (720, 25, "3M")};TC_HEIGHT, TC_FPS, TC_VBITRATE = TCP.get(str(TRANSCODE).lower().strip(), (0, 0, ""));TRANSCODE = TC_HEIGHT > 0;TC_LEAD = 8.0;import os, sys, time, json, signal, select, shutil, hashlib, threading, collections, subprocess as sp, re, urllib.parse as up
try:import requests
except ImportError:print("[tsloop stream] python requests module not found", file=sys.stderr);sys.exit(1)
src, ua = (sys.argv[1], sys.argv[2]);chid = sys.argv[3] if len(sys.argv) > 3 else "";cdvr = (sys.argv[4] if len(sys.argv) > 4 else os.environ.get("CHANNELS_DVR", "")).strip().rstrip("/");FF = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg";s = requests.Session();s.headers["User-Agent"] = ua;mono = time.monotonic;BUSY = (429, 503, 509);run = 1;WR = 95443.717688888;CL = 3.0;JB = 5.0;xb = 0.0;vpk = 1;vlj = 0.0;xo = 0.0;dmx = 0.0;STALL = 8.0;FAILT = 60.0;QUIET = 4.0;SIL = 6.0;RLOW = 4.0;RES = 20.0;QMAX = 60.0;le = {};pd = 0.04;fq = collections.deque();fql = threading.Lock();shut = {};wb = time.monotonic();rb = wb;skf = False;rstk = False;sl0 = 0.0;sl1 = 0.0;PM = 45.0;avok = None;RAMP = 0.02;RXL = 1.5;RXR = 0.15;nrx = [0];hc = {"r": 0, "x": 0, "f": 0, "k": 0.0}
def log(m):
 for a, b in (("stream error", "r"), ("restarting remuxer", "x"), ("switching to stream", "f")):
  if m.startswith(a) or "; " + a in m:hc[b] += 1
 try:sys.stderr.write("[tsloop stream] %s\n" % m);sys.stderr.flush()
 except Exception:pass
def dif(a, b):return (a - b + WR / 2) % WR - WR / 2
ff = None;off = 0.0;tout = 0.0;HW = None;STL = collections.deque()
def hwpick():
 global HW
 if HW:return HW
 hf = "/tmp/tsloop-hw.txt"
 try:
  if time.time() - os.path.getmtime(hf) < 3600:HW = open(hf).read().strip()
 except Exception:pass
 if not HW:
  HW = "cpu";t_ = ["-f", "lavfi", "-i", "color=s=320x240:d=0.3", "-frames:v", "5", "-f", "null", "-"]
  try:
   if os.path.exists("/dev/dri/renderD128") and sp.run([FF, "-hide_banner", "-loglevel", "error", "-vaapi_device", "/dev/dri/renderD128"] + t_[:4] + ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"] + t_[4:], capture_output=True, timeout=15).returncode == 0:HW = "vaapi"
   elif sp.run([FF, "-hide_banner", "-loglevel", "error"] + t_[:4] + ["-c:v", "h264_nvenc"] + t_[4:], capture_output=True, timeout=15).returncode == 0:HW = "nvenc"
  except Exception:pass
  try:open(hf, "w").write(HW)
  except Exception:pass
 log("transcoding with " + {"vaapi": "Intel/AMD graphics (VAAPI)", "nvenc": "NVIDIA graphics (NVENC)", "cpu": "the CPU (no hardware encoder found)"}[HW]);return HW
def frs(f):
 for a, b in ((59.94, "60000/1001"), (29.97, "30000/1001"), (23.976, "24000/1001"), (47.952, "48000/1001")):
  if abs(f - a) < 0.01:return b
 return "%g" % f
SLOW = 1.03;SPD = 1.0;SPA = 1.0;ADJ = [0.0];RP = [None, 0.0];RQ = collections.deque(maxlen=6000);lowt = None;ltk = -1000000000.0
def rstep(now, qs, lo, hi, win):
 global SPD, lowt, ltk
 while RQ and now - RQ[0][0] > win:RQ.popleft()
 qm = min((q for _, q in RQ))
 if SPD == 1.0:
  if qm >= lo:lowt = None;return
  if lowt is None:lowt = now
  if now - lowt < 30 or now - ltk < 60:return
  SPD = SLOW;lowt = None;ltk = now;RP[1] = now;RP[0] = "reserve down to %.0fs; playing 3 percent slower, pitch unchanged, until it is back to %.0fs" % (qm, hi)
 elif qm >= hi:SPD = 1.0;ltk = now;RP[1] = now;RP[0] = "reserve back to %.0fs; back to real speed" % qm
def kfo(d, a, b):
 for j in range(a, b - 187, 188):
  p = d[j:j + 188]
  if p[0] == 71 and p[1] & 64:
   q = pests(p)
   if q and 224 <= q[0] <= 239 and iskey(p):return j
 return None
def avlead(d, k):
 q = pests(d[k:k + 188]);vk = q[1] if q else None
 if vk is None:return 0.0
 for j in range(k, min(len(d), k + 188 * 3000) - 187, 188):
  p = d[j:j + 188]
  if p[0] == 71 and p[1] & 64:
   q = pests(p)
   if q and (192 <= q[0] <= 223 or q[0] == 189):return min(max(dif(vk, q[1]), 0.0), 1.0)
 return 0.0
def ffcmd():
 b = [FF, "-hide_banner", "-loglevel", "error", "-fflags", "+discardcorrupt", "-probesize", "1000000", "-analyzeduration", "1000000"] + (["-probesize", "5000000", "-analyzeduration", "3000000"] if TRANSCODE else [])
 if not TRANSCODE:return b + ["-i", "pipe:0", "-map", "0:v?", "-map", "0:a?", "-c", "copy", "-copyts"] + (["-output_ts_offset", "%.3f" % off] if off else []) + ["-f", "mpegts", "pipe:1"]
 h = hwpick();fr = frs(TC_FPS);g = str(int(round(2 * TC_FPS)));vf = "bwdif=mode=send_frame:deint=interlaced," + ("setpts=(PTS-STARTPTS)*%.4f+STARTPTS," % SPA if SPA != 1.0 else "") + "fps=%s,scale=-2:%d:flags=fast_bilinear" % (fr, TC_HEIGHT);rc = ["-b:v", TC_VBITRATE, "-maxrate", TC_VBITRATE, "-bufsize", TC_VBITRATE, "-g", g]
 if h == "vaapi":b += ["-vaapi_device", "/dev/dri/renderD128"];vc = ["-vf", vf + ",format=nv12,hwupload", "-c:v", "h264_vaapi"] + rc + ["-bf", "0"]
 elif h == "nvenc":vc = ["-vf", vf + ",format=yuv420p", "-c:v", "h264_nvenc", "-preset", "p4"] + rc + ["-bf", "0"]
 else:vc = ["-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "veryfast"] + rc
 af = "aresample=async=1000" + (",atempo=%.6f" % (1 / SPA) if SPA != 1.0 else "");return b + ["-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?"] + vc + ["-af", af, "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-output_ts_offset", "%.3f" % (tout + 1.4 - ADJ[0]), "-f", "mpegts", "pipe:1"]
def rtgt():return RES
def remux(why=None):
 global ff, SPA
 if why:log("restarting remuxer: " + why)
 old = ff;SPA = SPD;RP[0] = None;ff = sp.Popen(ffcmd(), stdin=sp.PIPE, stdout=sp.PIPE);ADJ[0] = 0.0
 with fql:fq.append(ff)
 if old:
  shut[old.pid] = mono()
  try:old.stdin.close()
  except Exception:pass
NUL = (b"G\x1f\xff\x10" + b"\xff" * 184) * 7
def pump():
 global skf;ob = 0;lw = mono();kas = None;kag = False
 while True:
  with fql:p = fq[0] if fq else None;more = len(fq) > 1
  if p is None:time.sleep(0.05);continue
  r, _, _ = select.select([p.stdout], [], [], 0.5)
  if skf and mono() - rb < 25:skf = False
  if not r and mono() - lw > 3 and (not ob % 188) and (mono() - wb < 10) and (mono() - rb < 25):
   if kas is None:kas = lw;hc["ks"] = kas;log("output: no video to send for 3s, sending keepalive packets so the stream stays open")
   try:os.write(1, NUL);lw = mono()
   except OSError:bye()
  elif not r and mono() - lw > 3 and (not kag) and ob % 188:kag = True;log("output: stopped for %.0fs partway through a packet; no keepalive" % (mono() - lw))
  elif not r and mono() - lw > 3 and (not kag) and (mono() - wb >= 10 or mono() - rb >= 25):kag = True;skf = mono() - rb >= 25;log("output: stopped for %.0fs and the %s is stuck; no keepalive, so Dispatcharr can restart the stream" % (mono() - lw, "writer" if mono() - wb >= 10 else "reader"))
  if not r:
   if more and mono() - shut.get(p.pid, mono()) > 5:
    try:p.kill()
    except Exception:pass
   elif more or p.poll() is None:continue
  b = os.read(p.stdout.fileno(), 65536) if r else b""
  if not b:
   if more:
    with fql:fq.popleft()
    try:p.wait(1)
    except Exception:pass
    shut.pop(p.pid, None);b = b"\xff" * ((188 - ob % 188) % 188)
   elif p.poll() is not None:time.sleep(0.05)
   if not b:continue
  try:
   while b:n = os.write(1, b);b = b[n:];ob += n
   lw = mono();kag = False
   if kas is not None:hc["k"] += lw - kas;hc["ks"] = None;log("output: video flowing again after %.1fs without video" % (lw - kas));kas = None
  except OSError:bye()
def gone():
 try:pl = select.poll();pl.register(1, select.POLLOUT);return any((e & (select.POLLERR | select.POLLHUP) for _, e in pl.poll(0)))
 except Exception:return False
def put(b):
 try:ff.stdin.write(b);ff.stdin.flush()
 except Exception as e:
  if gone():bye()
  remux("remuxer rejected data (%s)" % e)
  try:ff.stdin.write(b);ff.stdin.flush()
  except Exception:pass
def pests(p):
 if not p[1] & 64:return None
 af = p[3] >> 4 & 3
 if not af & 1:return None
 o = 5 + p[4] if af == 3 else 4
 if o + 14 > 188 or p[o:o + 3] != b"\x00\x00\x01":return None
 sid = p[o + 3]
 if not (192 <= sid <= 239 or sid == 189):return None
 fl = p[o + 7] & 192
 if not fl & 128:return None
 b = p[o + 14:o + 19] if fl == 192 and o + 19 <= 188 else p[o + 9:o + 14];return (sid, ((b[0] >> 1 & 7) << 30 | b[1] << 22 | b[2] >> 1 << 15 | b[3] << 7 | b[4] >> 1) / 90000.0)
def iskey(p):
 af = p[3] >> 4 & 3
 if af == 3 and p[4] and p[5] & 64:return True
 o = 5 + p[4] if af == 3 else 4;b = bytes(p[o + 9 + p[o + 8]:]);i = b.find(b"\x00\x00\x01")
 while 0 <= i < len(b) - 3:
  n = b[i + 3]
  if not n & 128 and (i + 4 < len(b) and b[i + 4] == 1 and (n >> 1 & 63 in (19, 20, 21, 32, 33)) or n & 31 in (5, 7)):return True
  i = b.find(b"\x00\x00\x01", i + 3)
 return False
st = "/tmp/tsloop-" + hashlib.md5(("ch" + chid if chid.isdigit() else src).encode()).hexdigest() + ".json";last = {};dl = {};rcur = None;wlead = 0.0;prim = None;sl = threading.Lock();resumed = False;rg = 0.0
try:
 j = json.load(open(st))
 if time.time() - j["t"] < 4 and j.get("src") == src:dl = {int(k): v for k, v in j["dl"].items()};last = dict(dl);off = j.get("off", 0.0);rcur = j.get("cur");prim = j.get("prim");xb = float(j.get("xb", 0.0));vpk = max(1, int(j.get("pk", 1)));rstk = bool(j.get("stk"));vlj = mono() if xb > JB else 0.0;wlead = max(j.get("lead", 0.0) - (time.time() - j["t"]), 0.0);resumed = True;rg = min(max(j.get("gage", 0.0), 0.0), FAILT);log("resuming after Dispatcharr restart")
except Exception:pass
cur = rcur or src
def save():
 try:
  with sl:
   with open(st + ".tmp", "w") as f:json.dump({"dl": {str(k): v for k, v in dl.items()}, "gage": mono() - got, "off": off, "cur": wcur, "src": src, "prim": prim, "lead": wlead, "xb": xb, "pk": vpk, "res": round(Qs[0], 2), "stk": skf, "to": tout, "t": time.time()}, f)
   os.replace(st + ".tmp", st)
 except Exception:pass
wcur = cur
def status(h0):
 try:fl_ = feeds(True) if chid.isdigit() else [src];ks = hc.get("ks");log("status: stream %s of %d, reserve %.0fs, client lead %.0fs, extra lead for shared viewers %.0fs, last 10 min: %d stream retries, %d remuxes, %d failovers, %.0fs keepalive%s" % (str(fl_.index(wcur) + 1) if wcur in fl_ else "?", len(fl_), Qs[0], max(wlead, 0.0), xb, hc["r"] - h0["r"], hc["x"] - h0["x"], hc["f"] - h0["f"], hc["k"] - h0["k"], ", keepalive running for %.0fs" % (mono() - ks) if ks else ""))
 except Exception:pass
def beat():
 n = 0;h0 = dict(hc)
 while run:
  save();time.sleep(1);n += 1
  if n % 600 == 0:threading.Thread(target=status, args=(dict(h0),), daemon=True).start();h0 = dict(hc)
threading.Thread(target=beat, daemon=True).start();XC = re.compile("/(live|hls)/([^/]+)/([^/]+)/")
def feeds(quiet=False):
 if not chid.isdigit():return [src]
 try:
  import psycopg;e = os.environ.get
  with psycopg.connect(dbname=e("POSTGRES_DB", "dispatcharr"), user=e("POSTGRES_USER", "dispatch"), password=e("POSTGRES_PASSWORD", ""), host=e("POSTGRES_HOST", "localhost"), port=int(e("POSTGRES_PORT", "5432")), connect_timeout=3) as c:urls = [u for u, in c.execute("select s.url from dispatcharr_channels_channelstream cs join dispatcharr_channels_stream s on s.id=cs.stream_id where cs.channel_id=%s order by cs.order", (int(chid),)).fetchall() if u]
 except Exception as x:
  if not quiet:log("could not read backup streams (%s)" % x)
  return [src]
 m = XC.search(src)
 if m:urls = [XC.sub("/%s/%s/%s/" % (mm.group(1), m.group(2), m.group(3)), u, count=1) if (mm := XC.search(u)) and up.urlsplit(u).netloc == up.urlsplit(src).netloc else u for u in urls]
 out = [src]
 for u in urls:
  if u not in out and XC.sub("/", u) != XC.sub("/", src):out.append(u)
 return out
stalled = False;pend = False;got = mono() - rg;ost = got;lv = got;gap = None;dlv = False;fbad = {};lpr = -1000000000.0;prr = None;fst = mono()
def mark(u):b = fbad.get(u, [0, 0]);fbad[u] = [mono() + min(60 * 2 ** b[1], 600), b[1] + 1];return fbad[u][0] - mono()
def probe(u):
 r = None
 try:
  r = s.get(u, stream=True, timeout=(2, 3), allow_redirects=True);r.raise_for_status();ct = (r.headers.get("Content-Type") or "").lower()
  if "html" in ct or "mpegurl" in ct or r.url.rsplit("/", 1)[-1].lower().startswith("black."):raise ValueError("not a live MPEG-TS stream")
  b = b"";t = mono();r.it = r.iter_content(65536)
  for x in r.it:
   b += x
   if len(b) >= 188 * 9:
    if any((all((b[i + 188 * k] == 71 for k in range(8))) for i in range(188))):r.pk = b;return r
    break
   if mono() - t > 3:break
 except Exception:pass
 if r is not None:r.close()
 return None
def handoff(why):
 global stalled, got, cur, pend, sticky, gap, dlv, lpr, prr, fst, ost;fl = feeds()
 if len(fl) < 2:
  if not stalled:ost = got;log(why + "; no backup stream on this channel, so staying on this feed")
  stalled = True;got = mono();return
 if stalled and mono() - lpr < 30:got = mono();return
 lpr = mono();i0 = fl.index(cur) if cur in fl else 0;nxt = None;pt = mono()
 if not stalled:log("stream %d of %d failed; not using it again for %.0fs" % (i0 + 1, len(fl), mark(cur)))
 cand = [fl[(i0 + j) % len(fl)] for j in range(1, len(fl))];cand = [u for u in cand if fbad.get(u, [0])[0] <= mono()] or sorted(cand, key=lambda u: fbad.get(u, [0])[0])
 for u in cand:
  if mono() - pt > 6:break
  pr = probe(u)
  if pr is not None:nxt = u;break
  log("backup stream %d of %d is not answering; not trying it again for %.0fs" % (fl.index(u) + 1, len(fl), mark(u)))
 if nxt is None:
  if not stalled:ost = got;log(why + "; no backup stream is answering right now, so staying on this feed")
  stalled = True;got = mono();return
 log(why + "; switching to stream %d of %d" % (fl.index(nxt) + 1, len(fl)));gap = mono() - ost if stalled else (gap or 0.0) + mono() - got if gap is not None or dlv else None;cur = nxt;prr = [nxt, pr];pend = True;stalled = False;got = mono();sticky = None;dlv = False;hc["e"] = 0;fst = mono()
def bye():global run;run = 0;save();killall();os._exit(0)
def killall():
 k = fql.acquire(timeout=1);ps = list(fq)
 if k:fql.release()
 for p in ps:
  try:p.kill()
  except Exception:pass
for g in (15, 2, 1):signal.signal(g, lambda *a: (killall(), os._exit(0)))
Q = collections.deque();Qs = [0.0];ql = threading.Lock();moff = off;qend = None;wput = mono()
if prim is not None and prim in dl:qend = dl[prim] + off
def enq(it):
 with ql:Q.append(it);Qs[0] += it[2];nrx[0] += it[0] == "remux"
def clcap():return max(CL, dmx - STALL - RLOW)
def watch():
 global xb, vpk, vlj, xo
 if not chid.isdigit():return
 e = os.environ.get;w = requests.Session();base = cdvr;dev = None;num = None;tmap = -1000000000.0;seen = None;t0 = mono();fails = 0;said = set();tj = -1000000000.0;jerr = set();jfail = False;og = None;jt = None;JF = ("/data" if os.access("/data", os.W_OK) else "/tmp") + "/tsloop-jobs-%s.json" % chid
 try:
  for f_ in os.listdir(os.path.dirname(JF)):
   p_ = os.path.join(os.path.dirname(JF), f_)
   if f_.startswith(("hlsloop-jobs-", "tsloop-jobs-")) and time.time() - os.path.getmtime(p_) > 86400:os.remove(p_)
 except Exception:pass
 def say(k, m):
  if k not in said:said.add(k);log("viewer buffer: " + m)
 def ext(rj):
  nonlocal og, jfail;jw = requests.Session()
  try:
   with ql:rv = min(Qs[0] + max(wlead, 0.0), rtgt() + 15.0)
   ad = min(int(rv + PM), 180);now_ = time.time()
   if og is None:
    try:og = json.load(open(JF));og = {a: b for a, b in og.items() if isinstance(b, list) and len(b) == 2}
    except Exception:og = {}
   def wog():
    nonlocal og;og = {a: b for a, b in og.items() if time.time() - b[1] < 86400};tf = JF + ".%d.tmp" % os.getpid()
    with open(tf, "w") as f:json.dump(og, f)
    os.replace(tf, JF)
   for jb in jw.get(base + "/dvr/jobs", timeout=5).json() or []:
    jid = str(jb.get("ID") or "")
    if jid not in rj:continue
    t1 = float(jb.get("Time") or 0);du = float(jb.get("Duration") or 0)
    if jid not in og:og[jid] = [du, now_];wog()
    nd = int(og[jid][0] + ad)
    if now_ < t1 + du - 30 and nd > du + 10:
     try:jb["Duration"] = nd;jw.put(base + "/dvr/jobs/" + up.quote(jid), json=jb, timeout=5).raise_for_status();log("recording: %s on ch %s extended by %ds on top of its own padding (%ds for this stream running behind, %ds for the provider running behind the broadcast)" % (jb.get("Name") or jid, num, nd - int(og[jid][0]), int(rv), int(PM)))
     except Exception as x:
      if jid not in jerr:jerr.add(jid);log("recording: could not extend %s (%s)" % (jb.get("Name") or jid, str(x)[:80]))
   jfail = False
  except Exception as x:
   if not jfail:jfail = True;log("recording: could not check or extend recordings (%s); will keep trying quietly" % str(x)[:80])
 try:
  import psycopg
  with psycopg.connect(dbname=e("POSTGRES_DB", "dispatcharr"), user=e("POSTGRES_USER", "dispatch"), password=e("POSTGRES_PASSWORD", ""), host=e("POSTGRES_HOST", "localhost"), port=int(e("POSTGRES_PORT", "5432")), connect_timeout=3) as c:uid = str(c.execute("select uuid from dispatcharr_channels_channel where id=%s", (int(chid),)).fetchone()[0])
 except Exception as x:say("db", "off (could not read this channel id: %s)" % x);return
 def nm(l):x = re.sub("\\\"[^\\\"]*\\\"", "", l);return x.split(",", 1)[1].strip() if "," in x else ""
 while run:
  try:
   if not base:
    import redis;rd = redis.Redis(host=e("REDIS_HOST", "localhost"), port=int(e("REDIS_PORT", "6379")), db=int(e("REDIS_DB", "0")), password=e("REDIS_PASSWORD") or None, socket_timeout=2)
    for m in rd.smembers("live:channel:%s:clients" % uid):
     h = rd.hgetall("live:channel:%s:clients:%s" % (uid, m.decode()))
     if b"channels-dvr" in h.get(b"user_agent", b"").lower():base = "http://%s:8089" % h[b"ip_address"].decode();break
    if not base:
     if mono() - t0 > 120:say("nocd", "waiting (no Channels DVR client on this stream yet)");time.sleep(30);continue
     time.sleep(5);continue
   if mono() - tmap > 300:
    tmap = mono();num = dev = None;st = w.get(base + "/settings", timeout=5).json()
    for k, v in st.items():
     mm = re.fullmatch("m3u\\.src\\.(.+)\\.url", k)
     if not mm or "/output/m3u" not in str(v):continue
     t = w.get(v, timeout=20).text.splitlines();ent = [(nm(t[i]), t[i + 1].strip()) for i in range(len(t) - 1) if t[i].startswith("#EXTINF")];hit = [i for i, (n, x) in enumerate(ent) if uid in x]
     if not hit:continue
     d = "M3U-" + mm.group(1)
     for tr in range(6):
      cl = w.get(base + "/devices/%s/channels" % d, timeout=10).json()
      if len(cl) == len(ent) and all((str(c.get("GuideName", "")).strip() == n for c, (n, x) in zip(cl, ent))):break
      if tr == 0 and "post" + d not in said:said.add("post" + d);log("viewer buffer: Channels DVR source %s is out of date, refreshing it" % d);w.post(base + "/providers/m3u/sources/%s/refresh" % up.quote(mm.group(1)), timeout=30)
      time.sleep(1)
     else:say("drift" + d, "Channels DVR source %s still does not match the playlist after a refresh" % d);continue
     dev = d;num = str(cl[hit[0]]["GuideNumber"]);say("map" + dev + num, "this stream is Channels DVR %s ch %s" % (dev, num));break
    if num is None:say("nomap", "off for now (this channel is not in a Channels DVR source that matches the playlist)")
   if num is None:time.sleep(30);continue
   k = 0;nr = 0;rj = set();act = w.get(base + "/dvr/activity", timeout=3).json()
   for x in act.get("streams") or []:
    tu = x.get("tuner") or {}
    if tu.get("device_id") == dev and str(tu.get("channel")) == num:k = max(k, int(tu.get("readers") or 1))
   for x in act.get("recordings") or []:
    tu = x.get("tuner") or {}
    if tu.get("device_id") == dev and str(tu.get("channel")) == num and (x.get("state") == "recording"):nr += 1;rj.add(str(x.get("job_id") or ""))
   rj.discard("")
   if rj and mono() - tj > 30 and (not (jt and jt.is_alive())):tj = mono();jt = threading.Thread(target=ext, args=(set(rj),), daemon=True);jt.start()
   if k or nr:seen = mono()
   elif seen is None and mono() - t0 > 90:say("unseen", "waiting (ch %s not playing in Channels DVR yet)" % num);time.sleep(30);continue
   if k and k > vpk:room = min(max(clcap() - CL - xb, 0.0), max(Qs[0] - STALL - RLOW, 0.0));add = min(JB * (k - vpk), room);xb += add;vlj = mono();xo = JB * (k - vpk) - add;log("viewer buffer: %d viewers on Channels DVR ch %s, sent the new viewer a %.0fs head start (lead now %.0fs, reserve %.0fs)" % (k, num, add, CL + xb, max(Qs[0] - add, 0.0)) + ("" if xo <= 0 else ", %.0fs more as the reserve allows" % xo))
   elif xo > 0:
    if mono() - vlj > 30 or not k or k < vpk:log("viewer buffer: %.0fs of the head start dropped (reserve did not grow in time or the viewer left)" % xo);xo = 0.0
    else:
     add = min(xo, max(clcap() - CL - xb, 0.0), max(Qs[0] - STALL - RLOW, 0.0));xb += add;xo -= add
     if xo <= 0.01:xo = 0.0;log("viewer buffer: rest of the head start sent (lead now %.0fs)" % (CL + xb))
   if k:vpk = k
   if xb > JB and mono() - vlj > 120 and (Qs[0] < RES):
    xb = max(JB, xb - 0.02)
    if xb == JB:log("viewer buffer: reserve rebuilt to %.0fs" % Qs[0])
   fails = 0
  except Exception as x:
   fails += 1
   if fails == 3:log("viewer buffer: Channels DVR not reachable (%s)" % str(x)[:80])
   if not base and mono() - t0 > 120:time.sleep(28)
  time.sleep(2)
def writer():
 global dl, off, wlead, wcur, wput, wb, dmx, tout;lead = wlead;t = mono();sv = 0.0;remp = False;dh = collections.deque();dt = 0.0;ce = max(CL, min(wlead, CL + xb));tc = mono();rxt = -1000000000.0;pxb = xb
 while run:
  try:
   now = mono();wb = now;lead = max(lead - (now - t), 0.0);t = now;wlead = lead
   if ff.poll() is not None:remux("remuxer exited");time.sleep(1 if mono() - rxt < 10 else 0.1);rxt = mono();continue
   with ql:
    it = Q[0] if Q else None;qs = Qs[0]
    if TRANSCODE:RQ.append((now, qs))
    if it is None:Qs[0] = 0.0
   if now - dt >= 1:
    dt = now;dh.append((now, qs + max(lead, 0.0)))
    while dh[0][0] < now - 120:dh.popleft()
    dmx = max((v for _, v in dh))
   if it is None:
    if not remp and hc.get("s") and (lead <= 0.2) and (dmx >= RLOW + 3):remp = True;log("reserve empty: nothing queued and the client lead is used up, playback will pause until video arrives")
    time.sleep(0.05);continue
   if remp and qs >= RLOW:remp = False;log("reserve refilled to %.0fs" % qs)
   if it[0] == "remux":
    with ql:Q.popleft();nrx[0] -= 1
    off = it[3];remux(it[1]);continue
   d = it[1];ix = it[5];i = it[6]
   if i < len(ix):en, ca, sn = ix[i]
   else:en, ca, sn = (len(d), it[2], it[3])
   cd = max(ca - it[8], 0.0);rx = nrx[0] > 0 and qs > 4.0;ce = min(max(min(CL + xb + (RXL if rx else 0.0), clcap(), max(ce, lead + qs - STALL - RLOW)), ce - 0.01 * (now - tc)), ce + (RXR if rx else RAMP) * (now - tc) + max(xb - pxb, 0.0));tc = now;pxb = xb
   if cd > 0 and lead >= ce and (qs <= QMAX + 30):time.sleep(0.02);continue
   if TRANSCODE and i == 0 and (mono() - fst > 90):rstep(now, qs, RES * 0.5, RES * 0.8, 3.0)
   if RP[0] and it[7] < en:
    k_ = kfo(d, it[7], en) if now - RP[1] < 10 else it[7]
    if k_ is not None:
     if k_ > it[7]:put(d[it[7]:k_])
     it[7] = k_;w_ = RP[0];RP[0] = None;ADJ[0] = avlead(d, k_);remux(w_)
   put(d[it[7]:en]);it[6] = i + 1;it[7] = en;it[8] = max(ca, it[8]);wput = mono();lead = min(lead + cd * SPA, ce + 10.0);tout += cd * SPA;dl = sn;wcur = it[4]
   with ql:
    Qs[0] -= cd
    if en >= len(d):Q.popleft();Qs[0] -= it[2] - it[8]
   if en >= len(d):dl = it[3]
   if en >= len(d) or mono() - sv > 0.2:save();sv = mono()
  except Exception as e:log("writer error (%s)" % e);time.sleep(1)
try:
 j_ = json.load(open(st))
 if time.time() - j_["t"] < 4 and j_.get("src") == src:tout = float(j_.get("to", 0.0))
except Exception:pass
remux();threading.Thread(target=pump, daemon=True).start();threading.Thread(target=writer, daemon=True).start();threading.Thread(target=watch, daemon=True).start();err = 0;e0 = None;ph = 0;short = 0;lrf = -1000000000.0;warned = set();cc = {};sticky = None;usest = False
def budget():now = mono();qs = Qs[0];return max(qs + min(max(wlead, 0.0), SIL - (now - wput if qs <= 0 else 0)) - 1.0, 3.0)
def conn(r):
 global last, moff, qend, got, prim, pend, resumed, stalled, ph, short, le, lrf, avok;rem = b"";pv = True;rest = [];mh = collections.deque();ta = 0.0;lc = None;mode = None;pre = [];sync = set();t0 = mono();media = 0.0;hold = {};seen = set();exact = set();kwait = set();gate = None;kt = mono();jump = None;nowait = False;beh = None;bs = [];hw = None;tw = None;hs = [];fin = False;c0 = mono();lb = {};lmin = None
 def emit(pk):
  global last, qend, got, prim, stalled, ph, gap, dlv, lv;nonlocal media, gate, kt, jump, beh, pv, rest;out = bytearray();adv = 0.0;ix = []
  def fix(p):
   pid = (p[1] & 31) << 8 | p[2]
   if p[3] & 16:c = cc[pid] + 1 & 15 if pid in cc else p[3] & 15;cc[pid] = c;p[3] = p[3] & 240 | c
   elif pid in cc:p[3] = p[3] & 240 | cc[pid]
   return p
  def commit(h):
   global qend, prim, pd;nonlocal adv, jump;pid, sid, ts, buf = h
   if prim is None and 224 <= sid <= 239:prim = pid
   if pid == prim:
    if pid in last:
     a = dif(ts, last[pid])
     if not -1 < a < 5:jump = a;return
     if 0 < a < 5:adv += a;pd = a
     if a > 0:last[pid] = ts
    else:last[pid] = ts
    qend = last[pid] + moff
   else:
    if prim in last and dif(ts, last[prim]) >= 5:return False
    e = le.get(pid, last.get(pid))
    if e is not None and -2 < dif(ts, e) < -0.001:return False
    if pid not in last or dif(ts, last[pid]) > 0:last[pid] = ts
   for i in range(0, len(buf), 188):out.extend(fix(bytearray(buf[i:i + 188])))
   if pid == prim:ix.append((len(out), adv, dict(last)))
   return True
  for n_, p in enumerate(pk):
   pid = (p[1] & 31) << 8 | p[2]
   if pid == 8191:continue
   if not p[3] & 16:
    if pid in hold:
     if hold[pid] is not None:hold[pid][3] += p
    else:out += fix(p)
    continue
   if p[1] & 64:seen.add(pid)
   elif pid not in seen:continue
   x = pests(p)
   if x:
    h = hold.pop(pid, None)
    if h and commit(h) and (pid != prim):le[pid] = x[1]
    if jump is not None and -1 < dif(x[1], last[pid]) < 5:jump = None
    if jump is not None:rest = ([bytearray(h[3][i:i + 188]) for i in range(0, len(h[3]), 188)] if h else []) + pk[n_:];break
    sid, ts = x
    if pid in sync:
     d = dif(ts, last.get(pid, ts - 1))
     if d <= 0.001:
      if pid == prim:beh = -d
      if d >= -0.001:exact.add(pid)
      hold[pid] = None;continue
     sync.discard(pid)
     if pid == prim and pid not in exact and pv:kwait.add(pid);kt = mono();log("reconnect did not line up with what was sent; waiting for a keyframe")
    if pid in kwait:
     if not iskey(p) and mono() - kt < 3:hold[pid] = None;continue
     kwait.discard(pid)
    if gate is not None and pid != prim and (dif(ts, gate) < 0):hold[pid] = None;continue
    hold[pid] = [pid, sid, ts, bytearray(p)]
   elif pid in hold:
    if hold[pid] is not None:hold[pid][3] += p
   else:out += fix(p)
  if adv > 0 and (not hc.get("s")):hc["s"] = 1;log("input: first video received from %s, building the reserve" % up.urlsplit(cur).netloc)
  if adv > 0 and hc.get("e"):log("input: video back after %d reconnects (reserve %.1fs)" % (hc["e"], Qs[0]));hc["e"] = 0
  if adv > 0:
   got = mono();lv = got;media += adv;stalled = False;ph = 0;gap = None;dlv = True
   if cur in fbad and got - fst > 120:fbad.pop(cur, None)
  if out:enq(["seg", bytes(out), adv, dict(last), cur, ix, 0, 0, 0.0])
  if jump is not None and jump < 0:raise ValueError("timeline jumped %.0fs inside the stream" % jump)
 def rdch():
  nonlocal fin;it = getattr(r, "it", None) or r.iter_content(65536)
  if getattr(r, "pk", b""):yield r.pk
  while True:
   try:c = next(it)
   except StopIteration:
    if mode is None and pre and pend:fin = True;yield b""
    return
   except Exception as x:
    if mode is None and pre and pend:fin = True;yield b""
    raise x
   yield c
 global rb, rto
 for chunk in rdch():
  rb = mono();ta += min(rb - lc, 2.0) if lc else 0.0;lc = rb
  if not run or gone():break
  pw = mode is None and pre and pend;nr = min(rto, 2.5) if pw else max(4.0, min(STALL, Qs[0] - RLOW))
  if nr > rto + 0.5 or (pw and nr < rto):
   rto = nr
   try:r.raw._fp.fp.raw._sock.settimeout(nr)
   except Exception:pass
  d = rem + chunk
  if d and d[0] != 71:
   i = d.find(b"G")
   while i >= 0 and (not (i + 188 < len(d) and d[i + 188] == 71)):i = d.find(b"G", i + 1)
   d = d[i:] if i >= 0 else b""
  k = len(d) // 188 * 188;rem = d[k:];pk = [bytearray(d[i:i + 188]) for i in range(0, k, 188)]
  if mode is None:
   pre += pk;dec = None
   for p in pre:
    x = pests(p)
    if x and (224 <= x[0] <= 239 or (prim is not None and (p[1] & 31) << 8 | p[2] == prim)):dec = ((p[1] & 31) << 8 | p[2], x[1]);break
   if dec is None and (len(pre) > 8000 or (prim is None and mono() - t0 > 3)):
    for au in (1, 0):
     for p in pre:
      x = pests(p)
      if x and (192 <= x[0] <= 223 or not au):dec = ((p[1] & 31) << 8 | p[2], x[1]);break
     if dec:break
   if dec is None:
    if mono() - t0 > STALL + 2:raise ValueError("no decodable video timestamps")
    continue
   dp, ts = dec;va = [x for x in (pests(p) for p in pre) if x];fv_ = next((x[1] for x in va if 224 <= x[0] <= 239), None);fa_ = next((x[1] for x in va if 192 <= x[0] <= 223), None)
   if fv_ is not None and fa_ is not None and (3 < abs(dif(fv_, fa_)) < WR - 60) and (not (stalled and mono() - lv > 30)) and (avok != cur):raise ValueError("audio and video are %.0fs apart in this feed" % abs(dif(fv_, fa_)))
   if fv_ is not None and fa_ is not None and (3 < abs(dif(fv_, fa_)) < WR - 60):avok = cur
   elif fv_ is not None and fa_ is not None:avok = None
   if prim is None or dp != prim:prim = dp
   pv = any((pests(p) and 224 <= pests(p)[0] <= 239 for p in pre if (p[1] & 31) << 8 | p[2] == prim));ref = last.get(prim)
   if ref is not None and (not pend) and (not nowait) and (-120 <= dif(ts, ref) < -1):
    for p in pk if hw is not None else pre:
     x = pests(p) if (p[1] & 31) << 8 | p[2] == prim else None
     if x and (hw is None or dif(x[1], hw) > 0):hw = x[1]
    if tw is None:tw = mono()
    if dif(hw, ref) < -1 and mono() - tw < 1.5:continue
    if -dif(hw, ref) > budget():nowait = True;log("reconnected %.1fs behind what was sent and the provider burst only reaches %.1fs of it; replaying instead of stalling" % (-dif(ts, ref), dif(hw, ts)))
   if pend and gap is not None and (qend is not None):
    for p in pk if hw is not None else pre:
     x = pests(p) if (p[1] & 31) << 8 | p[2] == prim else None
     if x and (hw is None or dif(x[1], hw) > 0):hw = x[1]
    if tw is None:tw = mono()
    hs.append((mono(), hw));hs = [x for x in hs if x[0] > mono() - 0.5]
    if not fin and mono() - tw < 2.0 and (mono() - tw < 0.4 or dif(hw, hs[0][1]) > 0.8 * (mono() - hs[0][0]) + 0.3):continue
   if ref is not None and (not pend) and (not nowait) and (-120 <= dif(ts, ref) <= 1.0):
    mode = "dedupe";sync = set(last);beh = max(-dif(ts, ref), 0.0);bs = []
    if resumed:log("resuming after Dispatcharr restart, %.1fs of video already sent" % -min(dif(ts, ref), 0))
    elif dif(ts, ref) < 0:log("reconnected, dropping %.1fs already sent (reserve %.1fs)" % (-dif(ts, ref), Qs[0]))
   else:
    ks = [i for i, p in enumerate(pre) if (p[1] & 31) << 8 | p[2] == prim and pests(p) and (iskey(p) or not pv)];ki = ks[0] if ks else None
    if ki is None:
     if not fin and len(pre) < 30000 and (mono() - t0 < STALL + 2):continue
     ki = 0
    tt = None
    if nowait and hw is not None:tt = hw - max(min(RES - Qs[0] - max(wlead, 0.0), 10.0), 3.0)
    elif pend and gap is not None and (hw is not None):tt = hw - gap - (mono() - got) - 0.5
    if tt is not None and len(ks) > 1:ki = next((i for i in reversed(ks) if dif(pests(pre[i])[1], tt) <= 0), ks[0]) if pend else next((i for i in ks if dif(pests(pre[i])[1], tt) >= 0), ks[-1]);pp = {(p[1] & 31) << 8 | p[2] for p in pre if pests(p)};pre = [p for p in pre[:ki] if (p[1] & 31) << 8 | p[2] not in pp] + pre[ki:];ki = next((i for i, p in enumerate(pre) if (p[1] & 31) << 8 | p[2] == prim and pests(p)))
    ts = pests(pre[ki])[1];gate = ts;kt = mono();mode = "splice"
    if pv:kwait.add(prim)
    if qend is not None:om = moff;moff = qend + max(0.1, pd + 0.02) - ts;why = "joining the backup stream (offset %.3fs, %.1fs behind its live edge)" % (qend + max(0.1, pd + 0.02) - ts, dif(hw, ts) if hw is not None else -1) if pend else "replaying %.0fs to keep playback going, keeping timestamps continuous" % -dif(ts, ref) if nowait and ref is not None else "provider skipped %.0fs, keeping timestamps continuous" % dif(ts, ref) if ref is not None and 0 < dif(ts, ref) < 600 else "stream timeline changed, keeping timestamps continuous";enq(["remux", why, 0, moff]);sh = om - moff;le = {k: v + sh for k, v in le.items() if k != prim};last = {}
    else:last = {};le = {}
   pend = False;resumed = False;pk = pre;pre = []
  emit(pk)
  if mode is not None and rto < 4.0:
   rto = max(4.0, min(STALL, Qs[0] - RLOW))
   try:r.raw._fp.fp.raw._sock.settimeout(rto)
   except Exception:pass
  if jump is not None:
   log("timeline jumped %.0fs inside the stream, re-syncing on this connection" % jump);jump = None;mode = None;nowait = False;sync = set();exact = set();kwait = set();hold = {};seen = set();gate = None;t0 = mono();lb = {};lmin = None;pre = rest;rest = [];pk = []
   if pre:rem = b"".join((bytes(p) for p in pre)) + rem;pre = []
   continue
  if Qs[0] > QMAX:
   if QMAX not in warned:warned.add(QMAX);log("input stream is arriving faster than real time (more than %.0fs queued); reading it at playback speed" % QMAX)
   while run and Qs[0] > QMAX - 5 and (not gone()):rb = mono();time.sleep(0.2)
   got = mono()
  dr = mode == "dedupe" and prim in sync and (beh is not None)
  if dr:
   now = mono();bs.append((now, beh));bs = [x for x in bs if x[0] > now - 1.5];o = bs[0]
   if now - o[0] >= 0.8 and beh / max((o[1] - beh) / (now - o[0]), 1.0) > budget():log("still %.1fs behind what was sent and arriving at real time; waiting would stall playback" % beh);mode = None;nowait = True;sync = set();exact = set();kwait = set();hold = {};seen = set();gate = None;t0 = mono();continue
  if mono() - got > STALL + 2 and mono() - t0 > STALL + 2 and (not dr):raise ValueError("no new video for %ds" % (mono() - got))
  if media > 0 and mode is not None and (not pend):
   nm = mono();lg = nm - c0 - media;bk = int(nm / 10);lb[bk] = min(lb.get(bk, lg), lg);lmin = lg if lmin is None else min(lmin, lg)
   if len(lb) > 7:lb.pop(min(lb))
   cu = Qs[0] + max(wlead - CL, 0.0)
   if cu < RES / 2 and nm - c0 > 180 and (nm - lrf > 300 or cu < RLOW + 2) and (not stalled) and (len(lb) >= 6):
    dg = min((v for k_, v in lb.items() if k_ >= bk - 5)) - lmin
    if dg > 4:lrf = nm;raise ValueError("provider fell %.0fs behind live on this connection and the reserve is down to %.1fs; reconnecting for a fresh burst" % (dg, cu))
  if media > 0:
   mh.append((mono(), media, ta))
   while mh[0][0] < mono() - 20:mh.popleft()
   if Qs[0] < RLOW and mono() - mh[0][0] > 19 and (ta - mh[0][2] > 15) and (media - mh[0][1] < 0.85 * (ta - mh[0][2])):raise ValueError("feed slower than real time (%.1fs of video in %.0fs)" % (media - mh[0][1], ta - mh[0][2]))
 return (mono() - t0, media)
if rstk:handoff("the previous process got stuck reading this feed")
while not gone():
 rb = mono()
 try:
  url = sticky if usest and sticky else cur;usest = False;rto = STALL if stalled and Qs[0] <= RLOW or Qs[0] <= 0 else max(4.0, min(STALL, Qs[0] - RLOW))
  if prr and prr[0] == url:
   r = prr[1];prr = None
   try:r.raw._fp.fp.raw._sock.settimeout(rto)
   except Exception:pass
  else:
   if prr:prr[1].close();prr = None
   try:r = s.get(url, stream=True, timeout=(3, rto), allow_redirects=True);r.raise_for_status()
   except Exception as x:
    if url != cur:sticky = None
    rs = getattr(x, "response", None)
    if rs is not None:rs.close()
    raise
  ct = (r.headers.get("Content-Type") or "").lower()
  if "mpegurl" in ct:
   r.close()
   if cur not in warned:log("this stream URL is HLS; use the HLS profile for it");warned.add(cur)
   raise ValueError("stream URL is HLS, not MPEG-TS")
  if r.url.rsplit("/", 1)[-1].lower().startswith("black."):r.close();ph += 1;handoff("provider is serving an offline placeholder");dl_ = 5 if stalled else 0;rb = mono() + dl_;time.sleep(dl_);continue
  if err > 0:hc["e"] = hc.get("e", 0) + err
  err = 0;e0 = None;sticky = r.url if url == cur and r.url != cur else None
  try:dur, media = conn(r)
  finally:r.close()
  if gone():break
  if media < 15 and dur < 60:short += 1
  else:short = 0
  if short >= 3:short = 0;ph += 1;handoff("stream keeps ending after a few seconds (offline placeholder?)");dl_ = 5 if stalled else 0;rb = mono() + dl_;time.sleep(dl_);continue
  raise ValueError("stream ended")
 except Exception as e:
  if gone():break
  usest = sticky is not None and err == 0
  if e0 is None:e0 = mono()
  if "fresh burst" in str(e):log("input: " + str(e));e0 = None;rb = mono();time.sleep(0.2);continue
  ng = mono() - got
  if "slower than real time" in str(e):
   if not sl0 or mono() - sl1 > 120:sl0 = mono()
   sl1 = mono()
  if ng > FAILT or (ng > STALL and Qs[0] + max(wlead, 0.0) < RLOW + 3.0) or (sl0 and mono() - sl0 > 300 and (mono() - sl1 < 120)):
   if sl0 and mono() - sl0 > 300:e = ValueError("feed has been slower than real time for 5 minutes");gap = max(gap or 0.0, RES) if pend else gap
   sl0 = 0.0;ph += 1;handoff("no new video for %ds (%s)" % (mono() - got, str(e)[:80]))
   if not stalled:err = 0;e0 = None;continue
  err += 1;rs = getattr(e, "response", None);c = getattr(rs, "status_code", 0);ra = rs.headers.get("Retry-After", "") if rs is not None else "";dl_ = min(int(ra), 30) if ra.isdigit() else min(2 * err, 10) if c in BUSY else 0.2 if err == 1 else min(err - 1, 5)
  if stalled and Qs[0] <= RLOW and (mono() - lv > FAILT):dl_ = max(dl_, 1.0 if "slower than real time" in str(e) else 5.0)
  if not stalled or Qs[0] > RLOW:dl_ = max(0.2, min(dl_, max(FAILT - ng, 0.2), max(STALL - ng, Qs[0] - RLOW)))
  if not stalled and mono() - wput > QUIET:dl_ = min(dl_, 2)
  if ph > 1:dl_ = max(dl_, min(ph - 1, 10))
  log("stream error (%s); reconnect %d in %.1fs" % (str(e)[:120], err, dl_));rb = mono() + dl_;time.sleep(dl_)
bye()' {streamUrl} {userAgent} {channelId}
```
<!-- END tsloop -->

## Backup streams app

`dispatcharr_backup_streams.py` adds backup streams to your Dispatcharr channels in bulk, so the
[stream profile](#hls-stream-profile-for-dispatcharr-and-channels-dvr) (or Dispatcharr's own stream
switching) has another feed to move to when one dies. It finds other feeds of the same channel
among all your streams, shows them in a table for you to review, and writes the ones you approve
through Dispatcharr's API.

### How it matches

Nothing is specific to one provider or one channel name; it works with any M3U or Xtream source.

- **Pre-ticked ("Ready to write")** only on strong evidence: the same channel name once provider
  prefixes (`US|`, `U.S|`, `TV|`) and quality words (HD, FHD, 4K, RAW) are ignored, the same call
  sign (for example `(WNBC)`), or the same provider guide ID (`tvg-id`). The guide ID is what
  catches renamed channels, such as an old "MSNBC" feed and a new "MS NOW" feed that the provider
  still tags with the same ID.
- **"Suggestions to check"** are similar names the app isn't sure about. They are never ticked for
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
**Connect**. Once connected, the form folds into a one-line bar with **Reload** and
**Connection…**, and the next launch connects by itself when an API key is saved. The API key is the same one the Dispatcharr matcher uses
([how to get it](#get-a-dispatcharr-api-key)).

### Review and write

1. Channels with sure backups are ticked for you and show green, **Ready to write**. After
   loading, the **Show** filter starts on **Needs attention** (suggestions to check, nothing found
   and errors). Each filter entry shows its count.
2. Click a channel. The panel below lists its streams: the main stream first (it always stays
   first), then its current backups, then the suggestions. **Click a line to tick or untick it.**
   Yellow lines are only similar names, so check them. Unticking a current backup marks it for
   removal on the next Write.
3. To add any other stream, type in the search box on the right (it is pre-filled with the
   channel's name) and **double-click** a result, or select several and press **Add as backup**.
   Streams already on the channel are hidden from the results.
4. Press **Return** (or **Done, next**) to move to the next channel. A channel you leave without
   ticking anything becomes **Left as is** and drops out of Needs attention.
5. Press **Write N channels to Dispatcharr** (Cmd/Ctrl-S). The button always shows how many
   channels will be written. The main stream always stays first, and every channel is read back
   afterwards to confirm the change.

Working on many channels at once:

- Select several channels with Cmd-click (Ctrl-click on Windows and Linux), Shift-click or
  Cmd/Ctrl-A, then use **Tick selected** or **Untick selected** to put them in or leave them out of
  the next Write. Clicking a channel's **Write** column, or pressing Space, toggles one.
- **Tick all suggestions** ticks every suggestion on the selected channels, including the
  uncertain ones. **Mark reviewed** takes the selected channels out of Needs attention as they are.
  The **Channels** menu (and right-click on a channel) has the same actions plus versions for
  every channel shown, **Clear changes** and **Reset** (back to what the matcher ticked).
- There is also a **Group** filter, a **Find** box (name, stream, channel number or a range like
  `500-600`) and sortable columns. Columns can be dragged as wide as you like; the table scrolls
  sideways. Drag the divider between the table and the panel to resize them.

Anything the app can't do (nothing selected, everything already on the channel, and so on) is shown
in red in the status line instead of silently doing nothing. **Help > Keyboard shortcuts** lists
every shortcut.

### Autosave and undo

- **Autosave:** every tick, add, removal and review is saved as you make it, and again when you
  close the window. The next launch offers to restore unwritten work; **Reload** keeps it without
  asking. Written channels drop out of the saved work.
- **Undo:** before each Write, the original stream lists of the channels being changed are saved
  to an `undo_<date>.json` file. **Undo a write…** lists them by date with the channels they
  touched, puts back the one you pick, and reloads.

### Where data is stored

The app keeps everything in one folder in your home directory:

| System | Folder |
|---|---|
| macOS and Linux | `~/.dispatcharr_backup_streams/` |
| Windows | `%USERPROFILE%\.dispatcharr_backup_streams\` |

The folder name starts with a dot, so it is hidden by default on macOS and Linux. Open it with
**File > Open data and log folder**, with `open ~/.dispatcharr_backup_streams` (macOS) or
`xdg-open ~/.dispatcharr_backup_streams` (Linux), or press Cmd-Shift-. in Finder. On Windows,
paste the path into Explorer's address bar.

| File | Contents |
|---|---|
| `settings.json` | Dispatcharr address, API key, username (never the password) and the backup-stream limit. Readable only by your user account. |
| `session.json` | Autosaved work that hasn't been written yet: for each channel, the picked and removed streams, streams added by hand, skips and reviews. Used for the restore prompt. Readable only by your user account. |
| `window.json` | Window size and the position of the panel dividers. |
| `undo_<date>-<time>.json` | One per **Write**: the Dispatcharr address and, for every channel that write changed, its name and the stream list it had before. **Undo a write…** reads these. Kept until you delete them. |
| `logs/backup_streams.log` | Log of connects, matching, writes and undos. Rotates at 2 MB and keeps 3 old files. Passwords and API keys are never logged. |

Nothing is stored anywhere else, and nothing is sent anywhere except your Dispatcharr server. The
API key is saved so you don't have to paste it every time; to make the app forget it, delete
`settings.json` while the app is closed. Deleting the whole folder resets the app and removes
your undo history; nothing in Dispatcharr is affected.

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
