"""
Base TestCase for the live E2E suite: opt-in skip gate + a shared
requests.Session-backed client.
"""
from __future__ import annotations

import unittest

import requests

from live_config import BASE_URL, REQUEST_TIMEOUT, RUN_LIVE, frames_dir


class LiveClient:
    """Thin requests.Session wrapper: bakes in BASE_URL + a default
    timeout so tests can call self.client.get("/history") instead of
    repeating the full URL and timeout at every call site."""

    def __init__(self, session: requests.Session):
        self._session = session

    def _url(self, path: str) -> str:
        return BASE_URL + path

    def get(self, path: str, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return self._session.get(self._url(path), **kwargs)

    def post(self, path: str, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return self._session.post(self._url(path), **kwargs)

    def delete(self, path: str, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return self._session.delete(self._url(path), **kwargs)


@unittest.skipUnless(
    RUN_LIVE,
    "live suite is opt-in: set SIGNBRIDGE_RUN_LIVE_TESTS=1 to run it",
)
class LiveTestCase(unittest.TestCase):
    """Common setup for every live test: a client, plus a skip-on-missing-
    env-var accessor for the SLT websocket frames dir.

    The skipUnless above applies to every subclass too (it's a plain
    inherited class attribute), so no test file needs to repeat the gate.
    """

    def setUp(self):
        self._session = requests.Session()
        self.client = LiveClient(self._session)

    def tearDown(self):
        self._session.close()

    def require_frames_dir(self):
        try:
            return frames_dir()
        except LookupError as exc:
            self.skipTest(str(exc))
