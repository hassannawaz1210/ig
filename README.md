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
ig comments SHORTCODE      # top comments of a post
ig feed nasa               # full archive JSON; may take a while
ig tags                    # top hashtags seen locally
ig tag gymmemes            # local reels and creators using #gymmemes
ig saved-reels             # list reels you saved with S
ig phone                   # use ig on your phone over the same Wi-Fi
ig logs                    # show the latest server log lines
ig serve --port 7349       # foreground server
ig stop                    # stop the background server
```

A saved feed name opens the combined mix, matching the original tool. The mix ranks individual reels using your local feedback and rotates creators, with room for new-account trials. The interface supports video playback, carousels, adding and removing accounts. In the reels view, use arrow keys or `j`/`k` to move. Each key press, wheel notch, or swipe moves one reel, including when a keyboard-scrolling extension such as Vimium handles `j`/`k`.

## Local data

The local SQLite databases are your user profile. Feeds, memberships, permanent blocks and selections live in `~/.config/ig/settings.sqlite3`; feedback, discovery metadata and Saved reels live in `~/.config/ig/recommendations.sqlite3`. New databases start with no feeds or predefined accounts. Existing databases keep their memberships and data. Browser tabs and devices connected to the same server share that local profile; there are no browser-specific profiles or logins. A separate OS account or `XDG_CONFIG_HOME` gives another person their own settings. Also set `XDG_CACHE_HOME` when isolating viewing history.

Use **+** or **Create feed** in the feed selector to create a feed. Accounts are optional at creation. **A** opens the add dialog with the current creator filled in (or a blank form if no reel is loaded); **Shift+A** always opens a blank account form. Choose an existing feed or **+ Create new feed**, paste handles or Instagram profile links separated by spaces, commas or new lines, then save with **Ctrl+Enter**. New feeds appear in the selector immediately. Delete a feed with its trash icon, or focus it in the selector and press **Delete**. Both controls first show an inline confirmation with **Cancel** focused; only **Delete feed** removes it. An **Undo** button then restores its accounts. Deletion snapshots are kept in SQLite; accounts in other feeds, saved reels, likes and blocks are unaffected. The dialog also works from a profile's Add button.

Legacy `feeds.json`, `blocked.json`, and `preferences.json` files are imported once and retained as backups. Old schema upgrades preserve custom memberships and no longer seed a gaming feed. Background server metadata lives in `~/.cache/ig/server.json`; public profile metadata is cached in `~/.cache/ig/profiles.sqlite3`; viewing history and temporary queue reservations live in `~/.cache/ig/history.sqlite3`. Sources start loading their next page below twelve unseen clips, ahead of new discovery accounts.

All SQLite databases track their schema with `PRAGMA user_version`. Ordered migrations run automatically in a transaction; failures roll back and newer unsupported versions are rejected. Existing unversioned databases are adopted without replacing their data or repeating completed JSON imports. To change a schema, append a migration after `_initial_schema` in that database’s migration sequence; do not edit an already released migration.

No virtual environment, personal feed configuration, or runtime cache is included in the project. The original installation is left in place.

## Profile cache and rate limits

Fetched profiles (bio, post metadata and the pagination cursor, but never sessions or tokens) are saved to `~/.cache/ig/profiles.sqlite3`, so restarting the server doesn't download every account again. A cached profile is used for an hour, then refreshed. While Instagram is rate limiting, copies up to a day old are shown instead. Entries older than a week are removed, and at most 1,000 profiles are kept.

When Instagram rate limits a request (HTTP 429, or a redirect to its login page), every request pauses together: 30 seconds at first, doubling up to 10 minutes, and any normal response resets it. A rate-limited account is retried in a later batch. Missing or private accounts are dropped for the session, and other failures drop an account only after three tries.

## Logs

The server writes to `~/.cache/ig/server.log`, rotating at 1 MB and keeping three old files. `ig logs` prints the latest lines. Set `IG_LOG_LEVEL=DEBUG` before `ig serve` to log every Instagram request. Browser error messages stay short; unexpected errors are logged in full and the page points to `ig logs`.

## Using ig on your phone

```sh
ig phone                   # share on your Wi-Fi and show a QR code
ig phone --new-code        # new access code; signs out other devices
ig phone --port 8080       # if 7350–7359 are busy
```

Scan the QR code, or open the printed address on the phone and type the 8-letter access code once. The phone remembers it with a cookie, so a bookmark or home-screen shortcut keeps working. The code stays the same until you run `--new-code`. Sharing lasts while the terminal is open; Ctrl+C stops it. Running `ig phone` again while it's already sharing just reprints the instructions.

Every page and request needs the code, and wrong codes are limited to 10 every 10 minutes across all devices. The connection is plain HTTP, so use it on your home Wi-Fi, not public networks. If the phone can't connect, check that both devices are on the same network and that your firewall allows the port (`sudo ufw allow 7350/tcp`). The phone server is a separate process from the desktop one; they share feeds, likes, history and the profile cache through SQLite.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

- `src/ig/cli.py`: commands and browser shortcuts
- `src/ig/config.py`: paths and saved feeds
- `src/ig/instagram.py`: page parsing, requests, and pagination
- `src/ig/dash.py`: best-quality streams assembled from DASH manifests
- `src/ig/feeds.py`: varied batches and account discovery
- `src/ig/views.py`: fills the page templates and serves `/static` files
- `src/ig/static/`: page templates (`*.html`), styles and browser scripts
- `src/ig/reasons.py`: “Why am I seeing this?” text
- `src/ig/server.py`: local HTTP routes and server lifecycle
- `src/ig/phone.py`: `ig phone` sharing and access codes
- `src/ig/logs.py`: server log file

The server binds to `127.0.0.1`. Instagram fetching uses `curl_cffi` browser impersonation and undocumented page payloads; login walls, rate limits, and upstream changes can interrupt browsing. The pagination query ID is in `instagram.py` and may need refreshing when Instagram changes it. Automated tests use fixtures and mocks rather than making Instagram requests. Node.js enables the JavaScript preloading regression check; that check is skipped if Node is unavailable.

The reels view prepares the next five videos and requests another batch when fewer than 24 unread reels remain. Temporary empty batches and request failures retry automatically. Media outside the nearby slides is released to limit memory use.

## Choosing feeds

Open the compact feed selector at the top of the reels view; a change applies right away, with no Apply button. **All** includes every saved feed. Tapping a feed while All is active picks just that feed, further taps add or remove feeds, and removing the last one goes back to All. Quick taps within about a third of a second are saved as one change. Selected feeds show a check. Use the arrow keys to move, Space to toggle a feed in your mix, and Enter to select only that feed.

Changing feeds does not reload the page. The reel you're watching and the ones before it stay; reels queued after it for the old choice are removed and a new batch for the new choice is added after the current reel. A batch that was requested before the change is thrown away when it arrives. Session blocks, likes and mute stay as they were. A small spinner shows while the switch is in progress; if saving fails, the chips go back to the saved choice and a short message says so. The choice persists in SQLite.

The page opens immediately and fetches its first batch afterwards, showing **Loading reels…** in the middle of the screen until the first reel appears (and a small notice when a new feed choice has nothing queued yet). Failed requests retry every 3 seconds; after two failures in a row the reason is shown with a **Try again** button. Selected feeds receive roughly balanced opportunities when eligible creators are available; discovery must also match a selected topic. A saved account's membership is trusted, including ironic humour with literal-looking captions. Accounts saved only in unselected feeds are excluded even if previously liked.

Settings migration v2 converts an enabled legacy meme preference into the Memes selection. Migration v3 removes the old studio and clip defaults (`imdb`, `netflix`, `marvelstudios`, `movieclips`) from `movie_edits`, keeping any accounts you added, without adding predefined feeds. Deleting the last selected feed does not silently enable All; choose another feed explicitly.

The Add button asks which feed to save an account to, in both reels and profile views. Liking a reel never adds its creator to a feed.

Opening batches avoid the last viewed account when another account is available. New queued reels are reserved for two minutes and renewed every thirty seconds while the tab is visible. Feed changes, abandoned batches and page departure release unseen reservations; expiry handles missed unload requests. Reservations prevent duplicate delivery across the desktop and phone servers without marking clips watched.

A reel enters viewing history only after its video makes playback progress while active and visible. The last 5,000 exclusions are retained. Existing delivered-only history is preserved and labeled `legacy_delivery`, with no invented watch times; those old exclusions age out under the same bounded-history policy. New viewed rows are labeled `viewed`. Playback progress stores unique portions played, excluding hidden-tab time, seeking and repeat loops. Buffering/seek interruptions suppress quick-skip feedback; explicit likes and dislikes remain authoritative. Watch progress is stored for inspection and future improvements, not used as a new positive ranking weight.

Creator rotation and trial cooldowns now use actual views rather than delivered batches. The old deliveries log remains separate for diagnostics. Bounded queue receipts allow delayed playback reports after a reservation expires or is released; a receipt does not hold a clip out of the feed. Each page has an ephemeral queue ID, not a user identity. All browsers still share the same local user databases. There is no history-off setting yet.

## Reel shortcuts and blocklists

- **?**: open the shortcut list (or click the keyboard icon); **Esc** closes it.
- **,**: open the feed selector. **↑/↓** moves between feeds, **Space** toggles a feed in your mix, **Enter** switches to that feed alone, and **Esc** closes the selector. **Home/End** jumps to the first/last feed.
- **L**: like or unlike an individual reel locally.
- **N**: mark this reel as not interesting and move on.
- **S**: save or unsave this reel in Saved reels (see below).
- **D**: download this reel (same as its download button).
- **Space**: pause or play the current reel.
- **M**: mute or unmute.
- **C**: show or hide top comments for the current reel (also click the comment count). Escape closes the panel. Comments load on demand and remain cached for that reel in the tab; navigating to another reel keeps the panel open and loads that reel’s comments.
- **R**: remove the current account from all saved feeds and block its reels, including discovery and queued clips. Adding the account back explicitly unblocks it.
- **A**: add the current account to a feed, or enter accounts when no reel is loaded.
- **Shift+A**: enter handles or profile links to add.
- **+**: create a feed, optionally with accounts.
- **Ctrl+Enter**: save the create/add dialog.
- **P**: open the current account's profile in a new tab.
- **W**: show why this reel was picked (or tap **why**).
- **B**: block the current account permanently.
- **Shift+B**: block the current account for this tab's session.

Shortcuts do not intercept typing or controls in the feed picker. Blocking hides all loaded reels from that account, skips to another account when possible, and excludes it from future batches and discovery picks.

Permanent blocks are stored in `~/.config/ig/settings.sqlite3` and take priority over saved feeds. Use `ig blocked` to list them, `ig block <username>` to add one, and `ig unblock <username>` to remove one. Reload after unblocking to update the open view. Blocking does not delete feed membership.

Session blocks are stored in the tab's `sessionStorage` and sent with its batch requests. They survive page refreshes and do not change the permanent list or other existing tabs. Closing the tab normally clears them; browser session restoration may restore them with the tab.

## Saved reels

Press **S** or the bookmark button on a reel to keep it in **Saved reels**; press it again to remove it. This saves that one reel, not its account; the `saved` feed of accounts is unrelated. Saving is not a like: it does not change feeds, likes, or the ranking. Open the list with the bookmark icon in the reels top bar (or `/saved-reels`), newest first; click a reel to play it. `ig saved-reels` prints each one's shortcode, account and link.

Saved reels are stored with their caption, link, date, like and comment counts and thumbnail in `~/.config/ig/recommendations.sqlite3`, and are never pruned. Instagram thumbnail links expire after a while, so older entries show a plain placeholder instead of the picture; the reel itself still plays.

## Video quality

Logged-out post pages only serve one progressive video rendition (720p), but the same payload carries a DASH manifest whose top representation matches the original upload resolution, typically 1080×1920 VP9. The app downloads that best video representation plus the best audio one, muxes them locally with the bundled ffmpeg, and plays the result instead of the progressive file; downloads (**D**) get the same best-quality file. Assembly happens in the background while a reel is still several slides away, and the last 48 assembled reels are cached in `~/.cache/ig/videos` for instant replay.

When a reel has no manifest — carousels, some older posts, or if Instagram changes the manifest format — playback falls back to the progressive URL and nothing changes for the user. Seeking uses HTTP range requests on the assembled file.

## Automatic song recognition

After a reel starts playing and stays visible for three seconds, the app identifies its music automatically and displays the song and artist beneath the caption. Recognition also works while playback is muted. Quickly skipped reels and preloaded videos do not trigger recognition.

This uses the free, open-source [ShazamIO client](https://github.com/shazamio/ShazamIO), which contacts Shazam's unofficial recognition service. No paid API key or microphone access is required. The server downloads the reel temporarily (up to 40 MB), extracts 12 seconds of audio using the bundled FFmpeg binary and creates its fingerprint locally, and sends that fingerprint to Shazam. Temporary video files are deleted after recognition. It is not an offline service, and upstream changes can interrupt it.

Matches are cached for 30 days and no-match results for one day in `~/.cache/ig/songs.sqlite3`, capped at 5,000 entries. Failures are shown separately from genuine no-match results and never block video playback. No-match results are possible for speech, remixes, and obscure music.

## Why am I seeing this?

Each reel has a **why** button (or press **W**). It says where the creator came from: a saved feed, a credit, tag or mention in another reel, shared hashtags, a creator you liked, or the built-in meme list. For new creators it also says what matched the topic (bio or recent captions), or that it's an occasional uncertain trial. Finally, it says how this reel was picked: features it shares with reels you liked (hashtags, words, audio or song), or a random pick to keep the feed varied.

## Discovering reels you like

The heart is now a **local reel like**, not an Instagram action. Its displayed count still comes from Instagram. Use the heart or **L** to ask for more similar content; use the minus-circle button or **N** for less. Neither action follows, saves, or blocks the creator. Account saving (**A**) and the two blocklists remain separate.

Likes and “not interested” train a small, transparent ranking model using caption words, hashtags, and audio IDs or recognized song metadata when available. A quick skip after 0.3–2.5 seconds of playback is a much weaker signal. Skips do not overwrite explicit likes/dislikes, and each reel contributes only its latest feedback. This is metadata matching; it does not understand the visuals or jokes inside a video.

New creator candidates come from tags, collaborations, mentions, explicit credits, liked creators, and the meme starter list. Source tags do not assign a topic to the candidate. Explicit credits are stronger leads; sponsor mentions are weaker, but neither is an automatic endorsement or exclusion. Stored leads come from selected saved accounts; All also includes tags from liked reels. Candidate profiles are checked against selected topics using their own bio and recent video captions. For movie edits that means edit words (edit, editor, fan edit, velocity, After Effects or AE, CapCut, AMV, scene pack); words like "movie", "film" or "cinema" alone are not enough, so studio and clip accounts don't qualify. Saved memberships and known meme starters bypass this heuristic.

**Local hashtag discovery.** Instagram serves no hashtag media to logged-out clients (verified again in 2026: tag pages are login shells and the keyword-search GraphQL rejects logged-out queries), so the app builds its own tag index from captions it has already fetched. Every stored reel's hashtags are indexed locally, and creators whose reels repeatedly share specific hashtags with your saved or liked reels become discovery leads — weaker than an explicit credit, comparable to a mention, and never from generic tags like `#funny`. Each feed also learns a hashtag vocabulary from its own accounts' reels, so a candidate posting `#gymmemes` in most recent captions matches the gym topic even though the plain-word rules cannot see inside the tag. Leads come from your most-used specific hashtags (likes count double), not the alphabetically first ones. Reach-bait tags such as `#explorepage`, and tags used by many unrelated creators, never count as leads or as topic vocabulary. Reels you disliked are not used as evidence. An account saved in several feeds teaches all of them. Editing a caption removes its old hashtags from the index. The vocabulary is cached for five minutes rather than rebuilt for every batch, and old reels are pruned together with their hashtag rows. `ig tags` and `ig tag <name>` show what the index knows; both read local data only and never contact Instagram.

**Comment leads.** Logged-out post pages do not embed comments, but a separate GraphQL query on the post's media id returns them, so the server fetches the top comments of each reel you actually watch — once per reel, in the background, after the media response is already on its way, and never blocking playback. @handles written by the reel's own creator in their comment replies count like caption mentions; other commenters are the weakest leads there are, kept to the eight most-liked per reel and weighing less than a sponsored mention. A like still approves only the reel's creator, never its commenters, and every comment-derived candidate passes the same topic checks, blocks and cooldowns as any other lead. `ig comments <shortcode>` shows what a post's comments look like.

The comments panel (**C**) stays open as you navigate reels and loads comments for the reel in view. It fetches more near the bottom, deduplicates by comment ID, and provides a Load more / Retry button even when the panel has no scrollbar. Failed or duplicate-only pages pause automatic loading; repeated cursors stop pagination. Responses for previous reels update only their cached comments. Discovery leads still use only the first page.

**Replies to comments are not available logged out.** Every surface was checked in 2026: the comments connection returns `child_comment_count` as null on every node and never includes preview replies; parent-id variables are silently ignored by the comments query; using a comment's id as the media id yields an empty connection; none of the 510 JavaScript bundles of the logged-out post page contains a replies or thread query (only Threads-app internals matched the names); the mobile private endpoints answer `fail` or a login shell; and the public embed page shows only the caption with a link out to instagram.com. Comment threads require a logged-in session. Only top-level comments are readable, which includes comments the reel's creator posted themselves — that is where the reply-mention discovery leads come from.

To avoid excluding unusual humour, at most one uncertain candidate can receive a trial in 25% of batches. Sponsor-only uncertain leads face an additional 20% chance. These candidates must come from the relevant source pool, have no detected conflicting topic, have no negative creator feedback, and pass creator cooldowns and blocks. Uncertainty is not a permanent rejection. Unknown custom feed topics use saved memberships plus this limited exploration. These checks use metadata and cannot understand visual irony. A batch contains at most one reel per creator, with up to six creators; source loading happens separately in bounded background workers. The scheduler prefers alternatives to the last eight actually viewed creators. Unfamiliar creators get a 20-view trial cooldown until liked, relaxed when there are no alternatives so the feed can continue. One fifth of clip selections remain exploratory. Blocks always take priority.

Feedback affects new batches. Already loaded clips remain in place, except an explicitly disliked reel is hidden immediately. The metadata and preferences survive server restarts; they stay on this machine. Media URLs and video files are not stored in the preferences database.

**Discovery limits:** Instagram's hashtag, keyword-search, audio, and private API surfaces all require a login; logged-out tag pages are login shells and return no media. There is no global hashtag/audio search in this version, and none is possible without credentials. Tag-driven discovery therefore works from the local index of already-fetched captions: it can only recommend creators whose reels this app has seen, and the per-feed vocabularies fill in as feeds are actually watched. Credited accounts may still be private or unavailable, and topic heuristics can still make mistakes. No subscription or video-analysis service was added.

There are no built-in creator candidates; discovery starts from your own saved accounts and feedback. Creator credits from stored saved-account reels are recovered after restarts. Each batch first tries an eligible unfamiliar creator (subject to blocks, cooldowns, and the request budget), then fills from the normal mix. Discovery remains limited to public profiles and credits; this is not logged-in Explore access.

Batch loading prioritizes cached unseen clips and returns partial batches before requesting additional cold profiles. The browser starts with a 24-reel queue target, then adapts between 12 and 48 based on forward scrolling speed and observed background loading time, including empty-response waits. Pauses, backward navigation and idle gaps do not count as consumption. Only the next five videos are preloaded. When multiple sources are available, each tab reserves at most two upcoming reels per creator across refill requests; watching or releasing one frees that creator’s next slot. A single-source feed can still fill normally. Refill requests exclude reels already shown or queued in the tab, and the browser also rejects duplicate IDs. The last 1,000 shown IDs survive a same-tab refresh in session storage; this does not mark prefetched clips as watched. When the queue is empty, a compact loading notice remains visible. A cold profile or upstream rate limit can still delay refills.

The mixed reels view uses the reels-only profile query, with its own cursor and metadata cache in `reels.sqlite3`; it does not paginate the photo grid. One profile-page request initializes a shared anonymous session for reels queries; other creators and subsequent pages reuse it. This path does not fetch creator bios. Each listing contains up to 12 reel IDs; at most three post-detail requests run per worker step to supply captions for ranking. Fetched playback metadata is reused for 15 minutes in memory, avoiding a second request when playback starts. Media URLs and sessions are not written to the reels cache. Source loading and pagination run in at most two background workers. Reel batch requests use memory/disk caches only; a slow Instagram request does not hold the batch lock. Sources start replenishing when fewer than twelve unseen clips remain. Equally urgent sources rotate by their last loading attempt, so the first alphabetical accounts cannot monopolize the workers. While work is pending, empty batches ask the browser to retry after 750 ms; exhausted sources report that no more reels are available instead of retrying forever. Existing source restrictions and upstream rate limits still apply.
