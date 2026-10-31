"""`ig phone`: use ig from a phone or tablet on the same Wi-Fi.

The shared server asks for an access code once per device, then remembers it
with a cookie. Scanning the QR code (or opening the full link) skips typing."""
import hmac
import logging
import shutil
import socket
import sys
import threading
import time
from html import escape
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlencode, urlsplit

from . import config, logs

log = logging.getLogger(__name__)

DEFAULT_PORT = 7350
COOKIE = "ig_phone"


class PhoneAccess:
    """Checks the access code for requests from other devices.

    Wrong codes are limited across all devices (10 per 10 minutes), so the
    code cannot be guessed by trying many addresses."""

    MAX_FAILURES = 10
    WINDOW = 10 * 60

    def __init__(self, code: str):
        self.code = code
        self._failures: list[float] = []
        self._devices: set[str] = set()
        self._lock = threading.Lock()

    def _matches(self, value: str) -> bool:
        return bool(value) and hmac.compare_digest(value.strip().lower().encode(), self.code.encode())

    def _locked(self) -> bool:
        with self._lock:
            now = time.time()
            self._failures = [t for t in self._failures if now - t < self.WINDOW]
            return len(self._failures) >= self.MAX_FAILURES

    def _failed(self, handler) -> None:
        with self._lock:
            self._failures.append(time.time())
        log.warning("Wrong phone access code from %s", handler.client_address[0])

    def _remember(self, handler) -> None:
        address = handler.client_address[0]
        with self._lock:
            new = address not in self._devices
            self._devices.add(address)
        if new:
            log.info("Device connected from %s", address)

    def _cookie_header(self) -> str:
        # Lax (not Strict) so the cookie survives opening the link from a QR
        # scanner; cross-site POSTs still never carry it.
        return f"{COOKIE}={self.code}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Lax"

    def allow(self, handler) -> bool:
        """True to continue; otherwise a reply (redirect or sign-in) was sent."""
        try:
            cookie = SimpleCookie(handler.headers.get("Cookie") or "")
        except CookieError:
            cookie = SimpleCookie()
        if COOKIE in cookie and self._matches(cookie[COOKIE].value):
            self._remember(handler)
            return True
        parts = urlsplit(handler.path)
        query = parse_qs(parts.query)
        key = query.pop("key", [""])[0]
        if key and handler.command == "GET":
            if self._locked():
                return self._page(handler, 429, "Too many wrong codes. Wait ten minutes and try again.")
            if self._matches(key):
                self._remember(handler)
                target = parts.path + ("?" + urlencode(query, doseq=True) if query else "")
                handler.send_response(303)
                handler.send_header("Set-Cookie", self._cookie_header())
                handler.send_header("Location", target or "/")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return False
            self._failed(handler)
        if handler.command != "GET" or parts.path.startswith(("/static/", "/download/")):
            handler._send(401, b"Open ig on this device first", "text/plain")
            return False
        return self._page(handler, 401, "Wrong code. Check the code shown by ig phone." if key else "")

    def sign_in(self, handler):
        """POST /sign-in from the code form."""
        try:
            length = int(handler.headers.get("Content-Length") or 0)
            if not 0 < length <= 1024:
                raise ValueError("bad length")
            form = parse_qs(handler.rfile.read(length).decode("utf-8", "replace"))
        except (ValueError, UnicodeError):
            return handler._send(400, b"invalid sign-in", "text/plain")
        target = form.get("next", ["/"])[0]
        if not target.startswith("/") or target.startswith("//"):
            target = "/"  # Only ever return to a page on this server.
        if self._locked():
            return self._page(handler, 429, "Too many wrong codes. Wait ten minutes and try again.", target)
        if not self._matches(form.get("code", [""])[0]):
            self._failed(handler)
            return self._page(handler, 401, "That code is not right. Check the code shown by ig phone.", target)
        self._remember(handler)
        handler.send_response(303)
        handler.send_header("Set-Cookie", self._cookie_header())
        handler.send_header("Location", target)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    @staticmethod
    def _page(handler, status: int, message: str, target: str | None = None) -> bool:
        target = target or urlsplit(handler.path).path or "/"
        body = SIGN_IN_PAGE.format(message=escape(message), next=escape(target, quote=True)).encode()
        handler._send(status, body, "text/html; charset=utf-8")
        return False


SIGN_IN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ig</title><style>
body {{ margin:0;min-height:100dvh;display:grid;place-items:center;background:#0b0b0f;color:#f2f2f5;
       font:16px system-ui,sans-serif; }}
form {{ width:min(340px,calc(100vw - 48px));padding:28px 24px;border:1px solid #2c2c36;border-radius:20px;
       background:#15151c; }}
h1 {{ margin:0 0 8px;font-size:22px; }} p {{ margin:0 0 18px;color:#a3a3b2;line-height:1.45; }}
input {{ box-sizing:border-box;width:100%;padding:14px;border:1px solid #3a3a48;border-radius:12px;
        background:#0b0b0f;color:inherit;font:600 22px ui-monospace,monospace;letter-spacing:4px;text-align:center; }}
button {{ width:100%;margin-top:14px;padding:13px;border:0;border-radius:12px;background:#655191;color:#fff;
         font:inherit;font-weight:600; }}
.error {{ color:#f3a6b8;margin-top:12px;margin-bottom:0; }} .error:empty {{ display:none; }}
</style></head><body>
<form method="post" action="/sign-in">
  <h1>Open ig</h1>
  <p>Enter the access code shown in the terminal where you ran <b>ig phone</b>.</p>
  <input name="code" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"
    inputmode="text" maxlength="20" required autofocus aria-label="Access code">
  <input type="hidden" name="next" value="{next}">
  <button type="submit">Continue</button>
  <p class="error" role="alert">{message}</p>
</form></body></html>"""


def network_address() -> str | None:
    """This computer's address on the local network, or None if offline."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # Connecting a UDP socket sends nothing; it only picks the route.
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    try:
        address = socket.gethostbyname(socket.gethostname())
        return None if address.startswith("127.") else address
    except OSError:
        return None


def _qr(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    import io
    out = io.StringIO()
    segno.make(text, error="m").terminal(out=out, compact=True, border=2)
    return out.getvalue()


def _already_running(port: int) -> bool:
    from curl_cffi import requests as cr
    try:
        return cr.get(f"http://127.0.0.1:{port}/ping", timeout=2).status_code == 200
    except Exception:
        return False


def instructions(address: str, port: int, code: str, *, running_elsewhere=False) -> str:
    base = f"http://{address}:{port}"
    link = f"{base}/?key={code}"
    qr = _qr(link)
    lines = ["", "  Open ig on your phone", "  ─────────────────────", ""]
    if running_elsewhere:
        lines += ["  Sharing is already running in another terminal.", ""]
    lines += ["  1. Connect your phone to the same Wi-Fi as this computer.",
              "  2. Scan this QR code with the phone's camera:" if qr else "  2. Open this link on the phone:", ""]
    if qr:
        lines += [qr.rstrip("\n"), "", "     Or open this link on the phone:", ""]
    lines += [f"     {link}", "",
              f"     Or go to {base} and type the code:  {code}", "",
              "  Your phone remembers the code, so next time just open the address.",
              "  Only share the code with your own devices. Anyone on this Wi-Fi with it",
              "  can use ig as you (the connection is not encrypted, so avoid public Wi-Fi).", ""]
    if shutil.which("ufw"):
        lines += [f"  Can't connect? Allow the port in your firewall:  sudo ufw allow {port}/tcp", ""]
    lines += ["  New code (signs out your other devices):  ig phone --new-code"]
    if not running_elsewhere:
        lines += ["  Press Ctrl+C to stop sharing."]
    return "\n".join(lines) + "\n"


def share(port: int | None = None, new_code: bool = False) -> int:
    from .server import make_server
    address = network_address()
    if address is None:
        print("ig: couldn't find this computer's network address. Is it connected to Wi-Fi?",
              file=sys.stderr)
        return 1
    if not port and _already_running(DEFAULT_PORT):
        if new_code:
            print("ig: sharing is already running in another terminal. Stop it there with Ctrl+C,\n"
                  "    then run ig phone --new-code again.", file=sys.stderr)
            return 1
        print(instructions(address, DEFAULT_PORT, config.phone_access_code(), running_elsewhere=True))
        return 0
    code = config.phone_access_code(renew=new_code)
    ports = [port] if port else list(range(DEFAULT_PORT, DEFAULT_PORT + 10))
    server = None
    for candidate in ports:
        try:
            server = make_server("0.0.0.0", candidate, PhoneAccess(code))
            break
        except OSError:
            continue
    if server is None:
        hint = f"port {port} is busy" if port else f"ports {ports[0]}–{ports[-1]} are busy"
        print(f"ig: couldn't start sharing: {hint}. Try: ig phone --port 8080", file=sys.stderr)
        return 1
    logs.setup()
    log.info("Sharing on http://%s:%d", address, server.server_address[1])
    print(instructions(address, server.server_address[1], code), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped sharing. Phones can't reach ig until you run ig phone again.")
    finally:
        server.server_close()
        log.info("Stopped sharing")
    return 0
