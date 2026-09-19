"""
SignBridge WebSocket Processing
================================

Contains CPU/model-heavy processing used by the WebSocket handler:

- MediaPipe initialization
- MediaPipe landmark drawing
- Frame preprocessing
- Model inference
- Optional test-video generation

This module does not know about Flask or WebSocket connections.
"""

import logging
import os
import time

import cv2
import numpy as np
from PIL import Image


logger = logging.getLogger(__name__)


# MediaPipe

_ENABLE_MEDIAPIPE = os.environ.get("ENABLE_MEDIAPIPE", "1") == "1"

mp_drawing = None
mp_vision = None
mp_python = None
MEDIAPIPE_OK = False


if _ENABLE_MEDIAPIPE:
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        mp_drawing = mp.solutions.drawing_utils
        MEDIAPIPE_OK = True

        logger.info("[MediaPipe] Loaded successfully.")

    except Exception as e:
        logger.warning(
            "[MediaPipe] Init failed. "
            "Landmarks will be disabled: %s",
            e,
        )

else:
    logger.info(
        "[MediaPipe] Disabled via ENABLE_MEDIAPIPE environment variable."
    )


# MediaPipe landmarkers

def build_landmarkers():
    """
    Create MediaPipe pose and hand landmark detectors.

    Returns:
        tuple:
            (pose_detector, hand_detector)

        If MediaPipe is unavailable or initialization fails:
            (None, None)
    """

    if not MEDIAPIPE_OK:
        logger.info(
            "MediaPipe not available. "
            "Landmarks will be disabled."
        )
        return None, None

    try:
        pose = mp_vision.PoseLandmarker.create_from_options(
            mp_vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(
                    model_asset_path="pose_landmarker_full.task"
                )
            )
        )

        hand = mp_vision.HandLandmarker.create_from_options(
            mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(
                    model_asset_path="hand_landmarker.task"
                ),
                num_hands=2,
            )
        )

        return pose, hand

    except Exception:
        logger.exception("Landmarker initialization failed.")
        return None, None


# Landmark processing

def apply_landmarks(
    image: Image.Image,
    pose_detector,
    hand_detector,
) -> Image.Image:
    """
    Run MediaPipe pose/hand detection and draw the detected landmarks.

    If no landmarks are detected, the original image is returned.
    """

    import mediapipe as mp
    from mediapipe.framework.formats import landmark_pb2

    img_rgb = np.array(image)

    mp_image = mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=img_rgb,
    )

    pose_result = (
        pose_detector.detect(mp_image)
        if pose_detector
        else None
    )

    hand_result = (
        hand_detector.detect(mp_image)
        if hand_detector
        else None
    )

    no_pose = (
        not pose_result
        or not pose_result.pose_landmarks
    )

    no_hands = (
        not hand_result
        or not hand_result.hand_landmarks
    )

    if no_pose and no_hands:
        del img_rgb
        del mp_image

        return image

    # Convert RGB -> BGR for OpenCV drawing.
    img_bgr = cv2.cvtColor(
        img_rgb,
        cv2.COLOR_RGB2BGR,
    )

    # Release large temporary objects as early as possible.
    del img_rgb
    del mp_image

    # Pose landmarks

    if pose_result and pose_result.pose_landmarks:

        for landmarks in pose_result.pose_landmarks:

            proto = landmark_pb2.NormalizedLandmarkList()

            proto.landmark.extend(
                [
                    landmark_pb2.NormalizedLandmark(
                        x=landmark.x,
                        y=landmark.y,
                        z=landmark.z,
                    )
                    for landmark in landmarks
                ]
            )

            mp_drawing.draw_landmarks(
                img_bgr,
                proto,
                mp.solutions.pose.POSE_CONNECTIONS,
                mp.solutions.drawing_styles.get_default_pose_landmarks_style(),
            )

    # Hand landmarks

    if hand_result and hand_result.hand_landmarks:

        for landmarks in hand_result.hand_landmarks:

            proto = landmark_pb2.NormalizedLandmarkList()

            proto.landmark.extend(
                [
                    landmark_pb2.NormalizedLandmark(
                        x=landmark.x,
                        y=landmark.y,
                        z=landmark.z,
                    )
                    for landmark in landmarks
                ]
            )

            mp_drawing.draw_landmarks(
                img_bgr,
                proto,
                mp.solutions.hands.HAND_CONNECTIONS,
                mp.solutions.drawing_styles.get_default_hand_landmarks_style(),
                mp.solutions.drawing_styles.get_default_hand_connections_style(),
            )

    # Convert back to PIL RGB.
    result_image = Image.fromarray(
        cv2.cvtColor(
            img_bgr,
            cv2.COLOR_BGR2RGB,
        )
    )

    del img_bgr

    return result_image



# Async landmark wrapper

def process_frame(
    raw_image: Image.Image,
    pose_detector,
    hand_detector,
    landmarks_enabled: bool,
) -> Image.Image:
    """
    Process one incoming frame.

    This function is intended to run inside the ThreadPoolExecutor.
    """

    try:
        if landmarks_enabled:
            return apply_landmarks(
                raw_image,
                pose_detector,
                hand_detector,
            )

        return raw_image

    finally:
        # Release this thread's reference.
        del raw_image


# Test video generation

def save_test_video(
    frames: list,
    output_dir: str = "temp_videos",
) -> str:
    """
    Save PIL frames as a local MP4.

    Used only when SAVE_TEST_VIDEOS=1.
    """

    try:
        if not frames:
            return ""

        os.makedirs(
            output_dir,
            exist_ok=True,
        )

        timestamp = int(time.time() * 1000)

        filepath = os.path.abspath(
            os.path.join(
                output_dir,
                f"test_clip_{timestamp}.mp4",
            )
        )

        width, height = frames[0].size

        fourcc = cv2.VideoWriter_fourcc(
            *"mp4v"
        )

        out = cv2.VideoWriter(
            filepath,
            fourcc,
            15.0,
            (width, height),
        )

        try:
            for frame in frames:

                frame_array = np.array(frame)

                frame_bgr = cv2.cvtColor(
                    frame_array,
                    cv2.COLOR_RGB2BGR,
                )

                out.write(frame_bgr)

                del frame_array
                del frame_bgr

        finally:
            out.release()

        logger.info(
            "Saved test video locally: %s",
            filepath,
        )

        return filepath

    except Exception:
        logger.exception(
            "Failed to save test video locally."
        )

        return ""


# Model inference

def run_inference(
    frames: list,
    mode: str,
    model_api,
    save_test_videos: bool = False,
) -> dict:
    """
    Run model inference for a buffered sequence.

    Supported modes:

        frames
        video
        hybrid
    """

    t0 = time.time()

    try:

        if save_test_videos:
            save_test_video(frames)

        # Frames mode

        if mode == "frames":

            result = model_api.predict_from_frames(
                frames
            )

        # Video mode

        elif mode == "video":

            result = model_api.predict(
                frames
            )

        # Hybrid mode

        else:

            result = model_api.predict_from_frames(
                frames
            )

            if "error" in result:

                logger.warning(
                    "Hybrid: frames path failed, "
                    "falling back to video."
                )

                result = model_api.predict(
                    frames
                )

        result["total_latency_ms"] = round(
            (time.time() - t0) * 1000,
            2,
        )

        return result

    finally:
        # Release the frames list held by this worker.
        del frames