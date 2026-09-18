"""RatanakQuickShare's local-only HTTP server (Python standard library)."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from ipaddress import ip_network
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from urllib.parse import quote, unquote, urlsplit
import uuid

PORT = 8765
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_INBOX_BYTES = 1024 * 1024 * 1024
MAX_CONCURRENT_UPLOADS = 2
MAX_CONCURRENT_CONNECTIONS = 12
PAIR_SECONDS = 60
APPROVAL_SECONDS = 90
SESSION_SECONDS = 3600
REQUEST_TIMEOUT = 12
MAX_CLIPBOARD_CHARS = 50_000
MAX_JSON_BYTES = 200_000
FILE_LIST_LIMIT = 150


class ShareState:
    """Shared state; only the Tk main thread touches the system clipboard."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.inbox = self.base_dir / "Inbox"
        self.outbox = self.base_dir / "Outbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.storage_lock = threading.Lock()
        self.pair_token = secrets.token_urlsafe(32)
        self.pair_deadline = time.monotonic() + PAIR_SECONDS
        self.pending = {}  # ticket -> {code, ip, deadline, decision}
        self.sessions = {}  # token -> {label, deadline, ip}
        self.upload_slots = threading.BoundedSemaphore(MAX_CONCURRENT_UPLOADS)
        self.upload_reserved = 0
        self.clipboard_enabled = False
        self.clipboard_text = ""
        self.clipboard_note = "Clipboard syncing is off. Enable it in the Windows app."
        self.clipboard_revision = 0
        self.pending_clipboard: str | None = None

    def reset_pairing(self):
        """Revoke every browser, pending approval and old QR in one operation."""
        with self.lock:
            self.pending.clear()
            self.sessions.clear()
            return self.new_pair_token()

    def new_pair_token(self):
        with self.lock:
            self.pair_token = secrets.token_urlsafe(32)
            self.pair_deadline = time.monotonic() + PAIR_SECONDS
            return self.pair_token

    def rotate_if_expired(self):
        with self.lock:
            if time.monotonic() >= self.pair_deadline:
                self.new_pair_token()
                return True
            return False

    def request_pair(self, candidate, ip):
        with self.lock:
            self.pending = {key: value for key, value in self.pending.items()
                            if value["deadline"] > time.monotonic()}
            if not (time.monotonic() < self.pair_deadline and
                    hmac.compare_digest(candidate, self.pair_token)):
                return None
            if sum(1 for p in self.pending.values() if p["decision"] == "waiting") >= 3:
                return None
            # Consume immediately; a second scan cannot replay the same code.
            self.new_pair_token()
            ticket = secrets.token_urlsafe(32)
            self.pending[ticket] = dict(code=f"{secrets.randbelow(1000000):06d}",
                                        ip=ip, deadline=time.monotonic() + APPROVAL_SECONDS,
                                        decision="waiting")
            return ticket, self.pending[ticket]["code"]

    def pending_requests(self):
        with self.lock:
            now = time.monotonic()
            return [(ticket, entry["code"], entry["ip"])
                    for ticket, entry in self.pending.items()
                    if entry["decision"] == "waiting" and entry["deadline"] > now]

    def decide(self, ticket, approve):
        with self.lock:
            entry = self.pending.get(ticket)
            if not entry or entry["decision"] != "waiting" or entry["deadline"] <= time.monotonic():
                return False
            entry["decision"] = "approved" if approve else "rejected"
            return True

    def pairing_status(self, ticket, ip):
        with self.lock:
            entry = self.pending.get(ticket)
            if not entry or not hmac.compare_digest(entry["ip"], ip):
                return "invalid", None
            if entry["deadline"] <= time.monotonic():
                self.pending.pop(ticket, None)
                return "expired", None
            if entry["decision"] != "approved":
                return entry["decision"], None
            self.pending.pop(ticket, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = dict(label=f"Browser {len(self.sessions) + 1} ({ip})",
                                        ip=ip, deadline=time.monotonic() + SESSION_SECONDS)
            return "approved", token

    def check_session(self, candidate, ip=None):
        with self.lock:
            for token, entry in list(self.sessions.items()):
                if entry["deadline"] <= time.monotonic():
                    del self.sessions[token]
                    continue
                if candidate and hmac.compare_digest(candidate, token) and (ip is None or ip == entry["ip"]):
                    return True
            return False

    def session_list(self):
        with self.lock:
            now = time.monotonic()
            return [(token, value["label"], max(0, int(value["deadline"] - now)))
                    for token, value in self.sessions.items() if value["deadline"] > now]

    def revoke_session(self, token):
        with self.lock:
            return self.sessions.pop(token, None) is not None

    def set_clipboard_enabled(self, enabled: bool):
        with self.lock:
            self.clipboard_enabled = enabled
            self.clipboard_text = ""
            self.clipboard_note = "Waiting for clipboard text…" if enabled else "Clipboard syncing is off."
            self.clipboard_revision += 1
            self.pending_clipboard = None

    def observe_clipboard(self, text: str | None):
        with self.lock:
            if not self.clipboard_enabled or self.pending_clipboard is not None:
                return
            if text is None:
                new_text, note = "", "No text in the Windows clipboard."
            elif len(text) > MAX_CLIPBOARD_CHARS:
                new_text, note = "", "Clipboard text exceeds the 50,000-character limit."
            else:
                new_text, note = text, ""
            if new_text != self.clipboard_text or note != self.clipboard_note:
                self.clipboard_text, self.clipboard_note = new_text, note
                self.clipboard_revision += 1

    def enqueue_clipboard(self, text: str):
        with self.lock:
            if not self.clipboard_enabled:
                return False
            self.pending_clipboard = text
            self.clipboard_text = text
            self.clipboard_note = ""
            self.clipboard_revision += 1
            return True

    def take_pending_clipboard(self):
        with self.lock:
            text = self.pending_clipboard
            self.pending_clipboard = None
            return text

    def snapshot(self):
        with self.lock:
            return {
                "clipboard_enabled": self.clipboard_enabled,
                "clipboard": self.clipboard_text if self.clipboard_enabled else "",
                "clipboard_note": self.clipboard_note,
                "revision": self.clipboard_revision,
            }

    def list_shared_files(self):
        result = []
        try:
            entries = sorted(self.outbox.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return result
        for path in entries:
            try:
                if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                    continue
                stat = path.stat()
                result.append({
                    "name": path.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "url": "/api/download/" + quote(path.name, safe=""),
                })
                if len(result) >= FILE_LIST_LIMIT:
                    break
            except OSError:
                continue
        return result


def safe_filename(name: str):
    """Keep Unicode names while stripping Windows-special and traversal characters."""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", name).strip(" .")
    name = name[:150].rstrip(" .")
    if not name or name in (".", ".."):
        name = "file"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}:
        name = "_" + name
    return name


def available_name(directory: Path, desired: str):
    """Call under state.storage_lock to prevent concurrent filename collisions."""
    base = Path(desired)
    stem, extension = base.stem, base.suffix
    candidate = desired
    number = 2
    while (directory / candidate).exists():
        candidate = f"{stem} ({number}){extension}"
        number += 1
    return candidate


def publish_pc_file(state: ShareState, source: Path):
    """Only explicitly selected PC files are copied into the web-visible Outbox."""
    source = Path(source)
    if not source.is_file():
        raise ValueError("Not a regular file")
    with state.storage_lock:
        name = available_name(state.outbox, safe_filename(source.name))
        target = state.outbox / name
        temporary = state.outbox / ("." + uuid.uuid4().hex + ".part")
        try:
            with source.open("rb") as input_file, temporary.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, 1024 * 1024)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return name


def _cookie_value(cookie_string: str, key: str):
    for fragment in cookie_string.split(";"):
        left, separator, right = fragment.strip().partition("=")
        if separator and left == key:
            return right
    return ""


def make_server(state: ShareState, assets: Path, host="0.0.0.0", port=PORT):
    """Create (but don't start) an HTTP server. Port 0 is handy for tests."""
    assets = Path(assets)
    # Never accept traffic to arbitrary interfaces or from a different /24.
    # This deliberately conservative /24 policy may exclude legitimate larger LANs.
    bound_ip = ip_address(host) if host != "0.0.0.0" else None
    allowed_net = ip_network(f"{bound_ip}/24", strict=False) if bound_ip and not bound_ip.is_loopback else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "RatanakQuickShare/1.4"
        sys_version = ""
        timeout = REQUEST_TIMEOUT

        def log_message(self, format, *args):
            # Never log the pairing key, which is part of the initial URL.
            return

        def send_bytes(self, status, body: bytes, content_type="text/plain; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_json(self, status, value):
            self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def fail(self, status, message):
            self.send_json(status, {"error": message})

        def authorized(self):
            return state.check_session(_cookie_value(self.headers.get("Cookie", ""), "quickshare_session"), self.client_address[0])

        def authenticated_or_error(self):
            if self.authorized():
                return True
            self.fail(HTTPStatus.UNAUTHORIZED, "Scan the QR code in the Windows app to pair this browser.")
            return False

        def safe_client(self):
            # A public internet address cannot be a local-network client.
            try:
                client = ip_address(self.client_address[0])
                if bound_ip and bound_ip.is_loopback:
                    return client.is_loopback
                if allowed_net:
                    return client in allowed_net
                return client.is_private or client.is_loopback
            except ValueError:
                return False

        def safe_host(self):
            # IP-literal hosts eliminate browser DNS-rebinding via arbitrary hostnames.
            host = self.headers.get("Host", "")
            if not host:
                return False
            try:
                target = urlsplit("//" + host).hostname
                address = ip_address(target)
                return (address == bound_ip if bound_ip is not None else
                        ((address.is_private and not address.is_multicast and not address.is_unspecified) or address.is_loopback))
            except (ValueError, TypeError):
                return False

        def csrf_ok(self):
            if self.headers.get("X-QuickShare-Request") != "1":
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    requested = urlsplit("http://" + self.headers.get("Host", ""))
                    if (parsed.scheme, parsed.hostname, parsed.port or 80) != ("http", requested.hostname, requested.port or 80):
                        return False
                except ValueError:
                    return False
            return True

        def setup(self):
            super().setup()
            self.connection.settimeout(REQUEST_TIMEOUT)

        def do_GET(self):
            if not self.safe_client():
                self.fail(HTTPStatus.FORBIDDEN, "Connections from public internet addresses are not allowed.")
                return
            if not self.safe_host():
                self.fail(HTTPStatus.BAD_REQUEST, "Use the local IP address shown by RatanakQuickShare.")
                return
            route = urlsplit(self.path).path
            if route.startswith("/pair/") and route != "/pair/status":
                candidate = unquote(route[len("/pair/"):])
                request = state.request_pair(candidate, self.client_address[0])
                if not request:
                    self.send_bytes(HTTPStatus.FORBIDDEN, b"Invalid, already used or expired QR code. Scan the current QR in Windows.")
                    return
                ticket, code = request
                # The ticket is never logged and is not put in the browser URL.
                cookie = f"qs_pending={ticket}; HttpOnly; SameSite=Strict; Path=/; Max-Age={APPROVAL_SECONDS}"
                page = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<title>Awaiting approval</title><h2>Approve on Windows</h2>"
                        "<p>Verify that this code matches your Windows window:</p><h1>" + code + "</h1>"
                        "<p id='status'>Waiting for approval…</p>"
                        "<script src='/assets/pair.js' defer></script>")
                self.send_bytes(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8", {"Set-Cookie": cookie})
                return
            if route == "/pair/status":
                ticket = _cookie_value(self.headers.get("Cookie", ""), "qs_pending")
                result, token = state.pairing_status(ticket, self.client_address[0])
                if token:
                    cookie = f"quickshare_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
                    self.send_bytes(HTTPStatus.OK, b'{"status":"approved"}', "application/json", {"Set-Cookie": cookie})
                else:
                    self.send_json(HTTPStatus.OK if result == "waiting" else HTTPStatus.FORBIDDEN, {"status": result})
                return
            if route == "/":
                if self.authorized():
                    self.send_asset("index.html", "text/html; charset=utf-8")
                else:
                    self.send_bytes(HTTPStatus.UNAUTHORIZED, b"<!doctype html><html><meta name='viewport' content='width=device-width,initial-scale=1'><title>RatanakQuickShare</title><body><h2>Pair your phone</h2><p>Scan the QR code in the RatanakQuickShare Windows window to connect.</p></body></html>", "text/html; charset=utf-8")
                return
            if route == "/assets/style.css":
                self.send_asset("style.css", "text/css; charset=utf-8")
                return
            if route == "/assets/app.js":
                self.send_asset("app.js", "text/javascript; charset=utf-8")
                return
            if route == "/assets/pair.js":
                self.send_asset("pair.js", "text/javascript; charset=utf-8")
                return
            if route == "/api/state":
                if self.authenticated_or_error():
                    self.send_json(HTTPStatus.OK, {**state.snapshot(), "files": state.list_shared_files(), "upload_limit": MAX_UPLOAD_BYTES})
                return
            if route.startswith("/api/download/"):
                if not self.authenticated_or_error():
                    return
                name = unquote(route[len("/api/download/"):])
                if not name or name in {".", ".."} or name != Path(name).name or "/" in name or "\\" in name or name.startswith("."):
                    self.fail(HTTPStatus.BAD_REQUEST, "Invalid filename")
                    return
                path = state.outbox / name
                if not path.is_file() or path.is_symlink():
                    self.fail(HTTPStatus.NOT_FOUND, "File not found")
                    return
                try:
                    size = path.stat().st_size
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Content-Disposition", "attachment; filename=\"download\"; filename*=UTF-8''" + quote(name, safe=""))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with path.open("rb") as file:
                        shutil.copyfileobj(file, self.wfile, 1024 * 1024)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            self.fail(HTTPStatus.NOT_FOUND, "Not found")

        def send_asset(self, filename, mime):
            try:
                body = (assets / filename).read_bytes()
            except OSError:
                self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, "App asset is missing")
                return
            self.send_bytes(HTTPStatus.OK, body, mime)

        def do_POST(self):
            if not self.safe_client():
                self.fail(HTTPStatus.FORBIDDEN, "Connections from public internet addresses are not allowed.")
                return
            if not self.safe_host():
                self.fail(HTTPStatus.BAD_REQUEST, "Use the local IP address shown by RatanakQuickShare.")
                return
            if not self.authenticated_or_error():
                return
            if not self.csrf_ok():
                self.fail(HTTPStatus.FORBIDDEN, "Request rejected. Open RatanakQuickShare from the QR code.")
                return
            route = urlsplit(self.path).path
            if route == "/api/clipboard":
                self.post_clipboard()
            elif route == "/api/upload":
                self.post_upload()
            else:
                self.fail(HTTPStatus.NOT_FOUND, "Not found")

        def read_length(self, limit):
            if self.headers.get("Transfer-Encoding"):
                self.fail(HTTPStatus.BAD_REQUEST, "Chunked uploads are not supported")
                return None
            raw = self.headers.get("Content-Length")
            if raw is None:
                self.fail(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
                return None
            try:
                length = int(raw)
            except ValueError:
                length = -1
            if length < 0:
                self.fail(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
                return None
            if length > limit:
                self.fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "File or message exceeds the size limit")
                return None
            return length

        def post_clipboard(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected JSON")
                return
            length = self.read_length(MAX_JSON_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.fail(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                self.fail(HTTPStatus.BAD_REQUEST, "Expected a text field")
                return
            text = body["text"]
            if len(text) > MAX_CLIPBOARD_CHARS:
                self.fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Clipboard text is too long")
            elif not state.enqueue_clipboard(text):
                self.fail(HTTPStatus.CONFLICT, "Enable clipboard syncing in the Windows app first")
            else:
                self.send_json(HTTPStatus.OK, {"ok": True})

        def post_upload(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
                self.fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected a raw file upload")
                return
            length = self.read_length(MAX_UPLOAD_BYTES)
            if length is None:
                return
            params = urlsplit(self.path).query
            from urllib.parse import parse_qs
            name = parse_qs(params).get("name", [""])[0]
            if not name or len(name) > 512:
                self.fail(HTTPStatus.BAD_REQUEST, "A filename is required")
                return
            cleaned = safe_filename(name)
            if not state.upload_slots.acquire(blocking=False):
                self.fail(HTTPStatus.TOO_MANY_REQUESTS, "Too many uploads. Retry shortly.")
                return
            temporary = state.inbox / ("." + uuid.uuid4().hex + ".part")
            reserved = False
            try:
                with state.storage_lock:
                    used = sum(f.stat().st_size for f in state.inbox.iterdir() if f.is_file())
                    if used + state.upload_reserved + length > MAX_INBOX_BYTES:
                        self.fail(HTTPStatus.INSUFFICIENT_STORAGE, "Inbox quota exceeded. Remove files first.")
                        return
                    state.upload_reserved += length
                    reserved = True
                with temporary.open("xb") as output:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ConnectionError("Upload interrupted")
                        output.write(chunk)
                        remaining -= len(chunk)
                with state.storage_lock:
                    final_name = available_name(state.inbox, cleaned)
                    temporary.replace(state.inbox / final_name)
                self.send_json(HTTPStatus.CREATED, {"ok": True, "name": final_name})
            except (OSError, ConnectionError, TimeoutError):
                try:
                    self.fail(HTTPStatus.INSUFFICIENT_STORAGE, "Upload interrupted or could not be saved")
                except OSError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
                if reserved:
                    with state.storage_lock:
                        state.upload_reserved -= length
                state.upload_slots.release()

    class AppServer(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False
        request_queue_size = 8

        def __init__(self, *args, **kwargs):
            self.connection_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)
            super().__init__(*args, **kwargs)

        def process_request(self, request, client_address):
            if not self.connection_slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.connection_slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.connection_slots.release()

    return AppServer((host, port), Handler)
