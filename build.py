#!/usr/bin/env python3
"""
Build the public Gold Watch site.

The original version ran as a server on one laptop: it polled the data sources
every 20 seconds and pushed updates to a browser on the same machine. That
cannot serve strangers -- it needs a machine that is awake, a network they
share, and it collapses the moment more than a few people open it.

So the work is split by how fast each thing actually changes:

  built here, every few minutes      news, social posts, the economic calendar,
  (GitHub Actions -> data.json)      candles, support/resistance, session clock,
                                     the bias read and the written briefing

  fetched by each visitor's browser  the spot gold price, straight from
  (live, every 10 seconds)           api.gold-api.com, which allows direct
                                     browser access

That split is the whole design. A headline is not more true five seconds after
it is published, so pre-building it costs nothing. A price is stale in seconds,
so it must not be pre-built -- and because every visitor fetches it themselves,
the price stays live no matter how many people are watching, with no server
anywhere in the path.

Nothing here needs an API key. The chat box talks to Groq from the visitor's own
browser using a key they supply, so no key of ours is ever shipped.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
DOCS = os.path.join(HERE, "docs")

import mrkt      # noqa: E402  (sys.path has to be set first)
import chat      # noqa: E402


# ------------------------------------------------------------------ the data
def collect():
    """Run every fetch task once, then the same derived fields the app shows."""
    errors = []
    for name, fn, _ in mrkt.TASKS:
        t0 = time.time()
        try:
            fn()
            print(f"  {name:9s} ok      {time.time() - t0:5.1f}s", flush=True)
        except Exception as e:
            # One dead source must not lose the other four. A partial build is
            # worth far more than no build, and the page names what is missing
            # rather than showing a gap as though it were a fact.
            errors.append(f"{name}: {type(e).__name__}")
            print(f"  {name:9s} FAILED  {type(e).__name__}: {e}", flush=True)

    with mrkt.LOCK:
        mrkt.STATE["dollar_story"] = mrkt.dollar_story(
            mrkt.STATE.get("peers", {}), mrkt.STATE.get("chg_pct", 0))
    mrkt.compute_bias()
    mrkt.rebuild_moves()

    with mrkt.LOCK:
        state = json.loads(json.dumps(mrkt.STATE))

    state["errors"] = errors
    state["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["built_epoch"] = int(time.time())
    try:
        state["brief"] = mrkt.briefing()
    except Exception as e:
        state["brief"] = f"(briefing unavailable: {type(e).__name__})"

    # The sparkline draws a few hundred bars. Shipping two days of minute
    # candles to every visitor is a megabyte of nothing -- trim it here, where
    # it costs one machine instead of all of them.
    bars = state.get("bars") or []
    if len(bars) > 400:
        state["bars"] = bars[-400:]
    return state


# ------------------------------------------------------------------ the page
# The dashboard markup lives in the engine as one string, and it is the same
# markup the local version serves. Rather than fork it -- two copies of a page
# drift apart within a week -- it is transformed here, in one place, with the
# handful of edits that turn "talks to a server" into "talks to a file".

LIVE_JS = r"""
// ---------------------------------------------------------------- live price
// data.json is rebuilt every few minutes, which is fine for headlines and
// useless for a price. So the price comes straight from the source, in this
// browser, every 10 seconds. No server sits in the middle -- which is also why
// this does not get slower as more people open the page.
var LAST = {};              // the last built payload, shared with the chat
var LIVEPX = null;

async function tickPrice(){
  try{
    const r = await fetch('https://api.gold-api.com/price/XAU', {cache:'no-store'});
    if(!r.ok) return;
    const j = await r.json();
    const p = parseFloat(j.price);
    if(!isFinite(p)) return;
    LIVEPX = p;
    paintLive();
  }catch(e){ /* offline or blocked: the last built price stays on screen */ }
}

function paintLive(){
  if(LIVEPX === null) return;
  const el = E('px');
  const shown = parseFloat(String(el.textContent).replace(/[^0-9.]/g,''));
  el.textContent = '$' + LIVEPX.toLocaleString(undefined,{minimumFractionDigits:2});
  if(isFinite(shown) && Math.abs(shown - LIVEPX) > 0.001){
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  }
  // recompute the day's change against the previous close from the last build
  const pc = LAST.prev_close;
  if(pc){
    const d = LIVEPX - pc, pct = (d/pc)*100, up = d >= 0;
    E('chg').innerHTML = '<span class="'+(up?'up':'dn')+'">'+(up?'+$':'-$')+
      Math.abs(d).toFixed(1)+' ('+(up?'+':'')+pct.toFixed(2)+'%)</span>'+
      '<span style="color:#5f6368;font-weight:400"> since yesterday</span>';
  }
  E('st').textContent = 'live';
  E('dot').style.background = '#00d4aa';
}

// How old is the pre-built half? Stated plainly: a page that looks live while
// showing twenty-minute-old headlines is worse than one that admits it.
function paintFreshness(d){
  const el = E('upd'); if(!d || !d.built_epoch) return;
  const mins = Math.max(0, Math.round((Date.now()/1000 - d.built_epoch)/60));
  let s;
  if(mins < 1)        s = 'news updated just now';
  else if(mins < 2)   s = 'news updated a minute ago';
  else if(mins < 60)  s = 'news updated ' + mins + ' minutes ago';
  else { const h = Math.round(mins/60);
         s = 'news updated ' + h + (h===1?' hour':' hours') + ' ago'; }
  el.textContent = s;
  el.style.color = mins > 25 ? '#ffb86b' : '';
}
"""

CHAT_JS = r"""
// ------------------------------------------------------------------ the chat
// There is no server here, so there is nowhere to keep a shared API key -- and
// putting one in the page would mean shipping a private credential to everyone
// and letting any visitor spend it. So each person brings their own free key.
// It is kept in their own browser, sent to Groq and to nobody else.
function gwKey(){ try{ return localStorage.getItem('gw_key')||''; }catch(e){ return ''; } }
function gwSetKey(k){
  try{ localStorage.setItem('gw_key', k.trim()); }catch(e){}
  paintKeyState();
}
function paintKeyState(){
  const b = E('keystate'); if(!b) return;
  const k = gwKey();
  b.textContent = k ? ('key saved · •••' + k.slice(-4)) : 'no key yet';
  b.style.color = k ? '#00d4aa' : '#5f6368';
  const inp = E('keybox'); if(inp && k) inp.value = '';
}
function gwForget(){
  try{ localStorage.removeItem('gw_key'); }catch(e){}
  paintKeyState();
  bubble('bot','Key removed from this browser.');
}

const GW_SYSTEM = __SYSTEM__;
const GW_NOKEY =
  'The chat needs your own free key from Groq. It takes a minute and costs nothing.\n\n'
+ '- Go to **console.groq.com/keys**\n'
+ '- Sign in and create a key\n'
+ '- Paste it into the box above\n\n'
+ 'The key is stored only in this browser. It goes to Groq and to nobody else '
+ '— there is no server behind this page to send it to.';

async function send(){
  const box=E('qbox'), btn=E('sendbtn');
  const q=box.value.trim(); if(!q)return;
  box.value=''; btn.disabled=true; bubble('me',q);
  const key = gwKey();
  if(!key){ bubble('bot', GW_NOKEY); btn.disabled=false; box.focus(); return; }
  const th=bubble('bot think','thinking...');
  try{
    const r = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},
      body: JSON.stringify({
        model:'openai/gpt-oss-120b',
        messages:[{role:'system', content: GW_SYSTEM + '\n\n' + (LAST.brief||'')},
                  {role:'user',   content: q}],
        temperature:0.4, max_tokens:700
      })
    });
    const j = await r.json();
    th.remove();
    if(!r.ok){
      const m = (j && j.error && j.error.message) ? j.error.message : ('HTTP '+r.status);
      bubble('bot', r.status===401
        ? 'That key was rejected. Check it was copied whole, then paste it again.'
        : ('Groq said: '+m));
    } else {
      bubble('bot', j.choices[0].message.content.trim());
      E('bk').textContent='(groq, your key)';
    }
  }catch(e){ th.remove(); bubble('bot','Could not reach Groq ('+e.message+').'); }
  btn.disabled=false; box.focus();
}
"""

# The key box, injected above the chat input.
KEY_HTML = """<div class="keyrow">
  <input id="keybox" type="password" placeholder="Paste your free Groq key (stays in this browser)"
         autocomplete="off" spellcheck="false">
  <button onclick="gwSetKey(E('keybox').value)">Save</button>
</div>
<div class="keynote"><span id="keystate">no key yet</span>
  &middot; <a href="https://console.groq.com/keys" target="_blank" rel="noopener">get a free key</a>
  &middot; <a href="#" onclick="gwForget();return false;">forget it</a></div>
"""

KEY_CSS = """
.keyrow{display:flex;gap:7px;margin-bottom:7px}
.keyrow input{flex:1;min-width:0;background:#0d1016;border:1px solid #242b38;color:#e8eaed;
  border-radius:9px;padding:10px 12px;font-size:13px;font-family:inherit}
.keyrow button{background:#1a1f2a;color:#9aa0a6;border:1px solid #242b38;border-radius:9px;
  padding:10px 14px;font-size:13px;cursor:pointer;font-family:inherit}
.keyrow button:hover{background:#242b38;color:#e8eaed}
.keynote{font-size:11px;color:#5f6368;margin-bottom:11px}
.keynote a{color:#7fb3ff;text-decoration:none}
@media(max-width:640px){.keyrow input{font-size:16px}}
"""


def make_page(system_prompt):
    """Turn the server-backed dashboard into a static one. Every replacement is
    asserted: a silent no-op here would ship a page that looks right and talks
    to a server that does not exist."""
    html = mrkt.DASH
    did = []

    def swap(old, new, label):
        nonlocal html
        if old not in html:
            raise SystemExit(f"TRANSFORM FAILED: {label!r} -- pattern not found. "
                             "The dashboard changed shape; fix build.py.")
        html = html.replace(old, new, 1)
        did.append(label)

    # 1. data comes from a file on a CDN, not a local server
    swap("fetch('/state.json',{cache:'no-store'})",
         "fetch('data.json?t='+Date.now(),{cache:'no-store'})", "data source")

    # 2. the briefing is baked into that file rather than generated per request
    swap("E('brief').value=await (await fetch('/brief')).text();",
         "E('brief').value=LAST.brief||'(not available)';", "briefing")

    # 3. no server push. Poll the built file; poll the price far faster.
    swap("try{ const es=new EventSource('/events'); es.onmessage=function(){render();}; }catch(e){}",
         "tickPrice(); setInterval(tickPrice, 10000); paintKeyState();", "live push")

    # 4. the chat talks to Groq directly, with the visitor's own key
    m = re.search(r"async function send\(\)\{.*?\n\}\n", html, re.S)
    if not m:
        raise SystemExit("TRANSFORM FAILED: send() not found")
    html = html.replace(m.group(0), "", 1)
    did.append("chat -> groq direct")

    # 5. inject the new code ahead of render()
    inject = LIVE_JS + CHAT_JS.replace("__SYSTEM__", json.dumps(system_prompt))
    swap("let CAT='all';", inject + "\nlet CAT='all';", "inject js")

    # 6. keep the built payload for the chat and the brief
    swap("  E('st').textContent=d.status==='live'?'live':d.status;",
         "  LAST=d;\n"
         "  E('st').textContent=d.status==='live'?'live':d.status;", "stash payload")

    # 6b. The original line prints the engine's own clock, which in a built site
    #     is the moment the BUILD ran, not now -- and it was being written after
    #     paintFreshness, silently overwriting it. Replace the line rather than
    #     running before it: prepending to a line that assigns the same element
    #     is not an edit, it is a no-op with extra steps.
    swap("  E('upd').textContent=d.updated?('updated '+d.updated+' SGT'):'';",
         "  paintFreshness(d);", "freshness line")

    # 7. the live price must win over the built one, or every render would
    #    overwrite a 10-second-old number with a 5-minute-old one
    swap("  lastPx=d.price;", "  lastPx=d.price;\n  if(LIVEPX!==null) paintLive();",
         "live price wins")

    # 8. the key box and its styling
    swap('<div class="chatrow">', KEY_HTML + '<div class="chatrow">', "key box")
    swap("</style>", KEY_CSS + "</style>", "key css")

    # 9. relative paths: the site lives in a subfolder, not at the domain root
    html = html.replace('href="/manifest.json"', 'href="manifest.json"')
    html = html.replace('href="/icon/touch-180.png"', 'href="touch-180.png"')

    print("  transforms applied:", ", ".join(did), flush=True)
    if "/state.json" in html or "EventSource" in html or "fetch('/chat'" in html:
        raise SystemExit("TRANSFORM FAILED: a server call survived the rewrite")
    return html


def main():
    os.makedirs(DOCS, exist_ok=True)
    print("fetching:", flush=True)
    state = collect()

    with open(os.path.join(DOCS, "data.json"), "w") as f:
        json.dump(state, f, separators=(",", ":"))

    print("building page:", flush=True)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(make_page(chat.SYSTEM))

    with open(os.path.join(DOCS, "manifest.json"), "w") as f:
        json.dump({
            "name": "Gold Watch", "short_name": "Gold Watch",
            "start_url": "./", "display": "standalone",
            "background_color": "#0a0c10", "theme_color": "#0a0c10",
            "icons": [
                {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "maskable"},
            ],
        }, f)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    kb = os.path.getsize(os.path.join(DOCS, "data.json")) / 1024
    print(f"\nbuilt  ${state.get('price')}  {state.get('bias')}  "
          f"{state.get('n_news')} headlines  {len(state.get('social') or [])} posts  "
          f"{len(state.get('bars') or [])} bars  ->  data.json {kb:.0f} KB", flush=True)
    if state["errors"]:
        print("PARTIAL BUILD, these sources failed:", state["errors"], flush=True)


if __name__ == "__main__":
    main()
