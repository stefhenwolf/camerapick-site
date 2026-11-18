"""Generic single-retailer source.

Many retailers expose a public search page and schema.org Product/Offer JSON-LD on
their product pages. A `Retailer` only needs to know how to list search results; the
matching (shared with Akakçe) and offer parsing are common. Only robots.txt-allowed
paths are requested.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

import akakce
from common import MarketResult, Offer, PoliteClient, tokens


@dataclass
class Retailer:
    key: str
    store_name: str
    currency: str
    search_url: str  # with {q}
    results: Callable[[str], list[tuple[str, str]]]  # page html -> [(absolute url, title)]

    def fetch(self, client: PoliteClient, item: dict[str, Any], cache: dict[str, Any]) -> MarketResult | None:
        cache_key = f"{self.key}:{item['id']}"
        cached = cache.get(cache_key)
        if cached is None:
            match = self._find(client, item)
            cached = {"url": match[0] if match else None, "title": match[1] if match else None}
            cache[cache_key] = cached
        if not cached.get("url"):
            return None
        offer = self._offer(client, cached["url"])
        if offer is None:
            return None
        return MarketResult(offers=[offer], offer_count=1, source_url=cached["url"], matched_title=cached.get("title") or "")

    def _find(self, client: PoliteClient, item: dict[str, Any]) -> tuple[str, str] | None:
        query = urllib.parse.quote_plus(f"{item['brandName']} {item['model']}")
        page = client.get(self.search_url.format(q=query))
        best: tuple[float, str, str] | None = None
        for url, title in self.results(page):
            trimmed = from_brand(title, item["brandName"])
            if trimmed is None:
                continue
            score = akakce._score(item, trimmed)
            if score is None or score < 4:
                continue
            if best is None or score > best[0]:
                best = (score, url, title)
        return (best[1], best[2]) if best else None

    def _offer(self, client: PoliteClient, url: str) -> Offer | None:
        page = client.get(url)
        for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            for node in data if isinstance(data, list) else [data]:
                if not isinstance(node, dict) or node.get("@type") != "Product":
                    continue
                offers = node.get("offers") or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                if not isinstance(offers, dict):
                    continue
                if "InStock" not in str(offers.get("availability", "InStock")):
                    return None
                if "NewCondition" not in str(offers.get("itemCondition", "NewCondition")):
                    return None
                try:
                    price = float(offers.get("price") or offers.get("lowPrice") or 0)
                except (TypeError, ValueError):
                    return None
                if price <= 0 or offers.get("priceCurrency", self.currency) != self.currency:
                    return None
                return Offer(store=self.store_name, price=price, currency=self.currency, url=url, source=self.key)
        return None


def from_brand(title: str, brand_name: str) -> str | None:
    """Drops leading category words ("Appareil photo Hybride SONY A7 IV" -> "SONY A7 IV")."""
    brand = tokens(brand_name)[0]
    words = title.split()
    for index, word in enumerate(words):
        if tokens(word) and tokens(word)[0] == brand:
            return " ".join(words[index:])
    return None


# ---------------------------------------------------------------- retailers

def _abt_results(page: str) -> list[tuple[str, str]]:
    page = html.unescape(page)
    seen, out = set(), []
    for url, title in re.findall(r'href="(https://www\.abt\.com/[^"]+/p/\d+\.html)"[^>]*>\s*([^<]{5,160}?)\s*<', page):
        if url not in seen and title.strip():
            seen.add(url)
            out.append((url, re.sub(r"\s+-\s+[A-Z0-9/\-]+$", "", title.strip())))
    return out


ABT = Retailer(
    key="abt", store_name="Abt Electronics", currency="USD",
    search_url="https://www.abt.com/resources/pages/search.php?keywords={q}", results=_abt_results,
)
