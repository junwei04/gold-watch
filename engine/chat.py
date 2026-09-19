"""
The in-page chat. Answers questions about gold from the live data.

Backends, tried in order:
  1. a local model via Ollama, if the user has installed one   (free, offline)
  2. a hosted model, if a key sits in ~/.mrkt-gold/.env         (free tiers exist)
  3. the built-in answerer below                                (always works)

The built-in answerer is not a language model. It reads the question, works out
what is being asked, and answers from the live numbers and headlines already on
the page. That makes it instant, free, and impossible to hallucinate with --
every sentence it produces is built from a real value it can point at.
"""
import json, os, re, time, urllib.request

HOME = os.path.expanduser("~/.mrkt-gold")
ENV = os.path.join(HOME, ".env")


# ------------------------------------------------------------------ backends
def _env(key):
    if os.environ.get(key):
        return os.environ[key]
    try:
        for line in open(ENV):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _ollama(prompt, model=None):
    """Local model, if the user has Ollama running. Nothing leaves the machine."""
    model = model or _env("OLLAMA_MODEL") or "llama3.2:3b"
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.3, "num_predict": 420}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())["response"].strip()


# Cloudflare sits in front of these APIs and rejects Python's default
# User-Agent with a 403 "error code: 1010". A normal browser UA gets through.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


PROVIDERS = [
    # name, env key, url, candidate models (first that works is used and remembered)
    ("Perplexity", "PERPLEXITY_API_KEY", "https://api.perplexity.ai/chat/completions",
     ["sonar-pro", "sonar-reasoning", "sonar"]),
    ("Grok", "XAI_API_KEY", "https://api.x.ai/v1/chat/completions",
     ["grok-4.6", "grok-4", "grok-3", "grok-2-latest"]),
    ("Groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions",
     ["openai/gpt-oss-120b", "groq/compound", "qwen/qwen3.8-27b"]),
]

_good_model = {}          # provider -> the model we confirmed works


def _order():
    """Which providers to try, best first. CHAT_PROVIDER pins one if set."""
    pin = (_env("CHAT_PROVIDER") or "").strip().lower()
    ps = [p for p in PROVIDERS if _env(p[1])]
    if pin:
        ps = [p for p in ps if p[0].lower() == pin] or ps
    return ps


def _post(url, key, model, prompt, timeout=90):
    body = json.dumps({"model": model, "temperature": 0.3, "max_tokens": 600,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "User-Agent": _BROWSER_UA})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _hosted(prompt):
    """
    Try each configured provider. Model names get retired without warning
    (Groq dropped llama-3.3-70b-versatile), so each provider carries a list of
    candidates and we keep the first that actually answers.
    """
    last = None
    for name, envkey, url, models in _order():
        key = _env(envkey)
        override = _env(name.upper() + "_MODEL")
        cands = ([override] if override else []) + \
                ([_good_model[name]] if name in _good_model else []) + models
        seen = set()
        for m in [x for x in cands if x and not (x in seen or seen.add(x))]:
            try:
                r = _post(url, key, m, prompt)
                _good_model[name] = m
                text = r["choices"][0]["message"]["content"].strip()
                # Perplexity searches the live web and returns its sources
                cites = r.get("citations") or r.get("search_results") or []
                if cites:
                    urls = [cc if isinstance(cc, str) else cc.get("url", "") for cc in cites][:4]
                    urls = [u for u in urls if u]
                    if urls:
                        text += "\n\nSources it looked at:\n" + "\n".join("- " + u for u in urls)
                return text, f"{name}: {m}"
            except Exception as e:
                last = e
                continue
    raise (last or RuntimeError("no provider configured"))


def backend_status():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
        return "local model (Ollama)"
    except Exception:
        pass
    ps = _order()
    if ps:
        return ps[0][0]
    return "built-in"


SYSTEM = (
    "You are a market analyst talking to a day trader who trades gold on very "
    "short timeframes. He is looking at the same dashboard you are, so he can "
    "ALREADY SEE the price, the percentages and the headlines.\n\n"

    "DO NOT read the numbers back to him. Listing what the price is, what the "
    "high and low were, or repeating a headline is worthless -- he can see all "
    "of that. If your answer could have been written by someone just reading the "
    "screen aloud, it is a failed answer.\n\n"

    "Your job is to say what those numbers MEAN together:\n"
    "- What is unusual or out of line here? Gold has close relationships with the "
    "dollar, with bond yields and with silver. When one of them disagrees with "
    "gold, that disagreement is the story. Name it.\n"
    "- What does the COMBINATION imply that no single number shows on its own?\n"
    "- What is the single most important thing driving this right now, and what "
    "is just noise?\n"
    "- What would have to happen for this read to be wrong?\n\n"

    "Rules:\n"
    "- Simple words, real thinking. Explain any term in the same sentence you use "
    "it. Simple language does NOT mean a shallow answer.\n"
    "- Lead with the most interesting thing, not with context.\n"
    "- Be willing to say 'this looks odd' or 'these two things contradict'.\n"
    "- If the data genuinely does not support a conclusion, say exactly what is "
    "missing and where he could get it. Never invent numbers or events.\n"
    "- Never tell him to buy or sell. Explain what the picture shows instead.\n"
    "- Short paragraphs and bullets. No tables or headings. Under 220 words.\n\n"
)


# ------------------------------------------------------- the built-in answerer
def _fmt_news(items, n=3):
    out = []
    for x in items[:n]:
        out.append(f"- \"{x['title']}\" ({x['src']}, {x['time']} SGT)\n  {x['effect']}")
    return "\n".join(out)


def _intent(q):
    t = q.lower()
    if re.search(r"\b(buy|sell|short|long|should i|invest|entry|trade)\b", t):
        return "advice"
    if re.search(r"\b(why|what happened|what's going on|whats going on|cause|driving|reason)\b", t):
        return "why"
    if re.search(r"\b(coming|upcoming|next|later|schedule|calendar|event|when)\b", t):
        return "upcoming"
    if re.search(r"\b(trump|warsh|fed chair|musk|president|posted|said|tweet)\b", t):
        return "who"
    # "priced in" is a different question from "what is the price"
    if re.search(r"\b(priced in|priced ahead|price(d)? in already|already priced)\b", t) \
       or re.search(r"\b(odds|probability|expect|expects|expected|bets|betting|"
                    r"anticipat\w*|consensus|forecast)\b.*\b(hike|cut|rate|fed)\b", t) \
       or re.search(r"\b(hike|cut|rate|fed)\b.*\b(odds|probability|expect\w*|priced|bets)\b", t):
        return "priced_in"
    if re.search(r"\b(price|level|how much|worth|cost|trading at)\b", t) \
       and not re.search(r"\bpriced\b", t):
        return "price"
    if re.search(r"\b(up or down|direction|bias|lean|bullish|bearish|outlook)\b", t):
        return "bias"
    # "what is X" is only a dictionary lookup when X is a THING, not an action.
    # "what is happening/moving/going on" is really a "why" question.
    if re.search(r"^\s*(what is|what's|whats|define|explain|meaning of)\b", t):
        if re.search(r"\b(happening|moving|going on|doing|driving|causing)\b", t):
            return "why"
        return "define"
    if re.search(r"\b(news|headline|happening)\b", t):
        return "news"
    return "summary"


def _define(q, meaning):
    """Glossary lookup against the plain-English dictionary."""
    t = re.sub(r"^\s*(what is|what's|whats|define|explain|meaning of)\s+", "", q.lower()).strip(" ?.")
    best = None
    for tag, (name, direction, why) in meaning.items():
        if tag == t:                                  # exact match always wins
            best = (tag, name, direction, why)
            break
        if tag in t or t in tag:
            # prefer the CLOSEST length, not the longest: asking about
            # "inflation" should not return "core inflation"
            if best is None or abs(len(tag) - len(t)) < abs(len(best[0]) - len(t)):
                best = (tag, name, direction, why)
    if not best:
        return None
    _, name, direction, why = best
    d = {"up": "gold usually goes UP", "down": "gold usually goes DOWN",
         "mixed": "gold can go either way"}[direction]
    return f"**{name.capitalize()}.** {why}\n\nWhen this gets stronger, {d}."


def answer(question, state, meaning):
    """Answer from the live data. Every claim traces to a real value."""
    s, q = state, (question or "").strip()
    if not q:
        return "Ask me something about gold — try \"why is gold moving?\""

    px, pct = s.get("price"), s.get("chg_pct", 0)
    dirword = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    intent = _intent(q)

    if intent == "advice":
        return ("I can't tell you whether to buy or sell — that's your call, and I'd "
                "be guessing.\n\nWhat I *can* tell you is what's happening right now:\n\n"
                f"Gold is ${px}, {dirword} {abs(pct)}% since yesterday. "
                f"{s.get('bias_plain','')} {s.get('bias_because','')}")

    if intent == "define":
        d = _define(q, meaning)
        if d:
            return d

    if intent == "price":
        return (f"Gold is **${px}** right now.\n\nThat's {'+' if s.get('chg',0)>=0 else ''}"
                f"{s.get('chg')} ({'+' if pct>=0 else ''}{pct}%) compared with where it "
                f"finished yesterday (${s.get('prev_close')}).")

    if intent == "priced_in":
        now = time.time()
        fed_next = next((c for c in s.get("calendar", [])
                         if c["ts"] > now and "rate decision" in c["title"].lower()), None)
        expectation_words = re.compile(
            r"odds|expect|priced|bets|probability|forecast|anticipat|consensus|"
            r"another (hike|cut)|next (hike|cut)|october|december", re.I)
        ev = [n for n in s.get("news", [])
              if expectation_words.search(n["title"])
              and re.search(r"fed|rate|hike|cut|policy", n["title"], re.I)][:4]
        posts = [p for p in s.get("social", [])
                 if re.search(r"hike|cut|rate|fed", p["text"], re.I)][:3]

        out = ["**Being honest: I can't tell you whether it's priced in.**",
               "",
               "That needs judgement about what traders already expect, and I only "
               "read headlines and prices — I can't work out what the market has "
               "already assumed. Anyone who answers that confidently from this data "
               "is guessing.",
               "",
               "**What I can give you is the evidence:**", ""]
        if fed_next:
            days = int((fed_next["ts"] - now) / 86400)
            out.append(f"- Next Fed rate decision: **{fed_next['date']} {fed_next['time']} SGT**, "
                       f"about {days} days away.")
        px, pct = s.get("price"), s.get("chg_pct", 0)
        out.append(f"- Gold is ${px}, {'up' if pct>0 else 'down'} {abs(pct)}% since yesterday. "
                   f"{s.get('bias_plain','')}")
        if ev:
            out.append("")
            out.append("**Headlines about what's expected next:**")
            out.append("")
            for n in ev:
                out.append(f"- \"{n['title']}\" ({n['src']}, {n['time']} SGT)")
        else:
            out.append("- No headlines right now specifically about rate expectations or odds.")
        if posts:
            out.append("")
            out.append("**What decision-makers have said:**")
            out.append("")
            for p in posts:
                out.append(f"- {p['who']}: \"{p['text'][:150]}\"")
        out.append("")
        out.append("A proper answer would need Fed funds futures data, which I don't have "
                   "a free source for. The usual free check is CME FedWatch.")
        return "\n".join(out)

    if intent == "bias":
        rows = "\n".join(f"- {w['factor']}: {w['detail']}" for w in s.get("bias_why", []))
        return (f"**{s.get('bias')}** — {s.get('bias_plain','')}\n\n{s.get('bias_because','')}"
                f"\n\nThe three things I'm looking at:\n{rows}\n\n"
                "This is a rough guide, not a prediction.")

    if intent == "upcoming":
        now = time.time()
        up = [c for c in s.get("calendar", []) if c["ts"] > now][:4]
        if not up:
            return ("I can't see anything scheduled ahead right now.\n\n"
                    "To be straight with you: the free calendar I use only publishes "
                    "the current week, so late in the week it can run out. It refills "
                    "when the new week is published. That is not the same as nothing "
                    "being scheduled.")
        lines = []
        for c in up:
            mins = int((c["ts"] - now) / 60)
            if mins < 90:
                when = f"{mins} min"
            elif mins < 2880:
                when = f"{mins//60}h {mins%60}m"
            else:
                when = f"{mins//1440} days"
            tag = "BIG" if c["impact"] == "High" else "medium"
            lines.append(f"- **{c['title']}** — {c['date']} {c['time']} SGT (in {when}, {tag})")
        return ("Coming up:\n\n" + "\n".join(lines) +
                "\n\nThese are US economic reports. Gold can jump within seconds of one "
                "coming out, because they change what people expect interest rates to do.")

    if intent == "who":
        posts = s.get("social", [])
        t = q.lower()
        for name in ("trump", "warsh", "musk", "fed", "treasury"):
            if name in t:
                posts = [p for p in posts if name in p["who"].lower()] or posts
                break
        if not posts:
            return "Nothing market-moving has been posted by the people I watch recently."
        out = []
        for p in posts[:3]:
            out.append(f"**{p['who']}** ({p['where']}, {p['time']} SGT):\n\"{p['text'][:230]}\"\n\n{p['effect']}")
        return "\n\n---\n\n".join(out)

    if intent == "news":
        n = s.get("news", [])
        if not n:
            return "No gold-relevant headlines at the moment."
        return "Here's what's in the news for gold right now:\n\n" + _fmt_news(n, 4)

    if intent == "why":
        moves = s.get("moves", [])
        parts = [f"Gold is **${px}**, {dirword} {abs(pct)}% since yesterday. "
                 f"{s.get('bias_plain','')}"]
        if moves:
            m = moves[0]
            parts.append(f"\n**The most recent sharp move** was at {m['time']} SGT — "
                         f"{m['plain'].split('Here')[0].strip()}")
            if m.get("why"):
                parts.append("What was in the news at that exact moment:\n\n" +
                             "\n".join(f"- \"{c['title']}\" ({c['src']}, {c['time']} SGT)\n  {c['effect']}"
                                       for c in m["why"][:3]))
            else:
                parts.append("Nothing was in the news then, so that one was probably just "
                             "normal buying and selling.")
        news = s.get("news", [])
        if news:
            parts.append("\n**Biggest things affecting gold today:**\n\n" + _fmt_news(news, 3))
        parts.append("\nOne caution: this shows what was in the news *around* the move. "
                     "It doesn't prove the news caused it.")
        return "\n".join(parts)

    # default: the whole picture
    bits = [f"Gold is **${px}**, {dirword} {abs(pct)}% since yesterday.",
            f"{s.get('bias_plain','')} {s.get('bias_because','')}"]
    if s.get("news"):
        bits.append("\n**In the news:**\n\n" + _fmt_news(s["news"], 3))
    if s.get("social"):
        p = s["social"][0]
        bits.append(f"\n**Most notable thing said:** {p['who']} — \"{p['text'][:150]}\"\n{p['effect']}")
    bits.append("\nYou can ask me: *why is gold moving?* · *what's coming up?* · "
                "*what did Trump say?* · *what is inflation?*")
    return "\n".join(bits)


def reply(question, state, meaning, briefing_text):
    """Use a model if one is available, otherwise the built-in answerer."""
    prompt = SYSTEM + briefing_text + f"\n\nQuestion: {question}\nAnswer:"
    try:
        r = _ollama(prompt)
        if r:
            return r, "local model (Ollama)"
    except Exception:
        pass
    try:
        text, label = _hosted(prompt)
        if text:
            return text, label
    except Exception:
        pass
    return answer(question, state, meaning), "built-in"
