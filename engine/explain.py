import re
"""
Turn market jargon into something a 12-year-old can read.

Every tag the scoring engine produces gets three things here:
  - a friendly name        ("the Fed" -> "America's central bank")
  - what it does to gold   ("makes gold go DOWN, usually")
  - a one-line reason      ("saving money pays more, so gold looks boring")

Rule for this file: no term is allowed to explain itself with another term you
would also have to look up.
"""

UP, DOWN, MIXED = "up", "down", "mixed"

# tag -> (friendly name, direction for gold, why in one plain sentence)
MEANING = {
    # --- money and interest rates -----------------------------------------
    "fed":            ("America's central bank", DOWN,
                       "They decide how expensive it is to borrow money in the US."),
    "fomc":           ("the Fed's rate meeting", MIXED,
                       "This is the meeting where they decide to raise or lower rates."),
    "federal reserve":("America's central bank", DOWN,
                       "They decide how expensive it is to borrow money in the US."),
    "warsh":          ("the boss of the Fed", MIXED,
                       "What he says about rates moves gold straight away."),
    "powell":         ("a former Fed boss", MIXED,
                       "People still listen to what he says about rates."),
    "rate hike":      ("raising interest rates", DOWN,
                       "Saving cash now pays you more, so gold (which pays nothing) looks boring."),
    "rate cut":       ("cutting interest rates", UP,
                       "Saving cash pays less, so people move money into gold instead."),
    "interest rate":  ("interest rates", DOWN,
                       "When rates go up, saving cash pays more, so gold looks boring."),
    "policy rate":    ("the official US interest rate", DOWN,
                       "When rates go up, gold looks less attractive."),
    "fed funds":      ("the official US interest rate", DOWN,
                       "When rates go up, gold looks less attractive."),
    "hawkish":        ("wanting HIGHER rates", DOWN,
                       "Higher rates make holding gold less attractive."),
    "dovish":         ("wanting LOWER rates", UP,
                       "Lower rates make holding gold more attractive."),
    "tightening":     ("making money harder to borrow", DOWN,
                       "Expensive borrowing pulls money out of gold."),
    "easing":         ("making money easier to borrow", UP,
                       "Cheap money tends to flow into gold."),

    # --- the dollar --------------------------------------------------------
    "dollar":         ("the US dollar", DOWN,
                       "Gold is priced in dollars, so a strong dollar makes gold pricier for everyone else."),
    "us dollar":      ("the US dollar", DOWN,
                       "Gold is priced in dollars, so a strong dollar makes gold pricier for everyone else."),
    "dxy":            ("the dollar's strength score", DOWN,
                       "When this goes up, gold usually goes down."),
    "greenback":      ("the US dollar", DOWN,
                       "Strong dollar, weaker gold. They usually move opposite ways."),

    # --- inflation ---------------------------------------------------------
    "inflation":      ("prices going up", UP,
                       "When money buys less each year, people buy gold to protect their savings."),
    "cpi":            ("the monthly price-rise report", UP,
                       "It shows how fast things are getting more expensive."),
    "pce":            ("the Fed's favourite price report", UP,
                       "It's the number the Fed actually watches to judge inflation."),
    "core inflation": ("price rises, ignoring food and fuel", UP,
                       "Strips out the jumpy stuff to show the real trend."),

    # --- bonds and yields --------------------------------------------------
    "treasury yield": ("what the US government pays to borrow", DOWN,
                       "If lending to the US pays well, gold looks boring by comparison."),
    "real yield":     ("interest after inflation is taken off", DOWN,
                       "This is gold's biggest rival. Higher real yield, lower gold."),
    "bond yield":     ("what lending money pays", DOWN,
                       "Higher payouts elsewhere pull money away from gold."),
    "bonds":          ("government IOUs", DOWN,
                       "When these pay more, gold usually struggles."),
    "treasuries":     ("US government IOUs", DOWN,
                       "The safest rival to gold. When they pay more, gold struggles."),
    "10-year":        ("the 10-year US loan rate", DOWN,
                       "The single number most traders watch against gold."),
    "yield curve":    ("short vs long borrowing costs", MIXED,
                       "Traders read it for hints about a coming slowdown."),

    # --- fear and safety ---------------------------------------------------
    "safe haven":     ("somewhere safe to hide money", UP,
                       "When people get scared, they buy gold."),
    "geopolitic":     ("tension between countries", UP,
                       "Trouble in the world makes people want gold."),
    "war":            ("war or fighting", UP,
                       "Fear sends people into gold."),
    "conflict":       ("countries clashing", UP,
                       "Uncertainty makes gold more wanted."),
    "sanction":       ("countries punishing each other financially", UP,
                       "It disrupts trade and makes people nervous, which helps gold."),
    "tariff":         ("a tax on imported goods", UP,
                       "It can push prices up and start trade fights, both good for gold."),
    "trade war":      ("countries taxing each other's goods", UP,
                       "Bad for trade, good for gold."),
    "recession":      ("the economy shrinking", UP,
                       "People expect rate cuts and hide in gold."),
    "risk off":       ("investors getting scared", UP,
                       "Money leaves risky things and goes into gold."),

    # --- demand ------------------------------------------------------------
    "central bank":   ("countries' own banks buying gold", UP,
                       "When governments stockpile gold, it soaks up supply."),
    "etf":            ("funds that hold gold for investors", UP,
                       "When money flows into these funds, they have to buy real gold."),
    "silver":         ("silver", UP,
                       "Silver and gold usually move together."),

    # --- the economy -------------------------------------------------------
    "payroll":        ("the US jobs report", DOWN,
                       "Lots of new jobs means the Fed can keep rates high, which hurts gold."),
    "nonfarm":        ("the big monthly US jobs number", DOWN,
                       "A strong number usually knocks gold down."),
    "nfp":            ("the big monthly US jobs number", DOWN,
                       "A strong number usually knocks gold down."),
    "jobs report":    ("how many people got hired", DOWN,
                       "Strong jobs, strong economy, higher rates, weaker gold."),
    "unemployment":   ("how many people are out of work", UP,
                       "More unemployment pushes the Fed toward cutting rates, which helps gold."),
    "gdp":            ("the size of the economy", DOWN,
                       "A booming economy usually means higher rates, which hurts gold."),
    "retail sales":   ("how much people are shopping", DOWN,
                       "Strong spending keeps rates high."),
    "crude":          ("oil", UP,
                       "Pricier oil feeds inflation, and inflation helps gold."),
    "oil prices":     ("oil", UP,
                       "Pricier oil feeds inflation, and inflation helps gold."),
    "deficit":        ("the US spending more than it earns", UP,
                       "Worries about government debt make gold attractive."),
}

# ---------------------------------------------------------------- polarity
# A topic alone does not tell you which way gold goes. "War" pushes gold up;
# "war ending" pushes it down. Same word, opposite meaning. So we read whether
# the text describes the thing INTENSIFYING or EASING, and flip if needed.
INTENSIFY = [
    "rises", "rise", "rising", "rose", "jumps", "jumped", "surges", "surged",
    "climbs", "climbed", "soars", "spikes", "spiked", "accelerates", "hotter",
    "higher", "increase", "increases", "increased", "escalates", "escalation",
    "escalating", "worsens", "intensifies", "expands", "threatens", "threat",
    "warns", "strikes", "strike", "attacks", "attack", "launches", "hits",
    "invades", "raises", "raise", "hike", "hikes", "tightens", "more",
    "boost", "ramps", "flares", "erupts", "beats expectations", "stronger",
    # FX headlines say "gains"/"advances", not "rises". Without these the most
    # common way a currency story is worded scored 0 and fell through to the
    # whole-sentence fallback, which is where the wrong verb lives.
    "gains", "gain", "gained", "advances", "advanced", "strengthens",
    "strengthened", "firms", "firmer", "rallies", "rallied", "extends",
    "extended", "rebounds", "rebounded", "holds gains", "steady gains",
    # levels, not verbs: "tests a July high" is a strengthening story
    "high", "highs", "record high", "peak", "peaks", "strongest",
    # singular forms -- a plural subject takes the bare verb ("prices rise")
    "climb", "surge", "jump", "spike", "rally", "advance", "strengthen",
    "escalate", "intensify", "expand", "accelerate", "rebound", "firm",
    # and the mirror: worries GROW and MOUNT rather than "rising"
    "mount", "mounts", "mounted", "mounting", "builds", "building",
    "grows", "growing", "deepen", "deepens", "deepening", "swells",
    "swelling", "flare", "flaring", "heats up", "picks up", "gathers",
]
CALM = [
    "falls", "fall", "falling", "fell", "drops", "dropped", "eases", "eased",
    "easing", "cools", "cooled", "cooling", "slides", "slid", "declines",
    "declined", "retreats", "retreated", "softens", "softened", "slows",
    "slowed", "lower", "lowers", "cut", "cuts", "trims", "trimmed", "pares",
    "weakens", "weaker", "calms", "halts", "halted", "pauses", "paused",
    "ends", "ended", "ceasefire", "truce", "peace", "peace deal", "agreed",
    "agreement", "deal", "talks", "resolve", "resolved", "de-escalat",
    "withdraw", "withdrawal", "lifts sanctions", "eases sanctions",
    "less", "reduce", "reduced", "misses expectations", "cooler",
    "slips", "slipped", "dips", "dipped", "sinks", "sank", "tumbles",
    "tumbled", "weakened", "losses", "loses", "lost", "sheds", "shed",
    "gives up", "erases", "erased", "pulls back", "pared",
    "low", "lows", "record low", "weakest",
    "slip", "dip", "sink", "tumble", "ease", "cool", "soften", "slow",
    "retreat", "decline", "weaken", "pull back", "pulled back", "pullback",
    "erase", "pare", "trim", "give up",
    # Things stop being a worry by FADING, not by "falling". This whole family
    # was missing, so "inflation concerns fade" and "recession fears recede"
    # scored neutral -- and then whichever stray word was left in the sentence
    # decided the direction.
    "fade", "fades", "faded", "fading", "recede", "recedes", "receded",
    "receding", "subside", "subsides", "subsided", "subsiding", "abate",
    "abates", "abated", "abating", "wane", "wanes", "waned", "waning",
    "diminish", "diminishes", "diminished", "dissipate", "dissipates",
    "dissipated", "ebb", "ebbs", "moderate", "moderates", "moderated",
    "moderating", "relents", "relented", "settles", "settled", "unwinds",
]

# "new zealand dollar", "aussie dollar" -- not the one gold is priced in
_FOREIGN_QUAL = re.compile(
    r"\b(new zealand|australian|canadian|singapore|singaporean|taiwan|"
    r"hong kong|jamaican|kiwi|aussie|loonie|nz|aud|cad|nzd|sgd|twd|hkd)\s*$",
    re.I)

_pol_cache = {}


def _pol_rx(terms, key):
    if key not in _pol_cache:
        _pol_cache[key] = re.compile(
            "|".join(r"\b" + re.escape(t) + (r"\w*" if t.endswith("escalat") else r"\b")
                     for t in terms), re.I)
    return _pol_cache[key]


# "agreed NOT to hit", "will not raise" -- a negated intensifier is calming
_NEG_INTENSIFY = re.compile(
    r"\b(not|no|never|won't|will not|refrain from|agreed not|stop|stopped|"
    r"halt|avoid|without)\b[^.]{0,28}?\b(hit|strike|attack|raise|hike|"
    r"escalat\w*|target|invade)\w*", re.I)



# Clause markers. The verb that belongs to a subject lives in the subject's own
# clause; everything past one of these words belongs to something else.
_BOUND = re.compile(r"\b(as|while|after|before|amid|despite|though|although|"
                    r"but|however|versus|vs|against|on|over|following|since|"
                    r"when|if|because)\b|[,;:\u2014\u2013()\-]{1,}")


# "trims gains" / "erases losses" -- here "gains" and "losses" are nouns and the
# verb in front reverses them. Counting the two words separately cancels them
# out (or picks the wrong winner), so collapse each pair to one plain verb
# before matching. Must run before the word counts, not after.
_PAIR_DOWN = re.compile(r"\b(trims?|trimmed|pares?|pared|erases?|erased|"
                        r"gives? up|gave up|sheds?|shed|cuts?|reduces?|"
                        r"reduced|wipes? out|surrenders?)\s+"
                        r"(gains?|ground|advances?|highs?)\b", re.I)
_PAIR_UP = re.compile(r"\b(trims?|trimmed|pares?|pared|erases?|erased|"
                      r"recovers?|recovered|recoups?|reduces?|reduced)\s+"
                      r"(losses|loss|declines?|lows?)\b", re.I)


# "back from highs" is a fall, but "tests a July high" is a rise -- the
# preposition is the whole difference, so it has to be matched, not the level
# word on its own.
_OFF_HIGH = re.compile(r"\b(from|off|below|under|short of)\s+(the\s+)?"
                       r"(recent\s+|session\s+|record\s+)?(highs?|peaks?)\b", re.I)
_OFF_LOW = re.compile(r"\b(from|off|above)\s+(the\s+)?"
                      r"(recent\s+|session\s+|record\s+)?(lows?|troughs?)\b", re.I)


# "halts a six-day rally" -- same shape as "trims gains": a calming verb on top
# of a rising noun, where the verb is the one that describes what just changed.
# The modifier in the middle ("six-day", "three-week") has to be allowed for.
# "three-day", "six-day", "two-week winning" -- one optional word was not
# enough: a hyphenated span is two \w+ groups, so the noun never got reached.
_MOD = r"(?:(?:a|an|its|the|another)\s+)?(?:\w+[-\s]){0,3}"
_STOP_UP = re.compile(r"\b(halts?|halted|ends?|ended|snaps?|snapped|breaks?|"
                      r"broke|stalls?|stalled|pauses?|paused|interrupts?)\s+"
                      + _MOD + r"(rally|rallies|winning streak|streak|run|"
                      r"advance|climb|ascent|surge|uptrend)\b", re.I)
_STOP_DOWN = re.compile(r"\b(halts?|halted|ends?|ended|snaps?|snapped|breaks?|"
                        r"broke|stalls?|stalled|arrests?|stems?|stemmed)\s+"
                        + _MOD + r"(slide|decline|losing streak|selloff|"
                        r"sell-off|rout|fall|drop|downtrend|losing run)\b", re.I)


def _collapse(t):
    # _STOP_DOWN first: its nouns are the more specific ones ("losing run"),
    # and _STOP_UP's list contains the bare "run" that would swallow them.
    t = _STOP_DOWN.sub(" rises ", t)
    t = _STOP_UP.sub(" declines ", t)
    t = _PAIR_DOWN.sub(" declines ", t)
    t = _PAIR_UP.sub(" rises ", t)
    t = _OFF_HIGH.sub(" declines ", t)
    t = _OFF_LOW.sub(" rises ", t)
    return t

def _anchor(t, topic):
    """
    Where in the sentence this topic actually is.

    The old version took the topic's last word and the FIRST place it appeared.
    On "New Zealand Dollar declines as US Dollar gains" that anchors on New
    Zealand's dollar and reports the US dollar falling when it is rising --
    backwards, on the one field a trader acts on. So: try the most specific
    wording first, and among bare-word matches skip the ones that belong to
    somebody else's currency.
    """
    words = topic.lower().split()
    cands = []
    if len(words) > 1:
        cands.append(" ".join(words))                 # "us dollar"
        cands.append(" ".join(words).replace("us ", "u.s. "))
    if words[-1] == "dollar":
        cands += ["dollar index", "greenback", "dxy", "us dollar", "u.s. dollar"]
    cands.append(words[-1])                           # bare "dollar", last resort

    for c in cands:
        start = 0
        while True:
            i = t.find(c, start)
            if i < 0:
                break
            if not _FOREIGN_QUAL.search(t[max(0, i - 26):i]):
                # swallow a plural "s" so "oil price" against "oil prices"
                # does not leave a stray letter where the verb should start
                hit = c + "s" if t[i + len(c):i + len(c) + 1] == "s" else c
                return i, hit
            start = i + 1          # this one belongs to another currency; keep looking
    # every occurrence was somebody else's currency -- treat as unknown rather
    # than scoring a sentence that is not about our topic at all
    return -1, ""


def _clause(t, i, key):
    """The topic's own clause: back to the previous boundary, on to the next."""
    head = t[:i]
    m = None
    for m in _BOUND.finditer(head):
        pass                                   # last boundary before the topic
    before = head[m.end():] if m else head
    tail = t[i + len(key):]
    m2 = _BOUND.search(tail)
    after = tail[:m2.start()] if m2 else tail
    w = (before + " " + key + " " + after).strip()
    return w or t

def polarity(text, topic=None):
    """
    +1 = the thing is getting bigger/worse, -1 = calming down, 0 = unclear.

    Scoped to the words AROUND the topic when we know it. One sentence can
    describe two things moving opposite ways -- "Gold climbs as the Dollar
    trims gains" -- and measuring the whole sentence makes them cancel out.
    The verb that matters is the one attached to the topic.
    """
    t = _collapse((text or "").lower())
    if _NEG_INTENSIFY.search(t):
        return -1

    window = t
    if topic:
        i, key = _anchor(t, topic)
        if i >= 0:
            window = _clause(t, i, key)

    up = len(_pol_rx(INTENSIFY, "up").findall(window))
    dn = len(_pol_rx(CALM, "dn").findall(window))
    if dn > up:
        return -1
    if up > dn:
        return 1
    if window is not t:                          # nothing near the topic: fall back
        up = len(_pol_rx(INTENSIFY, "up").findall(t))
        dn = len(_pol_rx(CALM, "dn").findall(t))
        return -1 if dn > up else (1 if up > dn else 0)
    return 0


def _flip(d):
    return {UP: DOWN, DOWN: UP, MIXED: MIXED}[d]


# a few generic fallbacks so nothing ever shows up unexplained
GENERIC = {
    UP:    "Usually pushes gold UP.",
    DOWN:  "Usually pushes gold DOWN.",
    MIXED: "Can push gold either way.",
}


def explain_tag(tag):
    """(friendly name, direction, one-line reason) for any tag we produce."""
    t = (tag or "").lower().strip()
    if t in MEANING:
        return MEANING[t]
    for k, v in MEANING.items():           # partial match: "etf inflow" -> "etf"
        if k in t or t in k:
            return v
    return (tag, MIXED, "")


def plain_headline_effect(tags, lean, text_for_polarity=""):
    """
    One short sentence a kid can read: what this headline means for gold.
    Built from the strongest tag we recognised, not from the whole headline.
    """
    best, best_tag = None, None
    for tg in tags or []:
        name, d, why = explain_tag(tg)
        if why:
            best, best_tag = (name, d, why), tg
            break
    if not best:
        if lean > 0:
            return "Sounds good for gold."
        if lean < 0:
            return "Sounds bad for gold."
        return "Background info, no clear effect on gold."

    name, d, why = best
    pol = polarity(text_for_polarity or "", topic=best_tag)
    note = ""
    if pol and d != MIXED:
        if pol < 0:
            d = _flip(d)
            note = " Here it is EASING OFF, which flips the usual effect."
        else:
            note = " Here it is getting STRONGER."
    direction = {UP: "gold usually goes UP",
                 DOWN: "gold usually goes DOWN",
                 MIXED: "gold can go either way"}[d]
    return f"This is about {name}. {why}{note} So {direction}."


def plain_bias(label, score, why_rows):
    """Explain the bias meter in one or two plain sentences."""
    head = {
        "BULLISH":      "Gold is being pushed UP right now.",
        "LEAN BULLISH": "Gold is leaning slightly UP.",
        "NEUTRAL":      "Gold is going sideways — nothing is pushing it hard either way.",
        "LEAN BEARISH": "Gold is leaning slightly DOWN.",
        "BEARISH":      "Gold is being pushed DOWN right now.",
    }.get(label, "")
    bits = []
    for w in (why_rows or []):
        f, d = w.get("factor", ""), w.get("detail", "")
        if f == "vs prev close":
            bits.append(f"it's {d} compared with where it finished yesterday")
        elif f == "last hour trend":
            up = not d.startswith("-")
            bits.append(f"in the last hour it's been going {'up' if up else 'down'} ({d})")
        elif f == "news tone (6h)":
            try:
                bull, bear = [int(x) for x in re.findall(r"\d+", d)][:2]
            except Exception:
                bull = bear = 0
            if bull > bear:
                bits.append(f"today's headlines lean good for gold ({bull} good vs {bear} bad)")
            elif bear > bull:
                bits.append(f"today's headlines lean bad for gold ({bear} bad vs {bull} good)")
            else:
                bits.append(f"today's headlines are evenly split ({bull} good, {bear} bad)")
        elif f == "event risk":
            bits.append(f"careful: {d} is coming, which can move gold fast")
    tail = ("Because " + ", and ".join(bits) + ".") if bits else ""
    return head, tail


def plain_move(pct, z, direction, minutes=1):
    """Explain a detected price move without using the word sigma."""
    size = "a lot more than usual" if abs(z) >= 4 else "more than usual"
    word = "jumped up" if direction == "up" else "dropped"
    span = "a minute" if minutes == 1 else f"{minutes} minutes"
    return (f"Gold {word} {abs(pct):.2f}% in {span} — that's {size}. "
            f"Here's what was in the news right then:")
