"""
3-Phase Circuit Breaker (CLOSED / OPEN / HALF_OPEN).
Prevents cascading failure by fast-failing downstream calls when error threshold is met.
"""

import enum
import random
import time
from typing import Callable, Any


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Raised when call is attempted on an OPEN circuit."""
    pass


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_time_seconds: float = 4.0,
        half_open_success_threshold: int = 2
    ):
        self.failure_threshold = failure_threshold
        self.recovery_time_seconds = recovery_time_seconds
        self.half_open_success_threshold = half_open_success_threshold

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_state_change = time.time()

    def call(self, func: Callable, *args, **kwargs) -> Any:
        now = time.time()

        # Check transition from OPEN to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.recovery_time_seconds:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                self.last_state_change = now
            else:
                raise CircuitBreakerOpenException(
                    f"Circuit Breaker is OPEN. Fast-fail in 0.1ms. Next probe in "
                    f"{max(0.0, self.recovery_time_seconds - (now - self.last_state_change)):.2f}s"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                self.last_state_change = time.time()
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def _on_failure(self):
        self.failure_count += 1
        self.last_state_change = time.time()
        if self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if self.failure_count >= self.failure_threshold or self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN

    def compute_backoff(self, attempt: int, base_delay: float = 0.5, max_delay: float = 8.0) -> float:
        """Full Jitter Exponential Backoff."""
        temp = min(max_delay, base_delay * (2 ** attempt))
        return random.uniform(0, temp)

