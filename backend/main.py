# main.py
"""
SignSight Backend - Flask + WebSocket Server
============================================
Endpoints:
  POST /register   → Firebase user registration
  POST /login      → Firebase user login
  GET  /history    → Retrieve translation history
  POST /history    → Store a translation
  WS   /ws         → Real-time sign detection
"""
# import gevent.monkey
# gevent.monkey.patch_all(ssl=False) 

import base64
import json
import logging
import os
import time
from collections import deque
from io import BytesIO

import cv2
import numpy as np
import concurrent.futures
from flask import Flask, request
from flask_cors import CORS
from flask_sock import Sock
from PIL import Image

# MediaPipe (optional)
mp_drawing      = None
mp_vision       = None
mp_python       = None
MEDIAPIPE_OK    = False

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.framework.formats import landmark_pb2
    mp_drawing   = mp.solutions.drawing_utils
    MEDIAPIPE_OK = True
except Exception as e:
    print(f"MediaPipe init failed (landmarks disabled): {e}")


def build_landmarkers():
    """Instantiate pose + hand landmarkers. Returns (pose, hand) or (None, None)."""
    if not MEDIAPIPE_OK:
        return None, None
    try:
        pose = mp_vision.PoseLandmarker.create_from_options(
            mp_vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path="pose_landmarker_full.task")
            )
        )
        hand = mp_vision.HandLandmarker.create_from_options(
            mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path="hand_landmarker.task"),
                num_hands=2,
            )
        )
        return pose, hand
    except Exception as e:
        logging.error(f"Landmarker init failed: {e}")
        return None, None


def apply_landmarks(image: Image.Image, pose_detector, hand_detector) -> Image.Image:
    """Overlay MediaPipe landmarks on a PIL RGB image. Returns annotated PIL image."""
    import mediapipe as mp
    from mediapipe.framework.formats import landmark_pb2

    img_rgb  = np.array(image)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)

    pose_result = pose_detector.detect(mp_image) if pose_detector else None
    hand_result = hand_detector.detect(mp_image) if hand_detector else None

    if (not pose_result or not pose_result.pose_landmarks) and \
       (not hand_result or not hand_result.hand_landmarks):
        return image

    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

    if pose_result and pose_result.pose_landmarks:
        for lms in pose_result.pose_landmarks:
            proto = landmark_pb2.NormalizedLandmarkList()
            proto.landmark.extend([
                landmark_pb2.NormalizedLandmark(x=l.x, y=l.y, z=l.z) for l in lms
            ])
            mp_drawing.draw_landmarks(
                img_bgr, proto,
                mp.solutions.pose.POSE_CONNECTIONS,
                mp.solutions.drawing_styles.get_default_pose_landmarks_style(),
            )

    if hand_result and hand_result.hand_landmarks:
        for lms in hand_result.hand_landmarks:
            proto = landmark_pb2.NormalizedLandmarkList()
            proto.landmark.extend([
                landmark_pb2.NormalizedLandmark(x=l.x, y=l.y, z=l.z) for l in lms
            ])
            mp_drawing.draw_landmarks(
                img_bgr, proto,
                mp.solutions.hands.HAND_CONNECTIONS,
                mp.solutions.drawing_styles.get_default_hand_landmarks_style(),
                mp.solutions.drawing_styles.get_default_hand_connections_style(),
            )

    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))


# App setup 
from authentication import register_account, login_account, forgot_password
from history import retrieve_history, store_translation
from model import ISLModelAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app  = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
sock = Sock(app)

# Shared thread pool - 2 workers: one for inference, one for MediaPipe
# Keep this small; more workers = more memory, not more throughput for a single GPU API
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# Shared model API instance (session is reused across all WebSocket connections)
model_api = ISLModelAPI(top_k=1)

# Config
CLIP_LENGTH  = 16
FRAME_DELAY  = 0.08    # ~12.5 fps max ingest rate from Flutter
RESIZE_DIM   = 224     # resize once here, skip redundant resize in backend_model


# REST routes
@app.route("/")
def index():
    return json.dumps({"message": "SignSight API is running", "version": "2.0"})

@app.route("/register", methods=["POST"])
def register():
    account = request.get_json(silent=True)
    if not account or "email" not in account or "password" not in account:
        return json.dumps({"id": "", "error": "Missing email or password"}), 400
    user_id = register_account(account["email"], account["password"])
    logger.info(f"Register: {account['email']} → id={user_id or 'FAILED'}")
    return json.dumps({"id": user_id})

@app.route("/login", methods=["POST"])
def login():
    account = request.get_json(silent=True)
    if not account or "email" not in account or "password" not in account:
        return json.dumps({"id": "", "error": "Missing email or password"}), 400
    user_id = login_account(account["email"], account["password"])
    logger.info(f"Login: {account['email']} → id={user_id or 'FAILED'}")
    return json.dumps({"id": user_id})

@app.route("/forgot-password", methods=["POST"])
def forgot_pwd():
    account = request.get_json(silent=True)
    if not account or "email" not in account:
        return json.dumps({"id": "", "error": "Missing email"}), 400
    user_id = forgot_password(account["email"])
    logger.info(f"Forgot Password: {account['email']} → id={user_id or 'FAILED'}")
    return json.dumps({"id": user_id})

@app.route("/email-verify", methods=["GET"])
def email_verify():
    id_token = request.args.get("id_token", "")
    if not id_token:
        return json.dumps({"id": "", "error": "Missing id token"}), 400
    message = email_verify(id_token)
    logger.info(f"Email Verify: {message}")
    return json.dumps({"message": message})

@app.route("/history", methods=["GET"])
def get_history():
    user_id = request.args.get("id", "")
    if not user_id:
        return json.dumps({"history": [], "error": "Missing user id"}), 400
    return json.dumps({"history": retrieve_history(user_id)})

@app.route("/history", methods=["POST"])
def post_history():
    try:
        body = request.get_json(silent=True)
        if not body:
            return json.dumps({"message": "error", "detail": "No JSON body"}), 400
        user_id     = body.get("id", "")
        translation = body.get("translation", "")
        if not user_id or not translation:
            return json.dumps({"message": "error", "detail": "Missing id or translation"}), 400
        store_translation(user_id, translation)
        return json.dumps({"message": "success"})
    except Exception as e:
        logger.error(f"post_history error: {e}")
        return json.dumps({"message": "error", "detail": str(e)}), 500


# WebSocket
@sock.route("/ws")
def websocket_translate(ws):
    logger.info("WebSocket client connected")
    ws.send(json.dumps({"status": "connected", "message": "Ready for frames"}))

    config = {"mode": "frames"}   # default to fastest path; Flutter can switch to "hybrid"

    frame_buffer          = deque(maxlen=CLIP_LENGTH)
    last_receive_time     = 0.0
    last_prediction_future = None

    # Build landmarkers per connection (they are not thread-safe to share)
    pose_detector, hand_detector = build_landmarkers()
    landmarks_enabled = pose_detector is not None and hand_detector is not None

    if not model_api.check_health():
        ws.send(json.dumps({"status": "api_warming", "message": "Model API warming up, please wait..."}))
        logger.warning("Remote model API not ready — predictions may fail.")
    else:
        logger.info("Remote model API healthy.")

    def _run_inference(frames: list, mode: str) -> dict:
        """Runs in the thread pool. Never touches the WebSocket."""
        t0 = time.time()
        if mode == "frames":
            res = model_api.predict_from_frames(frames)
        elif mode == "video":
            res = model_api.predict(frames)
        else:  # hybrid
            res = model_api.predict_from_frames(frames)
            if "error" in res:
                logger.warning("Hybrid: frames path failed, falling back to video")
                res = model_api.predict(frames)
        res["total_latency_ms"] = round((time.time() - t0) * 1000, 2)
        return res

    def _apply_landmarks_async(raw_image: Image.Image) -> Image.Image:
        """Runs MediaPipe in thread pool so it doesn't block frame ingestion."""
        if landmarks_enabled:
            return apply_landmarks(raw_image, pose_detector, hand_detector)
        return raw_image

    try:
        # Keep a future for landmark processing so we can overlap it with buffer management
        landmark_future = None

        while True:
            message = ws.receive(timeout=30)
            if not message:
                break

            # Config command
            try:
                data = json.loads(message)
                if data.get("type") == "config":
                    new_mode = data.get("mode")
                    if new_mode in ("frames", "video", "hybrid"):
                        config["mode"] = new_mode
                        ws.send(json.dumps({"status": "config_updated", "mode": new_mode}))
                        logger.info(f"Inference mode → {new_mode}")
                    continue
            except Exception:
                pass

            # Frame rate limiter
            now = time.monotonic()
            if now - last_receive_time < FRAME_DELAY:
                continue
            last_receive_time = now

            # Decode frame
            t0 = time.time()
            try:
                data      = json.loads(message)
                b64       = data.get("frame", "")
                if not b64:
                    continue
                img_bytes = base64.b64decode(b64)
                raw_image = Image.open(BytesIO(img_bytes)).convert("RGB")
            except Exception as e:
                logger.warning(f"Frame decode error: {e}")
                ws.send(json.dumps({"error": "Invalid frame"}))
                continue

            t_decode = time.time()

            # MediaPipe landmark overlay (async, non-blocking)
            # We don't wait for the previous landmark future here 
            # we collect the *previous* frame's result while the current one processes.
            # This keeps landmark processing off the critical path.
            if landmark_future is not None and landmark_future.done():
                try:
                    processed_image = landmark_future.result()
                    # Resize after landmarks so drawing coords are correct
                    frame_buffer.append(processed_image.resize((RESIZE_DIM, RESIZE_DIM)))
                except Exception as e:
                    logger.warning(f"Landmark future error: {e}")

            # Submit current frame's landmark processing
            landmark_future = executor.submit(_apply_landmarks_async, raw_image)

            t_process = time.time()
            logger.debug(
                f"[Latency] decode={((t_decode-t0)*1000):.1f}ms  "
                f"landmark_submit={((t_process-t_decode)*1000):.1f}ms"
            )

            # Collect completed inference result 
            if last_prediction_future is not None and last_prediction_future.done():
                try:
                    result = last_prediction_future.result()
                    if "error" in result:
                        logger.error(f"Inference error: {result['error']}")
                    else:
                        label = result.get("prediction", "")
                        conf  = float(result.get("confidence", 0.0))
                        logger.info(
                            f"[{config['mode'].upper()}] {label} {conf:.0%} | "
                            f"total={result.get('total_latency_ms', 0):.0f}ms "
                            f"hf={result.get('inference_time_ms', 0):.0f}ms"
                        )
                        if label and conf > 0.4:
                            ws.send(json.dumps({"label": label, "confidence": conf}))
                            frame_buffer.clear()
                except Exception as e:
                    logger.error(f"Inference result error: {e}")
                last_prediction_future = None

            # Dispatch new inference when buffer is full 
            if len(frame_buffer) == CLIP_LENGTH and last_prediction_future is None:
                frames_copy           = list(frame_buffer)
                last_prediction_future = executor.submit(_run_inference, frames_copy, config["mode"])

    except Exception as e:
        logger.warning(f"WebSocket closed: {e}")
    finally:
        if pose_detector:
            pose_detector.close()
        if hand_detector:
            hand_detector.close()
        logger.info("WebSocket client disconnected")


# Entry point 
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*60}")
    print(f"  SignSight Backend  →  http://0.0.0.0:{port}/")
    print(f"  WebSocket         →  ws://0.0.0.0:{port}/ws")
    print(f"  Android emulator  →  use 10.0.2.2 instead of localhost")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, threaded=True)