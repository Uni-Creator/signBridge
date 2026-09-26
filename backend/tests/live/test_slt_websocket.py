"""
SLT WebSocket session runner, used as the final stage of the single live
E2E scenario in test_live_e2e.py.

This module no longer defines its own standalone TestCase: the live
scenario is one self-contained lifecycle on a freshly registered account
(register -> login -> account APIs -> history -> logout -> revocation
check -> re-login -> SLT websocket), and chaining a separately-logged-in
websocket test onto the end would mean testing two different accounts'
lifecycles instead of one. See test_live_e2e.py for the full sequence;
it imports run_slt_session() below and calls it with the JWT issued by
its own final re-login step.

The name is kept as test_slt_websocket.py (rather than, say,
slt_websocket_helpers.py) to match the live suite's file layout, even
though unittest discovery will import this module and find no tests in
it - that's harmless, not an error.
"""
from __future__ import annotations

import asyncio
import json

import websockets

CONFIG_VERSION = 1
CLIP_LENGTH = 16
FRAME_DELAY = 1 / 12.5  # matches the server's ~12.5 fps expectation


async def run_slt_session(ws_url: str, token: str, frame_files: list):
    """
    Connect to /slt/ws with `token`, negotiate the config/handshake,
    stream `frame_files` as jpeg_binary frames, send end-of-stream, and
    return (frames_sent, predictions, complete_message).
    """
    async with websockets.connect(
        ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
    ) as ws:
        connected = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert connected.get("status") == "connected", connected

        await ws.send(json.dumps({
            "type": "config",
            "version": CONFIG_VERSION,
            "mode": "frames",
            "transport": "jpeg_binary",
        }))

        # Drain any pre-handshake status messages (e.g. MediaPipe
        # disabled / model warming info) until the actual config_ack
        # shows up.
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        while ack.get("type") != "config_ack":
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert ack["status"] == "accepted", f"config rejected: {ack}"

        frames_sent = 0
        for frame_file in frame_files:
            data = frame_file.read_bytes()
            if not data:
                continue
            await ws.send(data)
            frames_sent += 1
            await asyncio.sleep(FRAME_DELAY)

        assert frames_sent >= CLIP_LENGTH, (
            f"only {frames_sent} usable frames; need at least "
            f"{CLIP_LENGTH} for one inference window"
        )

        await ws.send(json.dumps({"type": "end"}))

        predictions = []
        complete = None
        while complete is None:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            if "label" in message:
                predictions.append(message)
            elif message.get("status") == "complete":
                complete = message

        # `async with` closes the WebSocket on the way out of this block.
        return frames_sent, predictions, complete
