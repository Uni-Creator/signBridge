"""
SignBridge WebSocket Handler (FastAPI / asyncio)

Responsibilities:
- Connection initialization (Firebase auth happens once, in main.py's
  require_ws_auth dependency, before handle_websocket() is called)
- WebSocket message receiving
- Input validation
- Config/handshake negotiation (version, mode, transport) + config_ack
- Transport/input boundary: jpeg_binary / json_base64 / h264 / h265 all
  decode to one common PIL image before anything transport-specific runs
- Frame-rate limiting
- Frame decoding
- Live sliding-window frame buffering
- Background MediaPipe processing
- Parallel overlapping inference
- Ordered inference-result delivery
- Cleanup
"""

import asyncio
import base64
import concurrent.futures
import gc
import json
import logging
from collections import deque
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websocket_processing import (
    MEDIAPIPE_OK,
    build_landmarkers,
    process_frame,
    run_inference,
)


# Logging

logger = logging.getLogger(__name__)


def _format_bytes(size: int) -> str:
    """Format byte count for readable logging."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def log_ws_received(kind: str, size: int):
    logger.info(
        "[WS RX] %s: %s (%d bytes)",
        kind,
        _format_bytes(size),
        size,
    )


def log_ws_sent(kind: str, size: int):
    logger.info(
        "[WS TX] %s: %s (%d bytes)",
        kind,
        _format_bytes(size),
        size,
    )


# WebSocket configuration

CLIP_LENGTH = 16

# Number of NEW frames required before creating the next overlapping window.
#
# Window 0: frames 0  - 15
# Window 1: frames 6  - 21
# Window 2: frames 12 - 27
# Window 3: frames 18 - 33
#
# Therefore:
#   overlap = 16 - 6 = 10 frames
CLIP_STRIDE = 6

# Maximum number of inference jobs allowed to run at the same time.
#
# Start with 2. Increase only after benchmarking the model server.
MAX_CONCURRENT_INFERENCES = 2

# Approximately 12.5 incoming frames/sec.
FRAME_DELAY = 0.08

RESIZE_DIM = 224

SAVE_TEST_VIDEOS = False

# How long to wait for a client message before treating the connection
# as idle.
RECEIVE_TIMEOUT = 30.0


# Input validation limits.
MAX_MESSAGE_CHARS = 2_000_000
MAX_FRAME_B64_CHARS = 2_000_000   # legacy base64 clients only
MAX_FRAME_BYTES = 500_000         # binary frames (224x224 JPEG is ~10-20 KB)
MAX_IMAGE_DIMENSION = 4096
MIN_IMAGE_DIMENSION = 1

VALID_CONFIG_MODES = (
    "frames",
    "video",
    "hybrid",
)

# Config/handshake protocol version this server implements.
CONFIG_VERSION = 1

# WebSocket frame transports.
#
# This is distinct from, and must never be confused with, the ISLF
# container used to talk to ISLModelAPI (see frame_codec.py). The
# WebSocket transport only governs how a frame arrives on THIS socket;
# every transport still funnels into the same decode_frame_bytes() /
# decode_frame() -> process_frame() -> BufferedFrame path.
TRANSPORT_JPEG_BINARY = "jpeg_binary"
TRANSPORT_JSON_BASE64 = "json_base64"
TRANSPORT_H264 = "h264"
TRANSPORT_H265 = "h265"

VALID_TRANSPORTS = (
    TRANSPORT_JPEG_BINARY,
    TRANSPORT_JSON_BASE64,
    TRANSPORT_H264,
    TRANSPORT_H265,
)

# Recognized by the protocol (so they are not "unsupported"), but not
# wired up to a decoder yet.
UNIMPLEMENTED_TRANSPORTS = (
    TRANSPORT_H264,
    TRANSPORT_H265,
)

# Transport assumed for a connection that never sends a config message,
# so pre-handshake clients keep working unchanged.
DEFAULT_TRANSPORT = TRANSPORT_JPEG_BINARY


class TransportNotImplementedError(ValueError):
    """A frame arrived for a transport that is reserved but not decodable."""


# WebSocket sending

async def send_json(ws: WebSocket, payload: dict):
    """
    Safely serialize and send a JSON WebSocket message.

    A client disconnect during send is normal WebSocket lifecycle behavior,
    so it is logged at debug level rather than as a server error.
    """
    if ws.application_state != WebSocketState.CONNECTED:
        return False

    try:
        message = json.dumps(
            payload,
            separators=(",", ":"),
        )

        payload_size = len(message.encode("utf-8"))

        logger.info(
            "[WS TX] sending text JSON: %s (%d bytes)",
            _format_bytes(payload_size),
            payload_size,
        )

        await ws.send_text(message)
        return True

    except WebSocketDisconnect:
        logger.debug(
            "WebSocket client disconnected while sending a message."
        )
        return False

    except Exception:
        logger.exception("Failed to send WebSocket message.")
        return False


# Frame decoding

def decode_frame(message: str):
    """
    Decode an incoming WebSocket message into a PIL RGB image.

    Expected format:

        {
            "frame": "<base64 encoded image>"
        }

    Validation order:

        message type/size
            ↓
        JSON structure
            ↓
        frame field
            ↓
        base64
            ↓
        image validity
            ↓
        image dimensions
    """

    if not isinstance(message, str):
        raise ValueError("Message must be text")

    if not message:
        raise ValueError("Empty message")

    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError("Message too large")

    try:
        data = json.loads(message)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Invalid JSON message") from exc

    if not isinstance(data, dict):
        raise ValueError("Message must be a JSON object")

    # New-protocol json_base64 frames are {"type": "frame", "frame": "..."}.
    # The type field is optional so legacy pre-handshake clients that never
    # set it keep working unchanged.
    msg_type = data.get("type")

    if msg_type is not None and msg_type != "frame":
        raise ValueError("Unexpected message type for a frame")

    b64 = data.get("frame", "")

    if not isinstance(b64, str):
        raise ValueError("frame must be a string")

    if not b64:
        raise ValueError("Missing frame")

    if len(b64) > MAX_FRAME_B64_CHARS:
        raise ValueError("frame payload too large")

    try:
        image_bytes = base64.b64decode(
            b64,
            validate=True,
        )
    except Exception as exc:
        raise ValueError("Invalid base64 frame") from exc

    if not image_bytes:
        raise ValueError("Empty frame payload")

    # Verify image structure first.
    try:
        probe = Image.open(BytesIO(image_bytes))
        probe.verify()
    except Exception as exc:
        raise ValueError("Invalid image") from exc

    # Decode the actual image.
    try:
        image = Image.open(
            BytesIO(image_bytes)
        ).convert("RGB")
    except Exception as exc:
        raise ValueError("Invalid image") from exc
    finally:
        del image_bytes

    width, height = image.size

    if (
        width < MIN_IMAGE_DIMENSION
        or height < MIN_IMAGE_DIMENSION
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
    ):
        raise ValueError(
            f"Image dimensions out of range ({width}x{height})"
        )

    return image


def decode_frame_bytes(image_bytes: bytes):
    """
    Decode a binary WebSocket message (raw JPEG bytes) into a PIL RGB image.

    The header is parsed lazily first, so the format and dimensions are
    checked BEFORE the expensive full decode. convert("RGB") forces the
    decode and raises on corrupt data, so no separate verify() pass is needed.
    """

    if not image_bytes:
        raise ValueError("Empty frame payload")

    if len(image_bytes) > MAX_FRAME_BYTES:
        raise ValueError("Frame too large")

    try:
        image = Image.open(BytesIO(image_bytes))

        if image.format != "JPEG":
            raise ValueError("Frame must be JPEG")

        width, height = image.size

        if (
            width < MIN_IMAGE_DIMENSION
            or height < MIN_IMAGE_DIMENSION
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
        ):
            raise ValueError(
                f"Image dimensions out of range ({width}x{height})"
            )

        return image.convert("RGB")

    except ValueError:
        raise

    except Exception as exc:
        raise ValueError("Invalid image") from exc


def decode_incoming_frame(frame_bytes, message: str, transport: str):
    """
    Transport/input boundary.

    Everything above this function knows about WebSocket messages and
    transports (jpeg_binary / json_base64 / h264 / h265). Everything
    below it only ever sees a decoded PIL image and has no idea which
    transport produced it - process_frame() and the sliding-window
    inference logic are unchanged regardless of transport.

    Raises:
        TransportNotImplementedError: transport is recognized but not
            decodable yet (h264 / h265).
        ValueError: the frame itself is invalid, or does not match the
            connection's active transport (e.g. a text frame while the
            active transport is jpeg_binary).
    """

    if transport in UNIMPLEMENTED_TRANSPORTS:
        raise TransportNotImplementedError(
            f"{transport} transport is reserved but not implemented yet"
        )

    # A binary WebSocket message is unambiguously raw JPEG bytes -
    # that is what jpeg_binary means - regardless of which transport is
    # currently configured.
    if frame_bytes is not None:
        return decode_frame_bytes(frame_bytes)

    if transport == TRANSPORT_JSON_BASE64:
        return decode_frame(message)  # base64 JSON frame

    raise ValueError(
        f"Received a text frame message but active transport is "
        f"{transport!r}, which does not accept text frames"
    )


# Configuration

async def _send_config_ack(
    ws: WebSocket,
    *,
    status: str,
    mode=None,
    transport=None,
    error: str = None,
    field: str = None,
):
    payload = {
        "type": "config_ack",
        "version": CONFIG_VERSION,
        "status": status,
    }

    if mode is not None:
        payload["mode"] = mode

    if transport is not None:
        payload["transport"] = transport

    if error is not None:
        payload["error"] = error

    if field is not None:
        payload["field"] = field

    await send_json(ws, payload)


async def handle_config_message(
    message: str,
    config: dict,
    ws: WebSocket,
) -> bool:
    """
    Process a possible config/handshake message and negotiate the
    transport BEFORE any frames are accepted for it.

    Expected message:

        {
            "type": "config",
            "version": 1,
            "mode": "frames",
            "transport": "jpeg_binary"
        }

    Replies with a "config_ack" message. Returns:
        True  -> message was a config message (handled, ack sent).
        False -> message was not a config message.
    """

    try:
        data = json.loads(message)
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    if data.get("type") != "config":
        return False

    version = data.get("version")
    mode = data.get("mode")
    transport = data.get("transport")

    # Version

    if version != CONFIG_VERSION:
        logger.warning("Rejected config with bad version: %r", version)

        await _send_config_ack(
            ws,
            status="error",
            error="Unsupported or missing config version",
            field="version",
        )

        return True

    # Mode

    if not isinstance(mode, str) or mode not in VALID_CONFIG_MODES:
        logger.warning("Rejected invalid config mode: %r", mode)

        await _send_config_ack(
            ws,
            status="error",
            error="Invalid config",
            field="mode",
        )

        return True

    # Transport

    if not isinstance(transport, str) or not transport:
        logger.warning("Rejected config with missing transport.")

        await _send_config_ack(
            ws,
            status="error",
            mode=mode,
            error="Missing transport",
            field="transport",
        )

        return True

    if transport not in VALID_TRANSPORTS:
        logger.warning("Rejected unsupported transport: %r", transport)

        await _send_config_ack(
            ws,
            status="error",
            mode=mode,
            transport=transport,
            error="Unsupported transport",
            field="transport",
        )

        return True

    # Accepted (possibly reserved-but-unimplemented)

    config["mode"] = mode
    config["transport"] = transport

    if transport in UNIMPLEMENTED_TRANSPORTS:
        logger.info(
            "Config accepted mode=%s, but transport=%s is not "
            "implemented yet.",
            mode,
            transport,
        )

        await _send_config_ack(
            ws,
            status="not_implemented",
            mode=mode,
            transport=transport,
            error=f"{transport} transport is reserved and not implemented yet",
        )

        return True

    logger.info("Config accepted: mode=%s transport=%s", mode, transport)

    await _send_config_ack(
        ws,
        status="accepted",
        mode=mode,
        transport=transport,
    )

    return True


# Inference result formatting

async def send_inference_result(
    ws: WebSocket,
    result: dict,
    mode: str,
) -> bool:
    """
    Process and send one inference result.

    Returns:
        True if a prediction label was sent.
        False otherwise.
    """

    if not isinstance(result, dict) or not result:
        return False

    if "error" in result:
        logger.error(
            "Inference error: %s",
            result["error"],
        )
        return False

    label = result.get(
        "prediction",
        "",
    )

    if not isinstance(label, str):
        label = ""

    try:
        confidence = float(
            result.get(
                "confidence",
                0.0,
            )
        )
    except (TypeError, ValueError):
        confidence = 0.0

    try:
        total_latency = float(
            result.get(
                "total_latency_ms",
                0,
            )
        )
    except (TypeError, ValueError):
        total_latency = 0.0

    try:
        inference_latency = float(
            result.get(
                "inference_time_ms",
                0,
            )
        )
    except (TypeError, ValueError):
        inference_latency = 0.0

    logger.info(
        "[%s] %s %.0f%% | total=%.0fms hf=%.0fms",
        mode.upper(),
        label,
        confidence * 100,
        total_latency,
        inference_latency,
    )

    if not label:
        return False

    await send_json(
        ws,
        {
            "label": label,
            "confidence": confidence,
        },
    )

    return True


# Inference worker

def execute_inference(
    sequence: int,
    frames: list,
    mode: str,
    model_api,
    save_test_videos: bool,
):
    """
    Synchronous worker executed by the inference ThreadPoolExecutor.

    Returns:
        (sequence, result)
    """

    try:
        result = run_inference(
            frames,
            mode,
            model_api,
            save_test_videos,
        )

        return sequence, result

    except Exception as exc:
        logger.exception(
            "Inference #%d failed.",
            sequence,
        )

        return sequence, {
            "error": "Inference failed",
            "sequence": sequence,
            "detail": str(exc),
        }


# Main WebSocket controller

async def handle_websocket(
    ws: WebSocket,
    model_api,
    landmark_executor: concurrent.futures.ThreadPoolExecutor,
    inference_executor: concurrent.futures.ThreadPoolExecutor,
):
    """
    Main WebSocket connection controller.

    Sliding-window behavior:

        WINDOW = 16
        STRIDE = 6

        frames 0-15
            ↓
        inference #0

        retain frames 6-15
        receive frames 16-21
            ↓
        frames 6-21
            ↓
        inference #1

        retain frames 12-21
        receive frames 22-27
            ↓
        frames 12-27
            ↓
        inference #2

    Multiple inference jobs may execute concurrently (bounded by
    MAX_CONCURRENT_INFERENCES), on a dedicated inference_executor so a
    busy inference pool never blocks MediaPipe landmark processing.

    Results are always sent in sequence order even if later inference
    jobs finish before earlier ones.
    """

    loop = asyncio.get_running_loop()

    await ws.accept()

    logger.info(
        "WebSocket client connected",
    )

    await send_json(
        ws,
        {
            "status": "connected",
            "message": "Ready for frames",
        },
    )

    # MediaPipe status

    if not MEDIAPIPE_OK:
        await send_json(
            ws,
            {
                "status": "info",
                "message": (
                    "Landmarks disabled on this server "
                    "(memory limit). Accuracy may be lower."
                ),
            },
        )

    # Connection state

    config = {
        "mode": "frames",
        "transport": DEFAULT_TRANSPORT,
    }

    # IMPORTANT:
    #
    # Do NOT use deque(maxlen=CLIP_LENGTH).
    #
    # We need to retain the overlapping frames after submitting an inference.
    #
    # Example:
    #
    #   [0 ... 15]
    #       submit
    #
    #   remove [0 ... 5]
    #
    #   remaining:
    #       [6 ... 15]
    #
    #   add [16 ... 21]
    #
    #   now:
    #       [6 ... 21]
    #
    frame_buffer = deque()

    last_receive_time = 0.0
    frame_count = 0

    # Inference state

    # Native concurrent.futures.Future objects.
    #
    # sequence -> Future
    #
    # This lets us keep multiple inference jobs alive simultaneously.
    inference_futures = {}

    # Completed results waiting to be sent.
    #
    # sequence -> result
    pending_results = {}

    # Sequence number assigned to the next inference window.
    next_sequence = 0

    # Next inference sequence that the client expects.
    next_result_sequence = 0

    # Landmark state

    landmark_future = None

    # MediaPipe detectors

    pose_detector, hand_detector = build_landmarkers()

    landmarks_enabled = (
        pose_detector is not None
        and hand_detector is not None
    )

    # Model API health

    try:
        model_ready = model_api.check_health()
    except Exception:
        logger.exception(
            "Model API health check failed."
        )
        model_ready = False

    if not model_ready:
        await send_json(
            ws,
            {
                "status": "api_warming",
                "message": (
                    "Model API warming up, "
                    "please wait..."
                ),
            },
        )

        logger.warning(
            "Remote model API not ready - predictions may fail."
        )
    else:
        logger.info(
            "Remote model API healthy."
        )

    # Helper: collect completed MediaPipe job

    def collect_landmark_result():
        nonlocal landmark_future

        if (
            landmark_future is None
            or not landmark_future.done()
        ):
            return

        try:
            # process_frame() already resized + JPEG-encoded the frame
            # inside the landmark worker thread (BufferedFrame).
            frame_buffer.append(landmark_future.result())

            logger.debug(
                "Landmark job collected. "
                "Buffered frames: %d",
                len(frame_buffer),
            )

        except Exception:
            logger.exception(
                "Landmark processing failed."
            )

        finally:
            landmark_future = None

    # Helper: submit one sliding inference window

    def submit_inference_window():
        """
        Submit one 16-frame window.

        IMPORTANT:
        Only CLIP_STRIDE frames are removed.

        This creates the sliding overlap.

        Example:

            Before:
                [0,1,2,...,15]

            Submit:
                [0,1,2,...,15]

            Remove first 6:

                [6,7,8,...,15]

            New frames arrive:

                [6,7,8,...,21]

            Submit that as the next window.
        """

        nonlocal next_sequence

        if len(frame_buffer) < CLIP_LENGTH:
            return False

        # Take exactly one 16-frame snapshot.
        frames_copy = list(
            frame_buffer
        )[:CLIP_LENGTH]

        sequence = next_sequence
        next_sequence += 1

        # Remove ONLY the stride.
        #
        # This is the critical difference from the previous implementation,
        # which called frame_buffer.clear().
        for _ in range(CLIP_STRIDE):
            frame_buffer.popleft()

        future = inference_executor.submit(
            execute_inference,
            sequence,
            frames_copy,
            config["mode"],
            model_api,
            SAVE_TEST_VIDEOS,
        )

        inference_futures[sequence] = future

        logger.info(
            "Submitted inference #%d "
            "window=%d frames "
            "remaining_buffer=%d "
            "in_flight=%d",
            sequence,
            len(frames_copy),
            len(frame_buffer),
            len(inference_futures),
        )

        del frames_copy

        return True

    # Helper: collect completed inference jobs

    def collect_completed_inferences():
        """
        Move completed inference futures into pending_results.

        Does NOT send them.

        Sending is handled separately so that results can be emitted
        strictly in sequence order.
        """

        completed = []

        for sequence, future in list(
            inference_futures.items()
        ):
            if not future.done():
                continue

            completed.append(sequence)

            try:
                result_sequence, result = future.result()

                pending_results[result_sequence] = result

                logger.info(
                    "Inference #%d completed.",
                    result_sequence,
                )

            except Exception:
                logger.exception(
                    "Failed collecting inference #%d.",
                    sequence,
                )

                pending_results[sequence] = {
                    "error": "Inference failed",
                    "sequence": sequence,
                }

        for sequence in completed:
            inference_futures.pop(
                sequence,
                None,
            )

    # Helper: send completed results IN ORDER

    async def flush_results_in_order():
        """
        Send every contiguous completed result starting at
        next_result_sequence.

        Example:

            completed:
                #2
                #0

            Do not send #2.

            Send:
                #0

            If #1 later completes:

                #1
                #2

            Then send:
                #1
                #2
        """

        nonlocal next_result_sequence

        while (
            next_result_sequence
            in pending_results
        ):
            sequence = next_result_sequence

            result = pending_results.pop(
                sequence
            )

            logger.info(
                "Sending inference #%d "
                "to client.",
                sequence,
            )

            if isinstance(result, dict):
                result = dict(result)

                # Sequence is useful for client-side debugging.
                result["sequence"] = sequence

            await send_inference_result(
                ws,
                result,
                config["mode"],
            )

            next_result_sequence += 1

    # Helper: wait for all inference jobs

    async def drain_inferences():
        """
        Wait for all outstanding inference jobs and send their results
        in sequence order.
        """

        while inference_futures:

            # Wait until at least one future completes.
            await asyncio.sleep(0.005)

            collect_completed_inferences()

            await flush_results_in_order()

    # Connection loop

    try:
        while True:

            # Collect inference jobs that completed since the last iteration.
            #
            # This is non-blocking.

            collect_completed_inferences()

            await flush_results_in_order()

            # Receive next WebSocket message.

            try:
                event = await asyncio.wait_for(
                    ws.receive(),
                    timeout=RECEIVE_TIMEOUT,
                )

            except asyncio.TimeoutError:
                logger.info(
                    "WebSocket idle timeout; closing."
                )
                break

            # receive() yields text events, binary events and the
            # disconnect event (receive_text() used to raise
            # WebSocketDisconnect for the latter; receive() does not).

            if event["type"] == "websocket.disconnect":
                logger.info(
                    "WebSocket client disconnected."
                )
                break

            frame_bytes = event.get("bytes")
            message = event.get("text")

            if frame_bytes is not None:
                log_ws_received(
                    "binary JPEG frame",
                    len(frame_bytes),
                )

                # Binary messages are always frames, never control messages.
                message_data = None

            else:
                # Basic message validation

                if not isinstance(message, str) or not message:
                    continue

                if len(message) > MAX_MESSAGE_CHARS:
                    logger.warning(
                        "Dropping oversized message (%d chars).",
                        len(message),
                    )

                    await send_json(
                        ws,
                        {
                            "error": "Message too large",
                        },
                    )

                    continue

                message_size = len(message.encode("utf-8"))

                logger.info(
                    "[WS RX] text message: %s (%d bytes)",
                    _format_bytes(message_size),
                    message_size,
                )

                # Parse possible control message.

                try:
                    message_data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    message_data = None

            # Configuration

            if (
                isinstance(message_data, dict)
                and message_data.get("type") == "config"
            ):
                if await handle_config_message(
                    message,
                    config,
                    ws,
                ):
                    continue

            # End of stream

            if (
                isinstance(message_data, dict)
                and message_data.get("type") == "end"
            ):
                logger.info(
                    "End-of-stream received "
                    "after %d frames.",
                    frame_count,
                )

                # Finish the last MediaPipe job.

                if landmark_future is not None:
                    try:
                        frame_buffer.append(
                            await asyncio.wrap_future(
                                landmark_future
                            )
                        )

                    except Exception:
                        logger.exception(
                            "Final landmark processing failed."
                        )

                    finally:
                        landmark_future = None

                # Submit any remaining complete sliding windows.
                #
                # Because all frames have now arrived, keep submitting
                # windows while at least 16 frames remain.

                while len(frame_buffer) >= CLIP_LENGTH:
                    submit_inference_window()

                # Wait for every outstanding inference.

                await drain_inferences()

                # If there are leftover frames, they are insufficient for
                # another 16-frame inference window.

                if frame_buffer:
                    logger.info(
                        "End-of-stream left %d frames "
                        "without a complete window.",
                        len(frame_buffer),
                    )

                await send_json(
                    ws,
                    {
                        "status": "complete",
                        "frames": frame_count,
                        "inferences": next_sequence,
                    },
                )

                break

            # Everything after this point should be a frame.

            now = loop.time()

            if (
                now - last_receive_time
                < FRAME_DELAY
            ):
                continue

            last_receive_time = now

            # Decode frame
            #
            # decode_incoming_frame() is the transport/input boundary:
            # it is the only place that knows about jpeg_binary /
            # json_base64 / h264 / h265. Everything after this point
            # (process_frame, BufferedFrame, sliding-window inference)
            # is unchanged and transport-agnostic.

            try:
                raw_image = decode_incoming_frame(
                    frame_bytes,
                    message,
                    config["transport"],
                )

            except TransportNotImplementedError as exc:
                logger.warning(
                    "Rejected frame for unimplemented transport: %s",
                    exc,
                )

                await send_json(
                    ws,
                    {
                        "error": "Transport not implemented",
                        "transport": config["transport"],
                    },
                )

                continue

            except ValueError as exc:
                logger.warning(
                    "Frame decode error: %s",
                    exc,
                )

                await send_json(
                    ws,
                    {
                        "error": "Invalid frame",
                    },
                )

                continue

            frame_count += 1

            # Collect completed MediaPipe job.

            collect_landmark_result()

            # Submit MediaPipe processing on its own dedicated executor.
            #
            # Keep one landmark job in flight so frame order remains stable.

            if landmark_future is None:
                landmark_future = landmark_executor.submit(
                    process_frame,
                    raw_image,
                    pose_detector,
                    hand_detector,
                    landmarks_enabled,
                    RESIZE_DIM,
                )

            del raw_image

            # Collect any inference that completed while this frame was
            # being processed.

            collect_completed_inferences()

            await flush_results_in_order()

            # IMPORTANT:
            #
            # We can only add processed frames to frame_buffer when the
            # MediaPipe job completes.
            #
            # Therefore the inference submission happens after collecting
            # the landmark result above.

            while (
                len(frame_buffer) >= CLIP_LENGTH
            ):
                # Backpressure.
                #
                # Do not allow unlimited inference jobs to accumulate.
                # Once MAX_CONCURRENT_INFERENCES are running, wait for one
                # to complete before creating another window.

                if (
                    len(inference_futures)
                    >= MAX_CONCURRENT_INFERENCES
                ):
                    break

                submit_inference_window()

            # Periodic garbage collection

            if frame_count % 100 == 0:
                gc.collect()

    except WebSocketDisconnect:
        logger.info(
            "WebSocket client disconnected."
        )

    except asyncio.CancelledError:
        logger.info(
            "WebSocket handler cancelled."
        )
        raise

    except Exception as exc:
        logger.exception(
            "WebSocket closed unexpectedly: %s",
            exc,
        )

    finally:
        # Cancel outstanding inference jobs

        for sequence, future in list(
            inference_futures.items()
        ):
            if not future.done():
                logger.info(
                    "Cancelling inference #%d.",
                    sequence,
                )

                future.cancel()

        inference_futures.clear()
        pending_results.clear()

        # Cancel / finish MediaPipe job

        if landmark_future is not None:

            if not landmark_future.cancel():

                try:
                    await asyncio.wrap_future(
                        landmark_future
                    )

                except asyncio.CancelledError:
                    pass

                except Exception:
                    logger.exception(
                        "Landmark processing failed "
                        "during disconnect."
                    )

        # Close MediaPipe detectors

        if pose_detector:
            try:
                pose_detector.close()
            except Exception:
                logger.exception(
                    "Failed to close pose detector."
                )

        if hand_detector:
            try:
                hand_detector.close()
            except Exception:
                logger.exception(
                    "Failed to close hand detector."
                )

        # Release connection state

        frame_buffer.clear()

        if (
            ws.application_state
            == WebSocketState.CONNECTED
        ):
            try:
                await ws.close()
            except Exception:
                pass

        logger.info(
            "WebSocket client disconnected",
        )