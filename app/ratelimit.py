import time
from typing import Dict, Tuple


class RateLimiter:
    """
    Simple in-memory token bucket per key (e.g., client IP).
    Resets on restart (intentional).

    Bounded on purpose. `state` is keyed by client IP, and nothing ever removed
    an entry: every distinct source address added one for the process lifetime,
    so a caller rotating IPs grew this dict without limit until the worker died
    of memory exhaustion — a way to take the service down that the rate limit
    itself does not prevent, because each individual request is allowed.

    The bound has to be chosen carefully, because evicting a key only ever
    GRANTS capacity — a forgotten caller starts again with a full bucket. So the
    rule is: forget whoever would gain the least.

    First, entries whose bucket has fully refilled with time are dropped; they
    are indistinguishable from a first-time caller and carry no information. If
    the table is still full, the remaining entries are dropped in order of how
    many tokens they currently hold, MOST first.

    Evicting the least-recently-seen instead is the obvious choice and it is
    backwards: a caller who has just burned its whole bucket and is being
    throttled is, by construction, the one whose `last` timestamp stops moving
    while other traffic arrives. LRU therefore drops exactly the callers the
    limiter exists to restrain, handing an abuser a fresh bucket whenever the
    table fills. A test pins this ("an active caller's bucket was dropped").
    """

    MAX_KEYS = 10_000

    def __init__(self, rate_per_min: int):
        self.rate = max(1, int(rate_per_min))
        self.capacity = float(self.rate)
        self.refill_per_sec = float(self.rate) / 60.0
        self.state: Dict[str, Tuple[float, float]] = {}  # key -> (tokens, last_ts)
        # Seconds after which an untouched bucket is certainly full again.
        self._full_after = self.capacity / self.refill_per_sec

    def _tokens_now(self, tokens: float, last: float, now: float) -> float:
        return min(self.capacity, tokens + (now - last) * self.refill_per_sec)

    def _evict(self, now: float) -> None:
        # Fully refilled by the passage of time: nothing to remember.
        for key, (_tokens, last) in list(self.state.items()):
            if now - last >= self._full_after:
                del self.state[key]
        if len(self.state) < self.MAX_KEYS:
            return
        # Still full. Drop whoever holds the most tokens, because forgetting a
        # key hands it a full bucket and they are already closest to that.
        ordered = sorted(
            self.state.items(),
            key=lambda kv: self._tokens_now(kv[1][0], kv[1][1], now),
            reverse=True,
        )
        for key, _ in ordered[: len(self.state) - self.MAX_KEYS // 2]:
            del self.state[key]

    def allow(self, key: str) -> bool:
        now = time.time()
        if key not in self.state and len(self.state) >= self.MAX_KEYS:
            self._evict(now)

        tokens, last = self.state.get(key, (self.capacity, now))

        # Refill bucket
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)

        if tokens >= 1.0:
            tokens -= 1.0
            self.state[key] = (tokens, now)
            return True

        self.state[key] = (tokens, now)
        return False