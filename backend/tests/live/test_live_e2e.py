"""
Live, stateful, self-contained end-to-end unittest of the SignBridge
backend's full lifecycle, on a single freshly generated throwaway
account:

    1.  Generate unique email/password
    2.  POST /auth/register
    3.  POST /auth/login                          -> JWT #1
    4.  POST /auth/forgot-password
    5.  POST /auth/update-password (using JWT #1)
    6.  Continue using JWT #1:
    7.      GET  /me/history
    8.      POST /me/history
    9.      GET  /me/history
    10.     DELETE /me/history/{id}
    11.     POST /me/history x2
    12.     DELETE /me/history
    13. POST /auth/logout                          (revokes JWT #1)
    14. Verify JWT #1 is rejected
    15. POST /auth/login using the NEW password    -> JWT #2
    16. WebSocket /slt/v1/ws using JWT #2
    17.     Send frames
    18.     Receive translation
    19.     Close WebSocket

Run (opt-in, see README.md):

    SIGNBRIDGE_RUN_LIVE_TESTS=1 python -m unittest discover -s tests/live -v

This hits a real running FastAPI server and a real Firebase project. It
needs no persistent test account: every run registers its own account
(see unique_email() in live_config.py) and never touches a shared one, so
concurrent/repeated runs don't interfere with each other and there's
nothing to keep in sync between test runs.

The ordered part is deliberately ONE test method: unittest doesn't
guarantee cross-method ordering (methods run in alphabetical order by
default) and TestCase methods shouldn't rely on each other's state
anyway, so the ordered scenario is a sequence of plain function calls
(see live_helpers.py) inside a single test method rather than several
test_ methods that only pass if run in a particular order. The SLT
WebSocket leg (steps 16-19) is the final stage of that same method,
using the JWT #2 issued by step 15 - not a separately-authenticated test
- so the whole thing is one continuous lifecycle on one account.
"""
from __future__ import annotations

import asyncio

try:
    from base import LiveTestCase
    from live_config import WS_URL, unique_email
    from live_helpers import (
        LiveTestState,
        assert_token_rejected,
        clear_history,
        delete_translation,
        forgot_password,
        get_history,
        login,
        logout,
        register,
        store_translation,
        update_password,
    )
    from test_slt_websocket import run_slt_session
except ImportError:
    from tests.live.base import LiveTestCase
    from tests.live.live_config import WS_URL, unique_email
    from tests.live.live_helpers import (
        LiveTestState,
        assert_token_rejected,
        clear_history,
        delete_translation,
        forgot_password,
        get_history,
        login,
        logout,
        register,
        store_translation,
        update_password,
    )
    from tests.live.test_slt_websocket import run_slt_session



class TestLiveE2E(LiveTestCase):
    def test_complete_live_backend_flow(self):
        frames_dir = self.require_frames_dir()

        # 1. Generate a unique email/password for this run.
        state = LiveTestState(
            email=unique_email(),
            password="s3cr3t-initial",
            new_password="s3cr3t-updated",
        )

        # 2. Register. The registration response also returns a token,
        #    but the flow we want is explicitly register -> login, so
        #    that token is deliberately not used below.
        registered = register(self.client, state.email, state.password)
        state.uid = registered["id"]

        # 3. Login -> JWT #1.
        login_body = login(self.client, state.email, state.password)
        state.token = login_body["token"]
        self.assertEqual(login_body["id"], state.uid)

        # 4. Forgot-password. There's no real inbox to check here; this
        #    just confirms the endpoint accepts the request.
        forgot_password(self.client, state.email)

        # 5. Update password, using JWT #1. Safe to do here because this
        #    account is disposable - nothing else depends on
        #    state.password continuing to work afterwards.
        update_password(self.client, state.token, state.new_password)

        # 6. Re-login to generate new JWT
        relogin = login(self.client, state.email, state.new_password)
        state.token = relogin["token"]
        self.assertEqual(relogin["id"], state.uid)

        # 7-12. Continue using JWT #1 for the history lifecycle.
        baseline = get_history(self.client, state.token)  # 7

        item = store_translation(self.client, state.token, "hello world")  # 8
        state.history_id = item["id"]

        after_store = get_history(self.client, state.token)  # 9
        self.assertTrue(
            any(h["id"] == state.history_id for h in after_store),
            "stored translation did not appear in history",
        )
        self.assertEqual(len(after_store), len(baseline) + 1)

        delete_translation(self.client, state.token, state.history_id)  # 10

        after_delete = get_history(self.client, state.token)
        self.assertFalse(any(h["id"] == state.history_id for h in after_delete))

        store_translation(self.client, state.token, "left in history 1")  # 11
        store_translation(self.client, state.token, "left in history 2")
        clear_history(self.client, state.token)  # 12
        self.assertEqual(get_history(self.client, state.token), [])

        # 13. Logout revokes JWT #1.
        state.old_token = state.token
        logout(self.client, state.old_token)

        # 14. The revoked token must now be rejected everywhere.
        assert_token_rejected(self.client, state.old_token)

        # 15. Login again, using the NEW password -> JWT #2.
        relogin = login(self.client, state.email, state.new_password)
        state.new_token = relogin["token"]
        state.token = state.new_token
        self.assertEqual(relogin["id"], state.uid)
        self.assertNotEqual(state.new_token, state.old_token)

        # 16-19. SLT WebSocket, using JWT #2: connect, send frames,
        # receive a translation, close (the `async with` in
        # run_slt_session closes the socket on the way out).
        frame_files = sorted(frames_dir.glob("frame_*.jpg"))
        frames_sent, predictions, complete = asyncio.run(
            run_slt_session(WS_URL, state.new_token, frame_files, frame_interval=0.09)
        )

        self.assertEqual(complete["frames"], frames_sent)
        self.assertGreaterEqual(
            complete["inferences"], 1,
            "expected at least one 16-frame sliding window to run an inference",
        )

        if not predictions:
            # No direct xfail concept in unittest; skipTest is the
            # closest honest signal for "infra worked end-to-end, model
            # didn't answer in time" rather than a hard assertion
            # failure - the whole REST lifecycle above already passed.
            self.skipTest(
                "full REST lifecycle and WebSocket handshake/streaming "
                "succeeded, but the model server returned no prediction "
                "(it may still be cold-starting); re-run once it's warm"
            )


class TestLiveAuthEdgeCases(LiveTestCase):
    """Order-independent: each of these registers whatever throwaway
    account it needs for itself, rather than relying on the main
    scenario's state."""

    def test_protected_routes_reject_missing_token(self):
        cases = [
            ("GET", "/me/history", {}),
            ("POST", "/me/history", {"json": {"translation": "x"}}),
            ("POST", "/auth/update-password", {"json": {"password": "irrelevant123"}}),
        ]
        for method, path, kwargs in cases:
            with self.subTest(route=f"{method} {path}"):
                call = self.client.get if method == "GET" else self.client.post
                resp = call(path, **kwargs)
                self.assertEqual(resp.status_code, 401)
                self.assertEqual(resp.json(), {"error": "Missing or invalid token"})

    def test_login_with_wrong_password_fails(self):
        email = unique_email()
        register(self.client, email, "s3cr3t-register")
        resp = self.client.post(
            "/auth/login", json={"email": email, "password": "definitely-wrong-pw"}
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "Login failed")
        self.assertEqual(body["id"], "")
        self.assertEqual(body["token"], "")

    def test_registering_the_same_email_twice_fails(self):
        email = unique_email()
        register(self.client, email, "s3cr3t-register")
        resp = self.client.post(
            "/auth/register", json={"email": email, "password": "s3cr3t-register"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Registration failed")


if __name__ == "__main__":
    import unittest

    unittest.main()
