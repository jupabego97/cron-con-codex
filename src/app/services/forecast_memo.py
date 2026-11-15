"""Small process-local memo: no Redis, database writes, or background threads."""

import time
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock
from typing import Any


class ForecastMemo:
    def __init__(
        self, *, max_entries: int = 2, max_products: int = 10000, ttl_seconds: float = 3600
    ) -> None:
        self._entries: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._lock = RLock()
        self._max_entries = max_entries
        self._max_products = max_products
        self._ttl_seconds = ttl_seconds

    def get_or_compute(self, key: tuple, *, product_count: int, compute: Callable) -> Any:
        # Single-flight prevents duplicate backtests for simultaneous requests.
        with self._lock:
            now = time.monotonic()
            for expired in [k for k, (until, _) in self._entries.items() if until <= now]:
                del self._entries[expired]
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached[1]
            result = compute()  # Exceptions must never become cached results.
            if product_count <= self._max_products and self._max_entries > 0:
                self._entries[key] = (time.monotonic() + self._ttl_seconds, result)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return result


forecast_memo = ForecastMemo()
