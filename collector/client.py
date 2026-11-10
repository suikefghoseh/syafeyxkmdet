"""Paced HTTP client: one request per MIN_REQUEST_INTERVAL_S, honours 429."""

import email.utils
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class HttpError(Exception):
    """A response that will not get better by retrying."""

    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url


class DeadlineExceeded(Exception):
    """The run's time budget is used up."""


def parse_retry_after(headers, now=None):
    """Seconds to wait as asked by the server, or None if it did not say.

    Understands Retry-After as seconds or as an HTTP date, and falls back to
    x-ratelimit-reset (seconds until the window resets).
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    value = lowered.get("retry-after")
    if value is not None:
        value = str(value).strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(value)
            current = time.time() if now is None else now
            return max(0.0, when.timestamp() - current)
        except (TypeError, ValueError):
            pass
    value = lowered.get("x-ratelimit-reset")
    if value is not None:
        try:
            return max(0.0, float(str(value).strip()))
        except ValueError:
            pass
    return None


class Client:
    def __init__(
        self,
        min_interval=config.MIN_REQUEST_INTERVAL_S,
        max_attempts=config.MAX_ATTEMPTS,
        deadline_s=None,
        sleep=time.sleep,
        clock=time.monotonic,
        log=print,
    ):
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._last_start = None
        self._deadline = None if deadline_s is None else clock() + deadline_s
        self.requests = 0
        self.retries = 0
        self.rate_limited = 0

    # -- overridable transport (tests replace this) -------------------------
    def _fetch(self, url):
        """Return (status, headers, body_bytes). Raises OSError on network errors."""
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": config.USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    # -- pacing -------------------------------------------------------------
    def time_left(self):
        return None if self._deadline is None else self._deadline - self._clock()

    def _wait(self, seconds):
        if seconds <= 0:
            return
        left = self.time_left()
        if left is not None and seconds > left:
            raise DeadlineExceeded(f"needed to wait {seconds:.0f}s with {left:.0f}s left")
        self._sleep(seconds)

    def pause(self, seconds):
        """Wait before the next request, for callers that want to go slower than the usual pace."""
        self._wait(seconds)

    def _pace(self):
        if self._last_start is not None:
            self._wait(self._last_start + self.min_interval - self._clock())
        left = self.time_left()
        if left is not None and left <= 0:
            raise DeadlineExceeded("run deadline reached")
        self._last_start = self._clock()

    # -- requests -----------------------------------------------------------
    def get_json(self, url, params=None):
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        last_problem = "no attempt made"
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            self.requests += 1
            try:
                status, headers, body = self._fetch(url)
            except (OSError, ValueError) as error:  # URLError and timeouts are OSError
                last_problem = f"network error: {error}"
                status, headers, body = None, {}, b""

            if status == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    last_problem = "response was not JSON"
            elif status == 429:
                self.rate_limited += 1
                asked = parse_retry_after(headers)
                wait = asked if asked is not None else min(60.0, 5.0 * 2 ** (attempt - 1))
                wait = min(max(wait, self.min_interval), config.MAX_RETRY_WAIT_S)
                last_problem = "HTTP 429"
                if attempt < self.max_attempts:
                    self._log(f"  429 from {_short(url)}; waiting {wait:.0f}s")
                    self.retries += 1
                    self._wait(wait)
                continue
            elif status is not None and status < 500:
                raise HttpError(status, _short(url), body.decode("utf-8", "replace"))
            elif status is not None:
                last_problem = f"HTTP {status}"

            if attempt < self.max_attempts:
                self.retries += 1
                backoff = min(60.0, 2.0 * 2 ** (attempt - 1))
                self._log(f"  {last_problem} from {_short(url)}; retrying in {backoff:.0f}s")
                self._wait(backoff)
        raise HttpError(0, _short(url), f"gave up after {self.max_attempts} attempts ({last_problem})")


def _short(url):
    return url if len(url) <= 120 else url[:117] + "..."
