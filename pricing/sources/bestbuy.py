"""US prices via the official Best Buy Products API.

Needs a free key from https://developer.bestbuy.com, provided as the
BESTBUY_API_KEY environment variable (GitHub Actions secret). Without a key
this source is skipped.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from common import MarketResult, Offer, PoliteClient, tokens

SOURCE = "bestbuy"
API = "https://api.bestbuy.com/v1/products"
BUNDLE_HINTS = {"kit", "bundle", "with", "w", "+", "lens", "case", "battery", "cage"}


def fetch(client: PoliteClient, item: dict[str, Any], cache: dict[str, Any]) -> MarketResult | None:
    key = os.environ.get("BESTBUY_API_KEY")
    if not key:
        return None
    words = [w for w in tokens(f"{item['brandName']} {item['model']}") if re.match(r"^[a-z0-9.\-]+$", w)]
    search = "&".join(f"search={urllib.parse.quote(w)}" for w in words)
    url = (
        f"{API}(({search}))?apiKey={key}&format=json&pageSize=10"
        "&show=sku,name,salePrice,regularPrice,url,onlineAvailability,condition"
    )
    data = client.get_json(url)
    required = set(words)
    offers: list[Offer] = []
    matched = ""
    for product in data.get("products", []):
        name_tokens = set(tokens(product.get("name", "")))
        if not required.issubset(name_tokens):
            continue
        if item["kind"] != "lens" and (name_tokens & BUNDLE_HINTS) - set(tokens(item["model"])):
            continue
        if str(product.get("condition", "New")).lower() != "new" or not product.get("onlineAvailability"):
            continue
        price = float(product.get("salePrice") or product.get("regularPrice") or 0)
        if price <= 0:
            continue
        matched = matched or product.get("name", "")
        offers.append(Offer(store="Best Buy", price=price, currency="USD", url=product.get("url", ""), source=SOURCE))
        break  # the first exact match is enough; Best Buy is a single store
    if not offers:
        return None
    return MarketResult(offers=offers, offer_count=1, source_url=offers[0].url, matched_title=matched)

