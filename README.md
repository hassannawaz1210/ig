# ig

A local Instagram browser for public profiles and a mixed reels feed, without logging in. Extracted from the original `~/.local/share/ig/ig.py` tool, with the existing browser interface and commands preserved.

## Setup

Requires Python 3.11+ and a desktop browser. The browser launcher uses Linux desktop commands.

```sh
cd /home/hassan/personal/web/ig
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/ig --help
.venv/bin/ig
```

Activating `.venv` also makes `ig` available directly. On this machine, `~/.local/bin/ig` points to this project's executable. The previous launcher is backed up at `~/.local/bin/ig.legacy-backup`. Run `ig stop` before switching between installations: they share the background server state.

## Commands

```sh
ig                         # open the mixed reels feed
ig nasa                    # open a profile grid
ig feeds                   # list feeds
ig add space nasa spacex   # create or extend a feed
ig rm space spacex         # remove an account
ig rm space                # delete a feed
ig profile nasa            # profile JSON
ig post SHORTCODE          # post JSON (also accepts a post URL)
ig feed nasa               # full archive JSON; may take a while
ig serve --port 7349       # foreground server
ig stop                    # stop the background server
```

A saved feed name opens the combined mix, matching the original tool. All eligible accounts have the same chance of being selected, including discovered accounts. The interface supports video playback, carousels, adding and removing accounts. In the reels view, use arrow keys or `j`/`k` to move.

## Local data

Existing feeds remain in `~/.config/ig/feeds.json`. Both the original list format and `{ "accounts": [...], "weight": 1 }` format are supported; legacy weights are ignored. Defaults are created on first use if the file does not exist. Background server metadata lives in `~/.cache/ig/server.json`; post and discovery state stays in memory. The latest 5,000 delivered clip IDs are saved in `~/.cache/ig/history.json` to avoid repeats across restarts. `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` can override these locations, including for isolated development.

No virtual environment, personal feed configuration, or runtime cache is included in the project. The original installation is left in place.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

- `src/ig/cli.py`: commands and browser shortcuts
- `src/ig/config.py`: paths and saved feeds
- `src/ig/instagram.py`: page parsing, requests, and pagination
- `src/ig/feeds.py`: random selection and account discovery
- `src/ig/views.py`: existing HTML, CSS, and JavaScript views
- `src/ig/server.py`: local HTTP routes and server lifecycle

The server binds to `127.0.0.1`. Instagram fetching uses `curl_cffi` browser impersonation and undocumented page payloads; login walls, rate limits, and upstream changes can interrupt browsing. The pagination query ID is in `instagram.py` and may need refreshing when Instagram changes it. Automated tests use fixtures and mocks rather than making Instagram requests. Node.js enables the JavaScript preloading regression check; that check is skipped if Node is unavailable.

The reels view prepares the next three videos and requests another batch when fewer than eight unread reels remain. Temporary empty batches and request failures retry automatically. Media outside the nearby slides is released to limit memory use.

## Prefer memes

Use **Prefer memes** at the top of the reels view to turn the preference on or off. It is off by default and persists in `~/.config/ig/preferences.json`. Changes apply to newly requested batches; already loaded reels stay in place.

When on, account selections target 70% saved accounts in the `memes` feed, 20% new accounts found through meme-account tags and mentions, and 10% other accounts. Empty or exhausted groups redistribute their share among available groups. These are approximate account-selection shares, not guaranteed reel counts. Tags and mentions are leads, not automatic content classification; commenters stay in general discovery. The Add button asks which feed to save an account to, in both the reels and profile views. Choose `memes` to include it in your preferred saved accounts. Discovery lasts for the server session. Turning the preference off restores equal selection among eligible accounts.

Opening batches avoid the previous batch’s opening account when another account is available. History includes prefetched clips, and starts accumulating with this version; clips delivered by earlier versions cannot be recovered.

## Reel shortcuts and blocklists

- **Space**: pause or play the current reel.
- **M**: mute or unmute.
- **A**: open the feed picker for the current account, even if it is already saved elsewhere.
- **P**: open the current account's profile in a new tab.
- **B**: block the current account permanently.
- **Shift+B**: block the current account for this tab's session.

Shortcuts do not intercept typing or controls in the feed picker. Blocking hides all loaded reels from that account, skips to another account when possible, and excludes it from future batches and discovery picks.

Permanent blocks are stored in `~/.config/ig/blocked.json` and take priority over saved feeds. Use `ig blocked` to list them, `ig block <username>` to add one, and `ig unblock <username>` to remove one. Reload after unblocking to update the open view. Blocking does not delete feed membership.

Session blocks are stored in the tab's `sessionStorage` and sent with its batch requests. They survive page refreshes and do not change the permanent list or other existing tabs. Closing the tab normally clears them; browser session restoration may restore them with the tab.

## Automatic song recognition

After a reel starts playing and stays visible for three seconds, the app identifies its music automatically and displays the song and artist beneath the caption. Recognition also works while playback is muted. Quickly skipped reels and preloaded videos do not trigger recognition.

This uses the free, open-source [ShazamIO client](https://github.com/shazamio/ShazamIO), which contacts Shazam's unofficial recognition service. No paid API key or microphone access is required. The server downloads the reel temporarily (up to 40 MB), extracts 12 seconds of audio using the bundled FFmpeg binary and creates its fingerprint locally, and sends that fingerprint to Shazam. Temporary video files are deleted after recognition. It is not an offline service, and upstream changes can interrupt it.

Matches are cached for 30 days and no-match results for one day in `~/.cache/ig/songs.sqlite3`, capped at 5,000 entries. Failures are shown separately from genuine no-match results and never block video playback. No-match results are possible for speech, remixes, and obscure music.
