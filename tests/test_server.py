"""Run with: python -m unittest discover -s tests -v"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from urllib.parse import quote

from server import MAX_UPLOAD_BYTES, ShareState, make_server, publish_pc_file, safe_filename


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.assets = Path(__file__).resolve().parents[1] / "assets"
        self.state = ShareState(self.path / "shared")
        self.server = make_server(self.state, self.assets, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, method, url, body=None, headers=None, auth=True):
        headers = dict(headers or {})
        if self.cookie and auth and "Cookie" not in headers:
            headers["Cookie"] = self.cookie
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, url, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def pair(self):
        old = self.state.pair_token
        status, headers, page = self.request("GET", "/pair/" + old)
        self.assertEqual(status, 200)
        self.assertIn(b"Approve on Windows", page)
        ticket = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(self.request("GET", "/pair/" + old)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", auth=False)[0], 401)
        pending = self.state.pending_requests()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0][0], ticket)
        self.assertTrue(self.state.decide(ticket, True))
        status, response_headers, data = self.request("GET", "/pair/status", headers={"Cookie": "qs_pending=" + ticket})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["status"], "approved")
        self.assertIn("HttpOnly", response_headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response_headers["Set-Cookie"])
        self.cookie = response_headers["Set-Cookie"].split(";", 1)[0]
        return old

    def test_cannot_access_before_pairing(self):
        for route in ["/", "/api/state", "/api/download/secret.txt"]:
            status, _, data = self.request("GET", route)
            self.assertEqual(status, 401, route)
        self.assertNotIn(b"secret", self.request("GET", "/api/state")[2])

    def test_pairing_and_state(self):
        self.assertEqual(self.request("GET", "/pair/wrong")[0], 403)
        self.pair()
        self.assertEqual(self.request("GET", "/")[0], 200)
        status, _, data = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)["clipboard_enabled"])

    def test_reset_pairing_expires_cookie_and_old_qr(self):
        old = self.state.pair_token
        self.pair()
        self.state.reset_pairing()
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("GET", "/pair/" + old)[0], 403)
        self.cookie = None
        self.pair()
        self.assertEqual(self.request("GET", "/api/state")[0], 200)

    def test_reject_pairing_and_expired_qr(self):
        token = self.state.pair_token
        self.state.pair_deadline = 0
        self.assertEqual(self.request("GET", "/pair/" + token)[0], 403)
        self.state.new_pair_token()
        status, headers, _ = self.request("GET", "/pair/" + self.state.pair_token)
        self.assertEqual(status, 200)
        ticket = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertTrue(self.state.decide(ticket, False))
        status, _, data = self.request("GET", "/pair/status", headers={"Cookie": "qs_pending=" + ticket})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(data)["status"], "rejected")
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_individual_session_revocation_and_expiry(self):
        self.pair()
        first = self.cookie
        self.pair()
        second = self.cookie
        self.assertNotEqual(first, second)
        first_token = first.split("=", 1)[1]
        self.state.revoke_session(first_token)
        self.cookie = first
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.cookie = second
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.state.sessions[second.split("=", 1)[1]]["deadline"] = 0
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_pending_approval_expires_and_reset_clears_it(self):
        _, headers, _ = self.request("GET", "/pair/" + self.state.pair_token)
        ticket = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        self.state.pending[ticket]["deadline"] = 0
        self.assertFalse(self.state.decide(ticket, True))
        self.assertEqual(self.request("GET", "/pair/status", headers={"Cookie": "qs_pending=" + ticket})[0], 403)
        self.state.reset_pairing()
        self.assertFalse(self.state.pending_requests())
        self.assertFalse(self.state.session_list())

    def test_inbox_quota_and_concurrent_upload_limit(self):
        from server import MAX_INBOX_BYTES
        self.pair()
        headers = {"Content-Type": "application/octet-stream", "X-QuickShare-Request": "1"}
        (self.state.inbox / "full.dat").write_bytes(b"x")
        from unittest.mock import patch
        with patch("server.MAX_INBOX_BYTES", 1):
            self.assertEqual(self.request("POST", "/api/upload?name=another.dat", b"y", headers)[0], 507)
        self.assertEqual(len(list(self.state.inbox.iterdir())), 1)
        self.assertTrue(self.state.upload_slots.acquire())
        self.assertTrue(self.state.upload_slots.acquire())
        try:
            self.assertEqual(self.request("POST", "/api/upload?name=busy.dat", b"y", headers)[0], 429)
        finally:
            self.state.upload_slots.release()
            self.state.upload_slots.release()

    def test_cross_origin_and_missing_custom_header_rejected(self):
        self.pair()
        body = json.dumps({"text": "hi"}).encode()
        status, _, _ = self.request("POST", "/api/clipboard", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/clipboard", body, {
            "Content-Type": "application/json", "X-QuickShare-Request": "1", "Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_clipboard_off_by_default_then_two_way(self):
        self.pair()
        headers = {"Content-Type": "application/json", "X-QuickShare-Request": "1"}
        body = json.dumps({"text": "Hi from phone 👋"}).encode()
        self.assertEqual(self.request("POST", "/api/clipboard", body, headers)[0], 409)
        self.state.set_clipboard_enabled(True)
        self.state.observe_clipboard("Hello from PC")
        current = json.loads(self.request("GET", "/api/state")[2])
        self.assertEqual(current["clipboard"], "Hello from PC")
        self.assertEqual(self.request("POST", "/api/clipboard", body, headers)[0], 200)
        self.assertEqual(self.state.take_pending_clipboard(), "Hi from phone 👋")
        self.state.observe_clipboard("Hi from phone 👋")
        self.assertEqual(json.loads(self.request("GET", "/api/state")[2])["clipboard"], "Hi from phone 👋")
        self.state.set_clipboard_enabled(False)
        self.assertEqual(json.loads(self.request("GET", "/api/state")[2])["clipboard"], "")

    def test_upload_then_inbox_only(self):
        self.pair()
        name = "travel 🏖.jpg"
        payload = b"\x00\x01\xfftestphoto"
        headers = {"Content-Type": "application/octet-stream", "X-QuickShare-Request": "1"}
        status, _, response = self.request("POST", "/api/upload?name=" + quote(name), payload, headers)
        self.assertEqual(status, 201)
        saved_name = json.loads(response)["name"]
        self.assertEqual((self.state.inbox / saved_name).read_bytes(), payload)
        self.assertEqual(json.loads(self.request("GET", "/api/state")[2])["files"], [])
        self.assertEqual(self.request("GET", "/api/download/" + quote(saved_name))[0], 404)

    def test_publish_download_and_filename_collision(self):
        self.pair()
        original = self.path / "report.pdf"
        original.write_bytes(b"document content")
        self.assertEqual(publish_pc_file(self.state, original), "report.pdf")
        self.assertEqual(publish_pc_file(self.state, original), "report (2).pdf")
        files = json.loads(self.request("GET", "/api/state")[2])["files"]
        self.assertEqual(len(files), 2)
        status, headers, data = self.request("GET", "/api/download/report.pdf")
        self.assertEqual(status, 200)
        self.assertEqual(data, b"document content")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_path_traversal_not_downloadable(self):
        self.pair()
        (self.path / "private.txt").write_text("private")
        for route in ["/api/download/../private.txt", "/api/download/%2E%2E%2Fprivate.txt", "/api/download/%5Cprivate.txt"]:
            self.assertNotEqual(self.request("GET", route)[0], 200)
        self.assertEqual(safe_filename("../../CON.txt"), "_CON.txt")

    def test_upload_size_and_missing_filename(self):
        self.pair()
        headers = {"Content-Type": "application/octet-stream", "X-QuickShare-Request": "1"}
        self.assertEqual(self.request("POST", "/api/upload", b"abc", headers)[0], 400)
        status, _, _ = self.request("POST", "/api/upload?name=huge.dat", b"", {
            **headers, "Content-Length": str(MAX_UPLOAD_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(list(self.state.inbox.iterdir()), [])

    def test_only_paired_browsers_can_upload(self):
        status, _, _ = self.request("POST", "/api/upload?name=a.txt", b"data", {
            "Content-Type": "application/octet-stream", "X-QuickShare-Request": "1"})
        self.assertEqual(status, 401)
        self.assertEqual(list(self.state.inbox.iterdir()), [])

    def test_html_and_assets_security_headers(self):
        self.pair()
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"RatanakQuickShare", page)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/assets/app.js")[0], 200)

    def test_clipped_text_not_available_when_too_long(self):
        self.state.set_clipboard_enabled(True)
        self.state.observe_clipboard("a" * 50001)
        self.assertEqual(self.state.snapshot()["clipboard"], "")
        self.assertIn("limit", self.state.snapshot()["clipboard_note"])


if __name__ == "__main__":
    unittest.main()
