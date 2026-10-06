"""Generates sounds/alarm.wav: an iPhone-style alarm with bursts of fast, piercing beeps.

Run once (or after tweaking the numbers below): .venv/bin/python make_alarm_sound.py
"""

import wave

import numpy as np

import config

SAMPLE_RATE = 44100
PITCHES = (2000, 2600)   # Hz; alternating pitches make it chirpy and hard to ignore
BEEP_SECONDS = 0.07
GAP_SECONDS = 0.04
BEEPS_PER_BURST = 4
BURST_PAUSE_SECONDS = 0.35
BURSTS = 3               # one file = 3 bursts; the app loops it


def tone(freq, seconds):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    # A square-ish wave (sine plus odd harmonics) sounds harsher than a pure sine.
    wave_ = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 3, 5))
    # Short fade in/out so the beeps click less.
    fade = min(len(t) // 10, int(SAMPLE_RATE * 0.005))
    envelope = np.ones(len(t))
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[-fade:] = np.linspace(1, 0, fade)
    return wave_ * envelope


def silence(seconds):
    return np.zeros(int(SAMPLE_RATE * seconds))


def build_alarm():
    parts = []
    for _ in range(BURSTS):
        for i in range(BEEPS_PER_BURST):
            parts += [tone(PITCHES[i % 2], BEEP_SECONDS), silence(GAP_SECONDS)]
        parts.append(silence(BURST_PAUSE_SECONDS))
    audio = np.concatenate(parts)
    return audio / np.abs(audio).max() * 0.98  # as loud as possible without clipping


def main():
    audio = build_alarm()
    config.ALARM_SOUND.parent.mkdir(exist_ok=True)
    with wave.open(str(config.ALARM_SOUND), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes((audio * 32767).astype(np.int16).tobytes())
    print(f"Wrote {config.ALARM_SOUND}")


if __name__ == "__main__":
    main()
