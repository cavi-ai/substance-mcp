"""A settable clock for tests that must not depend on real time."""


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now
