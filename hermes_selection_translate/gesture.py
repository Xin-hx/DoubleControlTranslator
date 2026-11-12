from __future__ import annotations


class DoubleCtrlDetector:
    """Accept exactly two complete Ctrl-only taps within the interval."""

    CTRL_KEYS = {0x11, 0xA2, 0xA3}

    def __init__(self, interval_ms: int = 350):
        self.interval_ms = interval_ms
        self.ctrl_down: set[int] = set()
        self.clean = False
        self.first_release: int | None = None

    def reset(self) -> None:
        self.ctrl_down.clear()
        self.clean = False
        self.first_release = None

    def feed(self, vk: int, is_down: bool, timestamp_ms: int, injected: bool = False,
             other_modifier_down: bool = False) -> bool:
        if injected:
            return False
        if vk not in self.CTRL_KEYS:
            # Any intervening key (including A after the first tap) invalidates
            # the sequence. Key-up is included so a key held before the first
            # Ctrl tap cannot leave a valid-looking sequence behind.
            self.first_release = None
            if self.ctrl_down:
                self.clean = False
            return False
        if is_down:
            if vk in self.ctrl_down:  # auto-repeat
                return False
            if not self.ctrl_down:
                self.clean = not other_modifier_down
                if other_modifier_down:
                    self.first_release = None
            self.ctrl_down.add(vk)
            return False
        if vk not in self.ctrl_down:
            return False
        self.ctrl_down.remove(vk)
        if self.ctrl_down:
            return False
        if not self.clean:
            self.first_release = None
            return False
        if self.first_release is not None:
            elapsed = (timestamp_ms - self.first_release) & 0xFFFFFFFF
            if elapsed <= self.interval_ms:
                self.first_release = None
                return True
        self.first_release = timestamp_ms
        return False
