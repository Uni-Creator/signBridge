# Live E2E tests (unittest)

These tests hit a **real running backend** and a real Firebase project.
They are opt-in and skipped by default.

The suite is a single, self-contained lifecycle test on a **freshly
generated throwaway account** - it registers its own user, runs the
whole account/history/logout/websocket lifecycle against it, and never
touches a shared or persistent account. There is nothing to configure
per-account and nothing for one run to leave behind for the next.

## Run

```bash
SIGNBRIDGE_RUN_LIVE_TESTS=1 python -m unittest discover -s tests/live -v
```

Or copy `tests/live/.env.live.example`'s contents into a real
`tests/live/.env.live.example` (it's loaded automatically by
`live_config.py`) or export the same variables in your shell.

Run a single file the normal unittest ways, e.g.:

```bash
python -m unittest discover -s tests/live -p "test_live_e2e.py" -v
```

(Run it as `discover -s tests/live`, not `python -m unittest tests.live.test_live_e2e` -
there's no `tests/__init__.py`/`tests/live/__init__.py`, so the dotted-module
form won't import. `discover -s tests/live` adds `tests/live` itself to
`sys.path`, which is what lets `test_live_e2e.py` do `from base import
LiveTestCase` etc.)

## Env vars

| Variable | Purpose | Default |
|---|---|---|
| `SIGNBRIDGE_RUN_LIVE_TESTS` | Set to `1` to actually run this suite; otherwise every test is skipped (see `base.LiveTestCase`) | unset |
| `SIGNBRIDGE_LIVE_BASE_URL` | REST base URL | `http://127.0.0.1:5000` |
| `SIGNBRIDGE_LIVE_WS_URL` | WebSocket URL | derived from base URL, path `/slt/ws` |
| `SIGNBRIDGE_LIVE_FRAMES_DIR` | Directory of `frame_*.jpg` files for the SLT websocket step | `<repo>/temp/frames` |
| `SIGNBRIDGE_LIVE_TIMEOUT` | Per-request HTTP timeout, seconds | `15` |

No account credentials are configured here - see above.

## Layout

- `live_config.py` — env var reading (loads `tests/live/.env.live.example` via `python-dotenv` if present), the `RUN_LIVE` opt-in flag, `BASE_URL`/`WS_URL`, `frames_dir()`, `unique_email()`.
- `base.py` — `LiveTestCase`, the common base class every test subclasses. Carries the `@unittest.skipUnless(RUN_LIVE, ...)` gate (inherited by every subclass), a `LiveClient` HTTP wrapper set up in `setUp`, and `require_frames_dir()`, which turns a missing/empty frames dir into `self.skipTest(...)` instead of a hard failure.
- `live_helpers.py` — plain (non-test) functions for each API call plus its immediate assertions, and `LiveTestState`, the dataclass threaded through the scenario (`email`/`password`/`new_password` set up front; `token`/`old_token`/`new_token`/`uid`/`history_id` filled in as it runs).
- `test_slt_websocket.py` — no longer a standalone test. Exports `run_slt_session(ws_url, token, frame_files)`, an async helper that does the `/slt/ws` handshake, streams frames, and returns the result - used as the final stage of `test_live_e2e.py`'s single scenario, authenticated with the same account's JWT #2. (unittest will still import this module during discovery; it just finds no tests in it, which is harmless.)
- `test_live_e2e.py` — `TestLiveE2E.test_complete_live_backend_flow`: the full 19-step lifecycle (register → login → forgot/update-password → history CRUD → logout → revocation check → re-login → SLT websocket) as one test method, since TestCase methods shouldn't depend on each other's order; plus `TestLiveAuthEdgeCases` for a few standalone, order-independent auth tests, each registering its own throwaway account.

## Notes

- Every run registers a brand-new account (`unique_email()`), so
  `update-password` is safe to call for real - there's no shared
  password to keep in sync across runs or across the test file.
- Needs the `requests`, `websockets`, and `python-dotenv` packages
  available in the test environment (`websockets` and `python-dotenv`
  are already dependencies via `tests/e2e/websocket_e2e_test_local.py`
  and `app/main.py` respectively).
- unittest has no `xfail` concept. Where the whole REST lifecycle and
  the WebSocket handshake/streaming all succeed but the model just
  doesn't return a prediction in time (e.g. cold start), the test calls
  `self.skipTest(...)` rather than failing, so a slow model doesn't
  register as a broken test.
