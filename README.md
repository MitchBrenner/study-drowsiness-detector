# No-Sleep Study

**A study timer that notices when you fall asleep and wakes you up.**

No-Sleep Study watches your eyes through your laptop's webcam during a study
session. If your eyes stay closed for too long (5 seconds by default), it sounds
an alarm and sends a push notification to your phone every second until you open
your eyes. That makes it usable even in a quiet library.

All video is processed locally on your computer and is never saved or uploaded.

Built with **Python, OpenCV, MediaPipe and CustomTkinter**.

## Features

- **Eye tracking in real time:** MediaPipe's 478-point face mesh finds your
  eyelids in every webcam frame, about 30 times a second.
- **Calibrates to your eyes:** it learns how open *your* eyes normally are at
  the start of each session, so it works across different eye shapes, glasses
  and squinting.
- **Two kinds of alarm:**
  - A loud alarm sound. It raises your Mac's volume while it rings, then puts
    your volume back.
  - Phone push notifications every second through [ntfy](https://ntfy.sh). Setup
    is scanning a QR code.
- **Session stats:** session timer, wake-ups, time since you last dozed off, and
  total studied today.
- **Adjustable:** alarm volume, how long before it rings (1–60 s), detection
  sensitivity, and recalibration in the middle of a session. Settings are saved
  between runs.

## How it works

```
Webcam ──► OpenCV ──► MediaPipe Face Landmarker ──► Eye Aspect Ratio ──► closed for N seconds? ──► alarm + phone alerts
 (frame)    (mirror,     (478 face landmarks)         (per frame)          (calibrated threshold)
            RGB)
```

**Eye Aspect Ratio (EAR).** For each eye, six landmarks give the corners
(p1, p4) and two points on each eyelid (p2, p3 on top; p6, p5 below):

```
EAR = (|p2 − p6| + |p3 − p5|) / (2 · |p1 − p4|)
```

EAR stays roughly constant while the eye is open and drops toward 0 as it
closes. A fixed cutoff didn't work in testing: open, slightly squinting eyes
measured about 0.20, which is the usual "closed" threshold. So the app
**calibrates** instead. For the first 2 seconds of a session it takes the median
EAR as your open-eye baseline, then treats your eyes as closed below a
percentage of that baseline (65% by default, adjustable in settings).

**Threading.** Camera capture and face tracking run on a background thread, and
the UI reads the latest state about 30 times a second. Alarm sound and phone
alerts each run on their own thread, so a slow network never freezes the window.

## Getting started

Requires macOS (the alarm uses the built-in `afplay` and AppleScript volume
control) and Python 3.12.

```sh
git clone https://github.com/MitchBrenner/study-drowsiness-detector.git
cd study-drowsiness-detector

brew install python@3.12 python-tk@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python main.py
```

The first time you run it, macOS asks for camera access for your terminal or
editor. Allow it.

The MediaPipe face model (`models/face_landmarker.task`) and the alarm sound
(`sounds/alarm.wav`) are included in the repo.

## Usage

1. Click **Start Study Session** (or press **⌘Return**).
2. Look at the screen with your eyes open for about 2 seconds while it calibrates.
3. Study. If you nod off, the alarm rings until your eyes open.
4. Click **End Session** to see how long you studied.

**Phone alerts:** open Settings (**⚙** or **⌘,**), install the free ntfy app on
your phone, scan the QR code, and send a test notification.

## Project structure

| File | What it does |
| --- | --- |
| `main.py` | Entry point |
| `ui.py` | App window: camera preview, session view and settings page |
| `eye_tracker.py` | Webcam capture, face landmarks, EAR and calibration (background thread) |
| `alerts.py` | Alarm sound with volume control, and ntfy phone notifications |
| `config.py` | Defaults for timings, thresholds, sound and camera |
| `make_alarm_sound.py` | Generates the alarm sound with NumPy |

Your settings are stored in `settings.json` and your session history in
`sessions.json`. Neither file is committed.

## Notes

- `mediapipe` is pinned to 1.0.0 because 1.0.1 crashes on macOS on startup
  (`Check failed: service_ Service is unavailable`).
- Phone alerts use the free public ntfy.sh server. It allows a burst of about 60
  messages, then roughly 1 every 5 seconds.
- How fast a notification arrives depends on Apple's and Google's push services.
  On Android, turn on **Instant delivery** in the ntfy app for the fastest alerts.

## Roadmap

- Handle "no face visible" (looking away or leaving the desk)
- Phone call escalation if you stay asleep
- Cross-platform support, or a browser version that needs no install
