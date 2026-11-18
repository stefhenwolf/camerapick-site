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
    "camera": ("/fotograf-makinesi/", "/video-kamera/"),
    "action": ("/aksiyon-kamera/", "/fotograf-makinesi/", "/video-kamera/"),
    "drone": ("/drone/",),
    "lens": ("/objektif/",),
}

# Words that mean the listing is a bundle / accessory instead of the plain product.
BUNDLE_WORDS = {
    "+", "kit", "lensli", "kafes", "cage", "batarya", "pil", "kilif", "canta", "adaptor",
    "adapter", "filtre", "filter", "kapak", "parasoley", "hood", "koruyucu", "jelatin",
    "tutucu", "aksesuar", "combo", "set", "seti", "bundle", "sarj", "kablo", "grip",
    "ekran", "montaj", "standart", "creator", "edition", "limited", "with", "w", "avec", "bundle",
}
GENERATION_WORDS = {"ii", "iii", "iv", "v", "vi", "mark", "mk", "2", "3", "4", "5", "6", "pro", "max", "ultra", "s", "r", "x"}
BODY_WORDS = {"body", "govde", "nu", "boitier", "only"}
FILLER_WORDS = {
    "aynasiz", "dslr", "fotograf", "makinesi", "lens", "objektif", "aksiyon", "kamerasi",
    "kamera", "eos", "lumix", "alpha", "black", "siyah", "gumus", "silver", "dijital",
    "kompakt", "camera", "digital", "on", "siparis", "mirrorless", "appareil", "photo", "hybride", "objectif", "reflex", "interchangeable", "full-frame", "frame", "full",
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
        result.update(re.findall(r"[a-z]+\d+(?:\.\d+)?", word))  # "z6iii" -> "z6"
    for a, b in zip(words, words[1:]):
        result.add(a + b)
    return result


def _score(item: dict[str, Any], title: str) -> float | None:
    title_norm = normalize(title)
    title_tokens = set(_expanded(tokens(title)))
    brand = normalize(item["brandName"]).split(" ")[0]
    # The listing must be *made by* the brand (first word), not merely compatible with it
    # ("7Artisans 50mm f/1.2 Nikon Z" is not a Nikon lens).
    if not tokens(title) or tokens(title)[0] != brand:
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
    allowed = CATEGORY_PATHS.get(item["kind"], ("/",))
    best: tuple[float, str, str] | None = None
    for query in _queries(item):
        best = _best_listing(client, item, query, allowed)
        if best:
            break
    if best is None:
        return None
    return BASE + best[1], best[2]


def _queries(item: dict[str, Any]) -> list[str]:
    """Plain name first, then a spaced variant ("GFX100S II" -> "GFX 100S II") used by some listings."""
    plain = f"{item['brandName']} {item['model']}"
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", item["model"])
    queries = [plain]
    if spaced != item["model"]:
        queries.append(f"{item['brandName']} {spaced}")
    return queries


def _best_listing(client: PoliteClient, item: dict[str, Any], query: str, allowed: tuple[str, ...]):
    best: tuple[float, str, str] | None = None
    for href, title in _search(client, query):
        if not href.startswith(allowed):
            continue
        score = _score(item, title)
        if score is None or score < 4:
            continue
        if best is None or score > best[0]:
            best = (score, href, title)
    return best


def _jsonld_product(page: str) -> dict[str, Any] | None:
    """The page's Product, or ProductGroup (colour variants) node."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") in ("Product", "ProductGroup"):
                return node
    return None


def _offer_list(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Flattens Offer / AggregateOffer / list forms, including every variant of a ProductGroup."""
    raw = node.get("offers")
    offers: list[dict[str, Any]] = []
    if isinstance(raw, list):
        offers.extend(o for o in raw if isinstance(o, dict))
    elif isinstance(raw, dict):
        if isinstance(raw.get("offers"), list):
            offers.extend(o for o in raw["offers"] if isinstance(o, dict))
        elif raw.get("price"):
            offers.append(raw)
    for variant in node.get("hasVariant") or []:
        if isinstance(variant, dict):
            offers.extend(_offer_list(variant))
    return offers


def fetch_offers(client: PoliteClient, product_url: str, matched_title: str) -> MarketResult | None:
    product = _jsonld_product(client.get(product_url))
    if not product:
        return None
    aggregate = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    raw_offers = _offer_list(product)
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
    unique: dict[tuple[str, float], Offer] = {}
    for offer in offers:
        unique.setdefault((offer.store, round(offer.price)), offer)
    offers = drop_outliers(list(unique.values()))
    if not offers:
        return None
    try:
        count = int(aggregate.get("offerCount", len(offers)))
    except (TypeError, ValueError):
        count = len(offers)
    return MarketResult(offers=offers, offer_count=count, source_url=product_url, matched_title=matched_title)
