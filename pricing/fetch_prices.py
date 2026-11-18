#!/usr/bin/env python3
"""Builds data/prices.json for the CameraPick app.

For every catalog item and market, collects offers from that market's sources,
keeps the cheapest in-stock new offers and records where each price came from.

Usage:
    python pricing/fetch_prices.py                 # all markets, all items
    python pricing/fetch_prices.py --markets TR --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "sources"))

import akakce  # noqa: E402
import bestbuy  # noqa: E402
import ebay  # noqa: E402
import retailer  # noqa: E402
from common import MarketResult, PoliteClient, fetch_usd_rates, now_iso  # noqa: E402

ROOT = HERE.parent
CATALOG_PATH = HERE / "catalog.json"
MATCH_CACHE_PATH = HERE / "match_cache.json"
OUTPUT_PATH = ROOT / "data" / "prices.json"

MARKETS: dict[str, dict[str, Any]] = {
    "TR": {"currency": "TRY", "sources": ["akakce"]},
    "US": {"currency": "USD", "sources": ["abt", "bestbuy", "ebay"]},
}

# Rejects matches whose price is wildly off the catalog's USD reference price.
PLAUSIBLE_RATIO = (0.45, 4.0)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def akakce_market(client: PoliteClient, item: dict[str, Any], cache: dict[str, Any]) -> MarketResult | None:
    key = f"akakce:{item['id']}"
    cached = cache.get(key)
    if cached is None or cached.get("url") is None and time.time() - cached.get("checkedAt", 0) > 7 * 86400:
        match = akakce.find_product_url(client, item)
        cached = {"url": match[0] if match else None, "title": match[1] if match else None, "checkedAt": int(time.time())}
        cache[key] = cached
    if not cached.get("url"):
        return None
    return akakce.fetch_offers(client, cached["url"], cached.get("title") or "")


SOURCE_FETCHERS: dict[str, Callable[[PoliteClient, dict[str, Any], dict[str, Any]], MarketResult | None]] = {
    "akakce": akakce_market,
    "bestbuy": bestbuy.fetch,
    "ebay": ebay.fetch,
    "abt": retailer.ABT.fetch,
}


def merge(results: list[MarketResult]) -> MarketResult | None:
    results = [r for r in results if r and r.offers]
    if not results:
        return None
    merged = MarketResult()
    for r in results:
        merged.offers.extend(r.offers)
        merged.offer_count += r.offer_count
    primary = min(results, key=lambda r: min(o.price for o in r.offers))
    merged.source_url = primary.source_url
    merged.matched_title = primary.matched_title
    return merged


def is_plausible(result: MarketResult, item: dict[str, Any], currency: str, rates: dict[str, float]) -> bool:
    reference_usd = item.get("referenceUSD") or 0
    if reference_usd <= 0 or currency not in rates:
        return True
    best_usd = min(o.price for o in result.offers) / rates[currency]
    low, high = PLAUSIBLE_RATIO
    return low <= best_usd / reference_usd <= high


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default=",".join(MARKETS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", default="", help="comma separated catalog ids")
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()

    catalog: list[dict[str, Any]] = load_json(CATALOG_PATH, [])
    if args.ids:
        wanted = set(args.ids.split(","))
        catalog = [c for c in catalog if c["id"] in wanted]
    if args.limit:
        catalog = catalog[: args.limit]

    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip().upper() in MARKETS]
    client = PoliteClient(delay_seconds=args.delay)
    cache = load_json(MATCH_CACHE_PATH, {})
    rates = fetch_usd_rates(client)

    previous = load_json(OUTPUT_PATH, {})
    items: dict[str, Any] = previous.get("items", {})
    health: dict[str, dict[str, int]] = {}
    started = now_iso()

    for index, item in enumerate(catalog, 1):
        entry = items.setdefault(item["id"], {})
        for market in markets:
            config = MARKETS[market]
            results = []
            for source in config["sources"]:
                stats = health.setdefault(source, {"ok": 0, "miss": 0, "error": 0})
                try:
                    result = SOURCE_FETCHERS[source](client, item, cache)
                except Exception:  # one broken source must never stop the run
                    stats["error"] += 1
                    traceback.print_exc(limit=1)
                    continue
                if result and result.offers:
                    stats["ok"] += 1
                    results.append(result)
                else:
                    stats["miss"] += 1
            merged = merge(results)
            if merged and not is_plausible(merged, item, config["currency"], rates):
                print(f"  ! implausible price for {item['id']} {market}: {merged.matched_title}", flush=True)
                merged = None
            if merged:
                entry[market] = merged.to_json(fetched_at=now_iso())
            # Keep a recent previous price (< 3 days) if this run failed; otherwise drop it.
            elif market in entry and entry[market].get("fetchedAt", "") < _days_ago(3):
                entry.pop(market, None)
        best = {m: entry[m]["best"]["price"] for m in markets if m in entry}
        print(f"[{index}/{len(catalog)}] {item['id']}: {best}", flush=True)
        if index % 20 == 0:
            MATCH_CACHE_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")

    output = {
        "version": 1,
        "generatedAt": now_iso(),
        "runStartedAt": started,
        "usdRates": {k: round(v, 4) for k, v in rates.items() if k in {"USD", "TRY", "EUR", "GBP", "CHF", "JPY"}},
        "markets": {m: {"currency": MARKETS[m]["currency"], "sources": MARKETS[m]["sources"]} for m in MARKETS},
        "sourceHealth": health,
        "items": {k: v for k, v in sorted(items.items()) if v},
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    MATCH_CACHE_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"health: {health}")
    return 0


def _days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
