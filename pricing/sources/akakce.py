"""Türkiye prices via Akakçe.

Akakçe aggregates offers from Turkish retailers (Hepsiburada, Trendyol, Teknosa,
MediaMarkt, photo shops...). Product pages expose every offer as schema.org JSON-LD,
so we read that structured data instead of scraping the visual layout.
Only paths allowed by https://www.akakce.com/robots.txt are requested
(/arama/ search and product pages).
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any

from common import MarketResult, Offer, PoliteClient, drop_outliers, normalize, tokens

BASE = "https://www.akakce.com"
SOURCE = "akakce"

CATEGORY_PATHS = {
    "camera": ("/fotograf-makinesi/",),
    "action": ("/aksiyon-kamera/", "/fotograf-makinesi/"),
    "lens": ("/objektif/",),
}

# Words that mean the listing is a bundle / accessory instead of the plain product.
BUNDLE_WORDS = {
    "+", "kit", "lensli", "kafes", "cage", "batarya", "pil", "kilif", "canta", "adaptor",
    "adapter", "filtre", "filter", "kapak", "parasoley", "hood", "koruyucu", "jelatin",
    "tutucu", "aksesuar", "combo", "set", "seti", "bundle", "sarj", "kablo", "grip",
    "ekran", "montaj", "standart", "creator", "edition", "limited",
}
GENERATION_WORDS = {"ii", "iii", "iv", "v", "vi", "mark", "mk", "2", "3", "4", "5", "6", "pro", "max", "ultra", "s", "r", "x"}
BODY_WORDS = {"body", "govde"}
FILLER_WORDS = {
    "aynasiz", "dslr", "fotograf", "makinesi", "lens", "objektif", "aksiyon", "kamerasi",
    "kamera", "eos", "lumix", "alpha", "black", "siyah", "gumus", "silver", "dijital",
    "kompakt", "camera", "digital", "on", "siparis",
}


def _search(client: PoliteClient, query: str) -> list[tuple[str, str]]:
    url = f"{BASE}/arama/?q={urllib.parse.quote_plus(query)}"
    page = html.unescape(client.get(url))
    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for href, title in re.findall(r'href="(/[^"]*en-ucuz-[^"]*\.html)"[^>]*title="([^"]*)"', page):
        if href not in seen:
            seen.add(href)
            results.append((href, title))
    return results


def _required_tokens(item: dict[str, Any]) -> list[str]:
    model = item["model"]
    if item["kind"] == "lens":
        req = []
        focal = re.search(r"(\d+(?:-\d+)?)\s*mm", model)
        if focal:
            req.append(focal.group(1))
        aperture = re.search(r"f/?\s*([\d.]+(?:-[\d.]+)?)", model, re.I)
        if aperture:
            req.append(aperture.group(1))
        mount = re.match(r"\s*([A-Za-z]{1,4})\b", model)
        if mount and mount.group(1).lower() not in {"af", "mf"}:
            req.append(mount.group(1).lower())
        return req
    return [t for t in tokens(model) if t not in FILLER_WORDS]


def _expanded(words: list[str]) -> set[str]:
    """Token variants so "Z5" == "Z 5", "S5 II" == "DC-S5 II", "GFX100" == "GFX 100"."""
    result = set(words)
    for word in words:
        result.update(p for p in word.split("-") if p)
        result.update(re.findall(r"[a-z]+|\d+(?:\.\d+)?", word))
    for a, b in zip(words, words[1:]):
        result.add(a + b)
    return result


def _score(item: dict[str, Any], title: str) -> float | None:
    title_norm = normalize(title)
    title_tokens = set(_expanded(tokens(title)))
    brand = normalize(item["brandName"]).split(" ")[0]
    if brand not in title_tokens:
        return None

    required = _required_tokens(item)
    if item["kind"] == "lens":
        if not all(req in title_norm for req in required):
            return None
    elif not all(req in title_tokens for req in required):
        return None

    score = 10.0
    model_tokens = _expanded(tokens(item["model"])) | set(tokens(item["brandName"]))
    bundle_words = BUNDLE_WORDS
    if item["kind"] == "action":
        # Action cameras are normally sold as a "Standard Combo"; bigger combos are bundles.
        bundle_words = (BUNDLE_WORDS - {"combo", "standart", "standard"}) | {"adventure", "motorcycle", "dive", "diving"}
    for word in bundle_words:
        if word in title_tokens and word not in model_tokens:
            score -= 6
    if "+" in title:
        score -= 6
    # A generation/variant word in the title that isn't in our model means a different product.
    for word in GENERATION_WORDS:
        if word in title_tokens and word not in model_tokens:
            score -= 8
    if any(word in title_tokens for word in BODY_WORDS):
        score += 2
    # Prefer shorter, cleaner titles.
    extras = [t for t in tokens(title) if t not in model_tokens and t not in FILLER_WORDS and t not in BODY_WORDS]
    score -= 0.3 * len(extras)
    return score


def find_product_url(client: PoliteClient, item: dict[str, Any]) -> tuple[str, str] | None:
    """Returns (absolute product url, matched title) or None if no confident match."""
    query = f"{item['brandName']} {item['model']}"
    allowed = CATEGORY_PATHS.get(item["kind"], ("/",))
    best: tuple[float, str, str] | None = None
    for href, title in _search(client, query):
        if not href.startswith(allowed):
            continue
        score = _score(item, title)
        if score is None or score < 4:
            continue
        if best is None or score > best[0]:
            best = (score, href, title)
    if best is None:
        return None
    return BASE + best[1], best[2]


def _jsonld_product(page: str) -> dict[str, Any] | None:
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Product":
                return node
    return None


def fetch_offers(client: PoliteClient, product_url: str, matched_title: str) -> MarketResult | None:
    product = _jsonld_product(client.get(product_url))
    if not product:
        return None
    aggregate = product.get("offers") or {}
    raw_offers = aggregate.get("offers") or []
    offers: list[Offer] = []
    for raw in raw_offers:
        try:
            price = float(raw.get("price", 0))
        except (TypeError, ValueError):
            continue
        condition = str(raw.get("itemCondition", "NewCondition"))
        if "NewCondition" not in condition:
            continue
        in_stock = "InStock" in str(raw.get("availability", "InStock"))
        if not in_stock:
            continue
        seller = (raw.get("seller") or {}).get("name") or "Mağaza"
        offers.append(
            Offer(
                store=seller,
                price=price,
                currency=raw.get("priceCurrency", "TRY"),
                url=raw.get("url") or product_url,
                in_stock=True,
                source=SOURCE,
            )
        )
    offers = drop_outliers(offers)
    if not offers:
        return None
    try:
        count = int(aggregate.get("offerCount", len(offers)))
    except (TypeError, ValueError):
        count = len(offers)
    return MarketResult(offers=offers, offer_count=count, source_url=product_url, matched_title=matched_title)
