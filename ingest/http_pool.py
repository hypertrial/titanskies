from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from contextvars import copy_context
from typing import Callable, TypeVar, cast

A = TypeVar("A")
B = TypeVar("B")

_MISSING = object()


def map_bounded(items: list[A], worker: Callable[[A], B], *, workers: int = 6) -> list[B]:
    if not items:
        return []
    if len(items) == 1 or workers <= 1:
        return [worker(item) for item in items]
    results: list[B | object] = [_MISSING] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = {pool.submit(copy_context().run, worker, item): index for index, item in enumerate(items)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    if any(item is _MISSING for item in results):
        raise RuntimeError("bounded worker returned no result")
    return cast(list[B], results)


def map_isolated(items: list[A], worker: Callable[[A], B], *, workers: int = 3) -> list[B | BaseException]:
    if not items:
        return []
    if len(items) == 1 or workers <= 1:
        results: list[B | BaseException] = []
        for item in items:
            try:
                results.append(worker(item))
            except BaseException as exc:
                results.append(exc)
        return results
    results: list[B | BaseException | object] = [_MISSING] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = {pool.submit(copy_context().run, worker, item): index for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except BaseException as exc:
                results[index] = exc
    if any(item is _MISSING for item in results):
        raise RuntimeError("isolated worker returned no result")
    return cast(list[B | BaseException], results)


def map_isolated_batches(
    items: list[A],
    worker: Callable[[A], B],
    *,
    workers: int = 3,
    stop_after: Callable[[list[B | BaseException]], bool] | None = None,
    before_batch: Callable[[], BaseException | None] | None = None,
) -> list[B | BaseException]:
    """Schedule at most one concurrency-sized batch ahead.

    Providers can stop a run after an outage without having already submitted
    every forecast hour to the executor.
    """
    size = max(1, workers)
    results: list[B | BaseException] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=min(size, len(items))) if size > 1 else nullcontext() as pool:
        for offset in range(0, len(items), size):
            stopped = before_batch() if before_batch else None
            if stopped is not None:
                results.append(stopped)
                break
            pending = items[offset:offset + size]
            if pool is None:
                batch = map_isolated(pending, worker, workers=1)
            else:
                futures = [pool.submit(copy_context().run, worker, item) for item in pending]
                batch = []
                for future in futures:
                    try:
                        batch.append(future.result())
                    except BaseException as exc:
                        batch.append(exc)
            results.extend(batch)
            if stop_after and stop_after(batch):
                break
    return results
