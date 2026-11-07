# Gold Watch

A free XAU/USD dashboard that explains *why* gold is moving, in language that
assumes you know nothing about finance.

No account, no sign-up, no server. Open the page.

---

## How it stays live without a server

The work is split by how fast each thing actually changes.

| | where it runs | how fresh |
|---|---|---|
| **Spot price** | your own browser, direct from `api.gold-api.com` | every 10 seconds |
| News, social posts, calendar, candles, the bias read | GitHub Actions, into `data.json` | every ~5 minutes |

A headline is not more true five seconds after it is published, so pre-building
it costs nothing. A price is stale in seconds, so it is never pre-built.

Because every visitor fetches the price themselves, **the price stays live no
matter how many people are watching** — there is no server in the middle to
overload, and nothing gets slower as the site gets busier.

The page always prints how old its news is. A dashboard that looks live while
showing twenty-minute-old headlines is worse than one that admits it.

## What it shows

- **Why gold moved** — unusual moves called out with a plain-English cause
- **Biggest things right now** — the headlines that actually matter, each with
  one line on which way it pushes gold and why
- **People, oil, wars and money** — 23 tracked topics across four categories,
  including posts from figures who move markets
- **Dollar, 10-year yield and silver** — and when one of them *disagrees* with
  gold, which is usually the real story
- **Trading sessions** — Tokyo / London / New York, DST-aware, with the typical
  volatility for the hour you are in
- **What's coming** — scheduled US data and Fed meetings

## The chat

Ask it anything about what is on screen. It needs a **free key from Groq**
([console.groq.com/keys](https://console.groq.com/keys)) which you paste into
the page once.

The key is stored in your own browser and sent to Groq and nobody else. There is
no server behind this page to send it to, and no shared key to run out — which
is why it keeps working however many people use the site.

## Running it locally

```bash
python3 build.py        # fetch everything, write docs/
cd docs && python3 -m http.server 8000
```

Standard library only. No `pip install`, no dependencies to break.

## Layout

```
build.py                 fetch -> docs/data.json, and rewrite the page to be static
engine/mrkt.py           the fetchers, the scoring, the dashboard markup
engine/drivers.py        which headlines are about gold, and which are mining noise
engine/explain.py        jargon -> plain English, and which way each thing pushes gold
engine/test_polarity.py  29 labelled headlines the direction scorer must get right
```

`test_polarity.py` runs in CI before every publish. Getting the direction
backwards is the worst failure this app has — telling you gold is headed up when
the news says down — so it is gated, not trusted.

## Not investment advice

This shows public information and explains what it usually means. It does not
tell you what to buy or sell, and it can be wrong. Free data sources go down,
and headline-reading is a blunt instrument.
