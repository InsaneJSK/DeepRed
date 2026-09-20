"""Propagate a window close out of nested navigation and battle loops."""

from contextlib import contextmanager

from pyboy import PyBoy


class EmulatorClosed(Exception):
    """Normal shutdown requested through the emulator window."""


class EmulatorSession:
    """Forward PyBoy access, turning its quit return value into cancellation.

    All controllers share this wrapper so a close is respected even inside a
    dialogue wait or movement animation, without treating it as a blocked move.
    The entry point owns cleanup and catches EmulatorClosed once.
    """

    def __init__(self, emulator):
        self._emulator = emulator

    def __getattr__(self, name):
        return getattr(self._emulator, name)

    def tick(self, *args, **kwargs):
        if not self._emulator.tick(*args, **kwargs):
            raise EmulatorClosed()
        return True


class FrameLimitReached(TimeoutError):
    """An operation exhausted its emulated-frame budget."""


class FrameBudget:
    """Count every frame, including input holds and settling within an operation."""

    def __init__(self, emulator, limit):
        self.emulator = emulator
        self.remaining = max(0, limit)

    def tick(self, count=1):
        for _ in range(count):
            if self.remaining <= 0:
                raise FrameLimitReached("Emulator frame budget exhausted")
            self.remaining -= 1
            self.emulator.tick()

    def press(self, button, release, hold, settle):
        if self.remaining <= 0:
            raise FrameLimitReached("Emulator frame budget exhausted")
        self.emulator.send_input(button)
        try:
            self.tick(hold)
        finally:
            self.emulator.send_input(release)
        self.tick(settle)


@contextmanager
def open_emulator(rom, **options):
    """Interactive utility lifetime: close cancels loops; never write cartridge RAM."""
    emulator = PyBoy(str(rom), **options)
    try:
        yield EmulatorSession(emulator)
    except (EmulatorClosed, KeyboardInterrupt):
        print("Emulator closed.")
    finally:
        emulator.stop(save=False)
