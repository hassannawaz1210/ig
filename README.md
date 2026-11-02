# ig

A local Instagram reels browser. Make your own feeds, mix them together, and
find new creators as you watch. No Instagram account needed.

Like individual reels to shape recommendations, save favorites, or block
accounts you don't want to see. There's keyboard navigation, comments, video
downloads, automatic song recognition, and phone access over Wi-Fi.

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
