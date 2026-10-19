# Channels DVR Station ID Matcher for Dispatcharr

A small desktop app that fills in the **Gracenote station ID** (`tvc-guide-stationid`) on your
[Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) channels, using the Gracenote data
your own [Channels DVR](https://getchannels.com) server already has access to.

Once your channels carry the right station IDs, Channels DVR can use its Gracenote guide for
them (two weeks of listings, artwork and logos) instead of your provider's EPG.

It is a single Python file that runs on Windows, macOS and Linux. It needs Python with
**Tk (tkinter)** installed; see [Requirements](#requirements).

## What it does

1. Reads your channels from Dispatcharr.
2. Downloads the real cable, satellite and over-the-air lineups Gracenote lists for the
   countries and postal codes you choose (through your Channels DVR server) and builds a
   local index of every station in them.
3. Matches each channel against that index and shows the results in a table:
   - **Matched** (green, pre-ticked): exact and unambiguous only. An explicit call sign
     such as `(WABC)`, or a channel name that equals the Gracenote name after removing
     words like HD/East/Channel.
   - **Review** (yellow): several stations share the name, or the name is only similar.
     Never ticked for you.
   - **Unmatched** (red): nothing similar in your lineups.
4. Lets you fix the rest by hand, one channel at a time, with numbered suggestions.
5. Writes the IDs you ticked back to Dispatcharr after you confirm, then reads them back to
   verify every one.

Fuzzy matches are never written automatically. Anything uncertain needs your pick.

## Features

- **Multiple countries at once** (`USA:10001, CAN, GBR:SW1A`), for IPTV lists that mix them.
  Lineups from all of them are merged into one index.
- **Guided walkthrough** for unmatched channels: press 1-9 to pick, Esc to skip, Back, search
  your lineups or all of Gracenote, or type an ID.
- **Word-order independent matching** that handles names like "Nat Geo Wild HD",
  "C-SPAN 2", "QVC 2" and run-together names like "SHOXBET".
- **Gracenote-wide search** when nothing in your lineups is a good match, clearly labelled
  "NOT in your lineups".
- **Fix or remove existing IDs**: channels that already have an ID are included, a different
  pick shows as "Will replace ID", and **Remove ID** clears one. The write confirmation lists
  every replacement and removal.
- **Filters**: Dispatcharr group dropdown, Find box (name, ID, group, or a channel-number range
  like `500-600`), sortable columns.
- **Safe writes**: channels deleted in Dispatcharr since you loaded the list are skipped
  instead of failing the batch; failed writes stay ticked so you can retry.
- **Your work is autosaved** and offered back after a restart or crash.
- **Logins renew themselves**, so a long session doesn't end in an expired-login error.
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
- A Dispatcharr instance and an admin account or API key.

Nothing else is needed: no `pip install` and no other packages.

When the script starts it checks for Tk. If it is missing it offers to install it with the
commands above (Homebrew, winget, apt, dnf, pacman or zypper), or switches to another Python on
the machine that already has it. To only see what it would do:

```
python3 channels_dvr_stationid_matcher.py --check-deps
```

Add `--yes` to install without being asked.

## Usage

```
# macOS / Linux
python3 channels_dvr_stationid_matcher.py

# Windows (or double-click the file)
py channels_dvr_stationid_matcher.py
```

1. Enter your **Channels DVR** address (e.g. `192.168.1.10`, port 8089 is assumed) and your
   **Dispatcharr** address (port 9191 is assumed).
2. Enter a Dispatcharr **API key**, or a username and password.
3. Enter your **countries** with a postal code, e.g. `USA:10001`, or use **Pick…**. The postal
   code decides which local stations count, so use your own area's.
4. Press **Connect & Match**. The first run for a country takes about a minute while lineups
   are read; after that they are cached for 7 days.
5. Review the table, use **Match unmatched one by one…** for the rest, then
   **Write ticked IDs to Dispatcharr**.

## How IDs are written

For channels Dispatcharr created from an M3U (auto-sync), the station ID is written as a
channel **override**. Without it, the next M3U refresh would overwrite the ID with whatever the
provider sends. Removing an ID on such a channel writes a blank override, so the provider's
value stays hidden. Manually created channels get the normal field.

To have Channels DVR use the IDs, point it at Dispatcharr's M3U output. Dispatcharr includes
`tvc-guide-stationid` there automatically.

## Where data is stored

Everything lives in `.channels_dvr_stationid_matcher` in your home folder
(`%USERPROFILE%\.channels_dvr_stationid_matcher` on Windows):

| File | Contents |
|---|---|
| `settings.json` | Server addresses, username and countries. Never the password or API key. |
| `session.json` | Autosaved picks, used for the restore prompt. |
| `index_<COUNTRY><POSTAL>.json` | Cached lineup data, refreshed after 7 days. |
| `logs/matcher.log` | Rotating log. Passwords, API keys and tokens are never logged. |

Deleting the folder resets the app. Nothing in Dispatcharr is affected.

## Known limitations

- Gracenote only returns lineups for about 36 countries (the Americas, most of Western and
  Northern Europe, Australia and South Korea). Other countries can still be entered; their
  channels only get Gracenote-wide suggestions.
- An unknown postal code doesn't produce an error from Gracenote; it returns only nationwide
  satellite lineups. The app warns when that happens.
- Some channels have no Gracenote station of their own (for example per-team sports feeds),
  so they will stay unmatched or need a hand-picked ID.

## Disclaimer

This is an unofficial community tool. It is not affiliated with, endorsed by or supported by
Fancy Bits (Channels DVR), Gracenote / Nielsen, or the Dispatcharr project. Channels DVR and
Gracenote are trademarks of their respective owners. Guide data is accessed through your own
Channels DVR server and is subject to its terms.

Back up your Dispatcharr database before bulk changes.

## Contributing

This project does not accept issues or pull requests; both are turned off.

You are welcome to fork, remix and build on it under the terms of the MIT license.

## License

[MIT](LICENSE)
