import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import sms_adb_relay


class CapturingRelayHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((self.path, self.headers.get("X-Cred-Token"), json.loads(body)))
        self.send_response(201)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class SmsAdbRelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), CapturingRelayHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        CapturingRelayHandler.received.clear()

    def test_generated_sms_are_parsed_filtered_and_posted(self):
        adb_output = sms_adb_relay.generate_fake_sms_input()
        config = {
            "target_senders": ["127", "CBE"],
            "server_url": f"http://127.0.0.1:{self.server.server_port}",
            "relay_token": "fake-test-token",
        }
        with patch.object(sms_adb_relay, "adb_shell", return_value=adb_output) as fake_adb:
            messages = sms_adb_relay.read_new_messages(config, last_seen=100)

        fake_adb.assert_called_once()
        self.assertIn('_id > 100', fake_adb.call_args.args[1])
        self.assertEqual([msg_id for msg_id, _ in messages], [101, 102, 103])
        self.assertEqual([payment is None for _, payment in messages], [False, False, True])
        self.assertEqual(messages[0][1]["amount"], 1250.5)
        self.assertEqual(messages[0][1]["payer"], "Alice Example")
        self.assertEqual(messages[0][1]["channel"], "Telebirr")
        self.assertEqual(messages[0][1]["external_id"], "adb-101")
        self.assertEqual(messages[1][1]["amount"], 300.0)
        self.assertEqual(messages[1][1]["payer"], "Bob Example")
        self.assertEqual(messages[1][1]["channel"], "CBE")

        for _, payment in messages:
            if payment is not None:
                self.assertTrue(sms_adb_relay.post_sms(config, payment))

        self.assertEqual(len(CapturingRelayHandler.received), 2)
        self.assertEqual(
            [path for path, _, _ in CapturingRelayHandler.received],
            ["/api/relay/sms", "/api/relay/sms"],
        )
        self.assertEqual(
            [token for _, token, _ in CapturingRelayHandler.received],
            ["fake-test-token", "fake-test-token"],
        )
        self.assertEqual(
            [payload["external_id"] for _, _, payload in CapturingRelayHandler.received],
            ["adb-101", "adb-102"],
        )

    def test_standalone_fake_mode_never_uses_adb(self):
        with patch.object(sms_adb_relay, "adb_shell", side_effect=AssertionError("ADB must not be used")):
            received = sms_adb_relay.test_sms_adb_relay()

        self.assertEqual(len(received), 2)
        self.assertEqual([payload["external_id"] for _, _, payload in received], ["adb-101", "adb-102"])

    def test_fake_stream_posts_one_marked_payment_per_interval_and_can_stop(self):
        config = {
            "target_senders": ["127", "CBE"],
            "server_url": f"http://127.0.0.1:{self.server.server_port}",
            "relay_token": "fake-test-token",
        }

        class StopAfterOnePost(threading.Event):
            def wait(self, timeout=None):
                self.timeout = timeout
                self.set()
                return True

        stop_event = StopAfterOnePost()
        with patch.object(sms_adb_relay.random, "uniform", return_value=7.5):
            sms_adb_relay.run_fake_sms_stream(config, stop_event)

        self.assertEqual(stop_event.timeout, 7.5)
        self.assertEqual(len(CapturingRelayHandler.received), 1)
        _, token, payment = CapturingRelayHandler.received[0]
        self.assertEqual(token, "fake-test-token")
        self.assertTrue(payment["external_id"].startswith("fake-stream-"))
        self.assertIn("[FAKE TEST SMS]", payment["body"])
        self.assertTrue(payment["payer"].startswith("FAKE TEST"))


if __name__ == "__main__":
    unittest.main()
