"""Webcam eye tracking: detects the face, measures how open the eyes are,
and tracks how long they've been closed. Runs in a background thread."""

import math
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

# MediaPipe face landmark indices for each eye, ordered p1..p6 for the
# Eye Aspect Ratio formula: p1/p4 are the corners, p2/p3 the upper lid,
# p6/p5 the lower lid.
LEFT_EYE = [362, 385, 387, 263, 373, 380]
RIGHT_EYE = [33, 160, 158, 133, 153, 144]


def eye_aspect_ratio(points):
    """(|p2-p6| + |p3-p5|) / (2 * |p1-p4|). Drops toward 0 as the eye closes."""
    p1, p2, p3, p4, p5, p6 = points
    horizontal = math.dist(p1, p4)
    if horizontal == 0:
        return 0.0
    return (math.dist(p2, p6) + math.dist(p3, p5)) / (2.0 * horizontal)


@dataclass
class TrackerState:
    face_found: bool = False
    ear: float = 0.0
    calibrating: bool = True
    open_ear: float = 0.0       # the user's normal open-eye EAR, once calibrated
    ear_threshold: float = 0.0  # EAR below this counts as closed
    eyes_closed: bool = False
    closed_seconds: float = 0.0
    frame: Optional[np.ndarray] = None  # mirrored RGB camera frame
    error: Optional[str] = None


class EyeTracker:
    def __init__(self, model_path, camera_index, calibration_seconds, closed_ratio):
        self.model_path = str(model_path)
        self.camera_index = camera_index
        self.calibration_seconds = calibration_seconds
        self.closed_ratio = closed_ratio  # can be changed while running
        self._state = TrackerState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._recalibrate = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def recalibrate(self):
        """Re-learn the user's open-eye EAR (e.g. after changing seats or lighting)."""
        self._recalibrate.set()

    def get_state(self):
        with self._lock:
            return TrackerState(**vars(self._state))

    def _set_state(self, **changes):
        with self._lock:
            for key, value in changes.items():
                setattr(self._state, key, value)

    def _run(self):
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            self._set_state(error="Couldn't open the camera. Check camera permissions "
                                  "in System Settings > Privacy & Security > Camera.")
            return

        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
        )
        try:
            with vision.FaceLandmarker.create_from_options(options) as landmarker:
                self._loop(cap, landmarker)
        except Exception as e:
            self._set_state(error=f"Eye tracking stopped: {e}")
        finally:
            cap.release()

    def _loop(self, cap, landmarker):
        started = time.monotonic()
        last_timestamp_ms = -1
        closed_since = None
        calibration_ears = []
        calibration_started = None
        open_ear = None

        while not self._stop.is_set():
            if self._recalibrate.is_set():
                self._recalibrate.clear()
                calibration_ears, calibration_started, open_ear = [], None, None
                closed_since = None
                self._set_state(calibrating=True, eyes_closed=False, closed_seconds=0.0)

            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            frame = cv2.flip(frame, 1)  # mirror so the preview feels natural
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]

            # VIDEO mode requires strictly increasing timestamps.
            timestamp_ms = max(int((time.monotonic() - started) * 1000), last_timestamp_ms + 1)
            last_timestamp_ms = timestamp_ms
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp_ms
            )

            now = time.monotonic()
            if not result.face_landmarks:
                # No face: don't count it as closed eyes (handled properly later).
                closed_since = None
                self._set_state(face_found=False, ear=0.0, eyes_closed=False,
                                closed_seconds=0.0, frame=rgb)
                continue

            landmarks = result.face_landmarks[0]
            eyes = [
                [(landmarks[i].x * width, landmarks[i].y * height) for i in eye]
                for eye in (LEFT_EYE, RIGHT_EYE)
            ]
            ear = sum(eye_aspect_ratio(eye) for eye in eyes) / 2

            if open_ear is None:
                # Calibrating: learn this person's normal open-eye EAR.
                calibration_started = calibration_started or now
                calibration_ears.append(ear)
                if now - calibration_started >= self.calibration_seconds:
                    open_ear = statistics.median(calibration_ears)
                self._set_state(face_found=True, ear=ear, calibrating=open_ear is None,
                                open_ear=open_ear or 0.0, frame=rgb)
                continue

            ear_threshold = open_ear * self.closed_ratio
            eyes_closed = ear < ear_threshold

            if eyes_closed:
                closed_since = closed_since or now
            else:
                closed_since = None
            closed_seconds = now - closed_since if closed_since else 0.0

            self._set_state(face_found=True, ear=ear, ear_threshold=ear_threshold,
                            eyes_closed=eyes_closed, closed_seconds=closed_seconds, frame=rgb)
