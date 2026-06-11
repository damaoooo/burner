from __future__ import annotations

import logging
from enum import Enum
from threading import Event

from .burn import BurnController
from .monitor import CpuLoadMonitor


class ChokerStrategy(str, Enum):
    COMPLEMENT = "complement"
    IDLE = "idle"


class ChokerDaemon:
    def __init__(
        self,
        monitor: CpuLoadMonitor,
        burner: BurnController,
        threshold_percent: float,
        window_seconds: float,
        strategy: ChokerStrategy | str = ChokerStrategy.COMPLEMENT,
        target_percent: float = 100.0,
        logger: logging.Logger | None = None,
    ):
        if threshold_percent < 0 or threshold_percent > 100:
            raise ValueError("threshold must be between 0 and 100")
        if target_percent < 0 or target_percent > 100:
            raise ValueError("target must be between 0 and 100")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than 0")
        self.monitor = monitor
        self.burner = burner
        self.threshold_percent = threshold_percent
        self.window_seconds = window_seconds
        self.strategy = ChokerStrategy(strategy)
        self.target_percent = target_percent
        self.logger = logger or logging.getLogger(__name__)
        self._stop_requested = Event()
        self._last_intensity: float | None = None

    def request_stop(self) -> None:
        self._stop_requested.set()

    def run(self, max_iterations: int | None = None) -> None:
        iterations = 0
        try:
            while not self._stop_requested.is_set():
                self.step()
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
        finally:
            self.shutdown()

    def step(self) -> None:
        owned_pid = self.burner.owned_pid()
        load = self.monitor.sample(self.window_seconds, owned_pid)
        self.logger.info(
            "cpu sample total=%.2f owned=%.2f external=%.2f threshold=%.2f target=%.2f strategy=%s",
            load.total_percent,
            load.owned_percent,
            load.external_percent,
            self.threshold_percent,
            self.target_percent,
            self.strategy.value,
        )

        intensity = self._desired_intensity(load.external_percent)
        self._apply_intensity(intensity)

    def _desired_intensity(self, external_percent: float) -> float:
        if self.strategy == ChokerStrategy.IDLE:
            return 1.0 if external_percent < self.threshold_percent else 0.0
        return min(1.0, max(0.0, (self.target_percent - external_percent) / 100.0))

    def _apply_intensity(self, intensity: float) -> None:
        if intensity <= 0:
            if self.burner.is_running():
                self._stop_burn("external CPU load reached target")
            else:
                self._last_intensity = 0.0
            return

        if (
            self._last_intensity is not None
            and abs(intensity - self._last_intensity) < 0.01
            and self.burner.is_running()
        ):
            return

        try:
            self.burner.set_intensity(intensity)
        except AttributeError:
            self._start_burn()
            self._last_intensity = 1.0
            return
        except Exception:
            self.logger.exception("failed to set CPU burn intensity")
            try:
                self.burner.stop()
            except Exception:
                self.logger.exception("failed to clean up CPU burn after intensity error")
            self._last_intensity = 0.0
            return

        self.logger.info("set CPU burn intensity to %.2f", intensity)
        self._last_intensity = intensity

    def shutdown(self) -> None:
        if hasattr(self.burner, "shutdown"):
            try:
                self.burner.shutdown()
            except Exception:
                self.logger.exception("failed to shut down burn backend")
                return
            self.logger.info("shut down burn backend")
            return

        if self.burner.is_running():
            self._stop_burn("daemon shutdown")

    def _start_burn(self) -> None:
        try:
            self.burner.start()
        except Exception:
            self.logger.exception("failed to start CPU burn")
            try:
                if self.burner.is_running():
                    self.burner.stop()
            except Exception:
                self.logger.exception("failed to clean up CPU burn after start error")
            return
        self.logger.info("started CPU burn")
        self._last_intensity = 1.0

    def _stop_burn(self, reason: str) -> None:
        try:
            self.burner.stop()
        except Exception:
            self.logger.exception("failed to stop CPU burn")
            return
        self.logger.info("stopped CPU burn: %s", reason)
        self._last_intensity = 0.0
