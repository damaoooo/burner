from __future__ import annotations

from typing import Protocol

from warpper.burner_backends import LookbusyCpuBackend


class BurnController(Protocol):
    def start(self) -> None:
        raise NotImplementedError

    def set_intensity(self, intensity: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_running(self) -> bool:
        raise NotImplementedError

    def owned_pid(self) -> int | None:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError


class CpuBurnController:
    def __init__(self, backend: LookbusyCpuBackend | None = None):
        self.backend = backend or LookbusyCpuBackend()
        self._intensity = 0.0

    def start(self) -> None:
        if self.is_running():
            return
        self.set_intensity(1.0)

    def set_intensity(self, intensity: float) -> None:
        intensity = min(1.0, max(0.0, intensity))
        if self.backend.process_pid is None:
            self.backend.prepare(0.0)
        self.backend.set_intensity(intensity, 0.0)
        self._intensity = intensity

    def stop(self) -> None:
        if self.backend.process_pid is not None:
            self.backend.set_intensity(0.0, 0.0)
        self._intensity = 0.0

    def shutdown(self) -> None:
        self._intensity = 0.0
        self.backend.stop()

    def is_running(self) -> bool:
        return self._intensity > 0 and self.backend.process_pid is not None

    def owned_pid(self) -> int | None:
        return self.backend.process_pid


class GpuBurnController:
    def start(self) -> None:
        raise NotImplementedError("GPU choker backend is not implemented yet")

    def set_intensity(self, intensity: float) -> None:
        del intensity
        raise NotImplementedError("GPU choker backend is not implemented yet")

    def stop(self) -> None:
        return None

    def is_running(self) -> bool:
        return False

    def owned_pid(self) -> int | None:
        return None

    def shutdown(self) -> None:
        return None
