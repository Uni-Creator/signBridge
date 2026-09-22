"""

SignBridge WebSocket Handler (FastAPI / asyncio)

=================================================



Handles the WebSocket connection lifecycle.



Responsibilities:



- Firebase token authentication

- Connection initialization

- WebSocket message receiving

- Input validation (size, type, and structure checks on every message

  and frame before any of it is trusted or processed)

- Configuration commands

- Frame-rate limiting

- Frame decoding

- Frame buffering

- Scheduling background processing

- Returning inference results

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

from starlette.websockets import WebSocketState



from fastapi import WebSocket, WebSocketDisconnect



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



# How long to wait for a client message before treating the connection as

# idle. Equivalent to flask-sock's `ws.receive(timeout=30)`.

RECEIVE_TIMEOUT = 30.0



# Input validation limits. Nothing coming off the socket is trusted until

# it passes these - size caps first (cheap), then type/structure checks,

# then content checks (base64, image decode, dimensions).

MAX_MESSAGE_CHARS = 2_000_000       # raw text message cap

MAX_FRAME_B64_CHARS = 2_000_000     # base64 "frame" field cap

MAX_IMAGE_DIMENSION = 4096          # reject absurdly large/decompression-bomb images

MIN_IMAGE_DIMENSION = 1

VALID_CONFIG_MODES = ("frames", "video", "hybrid")





# Helpers



def authenticate_websocket(token: str):

    """

    Verify a Firebase ID token.



    Returns:

        Firebase decoded token on success.

        None on failure.

    """



    if not isinstance(token, str) or not token:

        return None



    try:

        return admin_auth.verify_id_token(token)



    except Exception:

        return None





async def send_json(ws: WebSocket, payload: dict):

    """

    Safely serialize and send a JSON WebSocket message.

    """



    if ws.application_state != WebSocketState.CONNECTED:

        return



    try:

        await ws.send_text(json.dumps(payload))

    except Exception:

        logger.exception("Failed to send WebSocket message.")





def decode_frame(message: str):

    """

    Decode an incoming WebSocket message into a PIL RGB image.



    Expected format:



        {

            "frame": "<base64 encoded image>"

        }



    Every step below validates one thing before trusting the next:

    message type/size -> JSON structure -> field type/size -> base64

    validity -> image validity -> image dimensions.



    Returns:

        PIL.Image.Image



    Raises:

        ValueError

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

        raise ValueError(

            "Invalid JSON message"

        ) from exc



    if not isinstance(data, dict):

        raise ValueError(

            "Message must be a JSON object"

        )



    b64 = data.get("frame", "")



    if not isinstance(b64, str):

        raise ValueError(

            "frame must be a string"

        )



    if not b64:

        raise ValueError(

            "Missing frame"

        )



    if len(b64) > MAX_FRAME_B64_CHARS:

        raise ValueError(

            "frame payload too large"

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



    if not image_bytes:

        raise ValueError(

            "Empty frame payload"

        )



    # Structural check first (cheap, doesn't fully decode pixel data),

    # then a real decode. Image.verify() invalidates the file object for

    # further use, so a fresh handle is opened for the actual decode.

    try:

        probe = Image.open(BytesIO(image_bytes))

        probe.verify()



    except (UnidentifiedImageError, Exception) as exc:

        raise ValueError(

            "Invalid image"

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





async def handle_config_message(

    message: str,

    config: dict,

    ws: WebSocket,

) -> bool:

    """

    Process a possible configuration message.



    Returns:



        True

            Message was a configuration command (valid or not - the

            caller should not fall through to frame decoding either way).



        False

            Message was not a configuration command.

    """



    try:

        data = json.loads(message)



    except Exception:

        return False



    if not isinstance(data, dict):

        return False



    if data.get("type") != "config":

        return False



    new_mode = data.get("mode")



    if isinstance(new_mode, str) and new_mode in VALID_CONFIG_MODES:



        config["mode"] = new_mode



        await send_json(

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



    else:



        logger.warning(

            "Rejected invalid config mode: %r",

            new_mode,

        )



        await send_json(

            ws,

            {

                "error": "Invalid config",

                "field": "mode",

            },

        )



    return True





async def send_inference_result(

    ws: WebSocket,

    result: dict,

    mode: str,

) -> bool:

    """

    Process and send a completed inference result.



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



    await send_json(

        ws,

        {

            "label": label,

            "confidence": confidence,

        },

    )



    return True





async def run_inference_and_send(

    ws: WebSocket,

    loop: asyncio.AbstractEventLoop,

    executor: concurrent.futures.ThreadPoolExecutor,

    frames: list,

    mode: str,

    model_api,

    save_test_videos: bool,

) -> None:

    """

    Run inference on the executor and push the result to the client as

    soon as it's ready, without depending on another incoming frame.



    This coroutine is scheduled with `asyncio.ensure_future` and does the

    awaiting itself, so the send back to the client happens on the

    event loop rather than from a background thread.

    """



    try:

        result = await loop.run_in_executor(

            executor,

            run_inference,

            frames,

            mode,

            model_api,

            save_test_videos,

        )



        logger.info(

            "Inference future completed: %s",

            result,

        )



        await send_inference_result(

            ws,

            result,

            mode,

        )



    except asyncio.CancelledError:

        logger.info(

            "Inference task was cancelled."

        )

        raise



    except Exception:

        logger.exception(

            "Inference task failed."

        )





# Main WebSocket controller



async def handle_websocket(
    ws: WebSocket,
    model_api,
    executor: concurrent.futures.ThreadPoolExecutor,
):
    """
    Main WebSocket connection controller.

    Landmark and inference work are submitted directly to the supplied
    executor. The connection loop keeps at most one pending landmark job,
    while inference is allowed to run independently.
    """
    loop = asyncio.get_running_loop()

    # Authentication
    authorization = ws.headers.get("Authorization")

    if not authorization or not isinstance(authorization, str):
        await ws.close(
            code=1008,
            reason="Missing Authorization header",
        )
        return

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        await ws.close(
            code=1008,
            reason="Invalid Authorization header",
        )
        return

    decoded = authenticate_websocket(token)

    if decoded is None:
        await ws.accept()
        await send_json(ws, {"error": "Unauthorized"})
        try:
            await ws.close()
        except Exception:
            pass
        return

    await ws.accept()

    user_id = decoded["uid"]
    logger.info("WebSocket client connected")

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
    config = {"mode": "frames"}

    frame_buffer = deque(maxlen=CLIP_LENGTH)
    last_receive_time = 0.0

    # These are concurrent.futures.Future objects returned directly by
    # executor.submit(). Keeping the native Future here is important:
    # .done(), .result(), and .cancel() are deterministic and do not depend
    # on an asyncio callback having run between two WebSocket messages.
    last_prediction_future = None
    landmark_future = None

    frame_count = 0

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
        logger.exception("Model API health check failed.")
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
            "Remote model API not ready — predictions may fail."
        )
    else:
        logger.info("Remote model API healthy.")

    def collect_landmark_result():
        """
        Collect a completed landmark job.

        This is synchronous because the Future is already complete when
        called. Exceptions are caught so one failed MediaPipe job cannot
        terminate the WebSocket stream.
        """
        nonlocal landmark_future

        if landmark_future is None or not landmark_future.done():
            return

        try:
            processed_image = landmark_future.result()
            resized = processed_image.resize(
                (RESIZE_DIM, RESIZE_DIM)
            )
            frame_buffer.append(resized)

            logger.info(
                "Landmark job collected. Buffered frames: %d/%d",
                len(frame_buffer),
                CLIP_LENGTH,
            )

            del processed_image
            del resized

        except Exception:
            logger.exception("Landmark processing failed.")
        finally:
            landmark_future = None

    def collect_inference_result():
        """
        Collect a completed inference job and send its result.

        Returns True when a completed future was consumed.
        """
        nonlocal last_prediction_future

        if (
            last_prediction_future is None
            or not last_prediction_future.done()
        ):
            return False

        future = last_prediction_future
        last_prediction_future = None

        try:
            result = future.result()
            logger.info("Inference future completed: %s", result)

            # Return an asyncio task to the caller because sending over the
            # WebSocket is asynchronous.
            return result

        except Exception:
            logger.exception("Inference task failed.")
            return {"error": "Inference failed"}

    # Connection loop
    try:
        while True:
            # Collect work completed since the previous message before
            # waiting for the next message. This lets inference results be
            # delivered without requiring a special extra worker thread.
            inference_result = collect_inference_result()
            if inference_result is not False:
                if inference_result.get("error") == "Inference failed":
                    logger.error("Inference task failed.")
                else:
                    await send_inference_result(
                        ws,
                        inference_result,
                        config["mode"],
                    )

            try:
                message = await asyncio.wait_for(
                    ws.receive_text(),
                    timeout=RECEIVE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.info("WebSocket idle timeout; closing.")
                break
            except WebSocketDisconnect:
                break

            # Type/size gate before anything else touches the message.
            if not isinstance(message, str) or not message:
                continue

            if len(message) > MAX_MESSAGE_CHARS:
                logger.warning(
                    "Dropping oversized message (%d chars).",
                    len(message),
                )
                await send_json(
                    ws,
                    {"error": "Message too large"},
                )
                continue

            # Parse control messages before attempting frame decoding.
            try:
                message_data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                message_data = None

            # Configuration message
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

            # End-of-stream message
            if (
                isinstance(message_data, dict)
                and message_data.get("type") == "end"
            ):
                logger.info(
                    "End-of-stream received after %d frames.",
                    frame_count,
                )

                # Finish the last pending landmark job.
                if landmark_future is not None:
                    try:
                        processed_image = await asyncio.wrap_future(
                            landmark_future
                        )
                        resized = processed_image.resize(
                            (RESIZE_DIM, RESIZE_DIM)
                        )
                        frame_buffer.append(resized)

                        del processed_image
                        del resized

                        logger.info(
                            "Final landmark job collected. "
                            "Buffered frames: %d/%d",
                            len(frame_buffer),
                            CLIP_LENGTH,
                        )
                    except Exception:
                        logger.exception(
                            "Final landmark processing failed."
                        )
                    finally:
                        landmark_future = None

                # Submit final inference if a complete clip exists and no
                # previous inference is still running.
                if (
                    len(frame_buffer) >= CLIP_LENGTH
                    and last_prediction_future is None
                ):
                    frames_copy = list(frame_buffer)
                    frame_buffer.clear()

                    logger.info(
                        "Submitting final inference with %d frames.",
                        len(frames_copy),
                    )

                    last_prediction_future = executor.submit(
                        run_inference,
                        frames_copy,
                        config["mode"],
                        model_api,
                        SAVE_TEST_VIDEOS,
                    )

                    del frames_copy

                # Wait for the final inference and send its result.
                if last_prediction_future is not None:
                    try:
                        result = await asyncio.wrap_future(
                            last_prediction_future
                        )
                        await send_inference_result(
                            ws,
                            result,
                            config["mode"],
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Final inference failed.")
                        await send_json(
                            ws,
                            {"error": "Inference failed"},
                        )
                    finally:
                        last_prediction_future = None
                else:
                    logger.warning(
                        "End-of-stream received with only %d/%d frames.",
                        len(frame_buffer),
                        CLIP_LENGTH,
                    )
                    await send_json(
                        ws,
                        {
                            "error": "Not enough frames for inference",
                            "frames": len(frame_buffer),
                            "required": CLIP_LENGTH,
                        },
                    )

                break

            # Everything that reaches this point is expected to be a frame.

            # Frame-rate limiter
            now = loop.time()

            if now - last_receive_time < FRAME_DELAY:
                continue

            last_receive_time = now

            # Decode incoming frame.
            try:
                raw_image = decode_frame(message)
            except ValueError as exc:
                logger.warning("Frame decode error: %s", exc)
                await send_json(
                    ws,
                    {"error": "Invalid frame"},
                )
                continue

            frame_count += 1

            # Collect the previous landmark job if it has completed.
            # Do this BEFORE submitting the current frame so the worker
            # pipeline remains one job deep and frame order is preserved.
            collect_landmark_result()

            # Submit landmark processing only when there is no previous job
            # still running. A slow job therefore stays pending instead of
            # being discarded, while the next frame can still be received.
            if landmark_future is None:
                landmark_future = executor.submit(
                    process_frame,
                    raw_image,
                    pose_detector,
                    hand_detector,
                    landmarks_enabled,
                )

            del raw_image

            # Collect inference if it completed while this frame was being
            # processed.
            inference_result = collect_inference_result()
            if inference_result is not False:
                if inference_result.get("error") == "Inference failed":
                    logger.error("Inference task failed.")
                else:
                    await send_inference_result(
                        ws,
                        inference_result,
                        config["mode"],
                    )

            # Start inference once exactly CLIP_LENGTH processed frames exist.
            if (
                len(frame_buffer) == CLIP_LENGTH
                and last_prediction_future is None
            ):
                frames_copy = list(frame_buffer)
                frame_buffer.clear()

                logger.info(
                    "Inference submitted with %d frames.",
                    len(frames_copy),
                )

                # Submit run_inference directly so the native executor Future
                # can be inspected/cancelled independently of the WebSocket
                # receive loop.
                last_prediction_future = executor.submit(
                    run_inference,
                    frames_copy,
                    config["mode"],
                    model_api,
                    SAVE_TEST_VIDEOS,
                )

                del frames_copy

            # Periodic garbage collection
            if frame_count % 100 == 0:
                gc.collect()

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (client closed).")

    except Exception as exc:
        logger.warning("WebSocket closed: %s", exc)

    finally:
        # Cancel pending inference.
        if last_prediction_future is not None:
            if not last_prediction_future.done():
                logger.info(
                    "Cancelling pending inference during disconnect."
                )
                last_prediction_future.cancel()

        # Finish/cancel pending landmark processing.
        if landmark_future is not None:
            if not landmark_future.cancel():
                try:
                    await asyncio.wrap_future(landmark_future)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "Landmark processing failed during disconnect."
                    )

        # Close MediaPipe detectors.
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

        # Release connection state.
        frame_buffer.clear()

        if ws.application_state == WebSocketState.CONNECTED:
            try:
                await ws.close()
            except Exception:
                pass

        logger.info("WebSocket client disconnected")
