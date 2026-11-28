"""
What actually moves the gold price — and what only looks like it does.

Every list here was built by pulling a real headline corpus off the live feeds
and reading it, NOT from memory. That matters: more than half of all headlines
containing the word "gold" are junior mining companies reporting drill assays
("Hits 41 Meters of 4.59 g/t Gold"). Those have nothing to do with the gold
price and would drown the dashboard. Hence EXCLUDE.
"""
import re

# ---------------------------------------------------------------- relevance
# Things that genuinely drive the gold PRICE. Weight = how much it matters.
DRIVERS = {
    3: [  # the big four
        "dollar", "us dollar", "dxy", "greenback",
        "fed", "fomc", "federal reserve", "powell", "warsh",
        "rate hike", "rate cut", "interest rate", "policy rate", "fed funds",
        "real yield", "real yields", "treasury yield", "10-year", "10 year",
        "inflation", "cpi", "pce", "core inflation",
    ],
    2: [
        "safe haven", "safe-haven", "haven demand", "risk off", "risk-off",
        "geopolitic", "war", "conflict", "sanction", "tariff", "trade war",
        "central bank", "central banks", "reserve buying", "pboc",
        "etf", "etf inflow", "etf outflow", "bullion demand",
        "jobs report", "payroll", "nonfarm", "nfp", "unemployment",
        "recession", "stagflation", "debt ceiling", "deficit",
        "yield curve", "bond yield", "bonds", "treasuries",
        "silver", "commodities", "crude", "oil prices",
    ],
    1: [
        "hawkish", "dovish", "tightening", "easing", "stimulus",
        "jackson hole", "minutes", "projections", "dot plot",
        "gdp", "retail sales", "ism", "pmi", "consumer confidence",
        "election", "political", "fiscal",
    ],
}

# Price-action language: a headline talking about where gold IS, not who mined it
PRICE_ACTION = [
    "climbs", "rises", "rallies", "surges", "jumps", "gains", "advances",
    "falls", "drops", "slips", "slides", "retreats", "tumbles", "sinks",
    "rebounds", "recovers", "steadies", "holds", "consolidates", "extends",
    "record high", "all-time high", "high", "low", "support", "resistance",
    "momentum", "sma", "moving average", "breakout", "trades", "trading",
    "outlook", "forecast", "target", "bullish", "bearish", "bias",
    "%", "per cent", "percent",
]

# The noise floor. Mining/exploration/corporate — NOT the gold price.
# Every one of these was observed in a real feed pulling "gold" headlines.
EXCLUDE = [
    "drilling", "drill", "assay", "assays", "intercept", "g/t", "grams per tonne",
    "exploration", "explorer", "deposit", "orebody", "ore body", "mineral",
    "resource estimate", "feasibility", "metallurg", "tailings", "smelter",
    "mine site", "mining stock", "miner", "miners", "project", "claims",
    "shares rise", "shares fall", "share price", "stock popped", "buy rating",
    "price target", "analysts raise", "acquisition", "merger", "ownership",
    "consolidates", "closes c$", "private placement", "ipo", "earnings",
    "quarterly results", "dividend", "ceo", "appoints", "board of directors",
    "newmont", "barrick", "agnico", "kinross", "franco-nevada", "gldm", "gld",
]

# Directional lean, for the bias panel
BULLISH = ["climbs", "rises", "rallies", "surges", "jumps", "gains", "advances",
           "rebounds", "recovers", "record high", "all-time high", "breakout",
           "bullish", "haven demand", "inflows", "dovish", "rate cut",
           "weaker dollar", "dollar falls", "dollar slips", "yields fall",
           "risk off", "safe haven", "escalat"]
BEARISH = ["falls", "drops", "slips", "slides", "retreats", "tumbles", "sinks",
           "bearish", "outflows", "hawkish", "rate hike", "stronger dollar",
           "dollar rises", "dollar climbs", "yields rise", "risk on",
           "profit taking", "profit-taking", "correction", "de-escalat"]

_cache = {}


def _rx(terms, key):
    """Whole-word matcher, built once. Word boundaries matter: a substring
    test makes 'gold' match 'Goldman' and 'war' match 'forward'."""
    if key not in _cache:
        parts = []
        for t in terms:
            t = t.strip()
            if not t:
                continue
            if re.match(r"^[a-z0-9]", t):
                # optional plural/verb suffix: "bond yield" must catch "bond yields",
                # "payroll" must catch "payrolls". Caught in validation, not in theory.
                suffix = r"\w*" if t.endswith(("escalat", "geopolitic")) else r"(?:s|es)?\b"
                parts.append(r"\b" + re.escape(t) + suffix)
            else:
                parts.append(re.escape(t))
        _cache[key] = re.compile("|".join(parts), re.I)
    return _cache[key]


GOLD_RX = re.compile(r"\b(gold|xau|bullion|precious metal)\w*\b", re.I)

# "Dollar" only counts when it is the US dollar. A headline about the New Zealand
# Dollar is not a gold driver -- found in validation against real feed output.
OTHER_CCY = re.compile(
    r"\b(new zealand|australian|canadian|singapore|hong kong|taiwan|kiwi|aussie|"
    r"loonie|nz|aud|cad|sgd|nzd)\s+dollar|\b(kiwi|aussie|loonie)\b", re.I)


def _us_dollar_only(title, tags):
    """Drop a 'dollar' credit that actually belongs to another currency."""
    t = title.lower()
    if "dollar" not in t:
        return tags, 0.0
    explicit_us = re.search(r"\b(us dollar|u\.s\. dollar|dxy|greenback|dollar index)\b", t)
    if explicit_us:
        return tags, 0.0
    if OTHER_CCY.search(t):
        removed = [x for x in tags if x == "dollar"]
        return [x for x in tags if x != "dollar"], -3.0 * len(removed)
    return tags, 0.0


def score_headline(title):
    """
    How relevant is this headline to the gold PRICE?
    Returns (score, tags). Score <= 0 means don't show it.
    """
    t = title.lower()
    tags, score = [], 0.0

    is_gold = bool(GOLD_RX.search(t))
    has_action = bool(_rx(PRICE_ACTION, "pa").search(t))

    # the mining-noise veto
    ex = _rx(EXCLUDE, "ex").findall(t)
    if ex:
        return -1, ["mining/corporate: " + ex[0]]

    if is_gold:
        score += 4 if has_action else 2
        tags.append("gold")

    for wt, terms in DRIVERS.items():
        for m in set(x.lower() for x in _rx(terms, f"d{wt}").findall(t)):
            score += wt
            tags.append(m)

    tags, adj = _us_dollar_only(title, tags)
    score += adj

    # a macro headline with no gold mention still matters (dollar, Fed, yields)
    if not is_gold and score < 3:
        score = 0

    return round(score, 1), tags


def lean(title):
    """Directional tilt of a headline: +1 bullish gold, -1 bearish, 0 neutral."""
    t = title.lower()
    b = len(_rx(BULLISH, "bull").findall(t))
    s = len(_rx(BEARISH, "bear").findall(t))
    if b > s:
        return 1
    if s > b:
        return -1
    return 0


# ============================================================ social / political
# People whose posts and remarks move gold. X/Twitter killed free API access and
# Truth Social blocks direct calls, so: raw Trump posts come from the
# trumpstruth.org mirror, everyone else via targeted Google News RSS (which
# reports "X said Y" within minutes and needs no key).
WATCHLIST = {
    # ---------------- people who move gold when they open their mouth --------
    "Trump":        ("person", '"Donald Trump" (tariff OR Fed OR "interest rates" OR China OR dollar OR gold OR sanctions)'),
    "Fed Chair":    ("person", '"Kevin Warsh" OR "Fed Chair" ("interest rates" OR inflation OR policy)'),
    "Fed":          ("person", '"Federal Reserve" (Waller OR Bowman OR Williams OR "rate cut" OR "rate hike" OR minutes)'),
    "US Treasury":  ("person", '"Treasury Secretary" OR "Treasury Department" (dollar OR yields OR debt OR borrowing)'),
    "ECB":          ("person", '"European Central Bank" OR Lagarde ("interest rates" OR inflation OR euro)'),
    "Bank of Japan":("person", '"Bank of Japan" OR "BOJ" (yen OR "interest rates" OR intervention)'),
    "China / PBOC": ("person", '"PBOC" OR "Xi Jinping" OR "Peoples Bank of China" (yuan OR gold OR stimulus OR trade)'),
    "Putin":        ("person", 'Putin OR Kremlin (oil OR sanctions OR nuclear OR Ukraine OR energy)'),
    "Musk":         ("person", '"Elon Musk" (economy OR Fed OR dollar OR gold OR bitcoin)'),

    # ---------------- oil and energy: feeds inflation, feeds gold ------------
    "Oil / OPEC":   ("oil", 'OPEC (output OR production OR cut OR quota OR meeting OR supply)'),
    "Oil supply":   ("oil", '(oil OR crude) (supply OR shock OR outage OR refinery OR pipeline OR "strategic reserve" OR embargo)'),
    "Oil price":    ("oil", '(oil OR Brent OR WTI) (surges OR jumps OR plunges OR slumps OR rally OR selloff)'),
    "Saudi / Gulf": ("oil", '"Saudi Arabia" OR Aramco OR UAE (oil OR output OR production OR pricing)'),

    # ---------------- war and conflict: the classic gold trigger -------------
    "Russia/Ukraine":("war", '(Russia OR Ukraine) (strike OR attack OR ceasefire OR "peace talks" OR sanctions OR escalation OR drone OR energy)'),
    "Middle East":  ("war", '(Israel OR Iran OR Gaza OR Lebanon OR Yemen OR Houthi) (strike OR attack OR ceasefire OR escalation OR nuclear OR retaliation)'),
    "Taiwan/China": ("war", '(Taiwan OR "South China Sea") (military OR tension OR drill OR incursion OR blockade)'),
    "Shipping":     ("war", '("Red Sea" OR "Strait of Hormuz" OR "Suez Canal") (attack OR blocked OR disruption OR tanker)'),
    "Unrest":       ("war", '(coup OR "state of emergency" OR "martial law" OR uprising) (oil OR markets OR economy)'),

    # ---------------- money, debt and the dollar itself ----------------------
    "CB gold buying":("money",'"central bank" (gold OR bullion) (buying OR reserves OR purchases OR stockpile)'),
    "De-dollarisation":("money",'(BRICS OR "de-dollarization") (gold OR dollar OR "reserve currency" OR trade)'),
    "US debt":      ("money", '("US debt" OR "debt ceiling" OR "credit rating" OR downgrade) (Treasury OR default OR borrowing)'),
    "Dollar":       ("money", '("US dollar" OR "dollar index" OR DXY) (surges OR slides OR weakens OR strengthens OR rally)'),
    "Inflation":    ("money", '(inflation OR CPI OR PCE) (report OR data OR rises OR falls OR hotter OR cooler)'),
}

# kept for anything still importing the old name
FIGURES = {k: v[1] for k, v in WATCHLIST.items()}

# What makes a post market-moving rather than noise. Weight = how hard it hits gold.
SOCIAL_HOT = {
    4: ["tariff", "tariffs", "sanction", "sanctions", "trade war", "embargo",
        "interest rate", "interest rates", "rate cut", "rate hike", "the fed",
        "federal reserve", "fed chair", "powell", "warsh", "money supply"],
    3: ["inflation", "dollar", "gold", "oil price", "opec", "strategic reserve",
        "china", "trade deal", "tax", "taxes", "tariff rate", "devalue",
        "national debt", "deficit", "treasury", "bond", "recession"],
    2: ["economy", "jobs", "stock market", "market", "wall street", "energy",
        "iran", "russia", "ukraine", "israel", "gaza", "lebanon", "yemen",
        "houthi", "taiwan", "strike", "military", "war", "ceasefire", "truce",
        "drone", "missile", "nuclear", "retaliation", "escalation",
        "opec", "crude", "brent", "wti", "barrel", "refinery", "pipeline",
        "output cut", "production cut", "strategic reserve", "tanker",
        "red sea", "hormuz", "suez", "aramco", "saudi",
        "brics", "reserve currency", "downgrade", "credit rating",
        "election", "shutdown", "border"],
}

_SOC_NOISE = re.compile(
    r"\b(faith|church|golf|thanksgiving|witch hunt|fake news|lake america|"
    r"congratulations|happy birthday|rt @|weather service)\b", re.I)


def score_social(text, author=""):
    """
    Could this post or remark move gold? Returns (score, tags).
    Most of what prominent figures post is not market news -- filtered to 0.
    """
    t = (text or "").lower()
    if not t.strip():
        return 0, []
    if _SOC_NOISE.search(t) and len(t) < 240:
        return 0, ["off-topic"]

    score, tags = 0.0, []
    for wt, terms in SOCIAL_HOT.items():
        for m in set(x.lower() for x in _rx(terms, f"s{wt}").findall(t)):
            score += wt
            tags.append(m)
    if not tags:
        return 0, []

    # a sitting president or the Fed chair moves more than a commentator
    if re.search(r"trump|president", author or "", re.I):
        score *= 1.4
    elif re.search(r"warsh|fed|powell|treasury", author or "", re.I):
        score *= 1.25
    return round(min(score, 20), 1), tags[:5]
