# ig

A local Instagram reels browser. Make your own feeds, mix them together, and
find new creators as you watch. No Instagram account needed.

Like individual reels to shape recommendations, save favorites, or block
accounts you don't want to see. There's keyboard navigation, comments, video
downloads, automatic song recognition, and phone access over Wi-Fi.

The standalone [Android app](https://github.com/hassannawaz1210/ig/releases/download/android-updates/ig-android.apk)
runs on your phone without a desktop server. It checks GitHub for updates and
also has **Settings → Check for updates**. Tap Update and confirm Android's
installer; your feeds and settings stay in place. No app store is needed.
See [Android setup](android/README.md) for build instructions and current limitations.

## Getting started

You'll need Python 3.11+ and a browser. Linux is tested; macOS and Windows
haven't been verified end to end yet.

```sh
git clone https://github.com/hassannawaz1210/ig.git
cd ig
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
ig
```

<details>
<summary>Windows setup (PowerShell)</summary>

```powershell
git clone https://github.com/hassannawaz1210/ig.git
cd ig
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\ig.exe
```

</details>

`ig` starts the server and opens your browser. If it doesn't open, use the URL
printed in the terminal. Run `ig stop` when you're done.

To open a reel someone shared with you:

```sh
ig watch 'https://www.instagram.com/reel/DfNNjp-xLDt/'
```

You can also choose **Open reel link** in the feed menu and paste a link.

## Make it yours

Start with **Create feed** and add some Instagram handles or profile links.
Name your feeds however you like, then use the feed selector to watch one or
mix a few together. New installations start empty.

Open **Settings** from the desktop player or Android's bottom navigation to
manage all your feeds and accounts. Expand a feed to search its accounts, add
more, or remove an account from that feed. Feed deletion asks for confirmation
and offers Undo.

Settings also lets each user choose **Smooth** reel transitions or **Snappy**
instant switches. Smooth slides the next reel into view; Snappy preserves the
existing behavior. The choice is saved in the local profile.

Opening Settings, saved reels, or a profile keeps your player in place. Returning
resumes the same reel at the same position, with your sound and pause state intact.
The player remembers your sound choice, auto-scroll, and selected feeds in the
local database, including after restarting the app.

Use the paper-plane **Share** button beside a reel to share its Instagram link.
Android opens the system share sheet; browsers use their share menu or copy the
link to your clipboard.

Search finds accounts in your feeds and opens exact @handles or Instagram links.
It does not use Instagram's login-only global keyword search. On touch screens,
hold a playing reel for 2× speed and release to restore its previous speed.
Paused reels show a play icon. Drag the bottom progress bar to jump forward or
backward without changing whether the reel is playing or paused.

Already follow accounts on Instagram? Choose **Settings → Import Instagram
following** on desktop or Android. Select your Instagram export ZIP, `following.json`,
or `following.html`, review the handles, and import into an existing or new feed.
Duplicates are skipped, blocked accounts stay blocked, and existing feeds are
preserved. This is a one-time import, not automatic synchronization.

Get the export from Instagram's **Accounts Center → Your information and
permissions**, under the information export/download controls. Select
**Followers and following**, **All time**, and preferably **JSON**. Import files
up to 25 MB; for a larger archive, extract just the following file first.
The export is processed locally and only account handles are saved. Private
accounts still cannot be viewed without logging in, which ig does not support.

To move your setup to another device, choose **Settings → Export backup**.
Then choose **Settings → Import backup** on the other device and select the JSON file.
Review it before importing: feeds, permanent blocks, and saved reels are merged
with what you already have. Sound, auto-scroll, reel transitions, and selected
feeds are restored from the backup. Older feed-only backups still work and leave
your settings unchanged. Backups contain saved reel metadata, not video files;
viewing history, learned preferences, and temporary session blocks are excluded.
On Android, choose where to save the export using the system file picker.

You can also add accounts from the terminal:

```sh
ig add space nasa esa
ig feeds
```

Use **J / K** to move between reels, **Space** to pause, and **L** to like.
Press **?** for all shortcuts. To watch on your phone, run `ig phone` and scan
the QR code while connected to the same trusted Wi-Fi.

The [usage guide](docs/usage.md) covers feed management, shortcuts, discovery,
phone access, and playback options.

## Local data

Your feeds, likes, saves, and blocks live in SQLite under `~/.config/ig/`.
History and caches live under `~/.cache/ig/`. Each OS user gets their own data;
browsers and phones connected to the same server share that server's profile.
You can use `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` to keep separate profiles.

Browsing contacts Instagram. Song recognition sends an audio fingerprint to
Shazam; it doesn't use your microphone. Neither needs a paid API key.

Instagram sometimes rate-limits requests or changes its public endpoints, so
loading can fail. The app backs off automatically; `ig logs` shows details.
Private and login-gated content isn't available. This project isn't affiliated
with Instagram or Meta.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and the code layout.

Licensed under [GPL-3.0](LICENSE).
