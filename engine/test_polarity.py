#!/usr/bin/env python3
"""
Direction regression set.  Run after ANY edit to explain.py:

    python3 ~/.mrkt-gold/test_polarity.py

Every case here is a real headline that was once scored backwards, or a shape
that broke when a previous fix went in.  Getting the direction wrong is the
worst failure this app has -- it tells you gold is going up when the news says
down -- so these stay green.

+1 = the topic is intensifying / strengthening,  -1 = calming / weakening.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import explain

CASES = [
    # --- the original bug: two currencies in one headline ------------------
    ("New Zealand Dollar declines as US Dollar gains on hawkish Fed outlook", "US Dollar", 1),
    ("Australian Dollar falls while US Dollar strengthens", "US Dollar", 1),
    ("Canadian dollar rallies, greenback eases", "US Dollar", -1),
    ("Sterling today: Pound gains as UK retail sales beat and dollar cools", "US Dollar", -1),
    # --- calming verb + rising noun ---------------------------------------
    ("Gold climbs as the Dollar trims gains", "US Dollar", -1),
    ("Dollar pares gains after dovish remarks", "US Dollar", -1),
    ("Dollar erases losses to end higher", "US Dollar", 1),
    ("Dollar halts six-day rally after Fed hike, sterling slips after BoE", "US Dollar", -1),
    ("Dollar ends three-day slide on hawkish Fed", "US Dollar", 1),
    ("Dollar snaps its winning streak", "US Dollar", -1),
    ("Dollar ends a two-week losing run", "US Dollar", 1),
    # --- levels: the preposition decides ----------------------------------
    ("United States Dollar Index tests late July high, near 100.35 on hawkish Fed stance", "US Dollar", 1),
    ("Bond yields pull back from highs", "US Treasury", -1),
    ("Dollar index rebounds from session lows", "US Dollar", 1),
    # --- plain cases that must not regress --------------------------------
    ("Yen slides despite BOJ rate hike, dollar holds gains amid hawkish Fed", "US Dollar", 1),
    ("Dollar weakens after soft inflation print", "US Dollar", -1),
    ("Dollar slips as traders price in cuts", "US Dollar", -1),
    ("US Dollar advances against the euro", "US Dollar", 1),
    ("Gold rises as the dollar weakens", "US Dollar", -1),
    ("Gold falls as the dollar strengthens", "US Dollar", 1),
    ("Treasury yields extend gains after strong jobs data", "US Treasury", 1),
    # --- non-currency topics ----------------------------------------------
    ("Russia and Ukraine agreed to a ceasefire", "Russia-Ukraine war", -1),
    ("Israel strikes targets in southern Lebanon", "Middle East conflict", 1),
    ("Both sides agreed not to strike civilian targets", "Middle East conflict", -1),
    ("OPEC+ raises output quota", "OPEC", 1),
    ("Oil prices tumble on demand worries", "oil price", -1),
    ("Oil prices surge after supply outage", "oil price", 1),
    ("US inflation cools to 2.4%", "Inflation", -1),
    ("Inflation accelerates, hotter than expected", "Inflation", 1),
]

if __name__ == "__main__":
    bad = []
    for text, topic, want in CASES:
        got = explain.polarity(text, topic)
        if got != want:
            bad.append((got, want, topic, text))
    for got, want, topic, text in bad:
        print(f"FAIL  got {got:+d} want {want:+d}  [{topic}]  {text}")
    print(f"{len(CASES) - len(bad)}/{len(CASES)} passed")
    sys.exit(1 if bad else 0)
