"""All websocket-specific regression tests for the SignBridge backend.

These tests cover websocket_handler.py only: frame decoding, transport/config
negotiation, helpers, connection lifecycle, frame intake, inference and
cleanup.

Authentication is NOT performed by websocket_handler.py - it is performed
once by main.py's require_ws_auth dependency, which then calls
handle_websocket(ws, model_api, landmark_executor, inference_executor,
user_id) with the already-verified user_id. So there is nothing to mock
here for auth; these tests just pass a user_id straight in.

handle_websocket also reads messages via the ASGI-level ws.receive(), which
yields {"type": "websocket.receive", "bytes": ..., "text": ...} events and
a final {"type": "websocket.disconnect"} - NOT flask-sock-style
receive_text()/WebSocketDisconnect. The _ws() fixture below builds those
events directly.
"""

import asyncio
import base64
import itertools
import json
import unittest
from concurrent.futures import Future
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch, sentinel

from PIL import Image
from starlette.websockets import WebSocketState

import websocket_handler as wh


# Shared module loader for websocket_processing.py regression tests.
def _load_module(name, filename, fake_modules=None, env=None, env_remove=(), patches=()):
    """Execute a backend module as a fresh import with optional fakes."""
    import importlib.util
    from contextlib import ExitStack

    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, fake_modules or {}))
        stack.enter_context(patch.dict(os.environ, env or {}))
        for key in env_remove:
            os.environ.pop(key, None)
        for patcher in patches:
            stack.enter_context(patcher)
        spec.loader.exec_module(module)
    return module


def _jpeg_bytes(width=8, height=8, fmt="JPEG"):
    """Raw encoded image bytes - what a jpeg_binary WS message actually carries."""
    buf = BytesIO()
    Image.new("RGB", (width, height)).save(buf, format=fmt)
    return buf.getvalue()


def _b64_frame_message(width=8, height=8):
    """A json_base64-transport frame message: {"type": "frame", "frame": "<b64>"}."""
    return json.dumps({"type": "frame", "frame": base64.b64encode(_jpeg_bytes(width, height)).decode()})


def _frame_msg(width=8, height=8):
    """Legacy-shaped {"frame": "<b64>"} text message, for direct decode_frame()
    calls only. NEVER pass this into _ws() for a connection-loop test - the
    default transport is jpeg_binary, which rejects ALL text frame messages
    regardless of content. Use _jpeg_bytes() for connection-loop tests."""
    return json.dumps({"frame": base64.b64encode(_jpeg_bytes(width, height)).decode()})


def _config_message(**overrides):
    payload = {"type": "config", "version": wh.CONFIG_VERSION, "mode": "frames", "transport": "jpeg_binary"}
    payload.update(overrides)
    return json.dumps(payload)


def _fake_ws():
    """Minimal WS double for tests that call handler functions directly
    (handle_config_message, send_json, ...) without going through the
    handle_websocket connection loop - no receive() needed."""
    ws = MagicMock()
    ws.application_state = WebSocketState.CONNECTED
    ws.send_text = AsyncMock()
    return ws


def _sent(ws):
    return [json.loads(c.args[0]) for c in ws.send_text.await_args_list]


def _done_future(result=None):
    future = Future()
    future.set_result(result)
    return future


def _failed_future(exc):
    future = Future()
    future.set_exception(exc)
    return future


class DecodeFrameBytesTests(unittest.TestCase):
    """Binary jpeg_binary path: decode_frame_bytes."""

    def test_decodes_valid_jpeg(self):
        image = wh.decode_frame_bytes(_jpeg_bytes(16, 12))
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (16, 12))

    def test_rejects_empty_payload(self):
        with self.assertRaisesRegex(ValueError, "Empty frame payload"):
            wh.decode_frame_bytes(b"")

    def test_rejects_oversized_payload(self):
        with self.assertRaisesRegex(ValueError, "Frame too large"):
            wh.decode_frame_bytes(b"x" * (wh.MAX_FRAME_BYTES + 1))

    def test_rejects_non_jpeg_binary(self):
        png_bytes = _jpeg_bytes(8, 8, fmt="PNG")
        with self.assertRaisesRegex(ValueError, "Frame must be JPEG"):
            wh.decode_frame_bytes(png_bytes)

    def test_rejects_garbage_bytes(self):
        with self.assertRaisesRegex(ValueError, "Invalid image"):
            wh.decode_frame_bytes(b"not an image at all")

    def test_rejects_dimensions_out_of_range(self):
        huge = Image.new("RGB", (wh.MAX_IMAGE_DIMENSION + 1, 8))
        buf = BytesIO()
        huge.save(buf, format="JPEG")
        with self.assertRaisesRegex(ValueError, "dimensions out of range"):
            wh.decode_frame_bytes(buf.getvalue())


class DecodeFrameJsonBase64Tests(unittest.TestCase):
    """json_base64 path: decode_frame."""

    def test_decodes_valid_base64_jpeg(self):
        image = wh.decode_frame(_b64_frame_message(10, 5))
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (10, 5))

    def test_type_field_is_optional(self):
        payload = json.dumps({"frame": base64.b64encode(_jpeg_bytes()).decode()})
        image = wh.decode_frame(payload)
        self.assertEqual(image.size, (8, 8))

    def test_rejects_wrong_type_field(self):
        payload = json.dumps({"type": "config", "frame": base64.b64encode(_jpeg_bytes()).decode()})
        with self.assertRaisesRegex(ValueError, "Unexpected message type"):
            wh.decode_frame(payload)

    def test_rejects_missing_frame_field(self):
        with self.assertRaisesRegex(ValueError, "Missing frame"):
            wh.decode_frame(json.dumps({"type": "frame"}))

    def test_rejects_invalid_base64(self):
        payload = json.dumps({"type": "frame", "frame": "!!!not_valid_base64!!!"})
        with self.assertRaisesRegex(ValueError, "Invalid base64 frame"):
            wh.decode_frame(payload)

    def test_rejects_valid_base64_that_is_not_an_image(self):
        payload = json.dumps({"type": "frame", "frame": base64.b64encode(b"hello world").decode()})
        with self.assertRaisesRegex(ValueError, "Invalid image"):
            wh.decode_frame(payload)

    def test_rejects_oversized_base64_payload(self):
        # MAX_FRAME_B64_CHARS == MAX_MESSAGE_CHARS in this handler, so an
        # oversized base64 field trips the whole-message size check first.
        oversized = "A" * (wh.MAX_FRAME_B64_CHARS + 1)
        payload = json.dumps({"type": "frame", "frame": oversized})
        with self.assertRaisesRegex(ValueError, "Message too large"):
            wh.decode_frame(payload)

    def test_rejects_oversized_message(self):
        huge = json.dumps({"frame": "A" * (wh.MAX_MESSAGE_CHARS + 1)})
        with self.assertRaisesRegex(ValueError, "Message too large"):
            wh.decode_frame(huge)

    def test_rejects_malformed_json(self):
        with self.assertRaisesRegex(ValueError, "Invalid JSON message"):
            wh.decode_frame("not json at all")

    def test_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            wh.decode_frame(json.dumps([1, 2, 3]))


class DecodeIncomingFrameBoundaryTests(unittest.TestCase):
    """decode_incoming_frame: the transport/input boundary itself."""

    def test_binary_message_always_decodes_as_jpeg_regardless_of_transport(self):
        image = wh.decode_incoming_frame(_jpeg_bytes(4, 4), None, wh.TRANSPORT_JPEG_BINARY)
        self.assertEqual(image.size, (4, 4))

    def test_text_message_decodes_under_json_base64_transport(self):
        image = wh.decode_incoming_frame(None, _b64_frame_message(6, 6), wh.TRANSPORT_JSON_BASE64)
        self.assertEqual(image.size, (6, 6))

    def test_text_message_rejected_under_jpeg_binary_transport(self):
        with self.assertRaisesRegex(ValueError, "does not accept text frames"):
            wh.decode_incoming_frame(None, _b64_frame_message(), wh.TRANSPORT_JPEG_BINARY)

    def test_h264_raises_not_implemented_for_binary_frame(self):
        with self.assertRaises(wh.TransportNotImplementedError):
            wh.decode_incoming_frame(_jpeg_bytes(), None, wh.TRANSPORT_H264)

    def test_h265_raises_not_implemented_for_text_frame(self):
        with self.assertRaises(wh.TransportNotImplementedError):
            wh.decode_incoming_frame(None, _b64_frame_message(), wh.TRANSPORT_H265)

    def test_h264_checked_before_frame_bytes_are_even_inspected(self):
        """A binary frame under h264 must fail as not-implemented, never as a
        decode error - the exact bug class the transport boundary exists to
        prevent (routing unknown binary payloads through the JPEG decoder)."""
        garbage = b"\x00\x01\x02not a jpeg or h264 nal unit"
        with self.assertRaises(wh.TransportNotImplementedError):
            wh.decode_incoming_frame(garbage, None, wh.TRANSPORT_H264)


class HandleConfigMessageTests(unittest.IsolatedAsyncioTestCase):

    async def test_valid_jpeg_binary_handshake_is_accepted(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="jpeg_binary"), config, ws)
        self.assertTrue(handled)
        self.assertEqual(config["transport"], "jpeg_binary")
        self.assertEqual(
            _sent(ws),
            [{"type": "config_ack", "version": 1, "status": "accepted", "mode": "frames", "transport": "jpeg_binary"}],
        )

    async def test_valid_json_base64_handshake_is_accepted(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="json_base64"), config, ws)
        self.assertTrue(handled)
        self.assertEqual(config["transport"], "json_base64")
        acks = _sent(ws)
        self.assertEqual(acks[0]["status"], "accepted")
        self.assertEqual(acks[0]["transport"], "json_base64")

    async def test_unsupported_transport_is_rejected(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="vp9"), config, ws)
        self.assertTrue(handled)  # it was recognized as a config message...
        self.assertEqual(config["transport"], wh.DEFAULT_TRANSPORT)  # ...but not applied
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "error")
        self.assertEqual(ack["field"], "transport")
        self.assertEqual(ack["error"], "Unsupported transport")

    async def test_missing_transport_is_rejected(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        message = json.dumps({"type": "config", "version": wh.CONFIG_VERSION, "mode": "frames"})
        handled = await wh.handle_config_message(message, config, ws)
        self.assertTrue(handled)
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "error")
        self.assertEqual(ack["field"], "transport")
        self.assertEqual(ack["error"], "Missing transport")

    async def test_non_string_transport_is_rejected(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        message = json.dumps({"type": "config", "version": wh.CONFIG_VERSION, "mode": "frames", "transport": 5})
        handled = await wh.handle_config_message(message, config, ws)
        self.assertTrue(handled)
        self.assertEqual(_sent(ws)[0]["error"], "Missing transport")

    async def test_malformed_config_missing_mode_is_rejected(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        message = json.dumps({"type": "config", "version": wh.CONFIG_VERSION, "transport": "jpeg_binary"})
        handled = await wh.handle_config_message(message, config, ws)
        self.assertTrue(handled)
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "error")
        self.assertEqual(ack["field"], "mode")

    async def test_malformed_config_bad_version_is_rejected(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(version=99), config, ws)
        self.assertTrue(handled)
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "error")
        self.assertEqual(ack["field"], "version")

    async def test_malformed_config_not_json_is_ignored_not_treated_as_config(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message("not json", config, ws)
        self.assertFalse(handled)
        ws.send_text.assert_not_called()

    async def test_non_config_message_is_not_consumed(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(json.dumps({"type": "end"}), config, ws)
        self.assertFalse(handled)
        ws.send_text.assert_not_called()

    async def test_h264_is_accepted_at_protocol_level_but_marked_not_implemented(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="h264"), config, ws)
        self.assertTrue(handled)
        # It IS applied to config (recognized/reserved transport)...
        self.assertEqual(config["transport"], "h264")
        # ...but the client is told plainly it can't send frames yet.
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "not_implemented")
        self.assertEqual(ack["transport"], "h264")
        self.assertIn("not implemented", ack["error"])

    async def test_h265_is_accepted_at_protocol_level_but_marked_not_implemented(self):
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="h265"), config, ws)
        self.assertTrue(handled)
        self.assertEqual(config["transport"], "h265")
        ack = _sent(ws)[0]
        self.assertEqual(ack["status"], "not_implemented")

    async def test_config_before_any_frames_is_the_only_supported_order(self):
        """handle_config_message never falls through to frame decoding -
        handle_websocket's own dispatch order (config check before frame
        decode) is what enforces "config before frames"."""
        ws, config = _fake_ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        handled = await wh.handle_config_message(_config_message(transport="jpeg_binary"), config, ws)
        self.assertTrue(handled)
        self.assertEqual(_sent(ws), [{
            "type": "config_ack", "version": 1, "status": "accepted",
            "mode": "frames", "transport": "jpeg_binary",
        }])


class TransportConstantsTests(unittest.TestCase):
    """Guard against silent drift between the protocol constants."""

    def test_default_transport_is_jpeg_binary(self):
        self.assertEqual(wh.DEFAULT_TRANSPORT, wh.TRANSPORT_JPEG_BINARY)

    def test_unimplemented_transports_are_a_subset_of_valid_transports(self):
        self.assertTrue(set(wh.UNIMPLEMENTED_TRANSPORTS).issubset(set(wh.VALID_TRANSPORTS)))

    def test_h264_and_h265_are_the_only_unimplemented_transports(self):
        self.assertEqual(set(wh.UNIMPLEMENTED_TRANSPORTS), {wh.TRANSPORT_H264, wh.TRANSPORT_H265})

    def test_jpeg_binary_and_json_base64_are_implemented(self):
        implemented = set(wh.VALID_TRANSPORTS) - set(wh.UNIMPLEMENTED_TRANSPORTS)
        self.assertEqual(implemented, {wh.TRANSPORT_JPEG_BINARY, wh.TRANSPORT_JSON_BASE64})


class _HandlerTestCase(unittest.IsolatedAsyncioTestCase):
    """Loads the real websocket_handler module for handle_websocket tests.

    Auth is out of scope here (main.py's job) - handle_websocket takes an
    already-verified user_id directly, so tests just pass one in.
    """

    def setUp(self):
        self.handler = wh
        self.process_frame = MagicMock(name="process_frame")
        self.run_inference = MagicMock(name="run_inference")
        self.handler.process_frame = self.process_frame
        self.handler.run_inference = self.run_inference
        self.model_api = MagicMock()
        self.model_api.check_health.return_value = True

    def _ws(self, *messages):
        """A fake FastAPI WebSocket using the real ASGI-level receive() API.

        Each item in `messages` is either:
          - bytes/bytearray -> a binary WS message (jpeg_binary frame)
          - str              -> a text WS message (config/end/json_base64 frame)
        After they're exhausted, a websocket.disconnect event ends the loop -
        mirroring a client closing the connection.
        """
        ws = MagicMock()
        ws.application_state = WebSocketState.CONNECTED
        ws.accept = AsyncMock()

        async def _close(*args, **kwargs):
            ws.application_state = WebSocketState.DISCONNECTED

        ws.close = AsyncMock(side_effect=_close)
        ws.send_text = AsyncMock()

        events = []
        for m in messages:
            if isinstance(m, (bytes, bytearray)):
                events.append({"type": "websocket.receive", "bytes": bytes(m), "text": None})
            else:
                events.append({"type": "websocket.receive", "bytes": None, "text": m})
        events.append({"type": "websocket.disconnect"})

        ws.receive = AsyncMock(side_effect=events)
        return ws

    @staticmethod
    def _sent(ws):
        return [json.loads(c[0][0]) for c in ws.send_text.call_args_list]

    @staticmethod
    def _default_pool():
        """A pool whose submit() always hands back a real, resolved Future."""
        pool = MagicMock()
        pool.submit.side_effect = lambda *a, **k: _done_future(Image.new("RGB", (32, 32)))
        return pool

    async def _run(
        self,
        ws,
        landmark_executor=None,
        inference_executor=None,
        landmarkers=(None, None),
        ticks=None,
        user_id="alice",
    ):
        if landmark_executor is None:
            landmark_executor = self._default_pool()

        if inference_executor is None:
            inference_executor = self._default_pool()

        real_loop = asyncio.get_running_loop()
        tick_source = iter(ticks if ticks is not None else itertools.count(1))

        class _LoopTimeProxy:
            def time(self_proxy):
                try:
                    return next(tick_source)
                except StopIteration:
                    return real_loop.time()

            def __getattr__(self_proxy, name):
                return getattr(real_loop, name)

        build_patch = patch.object(self.handler, "build_landmarkers", return_value=landmarkers)

        with build_patch, patch.object(
            self.handler.asyncio, "get_running_loop", return_value=_LoopTimeProxy()
        ):
            await self.handler.handle_websocket(
                ws, self.model_api, landmark_executor, inference_executor
            )

        return landmark_executor, inference_executor

    def _pipeline_pools(self, inference_future):
        """landmark_executor finishes instantly (yielding a RESIZE_DIM-sized
        image, standing in for what a real process_frame() would have
        produced); inference_executor returns `inference_future`."""
        landmark_future = _done_future(Image.new("RGB", (wh.RESIZE_DIM, wh.RESIZE_DIM)))

        def landmark_submit(fn, *args):
            return landmark_future

        def inference_submit(fn, *args):
            return inference_future

        landmark_pool = MagicMock()
        landmark_pool.submit.side_effect = landmark_submit
        inference_pool = MagicMock()
        inference_pool.submit.side_effect = inference_submit
        return landmark_pool, inference_pool

    def _inference_calls(self, inference_pool):
        """Calls that submitted execute_inference (the actual function
        handle_websocket schedules on inference_executor - NOT run_inference
        directly; execute_inference wraps run_inference and formats errors)."""
        return [c for c in inference_pool.submit.call_args_list if c[0][0] is self.handler.execute_inference]


class WebSocketHelperTests(_HandlerTestCase):
    async def test_send_json_serialises_payload(self):
        ws = self._ws()
        await self.handler.send_json(ws, {"a": 1})
        ws.send_text.assert_called_once()
        self.assertEqual(json.loads(ws.send_text.call_args[0][0]), {"a": 1})

    async def test_send_json_skips_disconnected_socket(self):
        ws = self._ws()
        ws.application_state = WebSocketState.DISCONNECTED
        await self.handler.send_json(ws, {"a": 1})
        ws.send_text.assert_not_called()

    def test_decode_frame_returns_rgb_image(self):
        image = self.handler.decode_frame(_frame_msg(8, 6))
        self.assertIsInstance(image, Image.Image)
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (8, 6))

    def test_decode_frame_converts_other_modes_to_rgb(self):
        buf = BytesIO()
        Image.new("RGBA", (4, 4)).save(buf, format="PNG")
        message = json.dumps({"frame": base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(self.handler.decode_frame(message).mode, "RGB")

    def test_decode_frame_rejects_bad_input(self):
        cases = [
            ("not json", "not json", "Invalid JSON message"),
            ("no frame key", json.dumps({}), "Missing frame"),
            ("empty frame", json.dumps({"frame": ""}), "Missing frame"),
            ("bad base64", json.dumps({"frame": "!!!not_valid_base64!!!"}), "Invalid base64 frame"),
            ("not an image", json.dumps({"frame": base64.b64encode(b"hello").decode()}),
             "Invalid image"),
        ]
        for label, message, error in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, error):
                    self.handler.decode_frame(message)

    def test_decode_frame_rejects_oversized_message(self):
        huge = json.dumps({"frame": "A" * (self.handler.MAX_MESSAGE_CHARS + 1)})
        with self.assertRaisesRegex(ValueError, "Message too large"):
            self.handler.decode_frame(huge)

    async def test_config_message_switches_mode_and_acknowledges(self):
        for mode in ("frames", "video", "hybrid"):
            with self.subTest(mode=mode):
                ws, config = self._ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
                message = _config_message(mode=mode)
                self.assertTrue(await self.handler.handle_config_message(message, config, ws))
                self.assertEqual(config["mode"], mode)
                self.assertEqual(self._sent(ws), [{
                    "type": "config_ack",
                    "version": wh.CONFIG_VERSION,
                    "status": "accepted",
                    "mode": mode,
                    "transport": "jpeg_binary",
                }])

    async def test_config_message_with_unknown_mode_is_rejected(self):
        ws, config = self._ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        message = _config_message(mode="unknown_mode")
        self.assertTrue(await self.handler.handle_config_message(message, config, ws))
        self.assertEqual(config["mode"], "frames")
        self.assertEqual(self._sent(ws), [{
            "type": "config_ack",
            "version": wh.CONFIG_VERSION,
            "status": "error",
            "error": "Invalid config",
            "field": "mode",
        }])

    async def test_non_config_messages_are_not_consumed(self):
        ws, config = self._ws(), {"mode": "frames", "transport": wh.DEFAULT_TRANSPORT}
        for message in ("not json", _frame_msg(), json.dumps({"type": "ping"})):
            with self.subTest(message=message[:20]):
                self.assertFalse(await self.handler.handle_config_message(message, config, ws))
        ws.send_text.assert_not_called()

    async def test_send_inference_result_sends_label_and_confidence(self):
        ws = self._ws()
        result = {"prediction": "hello", "confidence": 0.95, "total_latency_ms": 12}
        self.assertTrue(await self.handler.send_inference_result(ws, result, "frames"))
        self.assertEqual(self._sent(ws), [{"label": "hello", "confidence": 0.95}])

    async def test_send_inference_result_tolerates_bad_confidence(self):
        ws = self._ws()
        result = {"prediction": "hello", "confidence": "high"}
        self.assertTrue(await self.handler.send_inference_result(ws, result, "video"))
        self.assertEqual(self._sent(ws), [{"label": "hello", "confidence": 0.0}])

    async def test_send_inference_result_skips_empty_and_missing_labels(self):
        for result in (None, {}, {"prediction": "", "confidence": 0.9}, {"confidence": 0.9}):
            with self.subTest(result=result):
                ws = self._ws()
                self.assertFalse(await self.handler.send_inference_result(ws, result, "frames"))
                ws.send_text.assert_not_called()

    async def test_send_inference_result_logs_errors_and_sends_nothing(self):
        ws = self._ws()
        with self.assertLogs(self.handler.logger, "ERROR"):
            sent = await self.handler.send_inference_result(ws, {"error": "boom"}, "frames")
        self.assertFalse(sent)
        ws.send_text.assert_not_called()


# WebSocketHandlerTests - handle_websocket connection lifecycle

class WebSocketHandlerTests(_HandlerTestCase):
    # start-up notices

    async def test_landmarks_disabled_notice_when_mediapipe_missing(self):
        self.handler.MEDIAPIPE_OK = False
        ws = self._ws()
        await self._run(ws)
        info = [m for m in self._sent(ws) if m.get("status") == "info"]
        self.assertEqual(len(info), 1)
        self.assertIn("Landmarks disabled", info[0]["message"])
        self.handler.MEDIAPIPE_OK = True  # don't leak into other tests

    async def test_no_landmarks_notice_when_mediapipe_available(self):
        ws = self._ws()
        await self._run(ws)
        self.assertFalse([m for m in self._sent(ws) if m.get("status") == "info"])

    async def test_model_unhealthy_sends_warming_message(self):
        self.model_api.check_health.return_value = False
        ws = self._ws()
        await self._run(ws)
        warming = [m for m in self._sent(ws) if m.get("status") == "api_warming"]
        self.assertEqual(len(warming), 1)

    async def test_model_health_check_error_sends_warming_message(self):
        self.model_api.check_health.side_effect = ConnectionError("refused")
        ws = self._ws()
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(ws)
        self.assertTrue([m for m in self._sent(ws) if m.get("status") == "api_warming"])

    async def test_model_healthy_sends_no_warming_message(self):
        ws = self._ws()
        await self._run(ws)
        self.assertFalse([m for m in self._sent(ws) if m.get("status") == "api_warming"])

    # configuration

    async def test_config_command_updates_mode(self):
        ws = self._ws(_config_message(mode="video"))
        landmark_executor = MagicMock()
        await self._run(ws, landmark_executor=landmark_executor)
        acks = [m for m in self._sent(ws) if m.get("type") == "config_ack"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["status"], "accepted")
        self.assertEqual(acks[0]["mode"], "video")
        landmark_executor.submit.assert_not_called()  # config messages are never treated as frames

    async def test_hybrid_mode_is_accepted(self):
        ws = self._ws(_config_message(mode="hybrid"))
        await self._run(ws)
        acks = [m for m in self._sent(ws) if m.get("type") == "config_ack"]
        self.assertEqual(acks[0]["status"], "accepted")
        self.assertEqual(acks[0]["mode"], "hybrid")

    async def test_invalid_config_mode_is_not_acknowledged(self):
        ws = self._ws(_config_message(mode="unknown_mode"))
        landmark_executor = MagicMock()
        await self._run(ws, landmark_executor=landmark_executor)
        acks = [m for m in self._sent(ws) if m.get("type") == "config_ack"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["status"], "error")
        self.assertEqual(acks[0]["field"], "mode")
        landmark_executor.submit.assert_not_called()

    # frame intake

    async def test_invalid_frame_sends_error_and_continues(self):
        # All three are TEXT messages while the active transport is the
        # default jpeg_binary, so every one is rejected for the same reason
        # (text not accepted under jpeg_binary) - which is fine, since the
        # client-facing error is the same generic "Invalid frame" either way.
        ws = self._ws(
            json.dumps({"frame": "!!!not_valid_base64!!!"}),
            "not json",
            json.dumps({"frame": base64.b64encode(b"hello").decode()}),
        )
        landmark_executor = MagicMock()
        await self._run(ws, landmark_executor=landmark_executor)
        errors = [m for m in self._sent(ws) if "error" in m]
        self.assertEqual(errors, [{"error": "Invalid frame"}] * 3)
        landmark_executor.submit.assert_not_called()

    async def test_empty_frame_field_is_reported_as_invalid(self):
        ws = self._ws(json.dumps({"frame": ""}))
        landmark_executor = MagicMock()
        await self._run(ws, landmark_executor=landmark_executor)
        self.assertIn({"error": "Invalid frame"}, self._sent(ws))
        landmark_executor.submit.assert_not_called()

    async def test_frames_faster_than_frame_delay_are_dropped(self):
        landmark_executor = MagicMock()
        landmark_executor.submit.return_value = _done_future(Image.new("RGB", (8, 8)))
        ws = self._ws(_jpeg_bytes(), _jpeg_bytes(), _jpeg_bytes())
        # 2nd frame arrives 10 ms after the 1st (< FRAME_DELAY) and is skipped.
        await self._run(ws, landmark_executor=landmark_executor, ticks=[1.0, 1.01, 1.2])
        self.assertEqual(landmark_executor.submit.call_count, 2)

    async def test_frame_is_scheduled_for_landmark_processing(self):
        pose, hand = MagicMock(), MagicMock()
        landmark_executor, _ = await self._run(self._ws(_jpeg_bytes(8, 6)), landmarkers=(pose, hand))
        fn, image, pose_arg, hand_arg, enabled, resize_dim = landmark_executor.submit.call_args[0]
        self.assertIs(fn, self.process_frame)
        self.assertEqual(image.size, (8, 6))
        self.assertIs(pose_arg, pose)
        self.assertIs(hand_arg, hand)
        self.assertTrue(enabled)
        self.assertEqual(resize_dim, self.handler.RESIZE_DIM)

    async def test_landmarks_flag_is_off_without_detectors(self):
        landmark_executor, _ = await self._run(self._ws(_jpeg_bytes()), landmarkers=(None, None))
        self.assertFalse(landmark_executor.submit.call_args[0][4])

    async def test_landmarks_flag_is_off_if_only_one_detector_exists(self):
        landmark_executor, _ = await self._run(self._ws(_jpeg_bytes()), landmarkers=(MagicMock(), None))
        self.assertFalse(landmark_executor.submit.call_args[0][4])

    async def test_slow_landmarks_keep_pending_job_and_collect_its_result(self):
        message = _jpeg_bytes()
        first, second = Future(), Future()
        first.result = MagicMock(wraps=first.result)
        landmark_executor = MagicMock()
        landmark_executor.submit.side_effect = [first, second]
        pose, hand = MagicMock(), MagicMock()

        ws = self._ws(message, message, message)
        original_receive = ws.receive
        calls = 0

        async def receive(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                # Frame 2 arrived while frame 1 was still being processed.
                self.assertEqual(landmark_executor.submit.call_count, 1)
                first.set_result(Image.new("RGB", (8, 8)))
            return await original_receive()

        ws.receive = AsyncMock(side_effect=receive)
        await self._run(ws, landmark_executor=landmark_executor, landmarkers=(pose, hand))
        self.assertEqual(landmark_executor.submit.call_count, 2)
        first.result.assert_called_once()
        self.assertTrue(second.cancelled())
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    async def test_landmark_failure_does_not_stop_the_stream(self):
        landmark_executor = MagicMock()
        landmark_executor.submit.return_value = _failed_future(RuntimeError("mediapipe crashed"))
        pose, hand = MagicMock(), MagicMock()
        ws = self._ws(_jpeg_bytes(), _jpeg_bytes(), _jpeg_bytes())
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(ws, landmark_executor=landmark_executor, landmarkers=(pose, hand))
        self.assertEqual(ws.receive.call_count, 4)  # ran until the disconnect
        self.assertFalse([m for m in self._sent(ws) if "label" in m])
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    # inference

    async def test_inference_result_sent_to_client(self):
        """16 buffered frames trigger inference; the label is echoed back."""
        inference = _done_future((0, {"prediction": "hello", "confidence": 0.95}))
        landmark_executor, inference_pool = self._pipeline_pools(inference)
        # Frame N collects the landmark job of frame N-1, so the 16th buffered
        # frame lands on message 17 (dispatch) and the result is sent on 18.
        ws = self._ws(*[_jpeg_bytes()] * 18)
        await self._run(ws, landmark_executor=landmark_executor, inference_executor=inference_pool)
        labels = [m for m in self._sent(ws) if "label" in m]
        self.assertEqual(labels, [{"label": "hello", "confidence": 0.95}])

    async def test_inference_gets_16_resized_frames_and_default_mode(self):
        landmark_executor, inference_pool = self._pipeline_pools(Future())
        await self._run(
            self._ws(*[_jpeg_bytes()] * 17),
            landmark_executor=landmark_executor,
            inference_executor=inference_pool,
        )
        calls = self._inference_calls(inference_pool)
        self.assertEqual(len(calls), 1)
        _, sequence, frames, mode, model_api, save_videos = calls[0][0]
        self.assertEqual(sequence, 0)
        self.assertEqual(len(frames), 16)
        self.assertTrue(all(f.size == (self.handler.RESIZE_DIM, self.handler.RESIZE_DIM) for f in frames))
        self.assertEqual(mode, "frames")
        self.assertIs(model_api, self.model_api)
        self.assertIs(save_videos, self.handler.SAVE_TEST_VIDEOS)

    async def test_inference_uses_the_configured_mode(self):
        landmark_executor, inference_executor = self._pipeline_pools(Future())
        ws = self._ws(_config_message(mode="hybrid"), *[_jpeg_bytes()] * 17)
        await self._run(ws, landmark_executor=landmark_executor, inference_executor=inference_executor)
        calls = self._inference_calls(inference_executor)
        self.assertEqual(calls[0][0][3], "hybrid")  # (fn, sequence, frames, mode, model_api, save_videos)

    async def test_no_inference_before_the_clip_is_full(self):
        landmark_executor, inference_executor = self._pipeline_pools(Future())
        await self._run(
            self._ws(*[_jpeg_bytes()] * 16),
            landmark_executor=landmark_executor,
            inference_executor=inference_executor,
        )
        self.assertEqual(self._inference_calls(inference_executor), [])

    async def test_inference_error_is_logged_and_no_label_sent(self):
        landmark_executor, inference_executor = self._pipeline_pools(_done_future({"error": "model down"}))
        ws = self._ws(*[_jpeg_bytes()] * 18)
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(ws, landmark_executor=landmark_executor, inference_executor=inference_executor)
        self.assertFalse([m for m in self._sent(ws) if "label" in m])

    async def test_inference_without_label_sends_nothing(self):
        landmark_executor, inference_executor = self._pipeline_pools(
            _done_future({"prediction": "", "confidence": 0.1})
        )
        ws = self._ws(*[_jpeg_bytes()] * 18)
        await self._run(ws, landmark_executor=landmark_executor, inference_executor=inference_executor)
        self.assertFalse([m for m in self._sent(ws) if "label" in m])

    async def test_inference_future_raising_does_not_crash_the_stream(self):
        landmark_executor, inference_executor = self._pipeline_pools(_failed_future(RuntimeError("worker died")))
        ws = self._ws(*[_jpeg_bytes()] * 18)
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(ws, landmark_executor=landmark_executor, inference_executor=inference_executor)
        self.assertEqual(ws.receive.call_count, 19)

    # disconnect / cleanup

    async def test_disconnect_cancels_pending_inference_future(self):
        pending_inference = Future()
        landmark_executor, inference_executor = self._pipeline_pools(pending_inference)
        await self._run(
            self._ws(*[_jpeg_bytes()] * 17),
            landmark_executor=landmark_executor,
            inference_executor=inference_executor,
        )
        self.assertTrue(pending_inference.cancelled())

    async def test_disconnect_waits_for_running_landmarks_before_closing_detectors(self):
        pose, hand = MagicMock(), MagicMock()
        pending_raw = Future()
        pending_raw.set_running_or_notify_cancel()  # in-flight: cancel() must fail
        pool = MagicMock()
        pool.submit.return_value = pending_raw

        async def resolve_once_awaited():
            await asyncio.sleep(0)
            pose.close.assert_not_called()
            hand.close.assert_not_called()
            pending_raw.set_result(Image.new("RGB", (8, 8)))

        resolver = asyncio.ensure_future(resolve_once_awaited())
        await self._run(self._ws(_jpeg_bytes()), landmark_executor=pool, landmarkers=(pose, hand))
        await resolver
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    async def test_disconnect_survives_failed_landmark_job_and_still_closes_detectors(self):
        pose, hand = MagicMock(), MagicMock()
        pending_raw = Future()
        pending_raw.set_running_or_notify_cancel()
        pool = MagicMock()
        pool.submit.return_value = pending_raw

        async def fail_once_awaited():
            await asyncio.sleep(0)
            pending_raw.set_exception(RuntimeError("landmarks failed"))

        resolver = asyncio.ensure_future(fail_once_awaited())
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(self._ws(_jpeg_bytes()), landmark_executor=pool, landmarkers=(pose, hand))
        await resolver
        pose.close.assert_called_once()
        hand.close.assert_called_once()

    async def test_detector_close_errors_do_not_leak(self):
        pose, hand = MagicMock(), MagicMock()
        pose.close.side_effect = RuntimeError("pose close failed")
        with self.assertLogs(self.handler.logger, "ERROR"):
            await self._run(self._ws(), landmarkers=(pose, hand))
        hand.close.assert_called_once()  # still closed after the pose failure

    async def test_receive_error_ends_session_and_cleans_up(self):
        pose, hand = MagicMock(), MagicMock()
        ws = self._ws()
        ws.receive = AsyncMock(side_effect=RuntimeError("socket reset"))
        with self.assertLogs(self.handler.logger, level="ERROR"):
            await self._run(ws, landmarkers=(pose, hand))
        pose.close.assert_called_once()
        hand.close.assert_called_once()


# WebSocketProcessingTests - websocket_processing.py (unchanged, still sync)

class WebSocketProcessingTests(unittest.TestCase):
    def setUp(self):
        self.proc = _load_module(
            "websocket_processing_under_test", "websocket_processing.py",
            env={"ENABLE_MEDIAPIPE": "0"},
        )

    @staticmethod
    def _landmark(x=0.1, y=0.2, z=0.0):
        return SimpleNamespace(x=x, y=y, z=z)

    @staticmethod
    def _fake_mediapipe():
        mp = MagicMock()
        mp.ImageFormat.SRGB = sentinel.srgb
        modules = {"mediapipe": mp}
        return modules, mp

    # module start-up

    def test_mediapipe_disabled_by_environment(self):
        self.assertFalse(self.proc.MEDIAPIPE_OK)
        self.assertIsNone(self.proc.mp_drawing)

    def test_mediapipe_import_failure_disables_landmarks(self):
        with self.assertLogs("websocket_processing_no_mp", "WARNING"):
            proc = _load_module(
                "websocket_processing_no_mp", "websocket_processing.py",
                fake_modules={"mediapipe": None},  # makes `import mediapipe` fail
                env={"ENABLE_MEDIAPIPE": "1"},
            )
        self.assertFalse(proc.MEDIAPIPE_OK)

    def test_mediapipe_enabled_when_import_succeeds(self):
        mp = MagicMock()
        tasks = MagicMock()
        python = MagicMock()
        vision = MagicMock()
        components = MagicMock()
        containers = MagicMock()
        landmark = MagicMock()
        drawing_styles = MagicMock()
        drawing_utils = MagicMock()

        tasks.python = python
        python.vision = vision
        python.components = components
        components.containers = containers
        containers.landmark = landmark

        vision.PoseLandmarksConnections.POSE_LANDMARKS = sentinel.pose_connections
        vision.HandLandmarksConnections.HAND_CONNECTIONS = sentinel.hand_connections
        vision.drawing_styles = drawing_styles
        vision.drawing_utils = drawing_utils

        drawing_styles.get_default_pose_landmarks_style.return_value = sentinel.pose_style
        drawing_styles.get_default_hand_landmarks_style.return_value = sentinel.hand_style
        drawing_styles.get_default_hand_connections_style.return_value = sentinel.hand_connection_style

        proc = _load_module(
            "websocket_processing_with_mp",
            "websocket_processing.py",
            fake_modules={
                "mediapipe": mp,
                "mediapipe.tasks": tasks,
                "mediapipe.tasks.python": python,
                "mediapipe.tasks.python.vision": vision,
                "mediapipe.tasks.python.components": components,
                "mediapipe.tasks.python.components.containers": containers,
                "mediapipe.tasks.python.components.containers.landmark": landmark,
                "mediapipe.tasks.python.vision.drawing_styles": drawing_styles,
                "mediapipe.tasks.python.vision.drawing_utils": drawing_utils,
            },
            env={"ENABLE_MEDIAPIPE": "1"},
        )

        self.assertTrue(proc.MEDIAPIPE_OK)
        self.assertIs(proc.mp, mp)
        self.assertIs(proc.mp_vision, vision)
        self.assertIs(proc.mp_python, python)
        self.assertIs(proc.mp_landmark, landmark)
        self.assertIs(proc.mp_styles, drawing_styles)
        self.assertIs(proc.mp_drawing, drawing_utils)

        self.assertIs(proc.POSE_CONNECTIONS, sentinel.pose_connections)
        self.assertIs(proc.HAND_CONNECTIONS, sentinel.hand_connections)
        self.assertIs(proc.POSE_LANDMARK_STYLE, sentinel.pose_style)
        self.assertIs(proc.HAND_LANDMARK_STYLE, sentinel.hand_style)
        self.assertIs(proc.HAND_CONNECTION_STYLE, sentinel.hand_connection_style)

    # build_landmarkers

    def test_build_landmarkers_returns_none_pair_without_mediapipe(self):
        self.assertEqual(self.proc.build_landmarkers(), (None, None))

    def test_build_landmarkers_creates_pose_and_hand_detectors(self):
        vision, python = MagicMock(), MagicMock()
        with patch.object(self.proc, "MEDIAPIPE_OK", True), \
                patch.object(self.proc, "mp_vision", vision), \
                patch.object(self.proc, "mp_python", python):
            pose, hand = self.proc.build_landmarkers()
        self.assertIs(pose, vision.PoseLandmarker.create_from_options.return_value)
        self.assertIs(hand, vision.HandLandmarker.create_from_options.return_value)
        self.assertEqual(vision.HandLandmarkerOptions.call_args[1]["num_hands"], 2)
        model_paths = [c[1]["model_asset_path"] for c in python.BaseOptions.call_args_list]
        self.assertEqual(
            [Path(path).name for path in model_paths],
            ["pose_landmarker_full.task", "hand_landmarker.task"],
        )

    def test_build_landmarkers_returns_none_pair_on_init_failure(self):
        vision = MagicMock()
        vision.PoseLandmarker.create_from_options.side_effect = RuntimeError("missing .task file")
        with patch.object(self.proc, "MEDIAPIPE_OK", True), \
                patch.object(self.proc, "mp_vision", vision), \
                patch.object(self.proc, "mp_python", MagicMock()):
            with self.assertLogs(self.proc.logger, "ERROR"):
                result = self.proc.build_landmarkers()
        self.assertEqual(result, (None, None))

    # apply_landmarks

    def test_apply_landmarks_returns_original_when_nothing_detected(self):
        image = Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()
        pose.detect.return_value = SimpleNamespace(pose_landmarks=[])
        hand.detect.return_value = SimpleNamespace(hand_landmarks=[])
        modules, mp = self._fake_mediapipe()
        with patch.object(self.proc, "mp", mp), \
                patch.object(self.proc, "mp_drawing", MagicMock()) as drawing:
            result = self.proc.apply_landmarks(image, pose, hand)
        self.assertIs(result, image)
        drawing.draw_landmarks.assert_not_called()

    def test_apply_landmarks_works_without_detectors(self):
        image = Image.new("RGB", (8, 8))
        modules, _ = self._fake_mediapipe()
        with patch.dict(sys.modules, modules):
            self.assertIs(self.proc.apply_landmarks(image, None, None), image)

    def test_apply_landmarks_draws_pose_and_every_hand(self):
        image = Image.new("RGB", (8, 6))
        pose, hand = MagicMock(), MagicMock()

        pose.detect.return_value = SimpleNamespace(pose_landmarks=[[self._landmark()] * 33])
        hand.detect.return_value = SimpleNamespace(
            hand_landmarks=[
                [self._landmark()] * 21,
                [self._landmark()] * 21,
            ]
        )

        modules, mp = self._fake_mediapipe()
        pose_connections = sentinel.pose_connections
        hand_connections = sentinel.hand_connections

        landmark_module = MagicMock()
        landmark_module.NormalizedLandmark.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)

        with patch.object(self.proc, "mp", mp), \
                patch.object(self.proc, "mp_landmark", landmark_module), \
                patch.object(self.proc, "POSE_CONNECTIONS", pose_connections), \
                patch.object(self.proc, "HAND_CONNECTIONS", hand_connections), \
                patch.object(self.proc, "mp_drawing", MagicMock()) as drawing:
            result = self.proc.apply_landmarks(image, pose, hand)

        self.assertEqual(drawing.draw_landmarks.call_count, 3)
        connections = [c[0][2] for c in drawing.draw_landmarks.call_args_list]
        self.assertIs(connections[0], pose_connections)
        self.assertIs(connections[1], hand_connections)
        self.assertIs(connections[2], hand_connections)

        self.assertIsInstance(result, Image.Image)
        self.assertIsNot(result, image)
        self.assertEqual((result.mode, result.size), ("RGB", (8, 6)))

    def test_apply_landmarks_draws_hands_when_no_pose_found(self):
        image = Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()
        pose.detect.return_value = SimpleNamespace(pose_landmarks=[])
        hand.detect.return_value = SimpleNamespace(hand_landmarks=[[self._landmark()] * 21])

        modules, mp = self._fake_mediapipe()
        landmark_module = MagicMock()
        landmark_module.NormalizedLandmark.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)

        with patch.object(self.proc, "mp", mp), \
                patch.object(self.proc, "mp_landmark", landmark_module), \
                patch.object(self.proc, "mp_drawing", MagicMock()) as drawing:
            self.proc.apply_landmarks(image, pose, hand)

        self.assertEqual(drawing.draw_landmarks.call_count, 1)

    # process_frame

    def test_process_frame_applies_landmarks_when_enabled(self):
        image = Image.new("RGB", (8, 8))
        annotated = Image.new("RGB", (8, 8))
        pose, hand = MagicMock(), MagicMock()

        with patch.object(self.proc, "apply_landmarks", return_value=annotated) as apply:
            result = self.proc.process_frame(image, pose, hand, True)

        self.assertIsInstance(result, self.proc.BufferedFrame)
        self.assertIsInstance(result.image, Image.Image)
        self.assertEqual(result.image.size, (224, 224))
        self.assertIsInstance(result.jpeg, bytes)
        self.assertTrue(result.jpeg.startswith(b"\xff\xd8"))  # real JPEG SOI marker
        apply.assert_called_once_with(image, pose, hand)

    def test_process_frame_passes_image_through_when_disabled(self):
        image = Image.new("RGB", (8, 8))

        with patch.object(self.proc, "apply_landmarks") as apply:
            result = self.proc.process_frame(image, None, None, False)

        self.assertIsInstance(result, self.proc.BufferedFrame)
        self.assertEqual(result.image.size, (224, 224))
        self.assertIsInstance(result.jpeg, bytes)
        self.assertTrue(result.jpeg.startswith(b"\xff\xd8"))  # real JPEG SOI marker
        apply.assert_not_called()

    # save_test_video

    def test_save_test_video_writes_every_frame(self):
        frames = [Image.new("RGB", (8, 6))] * 3
        writer = MagicMock()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(self.proc.cv2, "VideoWriter", return_value=writer) as video_writer:
            path = self.proc.save_test_video(frames, output_dir=tmp)
            self.assertTrue(path.startswith(os.path.abspath(tmp)))
        self.assertTrue(path.endswith(".mp4"))
        self.assertEqual(writer.write.call_count, 3)
        writer.release.assert_called_once()
        _, _, fps, size = video_writer.call_args[0]
        self.assertEqual((fps, size), (15.0, (8, 6)))

    def test_save_test_video_with_no_frames_returns_empty_string(self):
        self.assertEqual(self.proc.save_test_video([]), "")

    def test_save_test_video_releases_writer_and_returns_empty_on_error(self):
        writer = MagicMock()
        writer.write.side_effect = RuntimeError("disk full")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(self.proc.cv2, "VideoWriter", return_value=writer), \
                self.assertLogs(self.proc.logger, "ERROR"):
            path = self.proc.save_test_video([Image.new("RGB", (8, 8))], output_dir=tmp)
        self.assertEqual(path, "")
        writer.release.assert_called_once()

    # run_inference

    def test_run_inference_frames_mode_uses_frames_endpoint(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"prediction": "hi", "confidence": 0.9}
        frames = [Image.new("RGB", (8, 8))] * 16
        result = self.proc.run_inference(frames, "frames", model_api)
        model_api.predict_from_frames.assert_called_once_with(frames)
        model_api.predict.assert_not_called()
        self.assertEqual(result["prediction"], "hi")
        self.assertGreaterEqual(result["total_latency_ms"], 0)

    def test_run_inference_video_mode_uses_video_endpoint(self):
        model_api = MagicMock()
        model_api.predict.return_value = {"prediction": "bye"}
        frames = [Image.new("RGB", (8, 8))] * 16
        result = self.proc.run_inference(frames, "video", model_api)
        model_api.predict.assert_called_once_with(frames)
        model_api.predict_from_frames.assert_not_called()
        self.assertEqual(result["prediction"], "bye")
        self.assertIn("total_latency_ms", result)

    def test_run_inference_hybrid_prefers_frames_path(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"prediction": "hi"}
        result = self.proc.run_inference([Image.new("RGB", (8, 8))] * 16, "hybrid", model_api)
        model_api.predict.assert_not_called()
        self.assertEqual(result["prediction"], "hi")

    def test_run_inference_hybrid_falls_back_to_video_on_error(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {"error": "frames path down"}
        model_api.predict.return_value = {"prediction": "from video"}
        frames = [Image.new("RGB", (8, 8))] * 16
        with self.assertLogs(self.proc.logger, "WARNING"):
            result = self.proc.run_inference(frames, "hybrid", model_api)
        model_api.predict.assert_called_once_with(frames)
        self.assertEqual(result["prediction"], "from video")

    def test_run_inference_saves_test_video_only_when_asked(self):
        model_api = MagicMock()
        model_api.predict_from_frames.return_value = {}
        frames = [Image.new("RGB", (8, 8))] * 16
        with patch.object(self.proc, "save_test_video") as save:
            self.proc.run_inference(frames, "frames", model_api)
            save.assert_not_called()
            self.proc.run_inference(frames, "frames", model_api, save_test_videos=True)
            save.assert_called_once_with(frames)


if __name__ == "__main__":
    unittest.main()