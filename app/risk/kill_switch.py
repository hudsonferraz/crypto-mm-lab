class KillSwitch:
    def __init__(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def enable(self) -> None:
        self._active = True

    def disable(self) -> None:
        self._active = False

    def toggle(self) -> bool:
        self._active = not self._active
        return self._active
