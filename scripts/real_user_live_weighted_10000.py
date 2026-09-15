#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
import random

import scripts.real_user_live_simulation_10000 as core
from scripts.real_user_population_10000 import POPULATION, SyntheticSession

# Approximate consumer-chat mix informed by public usage research while keeping
# explicit regression capacity for context/memory/routing and expensive long-form.
_CATEGORY_WEIGHTS = {
    "instant": 0.04,
    "structured": 0.08,
    "atomic": 0.12,
    "direct": 0.13,
    "web_advice": 0.16,
    "fresh_web": 0.09,
    "writing": 0.19,
    "context": 0.05,
    "memory": 0.03,
    "longform": 0.04,
    "routing_edge": 0.07,
}


def _balanced_take(rows: list[SyntheticSession], count: int, rng: random.Random) -> list[SyntheticSession]:
    groups: defaultdict[tuple[str, str], list[SyntheticSession]] = defaultdict(list)
    for row in rows:
        groups[(row.persona, row.style)].append(row)
    for bucket in groups.values():
        rng.shuffle(bucket)
    keys = list(groups)
    rng.shuffle(keys)
    result: list[SyntheticSession] = []
    cursor = 0
    while len(result) < count and keys:
        key = keys[cursor % len(keys)]
        bucket = groups[key]
        if bucket:
            result.append(bucket.pop())
        else:
            keys.remove(key)
            cursor -= 1
        cursor += 1
    return result


def _weighted_select(limit: int, *, seed: int, full: bool, longform_limit: int) -> list[SyntheticSession]:
    if full or limit >= len(POPULATION):
        return list(POPULATION)
    limit = max(100, min(int(limit), len(POPULATION)))
    rng = random.Random(seed)
    by_category: defaultdict[str, list[SyntheticSession]] = defaultdict(list)
    for row in POPULATION:
        by_category[row.category].append(row)

    targets = {name: int(limit * weight) for name, weight in _CATEGORY_WEIGHTS.items()}
    remainder = limit - sum(targets.values())
    order = sorted(_CATEGORY_WEIGHTS, key=_CATEGORY_WEIGHTS.get, reverse=True)
    for index in range(remainder):
        targets[order[index % len(order)]] += 1

    if targets.get("longform", 0) > longform_limit:
        overflow = targets["longform"] - longform_limit
        targets["longform"] = longform_limit
        # Reallocate expensive long-form capacity to the three dominant normal-use buckets.
        refill = ("web_advice", "writing", "direct", "atomic", "fresh_web")
        for index in range(overflow):
            targets[refill[index % len(refill)]] += 1

    selected: list[SyntheticSession] = []
    selected_ids: set[str] = set()
    for category, target in targets.items():
        rows = by_category.get(category, [])
        picked = _balanced_take(rows, min(target, len(rows)), rng)
        selected.extend(picked)
        selected_ids.update(row.id for row in picked)

    if len(selected) < limit:
        candidates = [row for row in POPULATION if row.id not in selected_ids and row.category != "longform"]
        selected.extend(_balanced_take(candidates, limit - len(selected), rng))

    rng.shuffle(selected)
    return selected[:limit]


core._select_population = _weighted_select


if __name__ == "__main__":
    raise SystemExit(core.main())
