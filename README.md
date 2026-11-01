# ig

Browse public Instagram reels without an Instagram account. Run a local server,
create your own feeds, and use likes and skips to shape a mixed feed with creator
discovery. No Instagram login, paid API key, or scraping service is required.

- Create, mix, and edit feeds of accounts through the browser or CLI.
- Like individual reels, save them, and block creators permanently or for a session.
- Browse with keyboard shortcuts or phone gestures; view top-level comments.
- Play and download high-quality video when available, with automatic song recognition.
- Keep feeds, feedback, and viewing history in local SQLite databases.

This is an unofficial project with no affiliation with Instagram or Meta.
Instagram's undocumented public endpoints can change or rate-limit requests;
private and login-gated content is unavailable. Discovery works from public
content and local feedback, not Instagram's personalized recommendation API.

## Install and run

Requires **Python 3.11+** and a modern browser. Linux is tested locally; macOS and
Windows instructions are provided but have not been verified end to end.
FFmpeg is supplied by the Python dependency; Node.js is only needed for browser tests.

Clone this repository using its GitHub **Code** button (or download and extract
its ZIP), then open a terminal in the project directory.

**Linux / macOS**

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
ig
```

**Windows PowerShell**

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\ig.exe
```

`ig` starts a background server on a free localhost port and opens your browser.
If the browser does not open, paste the printed URL into it. `BROWSER` can select
a preferred browser. For a foreground server at a predictable address:

```sh
ig serve --port 7349
# Open http://127.0.0.1:7349
```

Use `ig stop` to stop the background server. After updating the installed code,
stop it and run `ig` again.

## Start with your own feeds

A fresh installation has **no feeds or accounts**. Click **Create feed**, name it,
and paste Instagram handles or profile links. Add several creators to give the
mix variety. You can also create a feed from the terminal:

```sh
ig add space nasa esa       # example only; replace with your own choices
ig feeds                   # inspect your saved feeds
ig                         # open the mix
```

In the browser, **+** creates a feed, **Shift+A** adds accounts, and **,** opens
the selector to choose one feed or mix several. Accounts aren't added to your
feeds automatically. Custom feed names are supported; automatic topic matching
is stronger for recognized topics and improves with locally observed hashtags.

Useful shortcuts: **J/K** next/previous, **Space** pause, **M** mute, **L** like,
**N** less like this, **A** add current creator, **B** block, **C** comments,
**S** save reel, **D** download, **?** all shortcuts.

See the [full usage guide](docs/usage.md) for phone sharing, discovery, video
quality, song recognition, feed deletion, and all commands.

## Your data

Each OS user has their own local profile. Everyone using the **same server**
(including phones) shares its feeds and feedback; this is not a multi-user hosted
service. Nothing from the maintainer's feeds or databases is shipped.

| Location | Contents |
| --- | --- |
| `~/.config/ig/` | SQLite settings, feeds, blocks, saved reels, and recommendations |
| `~/.cache/ig/` | Viewing history, metadata, temporary video cache, logs, and server state |

`XDG_CONFIG_HOME` and `XDG_CACHE_HOME` override these parent directories on all
platforms. Set **both** before starting `ig` to keep independent profiles on the
same computer. For example on Linux/macOS:

```sh
XDG_CONFIG_HOME="$HOME/ig-work/config" XDG_CACHE_HOME="$HOME/ig-work/cache" ig
```

Use the same variables for subsequent commands, including `ig stop`. Stop desktop
and phone servers before backing up both data directories. Keep backups outside
the repository. Schema migrations run automatically and preserve existing data.

The app contacts Instagram for public media and sends locally generated audio
fingerprints to Shazam for automatic song recognition. No microphone is used.
Downloads you explicitly save remain on your device; temporary media is cached
locally. Local storage does not mean offline browsing.

## Troubleshooting

- **Empty feed:** add public accounts that publish reels and select their feed.
- **Login wall / rate limit:** let the global backoff expire; repeated retries
  won't unlock unavailable content. Check `ig logs` for details.
- **Browser won't launch:** open the printed localhost URL manually.
- **Phone won't connect:** run `ig phone` and use its address and access code on
  the same trusted Wi-Fi. See [phone setup](docs/usage.md#using-ig-on-your-phone).
- **After an update:** reinstall with `python -m pip install .`, then `ig stop`
  and `ig`. Feed databases live outside the installation and are retained.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for isolated tests, packaging checks, and
project structure. CI runs mocked tests and browser checks without Instagram
credentials or live Instagram requests.

## License

Licensed under [GNU GPL v3.0](LICENSE) (GPL-3.0-only). Distributed modifications
must comply with its source-sharing terms. Third-party dependencies retain their
own licenses.
