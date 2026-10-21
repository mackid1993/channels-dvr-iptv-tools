# Channels DVR Station ID Matcher

Two small desktop apps that fill in the **Gracenote station ID** (`tvc-guide-stationid`) on your
IPTV channels, using the Gracenote data your own [Channels DVR](https://getchannels.com) server
already has access to. Once your channels carry the right station IDs, Channels DVR uses its
Gracenote guide for them (two weeks of listings, artwork and logos) instead of your provider's EPG.

There is one app for each IPTV manager. The matching is identical; only where the channels come
from and how the IDs are saved differ.

| File | Use it with | How IDs are saved |
|---|---|---|
| [`channels_dvr_stationid_matcher.py`](channels_dvr_stationid_matcher.py) | [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) | Written straight to Dispatcharr through its API |
| [`m3u_editor_stationid_matcher.py`](m3u_editor_stationid_matcher.py) | [m3u-editor](https://github.com/m3ue/m3u-editor) | Applied through a small m3u-editor plugin the app exports for you ([`plugin/`](plugin)) |

Each app is a single Python file that runs on Windows, macOS and Linux. Both need Python with
**Tk (tkinter)**; see [Requirements](#requirements).

## Which one should I use?

Use the app for whichever manager you already run. If you haven't picked a manager yet, this is
how the two compare when feeding an IPTV provider into Channels DVR:

| | Dispatcharr | m3u-editor |
|---|---|---|
| Provider sends HLS (`.m3u8`) | Pulls it through FFmpeg and re-serves one continuous stream | Passes the provider's HLS playlist and segments through unchanged |
| A provider hiccup | Counts as a connection failure; after 3 within 30 minutes the stream ends unless the channel has a backup stream | Costs one segment; Channels DVR asks for the next one and keeps playing |
| Provider sends only MPEG-TS | Strong: buffers and sends keepalives through stalls | Works, but has no special TS buffering |
| Backup streams per channel | Automatic switching on buffering | Failover channels (set by hand or with auto-merge) |
| Gracenote IDs from this tool | Direct API write | Through the plugin (m3u-editor's API can't set them) |
| Several logins for one provider | Line profiles | Provider profiles |

Rule of thumb:

- **Your provider offers HLS and you don't want to manage backup streams: m3u-editor.** Channels
  DVR's recovery is built around HLS, and m3u-editor keeps the stream as HLS end to end.
- **Your provider only offers MPEG-TS, or you want automatic switching between backup streams:
  Dispatcharr.**

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
 This app  ---- reads channels / saves IDs ---->  Dispatcharr or m3u-editor
```

The app runs on your computer. It talks to your Channels DVR server for Gracenote lineup data,
and to your Dispatcharr or m3u-editor server for the channel list. Nothing goes anywhere else.

## What the apps do

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

## Features

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

## Requirements

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
- **Dispatcharr version:** a Dispatcharr instance and an admin account or API key.
- **m3u-editor version:** m3u-editor 0.13 or newer, an admin account (to install the plugin once)
  and an API token.

Nothing else is needed: no `pip install` and no other packages.

When an app starts it checks for Tk. If it is missing it offers to install it with the commands
above (Homebrew, winget, apt, dnf, pacman or zypper), or switches to another Python on the
machine that already has it. To only see what it would do, add `--check-deps`; add `--yes` to
install without being asked.

## Running an app

```
# macOS / Linux
python3 channels_dvr_stationid_matcher.py      # Dispatcharr
python3 m3u_editor_stationid_matcher.py        # m3u-editor

# Windows (or double-click the file)
py channels_dvr_stationid_matcher.py
py m3u_editor_stationid_matcher.py
```

In both apps, enter your **Channels DVR** address (e.g. `192.168.1.10`; port 8089 is assumed) and
your **countries** with a postal code, e.g. `USA:10001`, or use **Pick…**. The postal code
decides which local stations count, so use your own area's. The first run for a country takes
about a minute while lineups are read; after that they are cached for 7 days.

## Using it with Dispatcharr

### Get a Dispatcharr API key

1. In Dispatcharr, open **Users** and edit your user.
2. Press **Generate API Key** and copy it. A username and password also work instead of a key.

### Match and write

1. Enter the **Dispatcharr** address (port 9191 is assumed) and the **API key**, or a username
   and password.
2. Press **Connect & Match**.
3. Review the table, use **Match unmatched one by one…** for the rest, then
   **Write ticked IDs to Dispatcharr**.

For channels Dispatcharr created from an M3U (auto-sync), the station ID is written as a channel
**override**; otherwise the next M3U refresh would overwrite it with whatever the provider sends.
Removing an ID on such a channel writes a blank override. Manually created channels get the
normal field.

Point Channels DVR at Dispatcharr's M3U output; Dispatcharr includes `tvc-guide-stationid` there
automatically.

## Using it with m3u-editor

### Why there is a plugin

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

### Install the plugin (once)

1. In the app, press **Export m3u-editor plugin…** and save the zip.
2. In m3u-editor, signed in as an admin: **Extensions > Plugin Installs > Upload Plugin Archive**,
   and choose the zip.
3. Open the review, run the scan, then **Install and Trust**.
4. **Extensions > Extensions > Gracenote Station IDs > Enable**.

A newer version installs the same way and replaces the old one.

If you also have m3u-editor's built-in **Channels DVR** plugin enabled, turn it off or turn off
its **Overwrite existing** setting. It runs after every playlist sync and can replace the IDs you
applied.

### Get an m3u-editor API token

1. In m3u-editor, open **Tools > API Tokens** and create a token.
2. Give it the **View** permission (that's all the app needs) and copy it. m3u-editor shows the
   token only once.

### Match and apply

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

### Channels DVR source settings for m3u-editor

- **Source:** the m3u-editor playlist URL.
- **Stream Format:** **HLS** if your m3u-editor playlist outputs HLS (Xtream playlists: **Output:
  HLS**), otherwise MPEG-TS. Use the format the URLs end in (`.m3u8` is HLS).
- **Stream limit:** no more than your provider's connection limit.

## Where data is stored

Each app keeps its own folder in your home directory:

| App | Folder |
|---|---|
| Dispatcharr | `~/.channels_dvr_stationid_matcher/` (Windows: `%USERPROFILE%\.channels_dvr_stationid_matcher\`) |
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

## Known limitations

- Gracenote only returns lineups for about 36 countries (the Americas, most of Western and
  Northern Europe, Australia and South Korea). Other countries can still be entered; their
  channels only get Gracenote-wide suggestions.
- An unknown postal code doesn't produce an error from Gracenote; it returns only nationwide
  satellite lineups. The app warns when that happens.
- Some channels have no Gracenote station of their own (for example per-team sports feeds), so
  they stay unmatched or need a hand-picked ID.
- m3u-editor version: IDs are applied by pasting into the plugin's action, because m3u-editor has
  no API for it and plugin actions can only be started from its web page.

## Disclaimer

This is an unofficial community tool. It is not affiliated with, endorsed by or supported by
Fancy Bits (Channels DVR), Gracenote / Nielsen, the Dispatcharr project or the m3u-editor
project. Channels DVR and Gracenote are trademarks of their respective owners. Guide data is
accessed through your own Channels DVR server and is subject to its terms.

Back up your Dispatcharr or m3u-editor database before bulk changes.

## Contributing

This project does not accept issues or pull requests; both are turned off.

You are welcome to fork, remix and build on it under the terms of the MIT license.

## License

[MIT](LICENSE)
