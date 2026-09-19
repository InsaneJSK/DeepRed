"""Propagate a window close out of nested navigation and battle loops."""


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
