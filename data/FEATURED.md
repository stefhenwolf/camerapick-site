# Featured and sponsored placements

`data/featured.json` controls the carousel at the top of the app's Home screen.
Edit the file and push; the app picks up changes within an hour without an App Store update.

## Fields

| Field | Required | Meaning |
| ----- | -------- | ------- |
| `id` | yes | Unique id. Used in analytics, so keep it stable for a campaign (`brand-2027-01-campaign`). |
| `slot` | yes | `home_featured` (camera list) or `home_lenses_featured` (lens list). |
| `sponsored` | yes | `true` if someone paid for the placement. |
| `sponsorName` | no | Shown next to the label: "Sponsorlu · Sony". |
| `countries` | no | e.g. `["tr", "de"]`. Leave it out to show the placement everywhere. |
| `startsAt` / `endsAt` | no | ISO 8601 UTC, e.g. `2027-01-01T00:00:00Z`. |
| `priority` | no | Higher numbers show first. |
| `title` / `subtitle` | title yes | Per language: `{"tr": "...", "en": "..."}`. Falls back to `en`. |
| `imageURL` | no | Custom HTTPS banner image. Without it, the product photo is used. |
| `accentHex` | no | Card colour, e.g. `#1F2A44`. |
| Target | one of | `cameraID`, `lensID`, `brand` (camera brand id such as `sony`), or an HTTPS `url`. |

## Sponsored placements must be labelled

When `sponsored` is `true`, the app always shows a small "Sponsorlu / Sponsored" label on the
card. This is intentional and cannot be configured off:

- In Turkey, the Ticari Reklam ve Haksız Ticari Uygulamalar Yönetmeliği prohibits covert
  advertising (örtülü reklam). The EU UCPD and US FTC rules require the same disclosure.
- App Store Review Guidelines reject apps that hide paid placements from users.
- Advertisers expect it. Labelled native placements are the industry standard
  (App Store Search Ads, Instagram, Google).

The label is styled to be discreet: a small pill that matches the card. Paid placements never
change product rankings, Camera Finder results or "best price" calculations.

## Measuring a campaign

The app emits `promotion_impression` (once per session per placement) and `promotion_tap`
events with `promotion_id`, `slot`, `sponsored` and `sponsor`. Connect an analytics provider
in `AnalyticsService.swift` to report these numbers to advertisers.
