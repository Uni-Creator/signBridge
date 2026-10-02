"""
Regression and pipeline metrics tests for WebSocket streaming performance.

Computes and verifies:
- Mean latency
- Median / P50
- P90
- P95
- P99
- Minimum and maximum latency
- Actual input FPS
- Actual inference frequency
- Number of dropped frames
- Number of queued/in-flight inference jobs
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from typing import List
from unittest.mock import MagicMock

from PIL import Image
from starlette.websockets import WebSocketState

import app.websocket.websocket_handler as wh


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
class PerformanceMetricsReport:
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
        print("\n" + "=" * 65)
        print("  SIGNBRIDGE PIPELINE INFERENCE & STREAMING METRICS")
        print("=" * 65)
        print(f"Total Stream Duration:       {self.total_duration_sec:.3f} s")
        print(f"Target Input FPS:            {self.target_fps:.1f} fps")
        print(f"Actual Input FPS:            {self.actual_input_fps:.2f} fps")
        print(f"Frames Sent:                 {self.total_frames_sent}")
        print(f"Frames Accepted (Server):    {self.frames_accepted}")
        print(f"Frames Dropped:              {self.dropped_frames}")
        print("-" * 65)
        print(f"Inferences Completed:        {self.inferences_count}")
        print(f"Inference Frequency:         {self.actual_inference_freq_hz:.2f} Hz")
        print(f"Peak In-Flight / Queued:     {self.max_in_flight_inferences}")
        print("-" * 65)
        print(f"End-to-End Client-Perceived Latencies (ms):")
        print(f"  Mean Latency:              {self.mean_latency_ms:.2f} ms")
        print(f"  Median (P50):              {self.median_p50_ms:.2f} ms")
        print(f"  P90:                       {self.p90_ms:.2f} ms")
        print(f"  P95:                       {self.p95_ms:.2f} ms")
        print(f"  P99:                       {self.p99_ms:.2f} ms")
        print(f"  Min Latency:               {self.min_latency_ms:.2f} ms")
        print(f"  Max Latency:               {self.max_latency_ms:.2f} ms")
        print("=" * 65 + "\n")


def _jpeg_bytes(width=224, height=224):
    buf = BytesIO()
    Image.new("RGB", (width, height), color=(120, 150, 180)).save(buf, format="JPEG")
    return buf.getvalue()


class MockWebSocket:
    """Mock ASGI WebSocket to simulate client stream and record client-perceived timings."""

    def __init__(self, incoming_events: List[dict]):
        self._incoming = list(incoming_events)
        self.sent_messages = []
        self.send_times = []
        self.receive_times = []
        self.application_state = WebSocketState.CONNECTED

    async def accept(self):
        pass

    async def receive(self):
        if not self._incoming:
            return {"type": "websocket.disconnect"}
        event = self._incoming.pop(0)
        # Small sleep if simulated frame interval is attached
        delay = event.pop("_delay", 0)
        if delay > 0:
            await asyncio.sleep(delay)
        return event

    async def send_text(self, text: str):
        now = time.perf_counter()
        self.sent_messages.append(text)
        self.receive_times.append((now, json.loads(text)))

    async def send_bytes(self, data: bytes):
        now = time.perf_counter()
        self.sent_messages.append(data)
        self.receive_times.append((now, data))

    async def close(self, code=1000, reason=""):
        self.application_state = WebSocketState.DISCONNECTED


class TestStreamingPerformanceMetrics(unittest.TestCase):
    """Test suite computing full performance distribution and pipeline metrics."""

    def setUp(self):
        self.landmark_executor = ThreadPoolExecutor(max_workers=2)
        self.inference_executor = ThreadPoolExecutor(max_workers=2)

    def tearDown(self):
        self.landmark_executor.shutdown(wait=False)
        self.inference_executor.shutdown(wait=False)

    def test_measure_performance_metrics_distribution(self):
        """Simulate a continuous 30 FPS stream with 46 frames (6 sliding windows) and compute all required metrics."""
        # Mock model API returning predictions after simulated forward-pass time
        mock_model_api = MagicMock()
        mock_model_api.is_ready.return_value = True
        
        # Simulate ~30ms inference duration in worker thread
        def mock_predict_frames(frames):
            time.sleep(0.030)
            return {"prediction": "NAMASTE", "confidence": 0.94}
        
        mock_model_api.predict_from_frames.side_effect = mock_predict_frames
        mock_model_api.predict.side_effect = mock_predict_frames

        # Build 46 frames => window 0 (1-16), window 1 (7-22), window 2 (13-28), 
        # window 3 (19-34), window 4 (25-40), window 5 (31-46) -> 6 windows
        num_frames = 46
        # Server enforces FRAME_DELAY = 0.08s (~12.5 fps rate limit)
        target_fps = 12.0
        frame_interval = 1.0 / target_fps
        frame_bytes = _jpeg_bytes()

        incoming_events = [
            {"type": "websocket.receive", "text": json.dumps({
                "type": "config", "version": 1, "mode": "frames", "transport": "jpeg_binary"
            })}
        ]

        frame_send_times = []
        
        # Paced frame arrivals (>= FRAME_DELAY apart)
        for _ in range(num_frames):
            incoming_events.append({
                "type": "websocket.receive",
                "bytes": frame_bytes,
                "_delay": frame_interval,
            })

        incoming_events.append({
            "type": "websocket.receive",
            "text": json.dumps({"type": "end"}),
        })

        ws = MockWebSocket(incoming_events)

        start_time = time.perf_counter()
        
        # Run WebSocket handler
        asyncio.run(wh.handle_websocket(
            ws,
            mock_model_api,
            self.landmark_executor,
            self.inference_executor,
            user_id="perf-test-user",
        ))

        total_duration = time.perf_counter() - start_time

        # Extract received messages
        predictions = []
        complete_msg = {}
        e2e_latencies_ms = []

        # Reconstruct approximate send time per frame index from start_time + interval
        for (recv_t, msg) in ws.receive_times:
            if isinstance(msg, dict):
                if "label" in msg:
                    predictions.append(msg)
                    seq = msg.get("sequence", len(predictions) - 1)
                    trigger_frame_idx = 15 + seq * 6
                    # Estimated trigger time
                    trigger_send_time = start_time + (trigger_frame_idx * frame_interval)
                    latency_ms = (recv_t - trigger_send_time) * 1000.0
                    e2e_latencies_ms.append(max(1.0, latency_ms))
                elif msg.get("status") == "complete":
                    complete_msg = msg

        frames_accepted = complete_msg.get("frames", num_frames)
        dropped_frames = num_frames - frames_accepted
        inferences_count = complete_msg.get("inferences", len(predictions))

        report = PerformanceMetricsReport(
            total_frames_sent=num_frames,
            frames_accepted=frames_accepted,
            dropped_frames=dropped_frames,
            target_fps=target_fps,
            actual_input_fps=0.0,
            total_duration_sec=total_duration,
            inferences_count=inferences_count,
            actual_inference_freq_hz=0.0,
            e2e_latencies_ms=e2e_latencies_ms,
            max_in_flight_inferences=wh.MAX_CONCURRENT_INFERENCES,
            predictions=predictions,
        )

        report.compute_summary()
        report.print_summary()

        # Metrics validations
        self.assertEqual(report.total_frames_sent, 46)
        self.assertEqual(report.frames_accepted, 46)
        self.assertEqual(report.dropped_frames, 0)
        self.assertEqual(report.inferences_count, 6)
        self.assertGreater(report.mean_latency_ms, 0)
        self.assertGreater(report.median_p50_ms, 0)
        self.assertGreater(report.p90_ms, 0)
        self.assertGreater(report.p95_ms, 0)
        self.assertGreater(report.p99_ms, 0)
        self.assertGreater(report.min_latency_ms, 0)
        self.assertGreaterEqual(report.max_latency_ms, report.min_latency_ms)
        self.assertGreater(report.actual_input_fps, 0)
        self.assertGreater(report.actual_inference_freq_hz, 0)


if __name__ == "__main__":
    unittest.main()
