"""
Central config for the live (network-hitting) SignBridge test suite.

Opt-in gate (see base.LiveTestCase, which every test in this suite
subclasses):

    SIGNBRIDGE_RUN_LIVE_TESTS=1 python -m unittest discover -s tests/live -v

Without that env var, every test here is skipped. See README.md for the
full list of environment variables.

The suite is fully self-contained: test_live_e2e.py generates its own
throwaway account (unique_email() below) and never touches a persistent
account, so there is no SIGNBRIDGE_LIVE_EMAIL/PASSWORD to configure here.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

_LIVE_DIR = Path(__file__).resolve().parent
load_dotenv(_LIVE_DIR / ".env.live")
load_dotenv(_LIVE_DIR / ".env.live.example")
load_dotenv(REPO_ROOT / ".env")

RUN_LIVE = os.environ.get("SIGNBRIDGE_RUN_LIVE_TESTS") == "1"

BASE_URL = os.environ.get("SIGNBRIDGE_LIVE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


_configured_ws_url = os.environ.get("SIGNBRIDGE_LIVE_WS_URL")
WS_URL = _configured_ws_url or (
    BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/slt/v1/ws"
)

REQUEST_TIMEOUT = float(os.environ.get("SIGNBRIDGE_LIVE_TIMEOUT", "15"))


def frames_dir() -> Path:
    """Returns the directory of frame_*.jpg files for the SLT websocket step.

    Raises LookupError if it doesn't exist or is empty. Test code should
    call this via LiveTestCase.require_frames_dir().
    """
    configured = os.environ.get("SIGNBRIDGE_LIVE_FRAMES_DIR")
    path = Path(configured) if configured else REPO_ROOT / "temp" / "frames"
    if not path.exists() or not sorted(path.glob("frame_*.jpg")):
        raise LookupError(f"no frame_*.jpg files found under: {path}")
    return path


def unique_email() -> str:
    return f"live-test-{uuid.uuid4().hex[:12]}@example.com"
