"""
SignBridge WebSocket Handler
============================

Handles the WebSocket connection lifecycle.

Responsibilities:

- Firebase token authentication
- Connection initialization
- WebSocket message receiving
- Configuration commands
- Frame-rate limiting
- Frame decoding
- Frame buffering
- Scheduling background processing
- Returning inference results
- Cleanup

Heavy processing is delegated to websocket_processing.py.
"""

import base64
import concurrent.futures
import gc
import json
import logging
import time
from collections import deque
from io import BytesIO

from PIL import Image

from firebase_admin_init import admin_auth

from websocket_processing import (
    MEDIAPIPE_OK,
    build_landmarkers,
    process_frame,
    run_inference,
)


logger = logging.getLogger(__name__)


# WebSocket configuration

CLIP_LENGTH = 16

# Approximately 12.5 incoming frames/sec.
FRAME_DELAY = 0.08

RESIZE_DIM = 224

SAVE_TEST_VIDEOS = False


# Helpers

def authenticate_websocket(token: str):
    """
    Verify a Firebase ID token.

    Returns:
        Firebase decoded token on success.
        None on failure.
    """

    if not token:
        return None

    try:
        return admin_auth.verify_id_token(token)

    except Exception:
        return None


def send_json(ws, payload: dict):
    """
    Safely serialize and send a JSON WebSocket message.
    """

    ws.send(
        json.dumps(payload)
    )


def decode_frame(message: str):
    """
    Decode an incoming WebSocket message into a PIL RGB image.

    Expected format:

        {
            "frame": "<base64 encoded image>"
        }

    Returns:
        PIL.Image.Image

    Raises:
        ValueError
    """

    try:
        data = json.loads(message)

    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            "Invalid JSON message"
        ) from exc

    b64 = data.get("frame", "")

    if not b64:
        raise ValueError(
            "Missing frame"
        )

    try:
        image_bytes = base64.b64decode(
            b64,
            validate=True,
        )

    except Exception as exc:
        raise ValueError(
            "Invalid base64 frame"
        ) from exc

    try:
        image = Image.open(
            BytesIO(image_bytes)
        ).convert("RGB")

    except Exception as exc:
        raise ValueError(
            "Invalid image"
        ) from exc

    finally:
        del image_bytes

    return image


def handle_config_message(
    message: str,
    config: dict,
    ws,
) -> bool:
    """
    Process a possible configuration message.

    Returns:

        True
            Message was a configuration command.

        False
            Message was not a configuration command.
    """

    try:
        data = json.loads(message)

    except Exception:
        return False

    if data.get("type") != "config":
        return False

    new_mode = data.get("mode")

    if new_mode in (
        "frames",
        "video",
        "hybrid",
    ):

        config["mode"] = new_mode

        send_json(
            ws,
            {
                "status": "config_updated",
                "mode": new_mode,
            },
        )

        logger.info(
            "Inference mode → %s",
            new_mode,
        )

    return True


def send_inference_result(
    ws,
    result: dict,
    mode: str,
) -> bool:
    """
    Process and send a completed inference result.

    Returns:
        True if a prediction label was sent.
        False otherwise.
    """

    if not result:
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

    try:
        confidence = float(
            result.get(
                "confidence",
                0.0,
            )
        )

    except (TypeError, ValueError):
        confidence = 0.0

    logger.info(
        "[%s] %s %.0f%% | total=%.0fms hf=%.0fms",
        mode.upper(),
        label,
        confidence * 100,
        float(
            result.get(
                "total_latency_ms",
                0,
            )
        ),
        float(
            result.get(
                "inference_time_ms",
                0,
            )
        ),
    )

    if not label:
        return False

    send_json(
        ws,
        {
            "label": label,
            "confidence": confidence,
        },
    )

    return True


# Main WebSocket controller

def handle_websocket(
    ws,
    model_api,
    executor: concurrent.futures.ThreadPoolExecutor,
):
    """
    Main WebSocket connection controller.

    This function owns the WebSocket lifecycle but delegates actual
    image/model processing to websocket_processing.py.
    """

    # Authentication

    # Current client protocol sends the Firebase token as:
    #
    #     /ws?token=<firebase_id_token>
    #
    token = getattr(
        ws,
        "request",
        None,
    )

    # flask-sock exposes the Flask request through the active Flask context,
    # so import request here rather than making Flask a module-level
    # dependency.
    from flask import request

    token = request.args.get(
        "token",
        "",
    )

    decoded = authenticate_websocket(
        token
    )

    if decoded is None:

        send_json(
            ws,
            {
                "error": "Unauthorized"
            },
        )

        try:
            ws.close()
        except Exception:
            pass

        return

    user_id = decoded["uid"]

    logger.info(
        "WebSocket client connected: %s",
        user_id,
    )

    send_json(
        ws,
        {
            "status": "connected",
            "message": "Ready for frames",
        },
    )

    # MediaPipe status

    if not MEDIAPIPE_OK:

        send_json(
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
        "mode": "frames"
    }

    frame_buffer = deque(
        maxlen=CLIP_LENGTH
    )

    last_receive_time = 0.0

    last_prediction_future = None

    landmark_future = None

    frame_count = 0

    # MediaPipe detectors

    pose_detector, hand_detector = (
        build_landmarkers()
    )

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

        send_json(
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
            "Remote model API not ready — "
            "predictions may fail."
        )

    else:

        logger.info(
            "Remote model API healthy."
        )

    # Connection loop

    try:

        while True:

            # Wait for client message

            message = ws.receive(
                timeout=30
            )

            if not message:
                break

            # Configuration message

            if handle_config_message(
                message,
                config,
                ws,
            ):
                continue

            # Frame rate limiter

            now = time.monotonic()

            if (
                now - last_receive_time
                < FRAME_DELAY
            ):
                continue

            last_receive_time = now

            # Decode incoming frame

            try:

                raw_image = decode_frame(
                    message
                )

            except ValueError as exc:

                logger.warning(
                    "Frame decode error: %s",
                    exc,
                )

                send_json(
                    ws,
                    {
                        "error": "Invalid frame"
                    },
                )

                continue

            frame_count += 1

            # Collect completed landmark job

            if (
                landmark_future is not None
                and landmark_future.done()
            ):

                try:

                    processed_image = (
                        landmark_future.result()
                    )

                    resized = processed_image.resize(
                        (
                            RESIZE_DIM,
                            RESIZE_DIM,
                        )
                    )

                    del processed_image

                    frame_buffer.append(
                        resized
                    )

                except Exception:

                    logger.exception(
                        "Landmark processing failed."
                    )

                finally:

                    landmark_future = None

            # Submit landmark processing

            if landmark_future is None:

                landmark_future = executor.submit(
                    process_frame,
                    raw_image,
                    pose_detector,
                    hand_detector,
                    landmarks_enabled,
                )

            # The worker now owns the image reference.
            del raw_image

            # Collect completed inference

            if (
                last_prediction_future is not None
                and last_prediction_future.done()
            ):

                try:

                    result = (
                        last_prediction_future.result()
                    )

                    prediction_sent = (
                        send_inference_result(
                            ws,
                            result,
                            config["mode"],
                        )
                    )

                    if prediction_sent:
                        frame_buffer.clear()

                except Exception:

                    logger.exception(
                        "Inference result handling failed."
                    )

                finally:

                    last_prediction_future = None

            # Start inference when enough frames exist

            if (
                len(frame_buffer)
                == CLIP_LENGTH
                and last_prediction_future is None
            ):

                frames_copy = list(
                    frame_buffer
                )

                frame_buffer.clear()

                last_prediction_future = (
                    executor.submit(
                        run_inference,
                        frames_copy,
                        config["mode"],
                        model_api,
                        SAVE_TEST_VIDEOS,
                    )
                )

                del frames_copy

            # Periodic garbage collection

            if frame_count % 100 == 0:
                gc.collect()

    except Exception as exc:

        logger.warning(
            "WebSocket closed: %s",
            exc,
        )

    finally:

        # Cancel pending inference

        if last_prediction_future is not None:

            last_prediction_future.cancel()

        # Finish/cancel pending landmark processing

        if landmark_future is not None:

            if not landmark_future.cancel():

                try:

                    landmark_future.result()

                except Exception:

                    logger.exception(
                        "Landmark processing failed during disconnect."
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

        logger.info(
            "WebSocket client disconnected: %s",
            user_id,
        )