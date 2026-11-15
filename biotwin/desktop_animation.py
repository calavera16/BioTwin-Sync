"""Deterministic elapsed-time playback and source plotting (stdlib only)."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from .desktop_assets import as_waveform, preview
from .waveforms import replay_window


@dataclass
class Playback:
    end: float
    position: float = 0.0
    speed: float = 1.0
    running: bool = False
    anchor: float = 0.0

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        if self.running:
            self.position = min(self.end, self.position + max(0.0, now-self.anchor)*self.speed)
            self.anchor = now
            if self.position >= self.end:
                self.running = False
        return self.position

    def play(self, now=None):
        if self.position >= self.end:
            self.position = 0.0
        self.anchor = time.monotonic() if now is None else now
        self.running = self.end > 0

    def pause(self, now=None):
        self.sample(now)
        self.running = False

    def seek(self, value, now=None):
        if not math.isfinite(float(value)):
            raise ValueError('Playback position must be finite.')
        self.position = max(0.0, min(self.end, float(value)))
        self.anchor = time.monotonic() if now is None else now
        if self.position >= self.end:
            self.running = False

    def set_speed(self, value, now=None):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Playback speed must be finite and positive.')
        self.sample(now)
        self.speed = value


def packet_progress(sent, arrives, now):
    """None before send; capped position after arrival. No pending contents."""
    if now < sent:
        return None
    return min(1.0, max(0.0, (now-sent)/max(arrives-sent, 1e-9)))


class RecordTrace:
    """Convert verified arrays once, then request bounded min/max windows."""
    def __init__(self, example):
        self.example = example
        self.waveform = as_waveform(example)
        self.first, self.last = example['times'][0], example['times'][-1]
        self.overlays = preview(example, self.first)['author_exclusions']
        self.playback = Playback(self.last-self.first)

    def window(self):
        cursor = self.first+self.playback.position
        duration = min(10.0, self.last-self.first)
        start = max(self.first, min(cursor-8.0, self.last-duration))
        result = replay_window(self.waveform, start, duration, 900)
        result['author_exclusions'] = self.overlays
        result['cursor'] = cursor
        return result
