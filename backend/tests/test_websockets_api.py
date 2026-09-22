#!/usr/bin/env python3

import argparse
import asyncio
import base64
import json
from pathlib import Path

import cv2
import websockets
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration

CLIP_LENGTH = 16

DEFAULT_VIDEO = "temp/test.mov"

JWT_TOKEN = os.getenv("JWT_TOKEN")

if not JWT_TOKEN:
    raise RuntimeError(
        "JWT_TOKEN is missing. Add it to the .env file."
    )

DEFAULT_URL = (
    "ws://127.0.0.1:5000/ws"
    f"?token={JWT_TOKEN}"
)


# WebSocket test

async def test_websocket(
    ws_url: str,
    video_path: str,
    fps: float | None = None,
):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open video: {video_path}"
        )

    video_fps = cap.get(cv2.CAP_PROP_FPS)

    if not video_fps or video_fps <= 0:
        video_fps = 30.0

    if fps is None:
        fps = video_fps

    frame_delay = 1.0 / fps

    print("=" * 70)
    print("SIGNBRIDGE WEBSOCKET END-TO-END TEST")
    print("=" * 70)
    print(f"WebSocket : {ws_url[:100]}...")
    print(f"Video     : {video_path}")
    print(f"Video FPS : {video_fps:.2f}")
    print(f"Send FPS  : {fps:.2f}")
    print(f"Clip size : {CLIP_LENGTH} frames")
    print("=" * 70)
    print()

    frame_count = 0
    response_count = 0

    inference_results = []
    server_messages = []

    connection_ready = asyncio.Event()
    inference_received = asyncio.Event()
    receiver_finished = asyncio.Event()

    # Connect

    async with websockets.connect(
        ws_url,
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=5,
    ) as websocket:

        print("[1/5] WebSocket connected")

        # Receiver

        async def receive_messages():
            nonlocal response_count

            try:
                async for message in websocket:

                    response_count += 1

                    print()
                    print("-" * 70)
                    print(f"SERVER RESPONSE #{response_count}")
                    print("-" * 70)

                    if isinstance(message, bytes):
                        print(
                            f"Binary response: "
                            f"{len(message)} bytes"
                        )
                        continue

                    try:
                        data = json.loads(message)

                    except json.JSONDecodeError:
                        print("Raw:")
                        print(message)
                        continue

                    server_messages.append(data)

                    print(
                        json.dumps(
                            data,
                            indent=2,
                            ensure_ascii=False,
                        )
                    )

                    # Connection

                    if data.get("status") == "connected":
                        connection_ready.set()

                    # Error

                    if "error" in data:
                        print()
                        print(
                            f"[SERVER ERROR] "
                            f"{data['error']}"
                        )

                    # MediaPipe information

                    if data.get("status") == "info":
                        print()
                        print(
                            "[SERVER INFO] "
                            f"{data.get('message', '')}"
                        )

                    # Actual model result
                    #
                    # Current handler sends:
                    #
                    # {
                    #     "label": "...",
                    #     "confidence": ...
                    # }

                    if (
                        "label" in data
                        or "prediction" in data
                    ):
                        inference_results.append(data)

                        print()
                        print(
                            "=" * 70
                        )
                        print(
                            "MODEL INFERENCE RESULT"
                        )
                        print(
                            "=" * 70
                        )

                        label = data.get(
                            "label",
                            data.get(
                                "prediction",
                                "",
                            ),
                        )

                        confidence = data.get(
                            "confidence"
                        )

                        print(
                            f"Prediction : {label}"
                        )

                        if confidence is not None:
                            try:
                                confidence = float(
                                    confidence
                                )
                                print(
                                    f"Confidence : "
                                    f"{confidence:.4f} "
                                    f"({confidence * 100:.2f}%)"
                                )
                            except (
                                TypeError,
                                ValueError,
                            ):
                                print(
                                    f"Confidence : "
                                    f"{confidence}"
                                )

                        inference_received.set()

            except websockets.exceptions.ConnectionClosed as e:
                print()
                print(
                    f"Receiver: WebSocket closed "
                    f"(code={e.code}, reason={e.reason})"
                )

            finally:
                receiver_finished.set()

        receiver = asyncio.create_task(
            receive_messages()
        )

        try:

            # Wait for authentication / connection message

            print(
                "[2/5] Waiting for server authentication..."
            )

            try:
                await asyncio.wait_for(
                    connection_ready.wait(),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "Server did not send the expected "
                    "'connected' response."
                )

            print(
                "[OK] Server authenticated the client"
            )

            # Send video frames

            print()
            print("[3/5] Streaming video frames")
            print()

            while True:

                ret, frame = cap.read()

                if not ret:
                    break

                # JPEG encode
                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [
                        cv2.IMWRITE_JPEG_QUALITY,
                        85,
                    ],
                )

                if not success:
                    print(
                        f"\nCould not encode frame "
                        f"{frame_count + 1}"
                    )
                    continue

                # JPEG -> base64
                frame_base64 = base64.b64encode(
                    encoded.tobytes()
                ).decode("utf-8")

                # This matches the server protocol:
                #
                # data.get("frame", "")

                payload = {
                    "frame": frame_base64,
                }

                try:
                    await websocket.send(
                        json.dumps(payload)
                    )

                except websockets.exceptions.ConnectionClosed as e:
                    print()
                    print(
                        "Server closed connection while "
                        f"sending frame: "
                        f"code={e.code}, reason={e.reason}"
                    )
                    break

                frame_count += 1

                print(
                    f"\rFrames sent: "
                    f"{frame_count:4d}",
                    end="",
                    flush=True,
                )

                # Important:
                #
                # Give the server time to process frames.

                await asyncio.sleep(
                    frame_delay
                )

            cap.release()

            print()
            print()
            print(
                f"[OK] Finished sending "
                f"{frame_count} frames"
            )

            print(
                "[3.5/5] Sending end-of-stream..."
            )

            await websocket.send(
                json.dumps(
                    {
                        "type": "end",
                    }
                )
            )

            print(
                "[OK] End-of-stream sent"
            )

            # Wait for model inference

            print()
            print(
                "[4/5] Waiting for model inference..."
            )

            if frame_count < CLIP_LENGTH:
                print(
                    f"[WARNING] Only {frame_count} frames "
                    f"were sent. "
                    f"The model requires "
                    f"{CLIP_LENGTH}."
                )

            try:

                await asyncio.wait_for(
                    inference_received.wait(),
                    timeout=120,
                )

            except asyncio.TimeoutError:

                print()
                print(
                    "[TIMEOUT] No model inference result "
                    "received within 120 seconds."
                )

        finally:

            cap.release()

            # Give receiver a moment to process the final
            # message before closing.

            if inference_received.is_set():
                await asyncio.sleep(0.2)

            if not receiver.done():
                receiver.cancel()

            try:
                await receiver
            except asyncio.CancelledError:
                pass

    # Save server responses

    output_file = Path(
        "websocket_results.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            server_messages,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # Final report

    print()
    print()
    print("=" * 70)
    print("END-TO-END TEST RESULT")
    print("=" * 70)

    print(
        f"Authentication : PASS"
        if connection_ready.is_set()
        else "Authentication : FAIL"
    )

    print(
        f"Frames sent    : {frame_count}"
    )

    print(
        f"Server replies : {response_count}"
    )

    print(
        f"Inference      : "
        f"{'PASS' if inference_results else 'FAIL'}"
    )

    print(
        f"Saved responses: {output_file}"
    )

    # Print all inference results

    if inference_results:

        print()
        print("=" * 70)
        print("INFERENCE RESULTS")
        print("=" * 70)

        for i, result in enumerate(
            inference_results,
            start=1,
        ):
            print()
            print(
                f"Result #{i}"
            )

            print(
                json.dumps(
                    result,
                    indent=2,
                    ensure_ascii=False,
                )
            )

    else:

        print()
        print("=" * 70)
        print("NO MODEL RESULT RECEIVED")
        print("=" * 70)

        print(
            """
The WebSocket connection and frame streaming succeeded,
but the server did not return a model prediction.

Check the backend terminal for:

    - model_api.check_health()
    - frame decoding errors
    - landmark processing errors
    - model inference errors
    - Hugging Face/API errors
    - executor/inference exceptions
"""
        )


# CLI

def main():

    parser = argparse.ArgumentParser(
        description=(
            "End-to-end SignBridge WebSocket "
            "model inference test."
        )
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=(
            "WebSocket URL including Firebase "
            "ID token."
        ),
    )

    parser.add_argument(
        "--video",
        default=DEFAULT_VIDEO,
        help="Input video.",
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help=(
            "Frame sending FPS. "
            "Defaults to video FPS."
        ),
    )

    args = parser.parse_args()

    asyncio.run(
        test_websocket(
            ws_url=args.url,
            video_path=args.video,
            fps=args.fps,
        )
    )


if __name__ == "__main__":
    main()