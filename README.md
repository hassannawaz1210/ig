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

A saved feed name opens the combined mix, matching the original tool. The mix ranks individual reels using your local feedback and rotates creators, with room for new-account trials. The interface supports video playback, carousels, adding and removing accounts. In the reels view, use arrow keys or `j`/`k` to move.

## Local data

Feeds, account memberships, permanent blocks, and the meme preference are stored in `~/.config/ig/settings.sqlite3`. On first use, existing `feeds.json`, `blocked.json`, and `preferences.json` are imported together in one transaction. Original JSON files are retained as backups and are no longer read after migration. Both legacy feed formats are accepted; weights are discarded. Defaults are created only if there is no legacy feed file. Feed changes use database transactions and update the running mix automatically. The `programming` feed combines programming, computer science, and software engineering, starting with `cs50`, `freecodecamp`, `fireship_dev`, and `codewithharry`. Background server metadata lives in `~/.cache/ig/server.json`; downloaded profile state stays in memory; feedback and liked-reel discovery leads persist in `~/.config/ig/recommendations.sqlite3`. The latest 5,000 delivered clip IDs are saved in `~/.cache/ig/history.sqlite3` to avoid repeats across restarts, with a one-time import from `history.json`. `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` can override these locations, including for isolated development.

All four SQLite databases track their schema with `PRAGMA user_version`. Ordered migrations run automatically in a transaction; failures roll back and newer unsupported versions are rejected. Existing unversioned databases are adopted without replacing their data or repeating completed JSON imports. To change a schema, append a migration after `_initial_schema` in that database’s migration sequence; do not edit an already released migration.

No virtual environment, personal feed configuration, or runtime cache is included in the project. The original installation is left in place.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

- `src/ig/cli.py`: commands and browser shortcuts
- `src/ig/config.py`: paths and saved feeds
- `src/ig/instagram.py`: page parsing, requests, and pagination
- `src/ig/feeds.py`: varied batches and account discovery
- `src/ig/views.py`: existing HTML, CSS, and JavaScript views
- `src/ig/server.py`: local HTTP routes and server lifecycle

The server binds to `127.0.0.1`. Instagram fetching uses `curl_cffi` browser impersonation and undocumented page payloads; login walls, rate limits, and upstream changes can interrupt browsing. The pagination query ID is in `instagram.py` and may need refreshing when Instagram changes it. Automated tests use fixtures and mocks rather than making Instagram requests. Node.js enables the JavaScript preloading regression check; that check is skipped if Node is unavailable.

The reels view prepares the next three videos and requests another batch when fewer than eight unread reels remain. Temporary empty batches and request failures retry automatically. Media outside the nearby slides is released to limit memory use.

## Prefer memes

Use **Prefer memes** at the top of the reels view to turn the preference on or off. It is off by default and persists in `~/.config/ig/settings.sqlite3`. Changes apply to newly requested batches; already loaded reels stay in place.

When on, the meme preference biases eligible account choices toward the saved `memes` feed and discoveries from meme accounts. Creator rotation, trial selection, and available clips also affect the mix, so this is not a fixed percentage of reels. Turning it off removes that feed bias; personal reel preferences still apply. Tags and mentions are leads, not automatic content classification. Random commenters are no longer used for discovery.

The Add button asks which feed to save an account to, in both the reels and profile views. Choose `memes` to include it in your preferred saved accounts. Liking a reel never adds its creator to a feed.

Opening batches avoid the previous batch’s opening account when another account is available. History includes prefetched clips, and starts accumulating with this version; clips delivered by earlier versions cannot be recovered.

## Reel shortcuts and blocklists

- **?**: open the shortcut list (or click the keyboard icon); **Esc** closes it.
- **L**: like or unlike an individual reel locally.
- **N**: mark this reel as not interesting and move on.
- **Space**: pause or play the current reel.
- **M**: mute or unmute.
- **R**: remove the current account from all saved feeds (does not block it).
- **A**: open the feed picker for the current account, even if it is already saved elsewhere.
- **P**: open the current account's profile in a new tab.
- **B**: block the current account permanently.
- **Shift+B**: block the current account for this tab's session.

Shortcuts do not intercept typing or controls in the feed picker. Blocking hides all loaded reels from that account, skips to another account when possible, and excludes it from future batches and discovery picks.

Permanent blocks are stored in `~/.config/ig/settings.sqlite3` and take priority over saved feeds. Use `ig blocked` to list them, `ig block <username>` to add one, and `ig unblock <username>` to remove one. Reload after unblocking to update the open view. Blocking does not delete feed membership.

Session blocks are stored in the tab's `sessionStorage` and sent with its batch requests. They survive page refreshes and do not change the permanent list or other existing tabs. Closing the tab normally clears them; browser session restoration may restore them with the tab.

## Automatic song recognition

After a reel starts playing and stays visible for three seconds, the app identifies its music automatically and displays the song and artist beneath the caption. Recognition also works while playback is muted. Quickly skipped reels and preloaded videos do not trigger recognition.

This uses the free, open-source [ShazamIO client](https://github.com/shazamio/ShazamIO), which contacts Shazam's unofficial recognition service. No paid API key or microphone access is required. The server downloads the reel temporarily (up to 40 MB), extracts 12 seconds of audio using the bundled FFmpeg binary and creates its fingerprint locally, and sends that fingerprint to Shazam. Temporary video files are deleted after recognition. It is not an offline service, and upstream changes can interrupt it.

Matches are cached for 30 days and no-match results for one day in `~/.cache/ig/songs.sqlite3`, capped at 5,000 entries. Failures are shown separately from genuine no-match results and never block video playback. No-match results are possible for speech, remixes, and obscure music.

## Discovering reels you like

The heart is now a **local reel like**, not an Instagram action. Its displayed count still comes from Instagram. Use the heart or **L** to ask for more similar content; use the minus-circle button or **N** for less. Neither action follows, saves, or blocks the creator. Account saving (**A**) and the two blocklists remain separate.

Likes and “not interested” train a small, transparent ranking model using caption words, hashtags, and audio IDs or recognized song metadata when available. A quick skip after 0.3–2.5 seconds of playback is a much weaker signal. Skips do not overwrite explicit likes/dislikes, and each reel contributes only its latest feedback. This is metadata matching; it does not understand the visuals or jokes inside a video.

New creator candidates come from tags and mentions in liked reels; saved creators' tags bootstrap exploration before you have likes. Liked creators can get further trials without being added to a feed. A batch contains at most one reel per creator, with up to six creators and at most three profile/pagination requests. The scheduler prefers alternatives to the last eight delivered creators. Unfamiliar creators get a 20-reel trial cooldown until liked, relaxed when there are no alternatives so the feed can continue. One fifth of clip selections remain exploratory. Blocks always take priority.

Feedback affects new batches. Already loaded clips remain in place, except an explicitly disliked reel is hidden immediately. The metadata and preferences survive server restarts; they stay on this machine. Media URLs and video files are not stored in the preferences database.

**Discovery limits:** logged-out hashtag pages tested here did not expose usable reel lists. There is no global hashtag/audio search in this version. Matching ranks candidates from accessible public profiles; tagged accounts may still be private, unrelated, or unavailable. No subscription or video-analysis service was added.

Meme discovery also samples a small built-in set of public profile candidates when the memes feed is nonempty. These are trial sources, never saved memberships. Creator credits from stored saved-account reels are recovered after restarts. Each batch first tries an eligible unfamiliar creator (subject to blocks, cooldowns, and the request budget), then fills from the normal mix. Discovery remains limited to public profiles and credits; this is not logged-in Explore access.
