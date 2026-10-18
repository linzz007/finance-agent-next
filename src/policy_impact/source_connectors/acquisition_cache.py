"""Small process-local public-source cache with single-flight acquisition."""

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from typing import Hashable
import time


_LOCK = Lock()
_CACHE: OrderedDict[Hashable, tuple[float, object, float]] = OrderedDict()
_RUNNING: dict[Hashable, Future] = {}
MAX_ENTRIES = 16


def cached_acquisition(key, loader, *, ttl_seconds=300, ttl_for_result=None):
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < min(ttl_seconds, cached[2]):
            _CACHE.move_to_end(key)
            return deepcopy(cached[1]), True
        future = _RUNNING.get(key)
        owner = future is None
        if owner:
            future = Future()
            _RUNNING[key] = future
    if not owner:
        return deepcopy(future.result()), True
    try:
        value = loader()
        result_ttl = max(0, min(ttl_seconds, ttl_for_result(value))) if ttl_for_result else ttl_seconds
        with _LOCK:
            _CACHE[key] = (time.monotonic(), deepcopy(value), result_ttl)
            _CACHE.move_to_end(key)
            while len(_CACHE) > MAX_ENTRIES:
                _CACHE.popitem(last=False)
        future.set_result(value)
        return deepcopy(value), False
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _LOCK:
            _RUNNING.pop(key, None)
