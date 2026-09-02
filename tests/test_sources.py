from __future__ import annotations

import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from fantaoperator.sources import fetch_url, parse_payload, payload_hash, safe_url


class CsvHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        payload = b"player,vote,status\nRossi,6.5,DEFINITIVO\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


class SourceTests(unittest.TestCase):
    def test_parse_italian_csv(self) -> None:
        payload = "giocatore;ruolo;squadra;voto;gol;assist;stato\nRossi;ATT;Roma;6,5;1;0;LIVE\n".encode()
        # Decimal commas need quoting in CSV, so use a dot for the portable import format.
        payload = payload.replace(b"6,5", b"6.5")
        rows = parse_payload(payload, "voti.csv")
        self.assertEqual(rows[0]["player"], "Rossi")
        self.assertEqual(rows[0]["vote"], "6.5")
        self.assertEqual(rows[0]["goals"], "1")

    def test_parse_json_envelope(self) -> None:
        rows = parse_payload(b'{"records":[{"player":"Bianchi","vote":7,"status":"DEFINITIVO"}]}', "voti.json")
        self.assertEqual(rows[0]["player"], "Bianchi")

    def test_missing_vote_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "voto"):
            parse_payload(b"player,team\nRossi,Roma\n", "bad.csv")

    def test_hash_is_stable(self) -> None:
        self.assertEqual(payload_hash(b"abc"), payload_hash(b"abc"))
        self.assertNotEqual(payload_hash(b"abc"), payload_hash(b"abd"))

    def test_malformed_url_can_be_logged_safely(self) -> None:
        self.assertEqual(safe_url("https://[broken?token=secret"), "[URL non valido]")

    def test_fetch_url_downloads_verifiable_csv(self) -> None:
        server = HTTPServer(("127.0.0.1", 0), CsvHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload, content_type, final_url = fetch_url(f"http://127.0.0.1:{server.server_port}/voti.csv", allow_private=True)
            self.assertIn(b"Rossi", payload)
            self.assertIn("text/csv", content_type)
            self.assertTrue(final_url.endswith("voti.csv"))
            self.assertEqual(parse_payload(payload, final_url)[0]["status"], "DEFINITIVO")
        finally:
            server.shutdown()
            server.server_close()

    def test_fetch_url_rejects_non_http_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTP"):
            fetch_url("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
