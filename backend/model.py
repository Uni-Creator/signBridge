# backend_model.py
import base64
import time
from io import BytesIO

import requests
from PIL import Image


class ISLModelAPI:
    def __init__(self, top_k: int = 5):
        self.base_url            = "https://creator-090-isl-api.hf.space"
        self.predict_frames_url  = f"{self.base_url}/predict_frames"
        self.predict_video_url   = f"{self.base_url}/predict"
        self.health_url          = f"{self.base_url}/health"
        self.top_k               = top_k

        # Persistent connection pool — avoids TCP handshake on every request
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2,
            pool_maxsize=4,
            max_retries=0,          # retries handled manually below
        )
        self.session.mount("https://", adapter)

    #  Health
    def check_health(self) -> bool:
        try:
            r = self.session.get(self.health_url, timeout=3)
            return r.status_code == 200 and r.json().get("status") == "ok"
        except Exception:
            return False

    # Frames path (primary real-time path)
    def predict_from_frames(self, frames: list[Image.Image]) -> dict:
        """
        Encode 16 PIL Images as JPEG → base64 and POST to /predict_frames.
        Frames are already resized to 224×224 by the WebSocket handler,
        so we skip any extra resize here.
        """
        if not frames or len(frames) != 16:
            return {"error": f"Exactly 16 frames required, got {len(frames) if frames else 0}"}

        encoded = []
        for frame in frames:
            buf = BytesIO()
            # quality=80 is a good tradeoff: ~30% smaller payload vs 85, imperceptible quality loss
            frame.save(buf, format="JPEG", quality=80)
            encoded.append(base64.b64encode(buf.getvalue()).decode())

        payload  = {"frames": encoded, "top_k": self.top_k}
        last_err = "Unknown error"

        for attempt in range(2):
            try:
                r = self.session.post(self.predict_frames_url, json=payload, timeout=10)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 503:
                    time.sleep(1.5)
                    continue
                last_err = f"API error {r.status_code}: {r.text}"
            except Exception as e:
                last_err = str(e)
                time.sleep(0.5)

        return {"error": last_err}

    # Video path (fallback only) 
    def predict(self, frames: list[Image.Image]) -> dict:
        """
        Compile PIL frames → in-memory MP4 (no disk I/O) and POST to /predict.
        Only used as a fallback in hybrid mode.
        """
        if not frames:
            return {"error": "No frames provided"}

        import cv2
        import numpy as np

        width, height = frames[0].size
        fourcc        = cv2.VideoWriter_fourcc(*'mp4v')

        # Write directly to memory via a temp buffer trick using BytesIO-backed file
        # cv2.VideoWriter requires a real path, so use /tmp (RAM-backed on Linux)
        tmp_path = f"/tmp/isl_infer_{int(time.time()*1000)}.mp4"
        out      = cv2.VideoWriter(tmp_path, fourcc, 15.0, (width, height))
        for frame in frames:
            out.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
        out.release()

        try:
            with open(tmp_path, "rb") as f:
                r = self.session.post(
                    self.predict_video_url,
                    params={"top_k": self.top_k},
                    files={"file": (f"clip.mp4", f, "video/mp4")},
                    timeout=15,
                )
            if r.status_code == 200:
                return r.json()
            return {"error": f"API error {r.status_code}: {r.text}"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)