"""Daily price history per product, served as data/history/<id>.json.

Each file maps a market to a list of [YYYY-MM-DD, lowest price that day]:
    {"currency": {"TR": "TRY"}, "TR": [["2026-11-19", 61999.0], ...]}
The app draws its price chart from these and compares today's price with the range.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_DAYS = 400


def update(history_dir: Path, items: dict[str, Any], markets: list[str] | None = None) -> int:
    """Records today's best price for every item/market in `items` (prices.json "items").
    Keeps the lowest price seen per day. Returns how many files changed."""
    history_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for item_id, entry in items.items():
        path = history_dir / f"{item_id}.json"
        data = _load(path)
        before = json.dumps(data, sort_keys=True)
        for market, result in entry.items():
            if markets and market not in markets:
                continue
            best = (result or {}).get("best") or {}
            price = best.get("price")
            day = (result.get("fetchedAt") or "")[:10]
            if not price or len(day) != 10:
                continue
            add_point(data, market, day, float(price), result.get("currency") or best.get("currency") or "")
        if json.dumps(data, sort_keys=True) != before:
            path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            changed += 1
    return changed


def add_point(data: dict[str, Any], market: str, day: str, price: float, currency: str) -> None:
    series: list[list[Any]] = data.setdefault(market, [])
    if currency:
        data.setdefault("currency", {})[market] = currency
    for point in series:
        if point[0] == day:
            point[1] = round(min(point[1], price), 2)
            break
    else:
        series.append([day, round(price, 2)])
        series.sort(key=lambda p: p[0])
        del series[:-MAX_DAYS]


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
