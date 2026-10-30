"""Command-line entry point, including the original shell launcher's shortcuts."""
import json
import sys
from .config import FEED_RE, USER_RE, load_feeds, save_feeds, accounts_from_feed, blocked_accounts, set_blocked
from .feeds import MIX, POOLS
from .instagram import profile, post, feed
from .server import open_user, serve, stop_server

HELP = """ig — browse Instagram logged out

  ig                        open the mixed reels feed
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
  ig serve [--port N]      run in foreground (default: 7349)
  ig stop                   stop the background server
"""


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if args and args[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    commands = {"profile", "post", "feed", "open", "stop", "go", "feeds", "add", "rm", "serve", "blocked", "block", "unblock"}
    if args and args[0] == "boost":
        raise SystemExit("Feed weighting has been removed.")
    if args and args[0] not in commands:
        args.insert(0, "go")
    if args and args[0] in {"profile", "post", "feed", "open", "go", "add", "rm", "block", "unblock"} and len(args) < 2:
        raise SystemExit(f"missing argument for ig {args[0]} (see ig --help)")
    argv = ["ig", *args]
    cmd = argv[1] if len(argv) > 1 else ""
    arg = argv[2] if len(argv) > 2 else ""
    if cmd == "":
        open_user("/")
    elif cmd == "open":
        open_user(arg.lstrip("@").lower())
    elif cmd == "go":
        name = arg.lstrip("@").lower()
        if name == "all" or name in load_feeds():
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
        feeds = load_feeds()
        accounts = [a.lstrip("@").lower() for a in argv[3:]]
        accounts = [a for a in accounts if USER_RE.fullmatch(a)]
        if not accounts:
            raise SystemExit("give at least one account: ig add <feed> <acc...>")
        cur = accounts_from_feed(feeds.get(name))
        feeds[name] = list(dict.fromkeys(cur + accounts))
        save_feeds(feeds)
        POOLS.pop(name, None)
        print(f"feed '{name}': {len(feeds[name])} accounts")
    elif cmd == "rm":
        name = arg.lower()
        feeds = load_feeds()
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
        save_feeds(feeds)
    elif cmd == "serve":
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 7349
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
