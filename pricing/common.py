"""Shared helpers for the CameraPick price pipeline (stdlib only)."""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 CameraPickPriceBot/1.0 "
    "(+https://camerapick.sedatpala.site/)"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PoliteClient:
    """Tiny HTTP client that waits between requests to the same host."""

    def __init__(self, delay_seconds: float = 2.0, timeout: float = 25.0):
        self.delay = delay_seconds
        self.timeout = timeout
        self._last_hit: dict[str, float] = {}

    def get(self, url: str, headers: dict[str, str] | None = None, attempts: int = 3) -> str:
        """GET with per-host pacing. On 429/503 it backs off (honouring Retry-After) and
        slows down for the rest of the run instead of hammering the site."""
        for attempt in range(attempts):
            try:
                return self._get_once(url, headers)
            except urllib.error.HTTPError as error:
                if error.code not in (429, 503) or attempt == attempts - 1:
                    raise
                retry_after = error.headers.get("Retry-After", "")
                pause = float(retry_after) if retry_after.isdigit() else 60.0 * (attempt + 1)
                self.delay = min(self.delay * 1.5, 10.0)
                print(f"  .. {error.code} from {urllib.parse.urlparse(url).netloc}, waiting {pause:.0f}s", flush=True)
                time.sleep(pause)
        raise RuntimeError("unreachable")

    def _get_once(self, url: str, headers: dict[str, str] | None) -> str:
        host = urllib.parse.urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        finally:
            self._last_hit[host] = time.monotonic()

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self.get(url, headers={"Accept": "application/json", **(headers or {})}))


# ---------------------------------------------------------------- text matching

_TR_FOLD = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def normalize(text: str) -> str:
    text = text.translate(_TR_FOLD)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("/", " ").replace(",", " ")
    text = re.sub(r"[^a-z0-9.\-+ ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[ ]+", normalize(text)) if t]


# ---------------------------------------------------------------- output model


@dataclass
class Offer:
    store: str
    price: float
    currency: str
    url: str
    in_stock: bool = True
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["inStock"] = data.pop("in_stock")
        data["price"] = round(self.price, 2)
        return data


@dataclass
class MarketResult:
    offers: list[Offer] = field(default_factory=list)
    offer_count: int = 0
    source_url: str = ""
    matched_title: str = ""

    def to_json(self, fetched_at: str, max_offers: int = 10) -> dict[str, Any] | None:
        clean = sorted((o for o in self.offers if o.price > 0), key=lambda o: o.price)
        if not clean:
            return None
        return {
            "best": clean[0].to_json(),
            "offers": [o.to_json() for o in clean[:max_offers]],
            "offerCount": max(self.offer_count, len(clean)),
            "low": round(clean[0].price, 2),
            "high": round(clean[-1].price, 2),
            "currency": clean[0].currency,
            "sourceURL": self.source_url,
            "matchedTitle": self.matched_title,
            "fetchedAt": fetched_at,
        }


def drop_outliers(offers: list[Offer]) -> list[Offer]:
    """Removes suspiciously cheap listings (parts, accessories, typos)."""
    if len(offers) < 3:
        return offers
    prices = sorted(o.price for o in offers)
    median = prices[len(prices) // 2]
    return [o for o in offers if o.price >= median * 0.6]


def fetch_usd_rates(client: PoliteClient) -> dict[str, float]:
    """Currency per 1 USD from the ECB daily reference rates."""
    fallback = {"USD": 1.0, "TRY": 41.0, "EUR": 0.86, "GBP": 0.76, "CHF": 0.8, "JPY": 150.0}
    try:
        xml = client.get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
    except (urllib.error.URLError, TimeoutError):
        return fallback
    per_eur = {m.group(1): float(m.group(2)) for m in re.finditer(r"currency='(\w+)' rate='([\d.]+)'", xml)}
    if "USD" not in per_eur:
        return fallback
    usd = per_eur["USD"]
    rates = {cur: rate / usd for cur, rate in per_eur.items()}
    rates["EUR"] = 1 / usd
    rates["USD"] = 1.0
    return rates
