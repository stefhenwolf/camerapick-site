"""US prices via the official eBay Browse API (new, buy-it-now listings only).

Needs an application keyset from https://developer.ebay.com, provided as
EBAY_CLIENT_ID and EBAY_CLIENT_SECRET environment variables. Without them this
source is skipped.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from common import MarketResult, Offer, PoliteClient, drop_outliers, tokens

SOURCE = "ebay"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
# eBay category ids: Digital Cameras, Camera Lenses, Camcorders (action cams live here too)
CATEGORIES = {"camera": "31388", "lens": "3323", "action": "11724"}
BUNDLE_HINTS = {"kit", "bundle", "w", "with", "+", "lot", "case", "battery", "cage", "for", "parts", "broken"}

_token: dict[str, Any] = {}


def _access_token() -> str | None:
    client_id = os.environ.get("EBAY_CLIENT_ID")
    secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not secret:
        return None
    if _token.get("value") and _token.get("expires", 0) > time.time() + 60:
        return _token["value"]
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 7200))
    return _token["value"]


def fetch(client: PoliteClient, item: dict[str, Any], cache: dict[str, Any]) -> MarketResult | None:
    token = _access_token()
    if not token:
        return None
    query = f"{item['brandName']} {item['model']}"
    params = {
        "q": query,
        "limit": "25",
        "category_ids": CATEGORIES.get(item["kind"], "31388"),
        "filter": "conditions:{NEW},buyingOptions:{FIXED_PRICE},itemLocationCountry:US,deliveryCountry:US",
        "sort": "price",
    }
    data = client.get_json(
        f"{SEARCH_URL}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
    )
    required = set(tokens(query))
    model_tokens = set(tokens(item["model"]))
    offers: list[Offer] = []
    for summary in data.get("itemSummaries", []):
        title_tokens = set(tokens(summary.get("title", "")))
        if not required.issubset(title_tokens):
            continue
        if item["kind"] != "lens" and (title_tokens & BUNDLE_HINTS) - model_tokens:
            continue
        price = summary.get("price") or {}
        if price.get("currency") != "USD":
            continue
        seller = (summary.get("seller") or {}).get("username", "seller")
        offers.append(
            Offer(
                store=f"eBay · {seller}",
                price=float(price.get("value", 0)),
                currency="USD",
                url=summary.get("itemWebUrl", ""),
                source=SOURCE,
            )
        )
    offers = drop_outliers(offers)[:5]
    if not offers:
        return None
    search_url = f"https://www.ebay.com/sch/i.html?{urllib.parse.urlencode({'_nkw': query, 'LH_ItemCondition': '1000', 'LH_BIN': '1'})}"
    return MarketResult(offers=offers, offer_count=len(offers), source_url=search_url, matched_title=query)
