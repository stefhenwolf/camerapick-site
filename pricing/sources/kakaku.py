"""Japan prices via kakaku.com (価格.com).

Search result pages already list maker, product name, category, the lowest price across
shops and the number of shops, so one robots-allowed request per product is enough.
Kakaku only exposes the lowest price (not every shop), so each product gets a single
"Kakaku.com" offer that links to its comparison page listing all shops.
"""

from __future__ import annotations

import html
import re
import unicodedata
import urllib.error
import urllib.parse
from typing import Any

import akakce
from common import MarketResult, Offer, PoliteClient

SOURCE = "kakaku"
BASE = "https://kakaku.com"

ROW_RE = re.compile(
    r'<p class="p-item_maker">([^<]*)</p>\s*<p class="p-item_name">\s*'
    r'<a href="https://kakaku\.com/item/([A-Z]\d+)/"[^>]*>([^<]+)</a>.*?'
    r'<p class="p-item_category">([^<]*)</p>.*?'
    r'<em class="p-item_priceNum">([^<]+)</em>.*?'
    r'p-item_shopCounts_num c-num">(\d+)<',
    re.S,
)

CATEGORIES = {
    "camera": {"デジタル一眼カメラ", "デジタルカメラ", "ビデオカメラ", "フィルムカメラ", "インスタントカメラ", "ドローン・マルチコプター"},
    "action": {"ビデオカメラ", "デジタルカメラ", "ウェアラブルカメラ", "アクションカメラ", "ジンバル"},
    "drone": {"ドローン・マルチコプター"},
    "lens": {"レンズ"},
}

# Japanese maker names -> our brand ids (first word of brandName, lowercased).
MAKERS = {
    "ソニー": "sony", "キヤノン": "canon", "ニコン": "nikon", "富士フイルム": "fujifilm", "パナソニック": "panasonic",
    "omデジタルソリューションズ": "om", "オリンパス": "om", "シグマ": "sigma", "タムロン": "tamron", "ライカ": "leica",
    "リコー": "ricoh", "ペンタックス": "pentax", "ハッセルブラッド": "hasselblad", "サムヤン": "samyang",
    "コダック": "kodak", "ポラロイド": "polaroid", "ブラックマジックデザイン": "blackmagic", "トキナー": "tokina",
    "フォクトレンダー": "voigtlander", "ツァイス": "zeiss",
}

JP_WORDS = [
    ("レンズキット", " kit "), ("ズームキット", " kit "), ("キット", " kit "), ("ボディ", " body "),
    ("スタンダードコンボ", " standard combo "), ("アドベンチャーコンボ", " adventure combo "),
    ("エッセンシャルコンボ", " essential combo "), ("コンボ", " combo "), ("セット", " set "),
]


def _maker_brand(maker: str) -> str:
    folded = unicodedata.normalize("NFKC", maker).strip().lower()
    return MAKERS.get(folded, MAKERS.get(maker.strip(), folded.split(" ")[0]))


def _to_latin(name: str) -> str:
    text = unicodedata.normalize("NFKC", name)
    text = re.sub(r"\[[^\]]*\]", " ", text)  # colour variants like [ブラック]
    text = text.replace("α", "a")
    # Sony part numbers (ILCE-7M4, DSC-RX100M7, SEL50F12GM) duplicate the model name.
    text = re.sub(r"\b(?:ILCE|ILME|DSC|SEL|FDR|HDR)-?[A-Z0-9]+\b", " ", text)
    for jp, en in JP_WORDS:
        text = text.replace(jp, en)
    return re.sub(r"\s+", " ", text).strip()


def _queries(item: dict[str, Any]) -> list[str]:
    model = item["model"]
    brand = item["brandName"]
    queries = []
    if brand.lower() == "sony" and re.match(r"^A\d", model):
        queries.append("α" + model[1:])
    queries.append(f"{brand.upper()} {model}")
    if item["kind"] == "lens":
        queries.append(re.sub(r"\s+(?=\d)", "", model))  # "RF 24-70mm" -> "RF24-70mm"
    else:
        queries.append(f"{model} ボディ")  # "X-T5 ボディ" finds bodies that brand-prefixed search misses
    # "/" would break the search path (f/1.2 -> f 1.2 still matches the listing text).
    return list(dict.fromkeys(q.replace("/", " ") for q in queries))


def _search(client: PoliteClient, query: str) -> list[tuple[str, ...]]:
    url = f"{BASE}/search_results/{urllib.parse.quote(query.encode('cp932', 'ignore'))}/"
    try:
        raw = client.get_bytes(url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise
    page = html.unescape(raw.decode("cp932", "ignore"))
    return ROW_RE.findall(page)


def fetch(client: PoliteClient, item: dict[str, Any], cache: dict[str, Any]) -> MarketResult | None:
    brand = item["brandName"].lower().split(" ")[0]
    allowed = CATEGORIES.get(item["kind"], CATEGORIES["camera"])
    best = None
    for query in _queries(item):
        for maker, code, name, category, price, shops in _search(client, query):
            if category not in allowed or _maker_brand(maker) != brand:
                continue
            score = akakce._score(item, f"{item['brandName']} {_to_latin(name)}")
            if score is None or score < 4:
                continue
            if best is None or score > best[0]:
                best = (score, code, name, price, int(shops))
        if best:
            break
    if not best:
        return None
    _, code, name, price_text, shops = best
    price = float(re.sub(r"[^\d.]", "", price_text) or 0)
    if price <= 0:
        return None
    url = f"{BASE}/item/{code}/"
    offer = Offer(store="Kakaku.com", price=price, currency="JPY", url=url, source=SOURCE)
    return MarketResult(offers=[offer], offer_count=shops, source_url=url, matched_title=name)
