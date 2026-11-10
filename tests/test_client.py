import json
import unittest

from collector.client import Client, DeadlineExceeded, HttpError, parse_retry_after


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedClient(Client):
    """Replays a list of (status, headers, body) responses; each fetch takes 0.3s."""

    def __init__(self, script, clock, **kwargs):
        super().__init__(sleep=clock.sleep, clock=clock.clock, log=lambda *_: None, **kwargs)
        self.script = list(script)
        self.fake = clock
        self.starts = []

    def _fetch(self, url):
        self.starts.append(self.fake.now)
        self.fake.now += 0.3
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        status, headers, body = item
        return status, headers, json.dumps(body).encode()


OK = (200, {}, {"ok": True})


class ClientTest(unittest.TestCase):
    def test_requests_start_at_least_two_seconds_apart(self):
        clock = FakeClock()
        client = ScriptedClient([OK, OK, OK], clock)
        for _ in range(3):
            client.get_json("https://example.test/a")
        gaps = [b - a for a, b in zip(client.starts, client.starts[1:])]
        self.assertTrue(all(gap >= 2.0 - 1e-9 for gap in gaps), gaps)

    def test_429_waits_for_retry_after_then_succeeds(self):
        clock = FakeClock()
        client = ScriptedClient([(429, {"Retry-After": "7"}, {}), OK], clock)
        self.assertEqual(client.get_json("https://example.test/a"), {"ok": True})
        self.assertIn(7.0, clock.sleeps)
        self.assertEqual((client.requests, client.retries, client.rate_limited), (2, 1, 1))

    def test_429_without_header_backs_off(self):
        clock = FakeClock()
        client = ScriptedClient([(429, {}, {}), (429, {}, {}), OK], clock)
        client.get_json("https://example.test/a")
        self.assertEqual([s for s in clock.sleeps if s >= 5], [5.0, 10.0])

    def test_server_errors_and_network_errors_are_retried(self):
        clock = FakeClock()
        client = ScriptedClient([(503, {}, {}), OSError("reset"), OK], clock)
        self.assertEqual(client.get_json("https://example.test/a"), {"ok": True})
        self.assertEqual(client.retries, 2)

    def test_client_errors_are_not_retried(self):
        clock = FakeClock()
        client = ScriptedClient([(400, {}, {"errors": []}), OK], clock)
        with self.assertRaises(HttpError) as caught:
            client.get_json("https://example.test/a")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(client.requests, 1)

    def test_gives_up_after_max_attempts(self):
        clock = FakeClock()
        client = ScriptedClient([(500, {}, {})] * 3, clock, max_attempts=3)
        with self.assertRaises(HttpError):
            client.get_json("https://example.test/a")
        self.assertEqual(client.requests, 3)

    def test_deadline_stops_waiting(self):
        clock = FakeClock()
        client = ScriptedClient([(429, {"Retry-After": "120"}, {}), OK], clock, deadline_s=30)
        with self.assertRaises(DeadlineExceeded):
            client.get_json("https://example.test/a")

    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after({"Retry-After": "12"}), 12.0)
        self.assertEqual(parse_retry_after({"retry-after": " 3 "}), 3.0)
        date = "Wed, 21 Oct 2015 07:28:30 GMT"
        self.assertAlmostEqual(parse_retry_after({"Retry-After": date}, now=1445412480), 30.0)
        self.assertEqual(parse_retry_after({"x-ratelimit-reset": "9"}), 9.0)
        self.assertIsNone(parse_retry_after({"Retry-After": "soon"}))
        self.assertIsNone(parse_retry_after({}))


if __name__ == "__main__":
    unittest.main()
