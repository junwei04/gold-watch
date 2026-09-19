#!/usr/bin/env python3
"""
GOLD WATCH — why is gold moving, explained simply.

Written so someone with no finance background can read it. Every number on the
screen comes with a plain sentence saying what it means for gold.

  1. watches the gold price, the news wires, the economic calendar, and what
     powerful people are posting online (Trump, the Fed, etc.)
  2. spots when gold moves more than it normally does
  3. shows what was published right at that moment, ranked
  4. explains, in one sentence, why each thing matters for gold

It ranks candidates. It never claims to know the cause. The real headline is
always on screen next to the score, because the score is the part that can be wrong.

All data is free and public. Nothing leaves your machine.
"""
import argparse, json, os, queue, re, statistics, sys, threading, time
import urllib.parse, urllib.request
import html as htmlmod
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drivers import score_headline, lean, score_social, WATCHLIST
from explain import plain_headline_effect, plain_bias, plain_move, MEANING
import chat as chatmod

HOME = os.path.expanduser("~/.mrkt-gold")
SGT  = timezone(timedelta(hours=8))
UA   = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

NEWS_FEEDS = [
    ("FXStreet",      "https://www.fxstreet.com/rss/news"),
    ("Investing",     "https://www.investing.com/rss/news_285.rss"),
    ("Commodities",   "https://www.investing.com/rss/commodities.rss"),
    ("Markets",       "https://www.investing.com/rss/news_1.rss"),
    ("MarketWatch",   "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Gold",    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US"),
    ("Fed press",     "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("Fed speeches",  "https://www.federalreserve.gov/feeds/speeches.xml"),
]
TRUMP_FEED = "https://trumpstruth.org/feed"
GNEWS = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"

STATE = {
    "price": None, "prev_close": None, "chg": 0.0, "chg_pct": 0.0,
    "bars": [], "moves": [], "news": [], "social": [], "calendar": [],
    "bias": "NEUTRAL", "bias_score": 0.0, "bias_colour": "#8e8e93", "bias_why": [],
    "bias_plain": "", "bias_because": "",
    "updated": None, "errors": [], "n_news": 0, "n_filtered": 0,
    "status": "starting", "rev": 0, "peers": {}, "levels": {}, "dollar_story": "", "sessions": {}, "hourly": [], "headline_events": [],
}
LOCK = threading.Lock()
SUBS = []           # live listeners (server-sent events)
_backoff = {}       # per-source "do not ask again before" timestamps
_rot = 0            # rotating cursor over the watchlist


def sgt(dt):
    return dt.astimezone(SGT).strftime("%H:%M")


def get(url, timeout=18):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def guarded(key, fn, minutes=15):
    """
    Run fn(); if it fails, do not try again for `minutes`.
    Retrying a rate-limited endpoint on every refresh is what KEEPS it
    rate-limited, so the backoff lives inside the fetch, not beside it.
    """
    now = time.time()
    if now < _backoff.get(key, 0):
        return None
    try:
        r = fn()
        _backoff.pop(key, None)
        return r
    except Exception as e:
        code = getattr(e, "code", None)
        _backoff[key] = now + minutes * 60
        with LOCK:
            STATE["errors"].append(
                f"{key}: {'rate limited' if code == 429 else type(e).__name__}"
                f" — retrying in {minutes}m")
        return None


def bump():
    with LOCK:
        STATE["rev"] += 1
        STATE["updated"] = datetime.now(SGT).strftime("%H:%M:%S")
        rev = STATE["rev"]
    for q in list(SUBS):
        try:
            q.put_nowait(rev)
        except Exception:
            pass


def rss_items(url):
    return ET.fromstring(get(url)).findall(".//item")


def item_time(it):
    try:
        w = parsedate_to_datetime(it.findtext("pubDate"))
        return w if w.tzinfo else w.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def clean(s):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ------------------------------------------------------------------ fetchers
# What actually drives XAU/USD intraday. All free from the same Yahoo endpoint.
PEERS = [("DXY", "DX-Y.NYB", "US dollar index"),
         ("US10Y", "%5ETNX", "US 10-year yield"),
         ("Silver", "SI=F", "Silver")]


def fetch_markets():
    """The dollar, yields and silver -- gold's three closest relationships."""
    out = {}
    for key, sym, label in PEERS:
        try:
            d = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/"
                               f"{sym}?interval=5m&range=1d", 12))
            m = d["chart"]["result"][0]["meta"]
            px = m.get("regularMarketPrice")
            prev = m.get("chartPreviousClose") or m.get("previousClose")
            if px and prev:
                out[key] = {"label": label, "px": round(px, 3),
                            "pct": round((px - prev) / prev * 100, 2)}
        except Exception:
            continue
    with LOCK:
        STATE["peers"] = out
    return out


# ---------------------------------------------------------- trading sessions
# jin10 has a 交易时钟 (trading clock). For a gold day trader this matters a lot:
# gold is quiet in Asia and violent in the London/New York overlap. Times are
# derived from UTC so they stay correct through daylight saving changes.
def sessions_now():
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60.0
    # London is UTC+1 in summer, UTC+0 in winter; New York UTC-4 / UTC-5.
    # Work out which by checking the US Eastern offset for today.
    import time as _t
    is_dst = _t.localtime().tm_isdst > 0
    ldn_open, ldn_close = (7, 15.5) if is_dst else (8, 16.5)
    ny_open, ny_close   = (13.5, 21) if is_dst else (14.5, 22)
    tok_open, tok_close = (0, 8)
    us_data             = 12.5 if is_dst else 13.5      # 8:30am ET releases

    def live(a, b):
        return a <= h < b

    out = []
    out.append({"name": "Tokyo", "on": live(tok_open, tok_close),
                "note": "Quiet. Gold usually drifts."})
    out.append({"name": "London", "on": live(ldn_open, ldn_close),
                "note": "Volume picks up. Real moves start here."})
    out.append({"name": "New York", "on": live(ny_open, ny_close),
                "note": "Biggest volume of the day for gold."})
    overlap = live(max(ldn_open, ny_open), min(ldn_close, ny_close))
    out.append({"name": "London + NY overlap", "on": overlap,
                "note": "The most volatile window. Most big gold moves happen now."})

    # how long until the next thing a day trader cares about
    marks = [("London opens", ldn_open), ("US data drop (8:30am ET)", us_data),
             ("New York opens", ny_open), ("London closes", ldn_close),
             ("New York closes", ny_close)]
    nxt = None
    for label, t in sorted(marks, key=lambda x: x[1]):
        if t > h:
            nxt = {"what": label, "mins": int((t - h) * 60)}
            break
    if nxt is None:
        label, t = sorted(marks, key=lambda x: x[1])[0]
        nxt = {"what": label, "mins": int((24 - h + t) * 60)}
    return {"list": out, "next": nxt, "overlap": overlap}


def hourly_volatility(bars):
    """
    Which hours does gold ACTUALLY move, measured from his own data rather
    than from a session table someone wrote down. Average absolute 1-minute
    move per hour of the day, in SGT.
    """
    if len(bars) < 120:
        return []
    buckets = {}
    for i in range(1, len(bars)):
        p0, p1 = bars[i-1]["c"], bars[i]["c"]
        if not p0:
            continue
        hr = datetime.fromtimestamp(bars[i]["t"], SGT).hour
        buckets.setdefault(hr, []).append(abs((p1 - p0) / p0 * 10000))   # in "pips"
    rows = [{"hour": hr, "avg": round(sum(v) / len(v), 1), "n": len(v)}
            for hr, v in buckets.items() if len(v) >= 10]
    days = round(len(bars) / 1440.0, 1)
    if not rows:
        return []
    mx = max(r["avg"] for r in rows) or 1
    for r in rows:
        r["rel"] = round(r["avg"] / mx * 100)
    for r in rows:
        r["days"] = days
    return sorted(rows, key=lambda r: r["hour"])


def read_levels(bars):
    """Where price sits inside today's range -- the thing a day trader looks at first."""
    if len(bars) < 10:
        return {}
    cs = [b["c"] for b in bars]
    hi, lo, last = max(cs), min(cs), cs[-1]
    rng = hi - lo or 1
    return {"day_high": round(hi, 2), "day_low": round(lo, 2),
            "pos_pct": round((last - lo) / rng * 100),
            "from_high": round(hi - last, 2), "from_low": round(last - lo, 2)}


def dollar_story(peers, gold_pct):
    """Is the dollar explaining gold right now, or not? Plain English."""
    d = peers.get("DXY")
    if not d:
        return ""
    dp = d["pct"]
    if abs(dp) < 0.05:
        return ("The US dollar is basically flat today, so it is not the thing "
                "pushing gold around right now.")
    updn = "stronger" if dp > 0 else "weaker"
    normal = (dp > 0 and gold_pct < 0) or (dp < 0 and gold_pct > 0)
    if normal:
        return (f"The US dollar is {updn} today ({dp:+.2f}%), and gold is "
                f"{'down' if gold_pct < 0 else 'up'} ({gold_pct:+.2f}%). "
                "That is the normal relationship: they usually move opposite ways, "
                "so the dollar is a good explanation for gold today.")
    return (f"Careful: the dollar is {updn} ({dp:+.2f}%) AND gold is "
            f"{'up' if gold_pct > 0 else 'down'} ({gold_pct:+.2f}%). "
            "They usually move opposite ways, so something else is driving gold "
            "today, not the dollar.")


def fetch_price():
    spot = None
    try:
        spot = float(json.loads(get("https://api.gold-api.com/price/XAU", 10))["price"])
    except Exception:
        pass
    bars, prev = [], None
    try:
        d = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/"
                           "GC=F?interval=1m&range=2d"))
        r = d["chart"]["result"][0]
        prev = r["meta"].get("chartPreviousClose")
        spot = spot or r["meta"].get("regularMarketPrice")
        ts, q = r["timestamp"], r["indicators"]["quote"][0]
        for i, t in enumerate(ts):
            c = q["close"][i]
            if c is None:
                continue
            # Open/high/low as well as close: a candle chart needs all four, and
            # a close-only series cannot show the wicks -- which is most of what
            # a 1-minute chart is read for. Yahoo occasionally nulls one leg of
            # an otherwise good bar, so each falls back to the close rather than
            # throwing the bar away.
            bars.append({"t": t, "c": c,
                         "o": q["open"][i]  if q["open"][i]  is not None else c,
                         "h": q["high"][i]  if q["high"][i]  is not None else c,
                         "l": q["low"][i]   if q["low"][i]   is not None else c})
    except Exception:
        pass
    # GC=F is not gold. It is the December futures contract, and it trades at a
    # premium to spot -- about $37 at the time of writing, because you are
    # buying delivery months away. The headline price here is SPOT XAU/USD, so
    # plotting the futures candles beside it put the chart $37 away from the
    # number above it, and made "today's high" a level that does not exist on
    # the pair anyone actually trades.
    #
    # There is no free spot candle feed (Yahoo has no working XAUUSD series), so
    # the shape comes from futures and is shifted onto spot by the current
    # basis. The spread moves slowly -- it is interest rates and time to expiry,
    # not sentiment -- so over a day it is near enough constant, and every bar
    # keeps its exact shape. The page says it is doing this rather than implying
    # the candles are raw spot prints.
    basis = None
    if bars and spot:
        basis = round(bars[-1]["c"] - spot, 2)
        if abs(basis) > 0.5:                  # below that it is just noise
            for b in bars:
                for k in ("o", "h", "l", "c"):
                    b[k] = round(b[k] - basis, 2)
        else:
            basis = None

    with LOCK:
        STATE["basis"] = basis
        if spot:
            STATE["price"] = round(spot, 2)
        if prev:
            STATE["prev_close"] = round(prev, 2)
            if spot:
                STATE["chg"] = round(spot - prev, 2)
                STATE["chg_pct"] = round((spot - prev) / prev * 100, 2)
        if bars:
            STATE["bars"] = bars[-1440:]
            STATE["levels"] = read_levels(bars)
            STATE["sessions"] = sessions_now()
            STATE["hourly"] = hourly_volatility(bars)
    return bars


def fetch_news():
    seen, out, raw = set(), [], 0
    for name, url in NEWS_FEEDS:
        items = guarded(f"news {name}", lambda u=url: rss_items(u), 10)
        if not items:
            continue
        for it in items:
            title = clean(it.findtext("title"))
            if not title:
                continue
            raw += 1
            k = re.sub(r"[^a-z0-9]", "", title.lower())[:70]
            if k in seen:
                continue
            seen.add(k)
            sc, tags = score_headline(title)
            if sc <= 0:
                continue
            w = item_time(it)
            ln = lean(title)
            out.append({"title": title, "src": name, "link": it.findtext("link") or "",
                        "ts": w.timestamp(), "time": sgt(w), "score": sc,
                        "tags": tags[:4], "lean": ln,
                        "effect": plain_headline_effect(tags, ln, title)})
    out.sort(key=lambda x: -x["ts"])
    # jin10 pins the three biggest stories above the feed. Same idea: rank by
    # how strong the signal is AND how fresh, so a huge story an hour ago still
    # beats a mild one from a minute ago.
    nowt = time.time()
    top = sorted(out, key=lambda n: -(n["score"] * max(0.25, 1 - (nowt - n["ts"]) / 21600)))[:3]
    with LOCK:
        STATE["headline_events"] = top
        STATE["news"] = out[:45]
        STATE["n_news"], STATE["n_filtered"] = len(out), raw - len(out)
    return out


def fetch_social():
    """What powerful people are saying online, filtered to what can move gold."""
    out, seen = [], set()

    # 1. Trump's actual Truth Social posts.
    #    X/Twitter has no free API left and Truth Social blocks direct calls,
    #    so this uses the public trumpstruth.org mirror.
    items = guarded("posts Trump", lambda: rss_items(TRUMP_FEED), 20)
    for it in (items or [])[:60]:
        body = clean(it.findtext("description")) or clean(it.findtext("title"))
        body = re.sub(r"https?://\S+", "", body).strip()
        if len(body) < 12:
            continue
        sc, tags = score_social(body, "Trump")
        if sc <= 0:
            continue
        k = re.sub(r"[^a-z0-9]", "", body.lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        w = item_time(it)
        eff = plain_headline_effect(tags, 0, body)
        if "no clear effect" in eff:      # cannot explain it -> do not show it
            continue
        out.append({"who": "Donald Trump", "cat": "person", "where": "Truth Social",
                    "text": body[:300],
                    "ts": w.timestamp(), "time": sgt(w), "score": sc, "tags": tags,
                    "link": it.findtext("link") or "", "effect": eff})

    # 2. everything else, via targeted news search.
    #    23 topics is too many to query every cycle without getting rate limited,
    #    so we rotate through them a batch at a time -- full coverage every few
    #    minutes, at a request rate the free endpoints tolerate.
    global _rot
    # Round-robin across categories, not straight down the list: taking the
    # first 8 in declaration order polled 8 "person" topics and zero oil or war.
    by_cat = {}
    for k, (c, _) in WATCHLIST.items():
        by_cat.setdefault(c, []).append(k)
    keys, i = [], 0
    while len(keys) < len(WATCHLIST):
        for c in ("person", "oil", "war", "money"):
            if i < len(by_cat.get(c, [])):
                keys.append(by_cat[c][i])
        i += 1
    batch = [keys[(_rot + i) % len(keys)] for i in range(8)]
    _rot = (_rot + 8) % len(keys)

    for who in batch:
        cat, query = WATCHLIST[who]
        q = query + (" when:1d" if who == "Trump" else " when:2d")
        url = GNEWS.format(urllib.parse.quote(q))
        items = guarded(f"posts {who}", lambda u=url: rss_items(u), 15)
        for it in (items or [])[:12]:
            title = clean(it.findtext("title"))
            if not title:
                continue
            sc, tags = score_social(title, who)
            if sc <= 0:
                continue
            w = item_time(it)
            if time.time() - w.timestamp() > 36 * 3600:
                continue
            k = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
            if k in seen:
                continue
            seen.add(k)
            src = title.rsplit(" - ", 1)[-1] if " - " in title else "news"
            # This is a REPORT about the person, not their own words. Label it
            # honestly -- a House press release is not a Trump post.
            out.append({"who": who, "cat": cat, "where": src,
                        "text": title.rsplit(" - ", 1)[0][:300],
                        "ts": w.timestamp(), "time": sgt(w), "score": sc, "tags": tags,
                        "link": it.findtext("link") or "",
                        "effect": plain_headline_effect(tags, 0, title)})

    out = [o for o in out if "no clear effect" not in o["effect"]]
    # merge with what we already have: this cycle only polled part of the list
    with LOCK:
        prev = list(STATE.get("social", []))
    have = {re.sub(r"[^a-z0-9]", "", o["text"].lower())[:60] for o in out}
    cutoff = time.time() - 36 * 3600
    for o in prev:
        k = re.sub(r"[^a-z0-9]", "", o["text"].lower())[:60]
        if k not in have and o["ts"] > cutoff:
            out.append(o); have.add(k)
    out.sort(key=lambda x: (-x["score"], -x["ts"]))
    # reserve slots per category: a busy news day for one topic must not push
    # every other kind of driver off the page
    kept, per = [], {}
    for o in out:
        c = o.get("cat", "person")
        if per.get(c, 0) < 9:
            per[c] = per.get(c, 0) + 1
            kept.append(o)
    with LOCK:
        STATE["social"] = kept[:34]
    return out


# ForexFactory only publishes the CURRENT week. By Thursday night there can be
# nothing left in it, which made the app say "nothing scheduled" when it really
# meant "I can only see to Friday". These two are known far in advance, so they
# fill the gap honestly.
#   FOMC  - exact dates published by the Fed (federalreserve.gov/monetarypolicy)
#   NFP   - the US jobs report, always the first Friday of the month, 8:30am ET
FOMC_DATES = [
    (2026, 10, 28), (2026, 12,  9),
    (2027,  1, 27), (2027,  3, 17), (2027,  4, 28), (2027,  6,  9),
    (2027,  7, 28), (2027,  9, 15), (2027, 10, 27), (2027, 12,  8),
]
FOMC_SEP = {(2027, 3, 17), (2027, 6, 9), (2027, 9, 15), (2027, 12, 8)}


def known_events():
    """Big US events we can name in advance, without any feed."""
    ET = timezone(timedelta(hours=-4))
    out, now = [], datetime.now(timezone.utc)

    for y, m, d in FOMC_DATES:
        when = datetime(y, m, d, 14, 0, tzinfo=ET)       # 2pm ET decision
        if when > now:
            extra = " + economic projections" if (y, m, d) in FOMC_SEP else ""
            out.append({"title": "Fed interest rate decision" + extra,
                        "impact": "High", "ts": when.timestamp(),
                        "time": sgt(when), "date": when.astimezone(SGT).strftime("%a %d %b"),
                        "forecast": "", "previous": ""})

    # first Friday of each of the next few months
    y, m = now.year, now.month
    for _ in range(4):
        first = datetime(y, m, 1, tzinfo=ET)
        fri = first + timedelta(days=(4 - first.weekday()) % 7)
        when = fri.replace(hour=8, minute=30)
        if when > now:
            out.append({"title": "US jobs report (Non-Farm Payrolls)", "impact": "High",
                        "ts": when.timestamp(), "time": sgt(when),
                        "date": when.astimezone(SGT).strftime("%a %d %b"),
                        "forecast": "", "previous": ""})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


CAL_CACHE = os.path.join(HOME, "cache", "calendar.json")


def fetch_calendar():
    os.makedirs(os.path.dirname(CAL_CACHE), exist_ok=True)
    cal = None
    if os.path.exists(CAL_CACHE) and time.time() - os.path.getmtime(CAL_CACHE) < 3600:
        try:
            cal = json.load(open(CAL_CACHE))
        except Exception:
            cal = None
    if cal is None:
        def pull():
            d = json.loads(get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
            json.dump(d, open(CAL_CACHE, "w"))
            return d
        cal = guarded("calendar", pull, 15)
        if cal is None:
            try:
                cal = json.load(open(CAL_CACHE))     # stale beats a false empty
            except Exception:
                return []
    out = []
    for c in cal:
        if c.get("country") != "USD" or c.get("impact") not in ("High", "Medium"):
            continue
        try:
            w = datetime.fromisoformat(c["date"])
        except Exception:
            continue
        out.append({"title": c["title"], "impact": c["impact"], "ts": w.timestamp(),
                    "time": sgt(w), "date": w.astimezone(SGT).strftime("%a %d %b"),
                    "forecast": c.get("forecast") or "", "previous": c.get("previous") or ""})
    have = {(c["title"][:22], int(c["ts"] // 3600)) for c in out}
    for k in known_events():
        if (k["title"][:22], int(k["ts"] // 3600)) not in have:
            out.append(k)
    out.sort(key=lambda x: x["ts"])
    with LOCK:
        STATE["calendar"] = out
    return out


# ------------------------------------------------------- moves, why, and bias
def detect_moves(bars, sigma=3.0):
    if len(bars) < 25:
        return []
    rets = [((bars[i]["c"] - bars[i-1]["c"]) / bars[i-1]["c"] * 100) if bars[i-1]["c"] else 0.0
            for i in range(1, len(bars))]
    found = []
    for i in range(20, len(rets)):
        win = rets[max(0, i-80):i]
        if len(win) < 15:
            continue
        sd = statistics.pstdev(win) or 1e-4
        z = rets[i] / sd
        if abs(z) >= sigma:
            b = bars[i+1]
            found.append({"ts": b["t"],
                          "time": sgt(datetime.fromtimestamp(b["t"], timezone.utc)),
                          "pct": round(rets[i], 3), "z": round(z, 1),
                          "from": round(bars[i]["c"], 2), "to": round(b["c"], 2),
                          "dir": "up" if rets[i] > 0 else "down"})
    merged = []
    for m in found:
        if merged and m["ts"] - merged[-1]["ts"] <= 900:
            if abs(m["z"]) > abs(merged[-1]["z"]):
                merged[-1] = m
        else:
            merged.append(m)
    for m in merged:
        m["plain"] = plain_move(m["pct"], m["z"], m["dir"])
    return merged[-8:]


def explain_move(move, news, social, calendar):
    t, out = move["ts"], []
    for n in news:
        dt = t - n["ts"]
        if -600 <= dt <= 2400:
            prox = max(0.0, 1 - abs(dt) / 2400)
            out.append({"title": n["title"], "src": n["src"], "time": n["time"],
                        "mins": int(dt / 60), "rank": round(n["score"] * (0.4 + 0.6 * prox), 1),
                        "link": n["link"], "effect": n["effect"]})
    for s in social:
        dt = t - s["ts"]
        if -600 <= dt <= 2400:
            prox = max(0.0, 1 - abs(dt) / 2400)
            out.append({"title": f"{s['who']}: {s['text'][:140]}", "src": s["where"],
                        "time": s["time"], "mins": int(dt / 60),
                        "rank": round(s["score"] * (0.5 + 0.5 * prox), 1),
                        "link": s["link"], "effect": s["effect"]})
    for c in calendar:
        dt = t - c["ts"]
        if -300 <= dt <= 1800:
            out.append({"title": c["title"], "src": "Scheduled US report", "time": c["time"],
                        "mins": int(dt / 60),
                        "rank": round((9 if c["impact"] == "High" else 5) *
                                      (0.5 + 0.5 * max(0.0, 1 - abs(dt) / 1800)), 1),
                        "link": "",
                        "effect": "A big US economic report came out at this time. "
                                  "These often move gold within seconds."})
    out.sort(key=lambda x: -x["rank"])
    return out[:4]


def compute_bias():
    with LOCK:
        bars, news, cal = list(STATE["bars"]), list(STATE["news"]), list(STATE["calendar"])
        price, prev = STATE["price"], STATE["prev_close"]
    why, score = [], 0.0
    if price and prev:
        pct = (price - prev) / prev * 100
        s = max(-2.0, min(2.0, pct)); score += s
        why.append({"factor": "vs prev close", "detail": f"{pct:+.2f}%", "pts": round(s, 1)})
    if len(bars) >= 12:
        rec = [b["c"] for b in bars[-12:]]
        sl = (rec[-1] - rec[0]) / rec[0] * 100
        s = max(-1.5, min(1.5, sl * 2)); score += s
        why.append({"factor": "last hour trend", "detail": f"{sl:+.2f}%", "pts": round(s, 1)})
    now = time.time()
    fresh = [n for n in news if now - n["ts"] < 6 * 3600 and n["lean"] != 0]
    if fresh:
        tilt = sum(n["lean"] * n["score"] for n in fresh) / sum(n["score"] for n in fresh)
        s = max(-1.5, min(1.5, tilt * 2)); score += s
        b = sum(1 for n in fresh if n["lean"] > 0)
        why.append({"factor": "news tone (6h)", "detail": f"{b} bullish / {len(fresh)-b} bearish",
                    "pts": round(s, 1)})
    soon = [c for c in cal if 0 < c["ts"] - now < 4 * 3600 and c["impact"] == "High"]
    if soon:
        why.append({"factor": "event risk",
                    "detail": f"{soon[0]['title']} at {soon[0]['time']}", "pts": 0.0})

    if score >= 1.8:   lab, col = "BULLISH", "#00d4aa"
    elif score >= 0.6: lab, col = "LEAN BULLISH", "#34c759"
    elif score > -0.6: lab, col = "NEUTRAL", "#8e8e93"
    elif score > -1.8: lab, col = "LEAN BEARISH", "#ff9500"
    else:              lab, col = "BEARISH", "#ff3b30"
    head, because = plain_bias(lab, score, why)
    with LOCK:
        STATE.update({"bias": lab, "bias_score": round(score, 2), "bias_colour": col,
                      "bias_why": why, "bias_plain": head, "bias_because": because})


def rebuild_moves():
    with LOCK:
        bars, news, soc, cal = (list(STATE["bars"]), list(STATE["news"]),
                                list(STATE["social"]), list(STATE["calendar"]))
    mv = detect_moves(bars)
    for m in mv:
        m["why"] = explain_move(m, news, soc, cal)
    with LOCK:
        STATE["moves"] = list(reversed(mv))


# --------------------------------------------------------------- the briefing
def briefing():
    """Everything on screen, as text you can paste to Claude and ask about."""
    with LOCK:
        s = json.loads(json.dumps(STATE))
    L = [f"# Gold (XAU/USD) briefing — {s['updated']} SGT", ""]
    L.append(f"Price is ${s['price']}, {s['chg']:+} ({s['chg_pct']:+}%) compared with "
             f"yesterday's close of {s['prev_close']}.")
    L.append(f"Current read: {s['bias']}. {s['bias_plain']} {s['bias_because']}")
    lv = s.get("levels") or {}
    if lv:
        L.append(f"Today's range: low ${lv['day_low']} to high ${lv['day_high']}. "
                 f"Price is {lv['pos_pct']}% of the way up that range "
                 f"(${lv['from_low']} above the low, ${lv['from_high']} below the high).")
    ss = s.get("sessions") or {}
    if ss:
        on = [x["name"] for x in ss.get("list", []) if x["on"]]
        L.append(f"Trading sessions open right now: {', '.join(on) if on else 'none (quiet hours)'}. "
                 f"Next: {ss['next']['what']} in {ss['next']['mins']} minutes.")
        if ss.get("overlap"):
            L.append("The London/New York overlap is running -- this is the most "
                     "volatile window of the day for gold.")
    hv = s.get("hourly") or []
    if hv:
        top = sorted(hv, key=lambda r: -r["avg"])[:3]
        L.append("Measured from the last two days of 1-minute data, gold moves most in these "
                 "hours (SGT): " + ", ".join(f"{r['hour']:02d}:00 ({r['avg']} pips/min)" for r in top) + ".")
    pr = s.get("peers") or {}
    if pr:
        L.append("Related markets today: " + ", ".join(
            f"{v['label']} {v['px']} ({v['pct']:+.2f}%)" for v in pr.values()) + ".")
    if s.get("dollar_story"):
        L.append(s["dollar_story"])

    # Spell out where gold disagrees with the things it normally tracks.
    # A disagreement is usually the most informative thing on the page, and the
    # model will not spot it unless it is stated.
    g = s.get("chg_pct", 0)
    notes = []
    dxy = (s.get("peers") or {}).get("DXY")
    y10 = (s.get("peers") or {}).get("US10Y")
    slv = (s.get("peers") or {}).get("Silver")
    if slv and abs(slv["pct"] - g) > 1.0:
        notes.append(f"Gold and silver are diverging: silver {slv['pct']:+.2f}% vs gold "
                     f"{g:+.2f}%. They normally move together, so this gap is unusual.")
    if y10 and ((y10["pct"] < -0.3 and g < 0) or (y10["pct"] > 0.3 and g > 0)):
        notes.append(f"Bond yields moved {y10['pct']:+.2f}% and gold moved {g:+.2f}%. "
                     f"Yields and gold normally move OPPOSITE ways, so this is backwards "
                     f"from the usual relationship.")
    if dxy and abs(dxy["pct"]) < 0.1 and abs(g) > 0.4:
        notes.append(f"Gold moved {g:+.2f}% while the dollar barely moved "
                     f"({dxy['pct']:+.2f}%). So whatever moved gold today was not the dollar.")
    if notes:
        L.append("\nThings that do not line up (these are usually the real story):")
        for n in notes:
            L.append("  - " + n)
    if s["moves"]:
        L.append("\n## Unusual moves, and what was in the news at that exact moment")
        for m in s["moves"][:5]:
            L.append(f"\n{m['time']} SGT — {m['pct']:+}% (${m['from']} to ${m['to']})")
            for c in m["why"]:
                when = f"{c['mins']}m before" if c["mins"] > 0 else f"{abs(c['mins'])}m after"
                L.append(f"  - [{c['src']}, {when}] {c['title']}")
            if not m["why"]:
                L.append("  - nothing in the news then, probably just normal trading")
    if s["social"]:
        L.append("\n## What powerful people have said online recently")
        for p in s["social"][:8]:
            L.append(f"  - {p['who']} ({p['where']}, {p['time']} SGT): {p['text'][:220]}")
    if s["news"]:
        L.append("\n## Gold-relevant headlines")
        for n in s["news"][:10]:
            L.append(f"  - [{n['time']} {n['src']}] {n['title']}")
    now = time.time()
    up = [c for c in s["calendar"] if c["ts"] > now][:5]
    if up:
        L.append("\n## Big US reports coming up")
        for c in up:
            extra = f" (forecast {c['forecast']}, previous {c['previous']})" if c["forecast"] else ""
            L.append(f"  - {c['date']} {c['time']} SGT — [{c['impact']}] {c['title']}{extra}")
    L.append("\n---")
    L.append("Please explain in very simple language, as if I am 12 and know nothing "
             "about finance: what is happening with gold right now, what is most likely "
             "driving it, and what should I watch next? Avoid jargon, and where you must "
             "use a term, explain it in the same sentence.")
    return "\n".join(L)


# ---------------------------------------------------------------- scheduler
TASKS = [("price", fetch_price, 20), ("markets", fetch_markets, 30),
         ("news", fetch_news, 90),
         ("posts", fetch_social, 150), ("calendar", fetch_calendar, 900)]


def scheduler():
    last = {k: 0.0 for k, _, _ in TASKS}
    while True:
        did = False
        for name, fn, every in TASKS:
            if time.time() - last[name] >= every:
                with LOCK:
                    STATE["errors"] = [e for e in STATE["errors"] if not e.startswith(name)]
                try:
                    fn()
                except Exception as e:
                    with LOCK:
                        STATE["errors"].append(f"{name}: {type(e).__name__}")
                last[name] = time.time()
                did = True
        if did:
            with LOCK:
                STATE["dollar_story"] = dollar_story(STATE.get("peers", {}),
                                                     STATE.get("chg_pct", 0))
            compute_bias()
            rebuild_moves()
            with LOCK:
                STATE["status"] = "live"
                p, b, n = STATE["price"], STATE["bias"], STATE["n_news"]
                sc = len(STATE["social"])
            bump()
            print(f"[{datetime.now(SGT):%H:%M:%S}] ${p}  {b}  |  {n} headlines  |  {sc} posts",
                  flush=True)
        time.sleep(2)


# --------------------------------------------------------------------- web
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        # Off by default -- it is noise. Switch on with MRKT_ACCESS_LOG=1 when a
        # client is misbehaving: which request stalls is the whole diagnosis.
        if os.environ.get("MRKT_ACCESS_LOG"):
            print(f"[{datetime.now(SGT):%H:%M:%S}] {self.client_address[0]} "
                  f"{fmt % a}", flush=True)

    def handle_one_request(self):
        # A phone closing the app mid-stream is normal, not an incident. Without
        # this every backgrounded tab writes a traceback into a log that now
        # lives forever under launchd.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith("/chat"):
            self._send(b"not found", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            q = json.loads(self.rfile.read(n) or b"{}").get("q", "")
        except Exception:
            q = ""
        with LOCK:
            snapshot = json.loads(json.dumps(STATE))
        try:
            text, backend = chatmod.reply(q, snapshot, MEANING, briefing())
        except Exception as e:
            text, backend = f"Something went wrong answering that ({type(e).__name__}).", "error"
        self._send(json.dumps({"answer": text, "backend": backend}).encode(), "application/json")

    def do_GET(self):
        if self.path.startswith("/events"):          # live push
            q = queue.Queue(maxsize=8)
            SUBS.append(q)
            # This declares HTTP/1.1, and an HTTP/1.1 response with a body must
            # say how long it is -- Content-Length, or chunked. A never-ending
            # stream cannot use the first, so it MUST use the second. Sending
            # neither (while also saying keep-alive) leaves a strict client with
            # no way to know a response ever ended: it just waits. Forgiving
            # clients guess and cope, which is why this worked everywhere it was
            # tested and stalled on the one browser that follows the spec.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def chunk(payload):
                self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")

            try:
                chunk(b": open\n\n")      # flush headers immediately
                self.wfile.flush()
                while True:
                    try:
                        rev = q.get(timeout=20)
                        chunk(f"data: {rev}\n\n".encode())
                    except queue.Empty:
                        chunk(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                if q in SUBS:
                    SUBS.remove(q)
            return
        if self.path.startswith("/state.json"):
            with LOCK:
                body = json.dumps(STATE).encode()
            self._send(body, "application/json")
        elif self.path.startswith("/brief"):
            self._send(briefing().encode(), "text/plain; charset=utf-8")
        elif self.path.startswith("/manifest.json"):
            self._send(MANIFEST.encode(), "application/manifest+json")
        elif self.path.startswith("/icon/"):
            # home-screen icons; anything outside static/ is refused outright
            name = os.path.basename(self.path.split("?")[0])
            f = os.path.join(HOME, "static", name)
            if name.endswith(".png") and os.path.isfile(f):
                with open(f, "rb") as fh:
                    self._send(fh.read(), "image/png")
            else:
                self.send_error(404)
        else:
            self._send(DASH.encode(), "text/html; charset=utf-8")


MANIFEST = json.dumps({
    "name": "Gold Watch",
    "short_name": "Gold Watch",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0a0c10",
    "theme_color": "#0a0c10",
    "orientation": "portrait",
    "icons": [
        {"src": "/icon/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/icon/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
})

DASH = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Gold Watch</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1">
<meta name="theme-color" content="#0a0c10">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon/touch-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Gold Watch">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="format-detection" content="telephone=no">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0c10;color:#e8eaed;font:15px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;padding:22px}
.wrap{max-width:1180px;margin:0 auto}
.card,.lead,.mv,.mbox{overflow-wrap:anywhere}   /* a long unbroken token must wrap, not widen the page */
header{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;flex-wrap:wrap}
.brand{font-size:12px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#6b7280}
.px{font-size:46px;font-weight:800;letter-spacing:-1.8px;font-variant-numeric:tabular-nums;line-height:1}
.chg{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:5px}
.up{color:#00d4aa}.dn{color:#ff3b30}
.meta{font-size:11.5px;color:#5f6368;text-align:right;line-height:1.8}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#00d4aa;margin-right:5px}
.flash{animation:fl 1s ease}@keyframes fl{0%{color:#ffd479}100%{color:#e8eaed}}
.lead{background:#12151c;border:1px solid #1e2430;border-left:3px solid #00d4aa;border-radius:11px;padding:15px 18px;margin:18px 0;font-size:16px}
.lead b{color:#fff}
.grid{display:grid;grid-template-columns:1fr 360px;gap:18px}
@media(max-width:960px){.grid{grid-template-columns:1fr}}
/* ---- phone ------------------------------------------------------------
   Added once this stopped being a desktop dashboard and became the thing
   he actually opens: a home-screen app on a ~390pt screen, held one-handed,
   often at arm's length while a chart is on the other screen. So: thumb-sized
   tap targets, a price big enough to read without focusing, and safe-area
   padding so nothing hides under the notch or the home indicator. */
@supports(padding:max(0px)){
  body{padding-left:max(14px,env(safe-area-inset-left));
       padding-right:max(14px,env(safe-area-inset-right));
       padding-top:max(14px,env(safe-area-inset-top));
       padding-bottom:max(18px,env(safe-area-inset-bottom))}
}
@media(max-width:640px){
  body{padding:16px 14px 26px;font-size:15.5px}
  header{align-items:flex-start}
  .px{font-size:54px;letter-spacing:-2px}      /* bigger, not smaller: it is the one number he reads at a glance */
  .chg{font-size:17px}
  .meta{text-align:left;font-size:11px;line-height:1.7}
  .card{padding:15px 14px;border-radius:14px;margin-bottom:14px}
  .lead{padding:14px 15px;font-size:16px}
  h2{font-size:13px}
  /* Apple's minimum comfortable touch target is 44pt. Anything smaller gets
     mis-tapped on a moving train, which is where this actually gets read. */
  .askbtn{padding:16px;font-size:16px;border-radius:12px}
  .filt button{padding:9px 14px;font-size:13px;border-radius:16px}
  .chip{padding:9px 13px;font-size:13px}
  .chatrow input{padding:13px;font-size:16px}   /* 16px stops iOS zooming the page on focus */
  .chatrow button{padding:13px 16px;font-size:15px}
  .chatlog{max-height:46vh}
  .ses .nt{max-width:46%}
  .cd{font-size:11px}
  .f,.ev,.pr,.ses{font-size:13px}
  .volchart{height:50px}
  .modal{padding:0;align-items:flex-end}
  .mbox{max-width:100%;border-radius:16px 16px 0 0;max-height:88vh;
        padding:18px 16px max(18px,env(safe-area-inset-bottom))}
}
@media(max-width:380px){
  .px{font-size:46px}
  body{padding-left:11px;padding-right:11px}
}
.card{background:#12151c;border:1px solid #1e2430;border-radius:13px;padding:18px;margin-bottom:18px}
h2{font-size:15px;color:#e8eaed;margin-bottom:4px;font-weight:700}
.sub{font-size:12.5px;color:#6b7280;margin-bottom:14px}
.bias{font-size:26px;font-weight:800;letter-spacing:-0.6px;margin:2px 0}
.biasbar{position:relative;height:8px;background:linear-gradient(90deg,#ff3b30,#ff9500,#2a2f3a 46%,#2a2f3a 54%,#34c759,#00d4aa);border-radius:4px;margin:14px 0 6px}
.needle{position:absolute;top:-4px;width:3px;height:16px;background:#fff;border-radius:2px;box-shadow:0 0 9px rgba(255,255,255,.85);transition:left .7s cubic-bezier(.22,1,.36,1)}
.ends{display:flex;justify-content:space-between;font-size:10.5px;color:#5f6368;text-transform:uppercase;letter-spacing:1px}
.f{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid #1a1f2a;font-size:12.5px}
.f:last-child{border:0}.f .n{color:#9aa0a6}.f .d{color:#c8ccd2;font-variant-numeric:tabular-nums}
.mv{border-left:3px solid;padding:12px 14px;margin-bottom:14px;background:#161a23;border-radius:0 10px 10px 0}
.mvt{font-size:14.5px;font-weight:600;margin-bottom:9px;color:#e8eaed}
.cand{padding:8px 0;border-top:1px solid #1e2430;font-size:13.5px}
.cand:first-of-type{border-top:0}
.ct{color:#dfe3e8}.ct a{color:inherit;text-decoration:none;border-bottom:1px dotted #3a4150}
.ctm{color:#6b7280;font-size:11px}
.eff{color:#7fb8a0;font-size:12.5px;margin-top:3px;line-height:1.5}
.hl{padding:11px 0;border-bottom:1px solid #1a1f2a}
.hl:last-child{border:0}
.hlh{font-size:11px;color:#6b7280;margin-bottom:3px}
.post{padding:11px 0;border-bottom:1px solid #1a1f2a}
.post:last-child{border:0}
.who{font-weight:700;color:#ffd479;font-size:12.5px}
.ptxt{color:#dfe3e8;font-size:13.5px;margin:3px 0}
.ev{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid #1a1f2a;font-size:13px}
.ev:last-child{border:0}
.hi{color:#ff453a;font-weight:700;font-size:9.5px}.med{color:#ff9f0a;font-weight:700;font-size:9.5px}
.cd{color:#9aa0a6;font-variant-numeric:tabular-nums;font-size:11.5px;white-space:nowrap;text-align:right}
.empty{color:#5f6368;font-size:13px;padding:12px 0}
.err{background:#2a1f14;border:1px solid #5a4021;color:#ffb86b;padding:9px 13px;border-radius:9px;margin-bottom:14px;font-size:12px}
.askbtn{display:block;width:100%;background:#1f6feb;color:#fff;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit}
.askbtn:hover{background:#2d7ff9}.askbtn:active{transform:translateY(1px)}
.askhint{font-size:11.5px;color:#6b7280;margin-top:8px;line-height:1.6}
.ses{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1a1f2a;font-size:12.5px}
.ses:last-of-type{border:0}
.ses .nm{color:#c8ccd2}
.ses.on .nm{color:#00d4aa;font-weight:700}
.ses .nt{color:#5f6368;font-size:11px;text-align:right;max-width:190px}
.pill2{display:inline-block;font-size:9px;padding:2px 7px;border-radius:9px;background:#0f2a22;color:#00d4aa;font-weight:700;letter-spacing:.5px;margin-right:6px}
.pill2.off{background:#1a1f2a;color:#4a4f58}
.nextev{margin-top:11px;font-size:12.5px;color:#ffd479}
.volchart{display:flex;align-items:flex-end;gap:2px;height:58px;margin-top:10px;overflow:hidden;max-width:100%}
.volbar{flex:1;min-width:0;background:#1f6feb;border-radius:2px 2px 0 0;position:relative;min-height:2px}
.volbar.pk{background:#ffd479}
.vollbl{display:flex;justify-content:space-between;font-size:9.5px;color:#5f6368;margin-top:4px}
.pn{padding:9px 0;border-bottom:1px solid #1a1f2a;font-size:13.5px}
.pn:last-child{border:0}
.pn .rank{display:inline-block;width:20px;color:#ffd479;font-weight:700}
.pr{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1a1f2a;font-size:13px}
.pr:last-of-type{border:0}
.pr .lbl{color:#9aa0a6}
.pr .val{font-variant-numeric:tabular-nums;color:#e8eaed}
.rangebar{position:relative;height:6px;background:#1e2430;border-radius:3px;margin:14px 0 5px}
.rangepos{position:absolute;top:-4px;width:3px;height:14px;background:#ffd479;border-radius:2px;box-shadow:0 0 8px rgba(255,212,121,.7);transition:left .6s ease}
.rangelbl{display:flex;justify-content:space-between;font-size:10.5px;color:#5f6368;font-variant-numeric:tabular-nums}
.dstory{margin-top:12px;font-size:12.5px;color:#7fb8a0;line-height:1.6}
.filt{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.filt button{background:#1a1f2a;color:#9aa0a6;border:1px solid #242b38;border-radius:14px;padding:5px 11px;font-size:11.5px;cursor:pointer;font-family:inherit}
.filt button.on{background:#1f6feb;color:#fff;border-color:#1f6feb}
.cat{display:inline-block;font-size:9px;padding:2px 7px;border-radius:9px;letter-spacing:.6px;text-transform:uppercase;font-weight:700;margin-right:6px}
.cat.person{background:#2b2416;color:#ffd479}
.cat.oil{background:#1a2b20;color:#6ee7a0}
.cat.war{background:#2e1a1a;color:#ff8a8a}
.cat.money{background:#1a2333;color:#7fb3ff}
.chatlog{max-height:340px;overflow-y:auto;margin-bottom:10px;padding-right:4px}
.msg{padding:9px 12px;border-radius:11px;margin-bottom:8px;font-size:13.5px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word}
.msg.bot{background:#161a23;color:#dfe3e8}
.msg.me{background:#1f6feb;color:#fff;margin-left:34px}
.msg b{color:#fff}
.msg.think{color:#6b7280;font-style:italic}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chips button{background:#1a1f2a;color:#9aa0a6;border:1px solid #242b38;border-radius:14px;padding:5px 10px;font-size:11.5px;cursor:pointer;font-family:inherit}
.chips button:hover{background:#222836;color:#c8ccd2}
.chatrow{display:flex;gap:7px}
#qbox{flex:1;background:#0a0c10;border:1px solid #242b38;border-radius:9px;color:#e8eaed;padding:11px 12px;font-size:14px;font-family:inherit}
#qbox:focus{outline:none;border-color:#1f6feb}
#sendbtn{background:#1f6feb;color:#fff;border:0;border-radius:9px;padding:11px 16px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
#sendbtn:disabled{opacity:.5}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.72);display:none;align-items:center;justify-content:center;padding:24px;z-index:50}
.modal.on{display:flex}
.mbox{background:#12151c;border:1px solid #2a3140;border-radius:14px;max-width:760px;width:100%;max-height:82vh;display:flex;flex-direction:column;padding:20px}
.mbox h3{font-size:16px;margin-bottom:4px}
.mbox p{font-size:12.5px;color:#6b7280;margin-bottom:12px}
textarea{flex:1;min-height:320px;background:#0a0c10;border:1px solid #1e2430;border-radius:9px;color:#c8ccd2;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;padding:13px;resize:none}
.mrow{display:flex;gap:9px;margin-top:12px}
.mrow button{flex:1;border:0;border-radius:9px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit}
.copy{background:#1f6feb;color:#fff}.close{background:#1e2430;color:#c8ccd2}
/* A flex row of N bars has an intrinsic minimum of N*min-width + (N-1)*gap.
   With 400 one-minute bars that was 799px, so this one chart silently set the
   width of the whole page and every line of text ran off the right edge of a
   phone. min-width:0 lets the bars shrink; overflow:hidden means a future
   chart with too many points clips itself instead of stretching the page. */
.spark{display:flex;align-items:flex-end;gap:1px;height:44px;margin-top:12px;overflow:hidden;max-width:100%}
.spark div{flex:1;min-width:0;border-radius:1px 1px 0 0}
@media(max-width:840px){.spark{gap:0}}   /* gaps do not shrink; below this they alone would overflow */
.note{font-size:11px;color:#454a52;line-height:1.7;margin-top:14px}
</style></head><body><div class="wrap">
<header>
  <div>
    <div class="brand">Gold Watch &middot; XAU/USD</div>
    <div class="px" id="px">&mdash;</div>
    <div class="chg" id="chg"></div>
  </div>
  <div class="meta">
    <span class="dot" id="dot"></span><span id="st">connecting</span><br>
    <span id="upd"></span><br><span id="cnt"></span>
  </div>
</header>
<div class="lead" id="lead">Loading&hellip;</div>
<div class="card" id="pinned" style="display:none">
  <h2>Biggest things right now</h2>
  <div id="pinnedlist"></div>
</div>
<div id="errbox"></div>
<div class="grid">
 <div>
  <div class="card">
    <h2>Why gold moved</h2>
    <div class="sub">When gold jumps more than normal, here is what was in the news at that exact moment.</div>
    <div id="moves"><div class="empty">Watching&hellip;</div></div>
  </div>
  <div class="card">
    <h2>What is moving the world</h2>
    <div class="sub">People, oil, wars and money news that can push gold around. Filtered to what actually matters for gold.</div>
    <div class="filt" id="filt">
      <button class="on" onclick="setCat(this,'all')">Everything</button>
      <button onclick="setCat(this,'person')">People</button>
      <button onclick="setCat(this,'oil')">Oil</button>
      <button onclick="setCat(this,'war')">War</button>
      <button onclick="setCat(this,'money')">Money</button>
    </div>
    <div id="social"><div class="empty">Loading&hellip;</div></div>
  </div>
  <div class="card">
    <h2>Gold news right now</h2>
    <div class="sub">Filtered down to things that actually affect the gold price.</div>
    <div id="news"><div class="empty">Loading&hellip;</div></div>
  </div>
 </div>
 <div>
  <div class="card">
    <h2>Trading clock</h2>
    <div class="sub">Gold is quiet in Asia and wild when London and New York are both open.</div>
    <div id="sess"></div>
    <div class="nextev" id="nextev"></div>
    <h2 style="margin-top:16px">When gold actually moves</h2>
    <div class="sub" id="volsub">Measured from your own 1-minute data.</div>
    <div class="volchart" id="volchart"></div>
  </div>
  <div class="card">
    <h2>Day trading view</h2>
    <div class="sub">Gold's three closest relationships, and where price sits in today's range.</div>
    <div id="peers"></div>
    <div class="rangebar"><div class="rangepos" id="rpos"></div></div>
    <div class="rangelbl"><span id="rlo">low</span><span id="rhi">high</span></div>
    <div class="dstory" id="dstory"></div>
  </div>
  <div class="card" id="chatcard">
    <h2>Ask about gold</h2>
    <div class="sub">Type a question. It answers from the live data on this page. <span id="bk"></span></div>
    <div class="chatlog" id="chatlog">
      <div class="msg bot">Hi. Ask me anything about gold and I will explain it simply.</div>
    </div>
    <div class="chips" id="chips">
      <button onclick="askQ('Why is gold moving?')">Why is gold moving?</button>
      <button onclick="askQ('What is coming up?')">What is coming up?</button>
      <button onclick="askQ('What did Trump say?')">What did Trump say?</button>
      <button onclick="askQ('What is inflation?')">What is inflation?</button>
    </div>
    <div class="chatrow">
      <input id="qbox" placeholder="Ask a question..." autocomplete="off"
             onkeydown="if(event.key==='Enter')send()">
      <button onclick="send()" id="sendbtn">Send</button>
    </div>
    <div class="askhint"><a href="#" onclick="openAsk();return false;" style="color:#6b7280">Or copy everything as text &rarr;</a></div>
  </div>
  <div class="card">
    <h2>Which way is gold leaning?</h2>
    <div class="sub">A simple score built from what is happening right now.</div>
    <div class="bias" id="bias">&mdash;</div>
    <div class="biasbar"><div class="needle" id="ndl" style="left:calc(50% - 1.5px)"></div></div>
    <div class="ends"><span>Going down</span><span>Going up</span></div>
    <div style="margin-top:14px" id="bwhy"></div>
    <div class="spark" id="spark"></div>
  </div>
  <div class="card">
    <h2>Big US reports coming</h2>
    <div class="sub">These can move gold within seconds of being released.</div>
    <div id="cal"><div class="empty">Loading&hellip;</div></div>
  </div>
  <div class="note">This shows what was in the news around a move. It does not prove
  the news caused it. Always read the actual headline, not just the score.<br><br>
  Free public data. Not financial advice.</div>
 </div>
</div></div>

<div class="modal" id="modal">
  <div class="mbox">
    <h3>Ask Claude about gold right now</h3>
    <p>Everything on the page, written out as a question. Copy it and paste it into Claude.</p>
    <textarea id="brief" readonly></textarea>
    <div class="mrow">
      <button class="copy" id="cbtn" onclick="copyBrief()">Copy to clipboard</button>
      <button class="close" onclick="E('modal').classList.remove('on')">Close</button>
    </div>
  </div>
</div>

<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
// Null-safe element lookup. Before this, one mistyped or missing id threw inside
// render() and every panel BELOW that line silently stayed on "Loading...".
// A broken panel should cost one panel, not the page.
const _stub={style:{},classList:{add(){},remove(){}},textContent:'',innerHTML:'',
             removeAttribute(){},setAttribute(){},select(){},focus(){},value:'',
             appendChild(){},remove(){},offsetWidth:0,scrollTop:0,scrollHeight:0};
function E(id){return document.getElementById(id)||_stub;}
const ago=m=>m>0?m+" min before":(m<0?(-m)+" min after":"same minute");
function cd(ts){const s=ts-Date.now()/1000;if(s<0)return"done";
  const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h>24?Math.floor(h/24)+" days":(h?h+"h "+m+"m":m+" min");}

async function openAsk(){
  const m=E('modal'); m.classList.add('on');
  E('brief').value='Building...';
  try{ E('brief').value=await (await fetch('/brief')).text(); }
  catch(e){ E('brief').value='Could not build the briefing.'; }
}
async function copyBrief(){
  const t=E('brief'), b=E('cbtn');
  try{ await navigator.clipboard.writeText(t.value); }
  catch(e){ t.removeAttribute('readonly'); t.select(); document.execCommand('copy');
            t.setAttribute('readonly',''); }
  b.textContent='Copied - now paste it into Claude';
  setTimeout(()=>b.textContent='Copy to clipboard',2600);
}

function md(t){return esc(t)
  .replace(/\*\*(.+?)\*\*/g,'<b>$1</b>')
  .replace(/\*(.+?)\*/g,'<i>$1</i>')
  .replace(/^- /gm,'&bull; ')
  .replace(/^---$/gm,'<hr style="border:0;border-top:1px solid #242b38;margin:8px 0">');}
function bubble(cls,txt){
  const l=E('chatlog');
  const d=document.createElement('div'); d.className='msg '+cls; d.innerHTML=md(txt);
  l.appendChild(d); l.scrollTop=l.scrollHeight; return d;}
function askQ(q){E('qbox').value=q;send();}
async function send(){
  const box=E('qbox'), btn=E('sendbtn');
  const q=box.value.trim(); if(!q)return;
  box.value=''; btn.disabled=true; bubble('me',q);
  const th=bubble('bot think','thinking...');
  try{
    const r=await (await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({q:q})})).json();
    th.remove(); bubble('bot',r.answer);
    E('bk').textContent='('+r.backend+')';
  }catch(e){ th.remove(); bubble('bot','Could not reach the answer engine.'); }
  btn.disabled=false; box.focus();
}

let CAT='all';
function setCat(b,c){CAT=c;document.querySelectorAll('.filt button').forEach(x=>x.classList.remove('on'));b.classList.add('on');render();}
let lastPx=null;
async function render(){
  let d; try{ d=await (await fetch('/state.json',{cache:'no-store'})).json(); }
  catch(e){ E('st').textContent='disconnected';
            E('dot').style.background='#ff3b30'; return; }

  E('st').textContent=d.status==='live'?'live':d.status;
  E('dot').style.background=d.status==='live'?'#00d4aa':'#ff9500';
  E('upd').textContent=d.updated?('updated '+d.updated+' SGT'):'';
  E('cnt').textContent=d.n_news+' headlines, '+(d.social||[]).length+' posts';

  const pxEl=E('px');
  pxEl.textContent=d.price?('$'+d.price.toLocaleString(undefined,{minimumFractionDigits:2})):'—';
  if(lastPx!==null&&d.price!==lastPx){pxEl.classList.remove('flash');void pxEl.offsetWidth;pxEl.classList.add('flash');}
  lastPx=d.price;
  const up=d.chg>=0;
  E('chg').innerHTML='<span class="'+(up?'up':'dn')+'">'+(up?'+$':'-$')+
    Math.abs(d.chg)+' ('+(up?'+':'')+d.chg_pct+'%)</span>'+
    '<span style="color:#5f6368;font-weight:400"> since yesterday</span>';

  E('lead').innerHTML='<b>'+esc(d.bias_plain||'')+'</b> '+esc(d.bias_because||'');
  E('errbox').innerHTML=(d.errors||[]).length?
    '<div class="err">Some data is temporarily unavailable: '+d.errors.map(esc).join(' &middot; ')+
    '. It retries by itself.</div>':'';

  const ss=d.sessions||{};
  E('sess').innerHTML=(ss.list||[]).map(function(x){
    return '<div class="ses'+(x.on?' on':'')+'"><span class="nm">'+
      '<span class="pill2'+(x.on?'':' off')+'">'+(x.on?'OPEN':'shut')+'</span>'+
      esc(x.name)+'</span><span class="nt">'+esc(x.note)+'</span></div>';}).join('');
  E('nextev').textContent=ss.next?
    ('Next: '+ss.next.what+' in '+ss.next.mins+' minutes'):'';

  const hv=d.hourly||[];
  if(hv.length){
    const mx=Math.max.apply(null,hv.map(r=>r.avg))||1;
    E('volchart').innerHTML=hv.map(function(r){
      return '<div class="volbar'+(r.avg>=mx*0.85?' pk':'')+'" style="height:'+
        Math.max(4,r.avg/mx*100)+'%" title="'+r.hour+':00 SGT — '+r.avg+' pips per minute"></div>';
      }).join('')+'';
    const d0=hv[0].days||0;
    E('volsub').innerHTML='Average movement per minute, by hour (your time). '+
      'Tallest bar = most movement.<br><span style="color:#ff9f0a">Only '+d0+
      ' days of data, and it includes a Fed decision — one big event can make an hour look busier than it usually is.</span>';
  }

  const pe=d.headline_events||[];
  E('pinned').style.display=pe.length?'block':'none';
  E('pinnedlist').innerHTML=pe.map(function(n,i){
    return '<div class="pn"><span class="rank">'+(i+1)+'</span>'+
      (n.link?'<a href="'+esc(n.link)+'" target="_blank" style="color:#dfe3e8;text-decoration:none">':'<span style="color:#dfe3e8">')+
      esc(n.title)+(n.link?'</a>':'</span>')+
      '<div class="eff" style="margin-left:20px">'+esc(n.effect||'')+'</div></div>';}).join('');

  const pr=d.peers||{};
  E('peers').innerHTML=Object.keys(pr).length?Object.keys(pr).map(function(k){
    const v=pr[k], up=v.pct>=0;
    return '<div class="pr"><span class="lbl">'+esc(v.label)+'</span><span class="val">'+v.px+
      ' <span class="'+(up?'up':'dn')+'">'+(up?'+':'')+v.pct+'%</span></span></div>';}).join('')
    :'<div class="empty">Loading related markets...</div>';
  const lv=d.levels||{};
  if(lv.day_high){
    E('rpos').style.left='calc('+lv.pos_pct+'% - 1.5px)';
    E('rlo').textContent='low $'+lv.day_low+'  (+$'+lv.from_low+')';
    E('rhi').textContent='($'+lv.from_high+' away)  high $'+lv.day_high;
  }
  E('dstory').textContent=d.dollar_story||'';

  E('bias').textContent=d.bias;
  E('bias').style.color=d.bias_colour;
  E('ndl').style.left='calc('+(50+d.bias_score*10)+'% - 1.5px)';
  E('bwhy').innerHTML=(d.bias_why||[]).map(function(w){
    const nm={'vs prev close':'Compared with yesterday','last hour trend':'Last hour',
              'news tone (6h)':'Today\'s news','event risk':'Watch out for'}[w.factor]||w.factor;
    return '<div class="f"><span class="n">'+esc(nm)+'</span><span class="d">'+esc(w.detail)+'</span></div>';
  }).join('');

  const sp=d.bars||[];
  if(sp.length>2){const cs=sp.map(b=>b.c),lo=Math.min.apply(null,cs),hi=Math.max.apply(null,cs),rg=(hi-lo)||1;
    E('spark').innerHTML=sp.map(function(b,i){
      const h=8+((b.c-lo)/rg)*92, c=i>0&&b.c>=sp[i-1].c?'#00d4aa':'#ff3b30';
      return '<div style="height:'+h+'%;background:'+c+';opacity:.72"></div>';}).join('');}

  E('moves').innerHTML=(d.moves||[]).length?d.moves.map(function(m){
    const col=m.dir==='up'?'#00d4aa':'#ff3b30';
    const c=(m.why||[]).length?m.why.map(function(x){
      return '<div class="cand"><div class="ct">'+
        (x.link?'<a href="'+esc(x.link)+'" target="_blank">'+esc(x.title)+'</a>':esc(x.title))+'</div>'+
        '<div class="ctm">'+esc(x.src)+' &middot; '+x.time+' SGT &middot; '+ago(x.mins)+'</div>'+
        (x.effect?'<div class="eff">'+esc(x.effect)+'</div>':'')+'</div>';}).join('')
      :'<div class="cand"><div class="ctm">Nothing was in the news then, so this was probably just normal buying and selling.</div></div>';
    return '<div class="mv" style="border-color:'+col+'"><div class="mvt">'+esc(m.plain||'')+
      '<br><span class="ctm">'+m.time+' SGT &middot; $'+m.from+' to $'+m.to+'</span></div>'+c+'</div>';
  }).join(''):'<div class="empty">Gold has been moving normally, no sudden jumps to explain.</div>';

  const items=(d.social||[]).filter(p=>CAT==='all'||p.cat===CAT);
  E('social').innerHTML=items.length?items.slice(0,10).map(function(p){
    return '<div class="post"><span class="cat '+esc(p.cat||'person')+'">'+esc(p.cat||'news')+'</span>'+
    '<span class="who">'+esc(p.who)+'</span> '+
    '<span class="ctm">&middot; '+esc(p.where)+' &middot; '+p.time+' SGT</span>'+
    '<div class="ptxt">'+(p.link?'<a href="'+esc(p.link)+'" target="_blank" style="color:inherit;text-decoration:none">':'')+
    esc(p.text)+(p.link?'</a>':'')+'</div>'+
    (p.effect?'<div class="eff">'+esc(p.effect)+'</div>':'')+'</div>';}).join('')
    :'<div class="empty">Nothing in this category right now.</div>';

  E('news').innerHTML=(d.news||[]).length?d.news.slice(0,12).map(function(n){
    return '<div class="hl"><div class="hlh">'+n.time+' SGT &middot; '+esc(n.src)+'</div>'+
    (n.link?'<a href="'+esc(n.link)+'" target="_blank" style="color:#dfe3e8;text-decoration:none">':'<span style="color:#dfe3e8">')+
    esc(n.title)+(n.link?'</a>':'</span>')+
    (n.effect?'<div class="eff">'+esc(n.effect)+'</div>':'')+'</div>';}).join('')
    :'<div class="empty">No gold news right now.</div>';

  const now=Date.now()/1000;
  const ev=(d.calendar||[]).filter(c=>c.ts>now-1800).slice(0,6);
  E('cal').innerHTML=ev.length?ev.map(function(c){
    return '<div class="ev"><span><span class="'+(c.impact==='High'?'hi':'med')+'">'+
    (c.impact==='High'?'BIG':'MEDIUM')+'</span> '+esc(c.title)+'</span>'+
    '<span class="cd">'+esc(c.date.split(' ').slice(0,2).join(' '))+' '+c.time+
    '<br><span style="color:#5f6368">in '+cd(c.ts)+'</span></span></div>';}).join('')
    :((d.errors||[]).some(e=>e.indexOf('calendar')===0)
      ? '<div class="empty">Cannot load the calendar right now. That is different from nothing being scheduled. It retries automatically.</div>'
      : '<div class="empty">Nothing scheduled.</div>');
}
render();
try{ const es=new EventSource('/events'); es.onmessage=function(){render();}; }catch(e){}
setInterval(render, 5000);
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-open", action="store_true")
    # 0.0.0.0 makes the dashboard reachable from a phone on the same wifi.
    # Default stays loopback: anything else is opt-in, not a surprise.
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--once", action="store_true",
                    help="print one text readout and exit")
    a = ap.parse_args()
    os.makedirs(os.path.join(HOME, "cache"), exist_ok=True)
    threading.Thread(target=scheduler, daemon=True).start()
    if a.once:
        # one pass over every task, then the same briefing the dashboard shows
        for name, fn, _ in TASKS:
            try:
                fn()
            except Exception as e:
                print(f"({name} unavailable: {type(e).__name__})", file=sys.stderr)
        with LOCK:
            STATE["dollar_story"] = dollar_story(STATE.get("peers", {}),
                                                 STATE.get("chg_pct", 0))
        compute_bias()
        rebuild_moves()
        print(briefing())
        return
    srv = ThreadingHTTPServer((a.host, a.port), H)
    print("=" * 64)
    print(f"  GOLD WATCH   ->  http://localhost:{a.port}")
    print("  price 20s | news 90s | posts 150s | calendar 15m | live push on")
    print("=" * 64, flush=True)
    if not a.no_open:
        import subprocess
        subprocess.run(["open", f"http://localhost:{a.port}"], check=False)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
