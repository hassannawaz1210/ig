# Contributing

Use Python 3.11 or newer. Install an editable checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Tests

Run with temporary data directories so tests cannot touch your normal profile:

```sh
XDG_CONFIG_HOME="$(mktemp -d)" XDG_CACHE_HOME="$(mktemp -d)" \
  python -m unittest discover -s tests -v
```

On PowerShell, set `$env:XDG_CONFIG_HOME` and `$env:XDG_CACHE_HOME` to separate
new temporary directories before running the same unittest command.

Tests use mocks and fixtures instead of live Instagram requests. Browser tests
require Node.js 22+ and Chromium/Chrome; set `IG_TEST_CHROME` to the browser binary
if it is not detected. These checks skip when browser prerequisites are missing.
The GitHub workflow installs them explicitly.

## Packaging

```sh
python -m pip install build
python -m build
```

Install the resulting `dist/*.whl` in a fresh virtual environment and run `ig
--help` from outside the checkout. Templates, styles, and JavaScript must be
included in the wheel; CI also checks rendering from the installed package.

## Code map

- `src/ig/instagram.py`, `reels.py`: public Instagram requests and reel pagination.
- `src/ig/feeds.py`, `refill.py`, `recommendations.py`: loading, ranking, discovery.
- `src/ig/config.py`, `history.py`, `migrations.py`: local data and schema upgrades.
- `src/ig/server.py`, `phone.py`, `cli.py`: HTTP server, LAN sharing, commands.
- `src/ig/static/`, `views.py`: browser interface and page rendering.
- `src/ig/dash.py`, `recognition.py`: video assembly and song recognition.

Keep the project usable without Instagram login, paid APIs, or scraping services.
Discovery leads must still pass blocks and relevance checks. Keep ranking rules
small and explainable, and handle unavailable upstream data gracefully.

Append new database migrations; don't rewrite released migrations. Test upgrades
against existing data and test fresh installations separately. Never commit
SQLite databases, access codes, browser sessions, caches, or personal logs.
Redact handles, local paths, and tokens when sharing debugging output.

For bugs, include the Python/OS/browser versions, reproduction steps, and relevant
redacted log lines from `ig logs`. Distinguish an app regression from an upstream
Instagram login wall or rate limit.
