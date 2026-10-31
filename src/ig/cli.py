"""Command-line entry point, including the original shell launcher's shortcuts."""
import json
import sys
from .config import FEED_RE, USER_RE, load_feeds, edit_feeds, accounts_from_feed, blocked_accounts, set_blocked
from .feeds import MIX, POOLS
from .instagram import profile, post, feed, InstagramError
from .server import open_user, serve, stop_server

HELP = """ig — browse Instagram logged out

  ig                        open the mixed reels feed
  ig reels                  open the mixed reels feed
  ig <username|feed>        open a profile or the mix for a saved feed
  ig blocked                list blocked accounts
  ig unblock <username>    allow an account in the mix again
  ig block <username>      block an account from the mix
  ig feeds                  list feeds
  ig add <feed> <acc...>    create or extend a feed
  ig rm <feed> [acc...]     remove accounts or a feed
  ig profile <username>    profile JSON
  ig post <shortcode|url>   post JSON
  ig feed <username>       full archive JSON (may be slow)
  ig tags                  top hashtags seen locally
  ig tag <name>            local reels and creators using #<name>
  ig phone                  use ig on your phone over the same Wi-Fi
  ig phone --new-code       same, with a new access code (signs out other devices)
  ig logs                   show the latest server log lines
  ig serve [--port N]      run in foreground (default: 7349)
  ig stop                   stop the background server
"""

PHONE_HELP = """ig phone — use ig on your phone or tablet

  ig phone                  start sharing on your Wi-Fi and show a QR code
  ig phone --new-code       make a new access code (signs out other devices)
  ig phone --port N         use port N instead of 7350

Keep the terminal open while you use ig on the phone; Ctrl+C stops sharing.
"""


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if args and args[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    commands = {"profile", "post", "feed", "open", "stop", "go", "feeds", "add", "rm", "serve", "blocked", "block", "unblock", "tags", "tag", "phone", "logs"}
    if args and args[0] == "boost":
        raise SystemExit("Feed weighting has been removed.")
    if args and args[0] not in commands:
        args.insert(0, "go")
    if args and args[0] in {"profile", "post", "feed", "open", "go", "add", "rm", "block", "unblock", "tag"} and len(args) < 2:
        raise SystemExit(f"missing argument for ig {args[0]} (see ig --help)")
    try:
        return _run(args)
    except InstagramError as error:
        raise SystemExit(f"ig: {error}")


def _option_value(args, name):
    """The value after --name, validated as a port number."""
    i = args.index(name)
    try:
        value = int(args[i + 1])
    except (IndexError, ValueError):
        raise SystemExit(f"ig: {name} needs a number, for example {name} 7350")
    if not 1 <= value <= 65535:
        raise SystemExit(f"ig: {name} must be between 1 and 65535")
    return value


def _run(args):
    argv = ["ig", *args]
    cmd = argv[1] if len(argv) > 1 else ""
    arg = argv[2] if len(argv) > 2 else ""
    if cmd == "":
        open_user("/")
    elif cmd == "open":
        open_user(arg.lstrip("@").lower())
    elif cmd == "go":
        name = arg.lstrip("@").lower()
        if name in {"all", "reels"} or name in load_feeds():
            open_user("/")
        else:
            open_user(f"/u/{name}")
    elif cmd == "blocked":
        print("\n".join(sorted(blocked_accounts())) or "No blocked accounts")
    elif cmd in {"block", "unblock"}:
        try:
            set_blocked(arg, cmd == "block")
        except ValueError as error:
            raise SystemExit(str(error))
        print(f"{cmd}ed @{arg.lstrip('@').lower()}")
    elif cmd == "feeds":
        for n, val in load_feeds().items():
            accounts = accounts_from_feed(val)
            print(f"{n:12} {len(accounts):>2} accts  "
                  f"{', '.join(accounts)}")
    elif cmd == "add":
        name = arg.lower()
        if not FEED_RE.fullmatch(name):
            raise SystemExit("feed name: lowercase/digits/-/_ , max 20 chars")
        with edit_feeds() as feeds:
            accounts = [a.lstrip("@").lower() for a in argv[3:]]
            accounts = [a for a in accounts if USER_RE.fullmatch(a)]
            if not accounts:
                raise SystemExit("give at least one account: ig add <feed> <acc...>")
            cur = accounts_from_feed(feeds.get(name))
            feeds[name] = list(dict.fromkeys(cur + accounts))
        POOLS.pop(name, None)
        print(f"feed '{name}': {len(feeds[name])} accounts")
    elif cmd == "rm":
        name = arg.lower()
        with edit_feeds() as feeds:
            if name not in feeds:
                raise SystemExit(f"no feed '{name}'")
            cur = accounts_from_feed(feeds[name])
            accounts = [a.lstrip("@").lower() for a in argv[3:]]
            if accounts:
                feeds[name] = [a for a in cur if a not in accounts]
                print(f"feed '{name}': {len(feeds[name])} accounts left")
            else:
                del feeds[name]
                print(f"removed feed '{name}'")
            POOLS.pop(name, None)
    elif cmd == "tags":
        from . import recommendations
        rows = recommendations.top_tags()
        liked = dict(recommendations.liked_tags())
        if not rows:
            raise SystemExit("no hashtags stored yet — watch some reels first")
        for tag, creators, reels in rows:
            star = f"  ({liked[tag]} liked)" if tag in liked else ""
            print(f"#{tag:30} {reels:>4} reels  {creators:>3} creators{star}")
    elif cmd == "tag":
        from . import recommendations
        name = arg.lstrip("#").lower()
        reels = recommendations.tag_reels(name)
        if not reels:
            raise SystemExit(f"nothing stored for #{name} yet — watch some reels first")
        creators = {}
        for reel in reels:
            creators[reel["owner"]] = creators.get(reel["owner"], 0) + 1
        print(f"#{name} — {len(reels)} local reels from {len(creators)} creators")
        for owner, count in sorted(creators.items(), key=lambda kv: -kv[1]):
            print(f"  @{owner:24} {count} reels")
        print("recent reels:")
        for reel in reels[:12]:
            mark = {"like": "✓", "dislike": "✗", "skip": "·"}.get(reel["feedback"], " ")
            print(f"  {mark} {reel['shortcode']:14} @{reel['owner']}")
    elif cmd == "phone":
        from .phone import share
        options = argv[2:]
        if {"-h", "--help", "help"} & set(options):
            print(PHONE_HELP)
            return 0
        port = _option_value(options, "--port") if "--port" in options else None
        unknown = [o for o in options if o.startswith("-") and o not in {"--port", "--new-code"}]
        if unknown:
            raise SystemExit(f"ig: unknown option {unknown[0]} (see ig phone --help)")
        return share(port=port, new_code="--new-code" in options)
    elif cmd == "logs":
        from . import logs
        lines = logs.tail(60)
        print("\n".join(lines) if lines else "No server log yet.")
        print(f"\n(log file: {logs.log_file()})", file=sys.stderr)
    elif cmd == "serve":
        port = _option_value(argv, "--port") if "--port" in argv else 7349
        serve(port)
    elif cmd == "stop":
        stop_server()
    elif cmd == "profile":
        u, codes = profile(arg)
        print(json.dumps({
            "username": u.get("username"),
            "full_name": u.get("full_name"),
            "verified": u.get("is_verified"),
            "private": u.get("is_private"),
            "followers": u.get("follower_count"),
            "bio": u.get("biography"),
            "profile_pic": u.get("profile_pic_url_hd") or u.get("profile_pic_url"),
            "recent_post_shortcodes": codes[:12],
        }, indent=2, ensure_ascii=False))
    elif cmd == "post":
        p = post(arg)
        print(json.dumps({
            "shortcode": p.get("code"),
            "taken_at": p.get("taken_at"),
            "like_count": p.get("like_count"),
            "comment_count": p.get("comment_count"),
            "caption": (p.get("caption") or {}).get("text", "")[:200],
        }, indent=2, ensure_ascii=False))
    elif cmd == "feed":
        print(json.dumps(feed(arg), indent=2, ensure_ascii=False))
    else:
        raise SystemExit(HELP)
