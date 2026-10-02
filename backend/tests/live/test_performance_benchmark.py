"""
Performance and Latency Benchmark Test Suite for SignBridge Live Pipeline.

Measures:
- Mean latency
- Median / P50
- P90
- P95
- P99
- Minimum and maximum latency
- Actual input FPS
- Actual inference frequency
- Number of dropped frames
- Number of queued / in-flight inference jobs
- End-to-end client-perceived latency
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import statistics
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PIL import Image

try:
    import websockets
except ImportError:
    websockets = None

try:
    from base import LiveTestCase
    from live_config import WS_URL, RUN_LIVE, unique_email
    from live_helpers import register, login
except ModuleNotFoundError:
    from tests.live.base import LiveTestCase
    from tests.live.live_config import WS_URL, RUN_LIVE, unique_email
    from tests.live.live_helpers import register, login


import os
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load environment variables from test and backend env files
_LIVE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _LIVE_DIR.parents[2]
load_dotenv(_LIVE_DIR / ".env.live")
load_dotenv(_LIVE_DIR / ".env.live.example")
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.cwd() / ".env")

CREATED_USER_IDS: List[str] = []


def cleanup_test_users() -> None:
    """Delete created test users via the admin API. Never fails the test run."""
    base = os.environ.get("SIGNBRIDGE_LIVE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
    key = os.environ.get("ADMIN_API_KEY", "")
    if not key:
        print("[cleanup] ADMIN_API_KEY not set; skipping user cleanup")
        return
    if not CREATED_USER_IDS:
        return
    try:
        res = requests.post(
            f"{base}/admin/users/delete",
            json={"user_ids": CREATED_USER_IDS},
            headers={"X-Admin-Key": key},
            timeout=60,
        )
        print(f"[cleanup] {res.status_code}: {res.text}")
    except Exception as exc:
        print(f"[cleanup] failed: {exc}")


def _percentile(data: List[float], p: float) -> float:
    """Calculate the p-th percentile (0 <= p <= 100) using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    if len(sorted_data) == 1:
        return sorted_data[0]
    
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


@dataclass
class BenchmarkReport:
    total_frames_sent: int
    frames_accepted: int
    dropped_frames: int
    target_fps: float
    actual_input_fps: float
    total_duration_sec: float
    
    inferences_count: int
    actual_inference_freq_hz: float
    
    # Latencies in milliseconds
    e2e_latencies_ms: List[float] = field(default_factory=list)
    mean_latency_ms: float = 0.0
    median_p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    
    max_in_flight_inferences: int = 0
    predictions: List[dict] = field(default_factory=list)

    def compute_summary(self):
        if self.e2e_latencies_ms:
            self.mean_latency_ms = statistics.mean(self.e2e_latencies_ms)
            self.median_p50_ms = _percentile(self.e2e_latencies_ms, 50)
            self.p90_ms = _percentile(self.e2e_latencies_ms, 90)
            self.p95_ms = _percentile(self.e2e_latencies_ms, 95)
            self.p99_ms = _percentile(self.e2e_latencies_ms, 99)
            self.min_latency_ms = min(self.e2e_latencies_ms)
            self.max_latency_ms = max(self.e2e_latencies_ms)
        
        if self.total_duration_sec > 0:
            self.actual_input_fps = self.total_frames_sent / self.total_duration_sec
            self.actual_inference_freq_hz = self.inferences_count / self.total_duration_sec

    def print_summary(self):
        print("\n" + "=" * 60)
        print("  SIGNBRIDGE PIPELINE BENCHMARK REPORT")
        print("=" * 60)
        print(f"Total Stream Duration:       {self.total_duration_sec:.2f} s")
        print(f"Target Input FPS:            {self.target_fps:.1f} fps")
        print(f"Actual Input FPS:            {self.actual_input_fps:.2f} fps")
        print(f"Frames Sent:                 {self.total_frames_sent}")
        print(f"Frames Accepted (Server):    {self.frames_accepted}")
        print(f"Frames Dropped:              {self.dropped_frames}")
        print("-" * 60)
        print(f"Inferences Completed:        {self.inferences_count}")
        print(f"Inference Frequency:         {self.actual_inference_freq_hz:.2f} Hz")
        print(f"Peak In-Flight / Queued:     {self.max_in_flight_inferences}")
        print("-" * 60)
        print(f"End-to-End Latencies (ms):")
        print(f"  Mean Latency:              {self.mean_latency_ms:.2f} ms")
        print(f"  Median (P50):              {self.median_p50_ms:.2f} ms")
        print(f"  P90:                       {self.p90_ms:.2f} ms")
        print(f"  P95:                       {self.p95_ms:.2f} ms")
        print(f"  P99:                       {self.p99_ms:.2f} ms")
        print(f"  Min Latency:               {self.min_latency_ms:.2f} ms")
        print(f"  Max Latency:               {self.max_latency_ms:.2f} ms")
        print("=" * 60 + "\n")


def generate_synthetic_jpeg_frames(num_frames: int = 50, width: int = 224, height: int = 224) -> List[bytes]:
    """Generate in-memory synthetic JPEG frame bytes."""
    frames = []
    for i in range(num_frames):
        color = (
            int((i * 17) % 256),
            int((i * 37) % 256),
            int((i * 73) % 256),
        )
        img = Image.new("RGB", (width, height), color=color)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        frames.append(buf.getvalue())
    return frames


async def run_benchmark_stream(
    ws_url: str,
    token: str,
    frame_bytes_list: List[bytes],
    target_fps: float = 30.0,
) -> BenchmarkReport:
    """Stream frames at a continuous target FPS, record timestamps and compute metrics."""
    assert websockets is not None, "websockets package required for benchmark"
    
    frame_interval = 1.0 / target_fps
    
    async with websockets.connect(
        ws_url,
        additional_headers={"Authorization": f"Bearer {token}"},
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=30,
    ) as ws:

        connected = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))

        assert connected.get("status") == "connected", connected

        await ws.send(json.dumps({
            "type": "config",
            "version": 1,
            "mode": "frames",
            "transport": "jpeg_binary",
        }))

        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        while ack.get("type") != "config_ack":
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert ack["status"] == "accepted", f"config rejected: {ack}"

        # Track send timestamps per frame index
        # For sliding window: window #0 completes at frame 16 (index 15)
        # Window k completes at frame 16 + k * 6 (index 15 + k * 6)
        frame_send_times: List[float] = []
        predictions: List[dict] = []
        e2e_latencies_ms: List[float] = []
        in_flight_tracker = {"current": 0, "max": 0}
        
        # Background receiver task
        recv_done = asyncio.Event()
        complete_msg = {}

        async def receive_loop():
            nonlocal complete_msg
            while not recv_done.is_set():
                try:
                    raw = await ws.recv()
                    recv_time = time.perf_counter()
                    msg = json.loads(raw)
                    if "label" in msg:
                        predictions.append(msg)
                        seq = msg.get("sequence", len(predictions) - 1)
                        # Corresponding trigger frame index: window 0 at frame 16 (idx 15), stride 6
                        trigger_frame_idx = 15 + seq * 6
                        if trigger_frame_idx < len(frame_send_times):
                            sent_time = frame_send_times[trigger_frame_idx]
                            latency_ms = (recv_time - sent_time) * 1000.0
                            e2e_latencies_ms.append(latency_ms)
                        
                        in_flight_tracker["current"] = max(0, in_flight_tracker["current"] - 1)
                    elif msg.get("status") == "complete":
                        complete_msg = msg
                        recv_done.set()
                        break
                except Exception:
                    break

        recv_task = asyncio.create_task(receive_loop())

        start_time = time.perf_counter()
        frames_sent = 0

        for frame_data in frame_bytes_list:
            send_start = time.perf_counter()
            frame_send_times.append(send_start)
            await ws.send(frame_data)
            frames_sent += 1
            
            # Estimate in-flight increment when window trigger reached
            if frames_sent >= 16 and (frames_sent - 16) % 6 == 0:
                in_flight_tracker["current"] += 1
                in_flight_tracker["max"] = max(in_flight_tracker["max"], in_flight_tracker["current"])

            # Pace at target FPS
            elapsed = time.perf_counter() - send_start
            sleep_duration = max(0.0, frame_interval - elapsed)
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

        total_stream_time = time.perf_counter() - start_time

        # End stream
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(recv_done.wait(), timeout=60)
        recv_task.cancel()

        frames_accepted = complete_msg.get("frames", frames_sent)
        dropped_frames = frames_sent - frames_accepted
        inferences_count = complete_msg.get("inferences", len(predictions))

        report = BenchmarkReport(
            total_frames_sent=frames_sent,
            frames_accepted=frames_accepted,
            dropped_frames=dropped_frames,
            target_fps=target_fps,
            actual_input_fps=0.0,
            total_duration_sec=total_stream_time,
            inferences_count=inferences_count,
            actual_inference_freq_hz=0.0,
            e2e_latencies_ms=e2e_latencies_ms,
            max_in_flight_inferences=in_flight_tracker["max"],
            predictions=predictions,
        )
        report.compute_summary()
        return report


class TestPipelinePerformanceBenchmark(LiveTestCase):
    """Benchmark test measuring latency distribution, throughput, and stream stability."""

    @classmethod
    def tearDownClass(cls):
        try:
            cleanup_test_users()
        finally:
            super().tearDownClass()

    def test_continuous_stream_benchmark(self):
        # 1. Setup disposable test account
        email = unique_email()
        password = "test-perf-password"
        reg = register(self.client, email, password)
        if "id" in reg:
            CREATED_USER_IDS.append(reg["id"])
        login_res = login(self.client, email, password)
        token = login_res["token"]


        # 2. Prepare synthetic frames (60 frames => (60 - 16)/6 + 1 = ~8 inference windows)
        frames = generate_synthetic_jpeg_frames(num_frames=60)

        # 3. Run benchmark at 30 FPS
        report = asyncio.run(
            run_benchmark_stream(
                ws_url=WS_URL,
                token=token,
                frame_bytes_list=frames,    
                target_fps=30.0,
            )
        )

        # 4. Print benchmark summary to test stdout
        report.print_summary()

        # 5. Assert sanity checks on metrics
        self.assertEqual(report.total_frames_sent, 60)
        self.assertGreaterEqual(report.frames_accepted, 16)
        self.assertGreaterEqual(report.inferences_count, 1)


if __name__ == "__main__":
    unittest.main()

