import time
from typing import Dict, Tuple

class RateLimiter:
    """Simple in-memory token bucket per key (e.g., client IP). Resets on restart."""
    def __init__(self, rate_per_min: int):
        self.rate = max(1, int(rate_per_min))
        self.capacity = float(self.rate)
        self.refill_per_sec = float(self.rate) / 60.0
        self.state: Dict[str, Tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def allow(self, key: str) -> bool:
        now = time.time()
        tokens, last = self.state.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens >= 1.0:
            tokens -= 1.0
            self.state[key] = (tokens, now)
            return True
        self.state[key] = (tokens, now)
        return False
