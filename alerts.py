"""Ways to wake the user up: a loud sound on the computer and push
notifications to the phone (via ntfy.sh)."""

import subprocess
import threading
import time

import requests


def _osascript(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


class SoundAlarm:
    """Plays a sound on repeat (via macOS `afplay`) until stopped.

    While active, the Mac's output volume is raised to `system_volume` (and
    unmuted); the previous volume is restored when the alarm stops."""

    def __init__(self, sound_path, volume_boost=1.0, system_volume=None):
        self.sound_path = sound_path
        self.volume_boost = volume_boost
        self.system_volume = system_volume
        self._stop = threading.Event()
        self._thread = None
        self._process = None
        self._saved_volume = None
        self._repeating = False

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active and not self._repeating:
            self.stop()  # a real alarm takes over from a test sound
        self._start(repeat=True)

    def play_once(self):
        """Play the sound a single time at the alarm volume (for testing)."""
        self._start(repeat=False)

    def _start(self, repeat):
        if self.active:
            return
        self._repeating = repeat
        self._raise_system_volume()
        self._stop.clear()
        self._thread = threading.Thread(target=self._play, args=(repeat,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
        self._thread = None
        self._restore_system_volume()

    def _play(self, repeat):
        while not self._stop.is_set():
            self._process = subprocess.Popen(["afplay", "-v", str(self.volume_boost), self.sound_path])
            self._process.wait()
            if not repeat:
                break
        if not repeat:
            self._restore_system_volume()

    def _raise_system_volume(self):
        if self.system_volume is None or self._saved_volume is not None:
            return
        volume = _osascript("output volume of (get volume settings)")
        muted = _osascript("output muted of (get volume settings)")
        if volume.isdigit():
            self._saved_volume = (int(volume), muted == "true")
        _osascript(f"set volume output volume {self.system_volume} without output muted")

    def _restore_system_volume(self):
        if self._saved_volume is None:
            return
        volume, muted = self._saved_volume
        self._saved_volume = None
        _osascript(f"set volume output volume {volume}" + (" with output muted" if muted else ""))


class PhoneAlert:
    """Sends ntfy push notifications to the user's phone every `interval`
    seconds until stopped. The phone subscribes to `topic` in the ntfy app."""

    def __init__(self, server, topic, interval=1.0):
        self.server = server.rstrip("/")
        self.topic = topic
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        # Reusing one connection skips a fresh TLS handshake (~0.5s) per alert.
        self._http = requests.Session()

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def warm_up(self):
        """Open the connection in the background so the first real alert goes out fast."""
        def ping():
            try:
                self._http.get(f"{self.server}/v1/health", timeout=5)
            except requests.RequestException:
                pass
        threading.Thread(target=ping, daemon=True).start()

    def start(self):
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None

    def send(self, title, message):
        """Send one notification. Raises requests.RequestException on failure."""
        response = self._http.post(
            f"{self.server}/{self.topic}",
            data=message.encode(),
            headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"},
            timeout=5,
        )
        response.raise_for_status()

    def _loop(self):
        started = time.monotonic()
        count = 0
        while not self._stop.is_set():
            count += 1
            try:
                self.send("WAKE UP!", f"Your eyes are closed — open them! (alert #{count})")
            except requests.RequestException as e:
                print(f"[phone] alert #{count} failed: {e}")  # offline or rate-limited; keep trying
            # Fixed schedule (0s, 1s, 2s, ...) so slow sends don't stretch the gaps.
            self._stop.wait(max(0.0, started + count * self.interval - time.monotonic()))
