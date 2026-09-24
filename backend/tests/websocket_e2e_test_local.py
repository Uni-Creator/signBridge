import argparse

import asyncio

import base64

import json

import os

from pathlib import Path

import websockets

from dotenv import load_dotenv



load_dotenv()



# Configuration

CLIP_LENGTH = 16

DEFAULT_FRAMES_DIR = "temp/frames"

DEFAULT_FRAME_PATTERN = "frame_*.jpg"

JWT_TOKEN = os.getenv("JWT_TOKEN")

if not JWT_TOKEN:

    raise RuntimeError(

        "JWT_TOKEN is missing. Add it to the .env file."

    )

DEFAULT_URL = "ws://127.0.0.1:5000/slt/ws"

CONFIG_VERSION = 1

# Must match websocket_handler.py's VALID_TRANSPORTS / UNIMPLEMENTED_TRANSPORTS.

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

# Only these can actually carry frames today.

IMPLEMENTED_TRANSPORTS = (

TRANSPORT_JPEG_BINARY,

TRANSPORT_JSON_BASE64,

)



# WebSocket test

async def test_websocket(

    ws_url: str,

    frames_dir: str,

    frame_pattern: str,

    transport: str,

    fps: float = 12.5,

):

    frames_path = Path(frames_dir)

    if not frames_path.exists():

        raise RuntimeError(

            f"Frames directory does not exist: {frames_path}"

        )

    frame_files = sorted(frames_path.glob(frame_pattern))

    if not frame_files:

        raise RuntimeError(

            f"No frames found in {frames_path} "

            f"matching '{frame_pattern}'"

        )

    print("=" * 70)

    print("SIGNBRIDGE WEBSOCKET END-TO-END TEST")

    print("=" * 70)

    print(f"WebSocket : {ws_url}")

    print(f"Transport : {transport}")

    print(f"Frames    : {frames_path}")

    print(f"Pattern   : {frame_pattern}")

    print(f"Found     : {len(frame_files)} frames")

    print(f"Send FPS  : {fps:.2f}")

    print(f"Clip size : {CLIP_LENGTH} frames")

    print("=" * 70)

    print()

    frame_delay = 1.0 / fps

    frame_count = 0

    response_count = 0

    inference_results = []

    server_messages = []

    connection_ready = asyncio.Event()

    config_acked = asyncio.Event()

    config_error = asyncio.Event()

    config_not_implemented = asyncio.Event()

    inference_received = asyncio.Event()

    config_ack_payload = {}

    # Connect

    async with websockets.connect(

        ws_url,

        additional_headers={

            "Authorization": "Bearer " + JWT_TOKEN,

        },

        max_size=None,

        ping_interval=20,

        ping_timeout=20,

        close_timeout=5,

    ) as websocket:

        print("[1/6] WebSocket connected")

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

                            f"Binary response: {len(message)} bytes"

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

                    # Config handshake ack/nack

                    if data.get("type") == "config_ack":

                        config_ack_payload.update(data)

                        if data.get("status") == "accepted":

                            config_acked.set()

                        else:

                            # "error" or "not_implemented"

                            config_error.set()

                    # Error

                    if "error" in data:

                        print()

                        print(

                            f"[SERVER ERROR] {data['error']}"

                        )

                    # MediaPipe information

                    if data.get("status") == "info":

                        print()

                        print(

                            "[SERVER INFO] "

                            f"{data.get('message', '')}"

                        )

                    # Actual model result

                    if (

                        "label" in data

                        or "prediction" in data

                    ):

                        inference_results.append(data)

                        print()

                        print("=" * 70)

                        print("MODEL INFERENCE RESULT")

                        print("=" * 70)

                        label = data.get(

                            "label",

                            data.get("prediction", ""),

                        )

                        confidence = data.get("confidence")

                        print(f"Prediction : {label}")

                        if confidence is not None:

                            try:

                                confidence = float(confidence)

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

                    "Receiver: WebSocket closed "

                    f"(code={e.code}, reason={e.reason})"

                )

            finally:

                pass

        receiver = asyncio.create_task(

            receive_messages()

        )

        try:

            # Wait for authentication / connection message

            print(

                "[2/6] Waiting for server authentication..."

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

            # Send configuration/handshake BEFORE any frames.

            #

            # This must be sent and acknowledged before any frame is

            # sent, per the versioned transport handshake protocol.

            print()

            print("[3/6] Sending config/handshake...")

            await websocket.send(

                json.dumps(

                    {

                        "type": "config",

                        "version": CONFIG_VERSION,

                        "mode": "frames",

                        "transport": transport,

                    }

                )

            )

            try:

                await asyncio.wait_for(

                    asyncio.wait(

                        [

                            asyncio.ensure_future(config_acked.wait()),

                            asyncio.ensure_future(config_error.wait()),

                            asyncio.ensure_future(config_not_implemented.wait()),

                        ],

                        return_when=asyncio.FIRST_COMPLETED,

                    ),

                    timeout=10,

                )

            except asyncio.TimeoutError:

                raise RuntimeError(

                    "Server did not respond to the config/handshake "

                    "(no config_ack received)."

                )

            if config_not_implemented.is_set():

                print(

                    "[HANDSHAKE NOT IMPLEMENTED] "

                    f"{json.dumps(config_ack_payload)}"

                )

                if transport in (TRANSPORT_H264, TRANSPORT_H265):

                    print(

                        f"[EXPECTED] transport={transport!r} is "

                        "reserved but not implemented yet - this is "

                        "not a failure."

                    )

                    return

                raise RuntimeError(

                    f"Server reported transport={transport!r} as "

                    "not implemented."

                )


            if config_error.is_set():

                print(

                    "[HANDSHAKE ERROR] "

                    f"{json.dumps(config_ack_payload)}"

                )

                raise RuntimeError(

                    f"Server rejected configuration for transport={transport!r}: "

                    f"{config_ack_payload.get('error')}"

                )


            print(f"[OK] Transport negotiated: {transport}")

            if transport not in IMPLEMENTED_TRANSPORTS:

                # Config was somehow accepted for a reserved transport;

                # don't attempt to send frames it can't decode.

                raise RuntimeError(

                    f"transport={transport!r} was accepted but this "

                    "test client only sends frames for "

                    f"{IMPLEMENTED_TRANSPORTS}."

                )

            # Send JPEG frames, encoded for the negotiated transport.

            print()

            print(f"[4/6] Streaming frames via {transport}")

            print()

            for frame_file in frame_files:

                try:

                    with frame_file.open("rb") as f:

                        jpeg_bytes = f.read()

                    if not jpeg_bytes:

                        print(

                            f"n[WARNING] Empty frame: "

                            f"{frame_file}"

                        )

                        continue

                    if transport == TRANSPORT_JPEG_BINARY:

                        # Raw JPEG bytes as a binary WebSocket message.

                        await websocket.send(jpeg_bytes)

                    elif transport == TRANSPORT_JSON_BASE64:

                        # {"type": "frame", "frame": "<base64 JPEG>"}

                        await websocket.send(

                            json.dumps(

                                {

                                    "type": "frame",

                                    "frame": base64.b64encode(

                                        jpeg_bytes

                                    ).decode("ascii"),

                                }

                            )

                        )

                    else:

                        raise RuntimeError(

                            f"No frame-sending path for transport "

                            f"{transport!r}"

                        )

                    frame_count += 1

                    print(

                        f"Frames sent: "

                        f"{frame_count:4d} | "

                        f"{frame_file.name} | "

                        f"{len(jpeg_bytes) / 1024:.2f} KB",

                        flush=True,

                    )

                except websockets.exceptions.ConnectionClosed as e:

                    print()

                    print(

                        "Server closed connection while "

                        f"sending frame: "

                        f"code={e.code}, reason={e.reason}"

                    )

                    break

                # Match approximately the camera/video FPS.

                await asyncio.sleep(frame_delay)

            print()

            print()

            print(

                f"[OK] Finished sending "

                f"{frame_count} frames"

            )

            # End of stream

            print(

                "[5/6] Sending end-of-stream..."

            )

            await websocket.send(

                json.dumps(

                    {

                        "type": "end",

                    }

                )

            )

            print("[OK] End-of-stream sent")

            # Wait for model inference

            print()

            print(

                "[6/6] Waiting for model inference..."

            )

            if frame_count < CLIP_LENGTH:

                print(

                    f"[WARNING] Only {frame_count} frames "

                    f"were sent. The model requires "

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

        "Authentication : "

        f"{'PASS' if connection_ready.is_set() else 'FAIL'}"

    )

    print(

        "Handshake      : "

        f"{'PASS' if config_acked.is_set() else 'NOT IMPLEMENTED' if config_not_implemented.is_set() else 'FAIL'}"

    )

    print(

        f"Frames sent    : {frame_count}"

    )

    print(

        f"Server replies : {response_count}"

    )

    print(

        "Inference      : "

        f"{'PASS' if inference_results else 'FAIL'}"

    )

    print(

        f"Saved responses: {output_file}"

    )

    # Print inference results

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

            print(f"Result #{i}")

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

            "test using pre-encoded JPEG frames."

        )

    )

    parser.add_argument(

        "--url",

        default=DEFAULT_URL,

        help="WebSocket URL.",

    )

    parser.add_argument(

        "--frames-dir",

        default=DEFAULT_FRAMES_DIR,

        help=(

            "Directory containing JPEG frames. "

            "Default: temp/frames"

        ),

    )

    parser.add_argument(

        "--pattern",

        default=DEFAULT_FRAME_PATTERN,

        help=(

            "Frame filename pattern. "

            "Default: frame__*.jpg"

        ),

    )

    parser.add_argument(

        "--fps",

        type=float,

        default=12.5,

        help=(

            "Frame sending FPS. "

            "Default: 12.5"

        ),

    )

    parser.add_argument(

        "--transport",

        choices=VALID_TRANSPORTS,

        default=TRANSPORT_JPEG_BINARY,

        help=(

            "WebSocket frame transport to negotiate. "

            "jpeg_binary (default, production) or json_base64 "

            "(debugging/Postman) actually send frames. h264/h265 "

            "are reserved and are expected to be rejected with "

            "status=not_implemented - this client exits cleanly "

            "in that case rather than treating it as a failure."

        ),

    )

    args = parser.parse_args()

    asyncio.run(

        test_websocket(

            ws_url=args.url,

            frames_dir=args.frames_dir,

            frame_pattern=args.pattern,

            transport=args.transport,

            fps=args.fps,

        )

    )



if __name__ == "__main__":

    main()