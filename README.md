# No-Sleep Study

A macOS study timer that watches your eyes through the webcam. If they stay
closed too long (5 seconds by default), it sounds an alarm and buzzes your phone
until you wake up. All video is processed locally and never leaves your computer.

## Setup

```sh
brew install python@3.12 python-tk@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The MediaPipe face model (`models/face_landmarker.task`) and the alarm sound
(`sounds/alarm.wav`) are included in the repo.

> `mediapipe` is pinned to 1.0.0 because 1.0.1 crashes on macOS
> (`Check failed: service_ Service is unavailable`).

## Run

```sh
.venv/bin/python main.py
```

The first time, macOS will ask to let your terminal/editor use the camera — allow it.

Click **Start Study Session** (or press ⌘Return), keep your eyes open for ~2
seconds while it calibrates, then study. Click **End Session** to stop.

The ⚙ button opens Settings: alarm volume and delay, phone alerts (scan the QR
code with your phone after installing the free [ntfy](https://ntfy.sh) app),
and detection sensitivity / recalibration.

Your settings are saved in `settings.json`, and finished sessions in
`sessions.json` (used for the "Studied today" total).

## How it works

- `eye_tracker.py`: OpenCV reads webcam frames, MediaPipe Face Landmarker finds
  eye landmarks, and we compute the **Eye Aspect Ratio** (eye height / width).
  It calibrates to your open-eye EAR, then counts eyes as closed when EAR drops
  below 65% of that.
- `alerts.py`: loops the alarm sound and sends phone pushes ([ntfy](https://ntfy.sh)) every second until your eyes open.
- `make_alarm_sound.py`: generates `sounds/alarm.wav`, an iPhone-style beeping alarm.
- `ui.py`: CustomTkinter window: camera on the left; session view or settings page on the right.
- `config.py`: thresholds, timings, sound and camera settings.

## Next up

- Phone call escalation (Twilio) if asleep a long time
- Handling "no face visible" (looked away / left the desk)
