# CameraPick price pipeline

Produces `data/prices.json`, served by GitHub Pages at
`https://stefhenwolf.github.io/camerapick-site/data/prices.json` and read by the app.

## How it works

- `pricing/catalog.json`: the products to price. It is generated from the app repo
  with `python scripts/export_price_catalog.py`.
- `pricing/fetch_prices.py`: for every product and market, asks each source for offers.
  It keeps new, in-stock offers, drops outliers, sorts them by price and records the store,
  link and fetch time.
- `pricing/match_cache.json`: remembers which listing each product was matched to, so later
  runs only fetch prices.
- `.github/workflows/prices.yml`: runs every 6 hours and commits the result; GitHub Pages
  republishes it within a minute or two.

## Sources

| Market | Source | Notes |
| ------ | ------ | ----- |
| TR | Akakçe | Aggregates Hepsiburada, Trendyol, Teknosa, MediaMarkt and photo shops. Reads schema.org offers on product pages; only paths allowed by robots.txt. |
| US | Best Buy Products API | Needs the `BESTBUY_API_KEY` secret (free at developer.bestbuy.com). |
| US | eBay Browse API | New, buy-it-now listings in the US. Needs the `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` secrets (developer.ebay.com). |

Sources without credentials are skipped. A failing source never stops the run;
`sourceHealth` in the output shows ok/miss/error counts per source.

## Run locally

```bash
python pricing/fetch_prices.py --markets TR --limit 10
python pricing/fetch_prices.py --ids sony-a7iv,canon-r6ii
```

## Adding a market

Write `pricing/sources/<name>.py` with
`fetch(client, item, cache) -> MarketResult | None`, register it in `SOURCE_FETCHERS`
and add the market to `MARKETS` in `fetch_prices.py`.
