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
    if len(bars) > 1440:
        state["bars"] = bars[-1440:]      # one full trading day of minutes
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

// Two sources for the same file, freshest first. The branch copy is rewritten
// by the refresh loop every few minutes; the copy shipped with the site is
// only as new as the last deploy, and is here so the page still works if
// raw.githubusercontent is blocked or down.
var DATA_URLS = [
  'https://raw.githubusercontent.com/junwei04/gold-watch/data/data.json',
  'data.json'
];
async function fetchData(){
  let err;
  for(const u of DATA_URLS){
    try{
      const r = await fetch(u + '?t=' + Date.now(), {cache:'no-store'});
      if(r.ok) return r;
    }catch(e){ err = e; }
  }
  throw (err || new Error('no data source reachable'));
}

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


CHART_JS = r"""
// ---------------------------------------------------------------------- chart
// Candles come from the build (Yahoo blocks browsers, so they cannot be fetched
// here), and the right-hand edge is kept live by the same 10-second spot tick
// that drives the price at the top. So the history is up to ~5 minutes old and
// the candle currently forming is always current -- which is the half that
// matters when you are watching a 1-minute chart.
var CHART=null, SERIES=null, VIEW=[], HI=null, LO=null;
var TF = (function(){ try{ return parseInt(localStorage.getItem('gw_tf'))||1; }
                      catch(e){ return 1; } })();

// Lightweight Charts renders a unix timestamp as UTC. Shifting by the viewer's
// own offset makes the axis read in THEIR local time -- which matters now that
// this is not one person's dashboard. Singapore, London and New York each see
// their own clock without the page knowing where anyone is.
var TZ = -(new Date().getTimezoneOffset()) * 60;

function aggregate(bars, tfMin){
  const span = tfMin*60, out = [];
  let cur = null;
  for(const b of bars){
    const bucket = Math.floor(b.t/span)*span;
    if(!cur || cur.time !== bucket + TZ){
      if(cur) out.push(cur);
      cur = {time: bucket + TZ, open:b.o, high:b.h, low:b.l, close:b.c};
    } else {
      if(b.h > cur.high) cur.high = b.h;
      if(b.l < cur.low)  cur.low  = b.l;
      cur.close = b.c;
    }
  }
  if(cur) out.push(cur);
  return out;
}

function initChart(){
  const el = E('chart');
  if(!el || el === _stub || !window.LightweightCharts || CHART) return;
  CHART = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: el.clientHeight,
    layout:{ background:{color:'transparent'}, textColor:'#8b919a',
             fontFamily:'-apple-system,BlinkMacSystemFont,system-ui,sans-serif', fontSize:11 },
    grid:{ vertLines:{color:'#161b24'}, horzLines:{color:'#161b24'} },
    rightPriceScale:{ borderColor:'#242b38', scaleMargins:{top:0.12, bottom:0.12} },
    timeScale:{ borderColor:'#242b38', timeVisible:true, secondsVisible:false,
                rightOffset:3 },
    crosshair:{ mode: LightweightCharts.CrosshairMode.Normal,
                vertLine:{color:'#3a4150', labelBackgroundColor:'#1f2632'},
                horzLine:{color:'#3a4150', labelBackgroundColor:'#1f2632'} },
    localization:{ priceFormatter: function(p){ return '$'+p.toFixed(2); } },
    handleScale:{ axisPressedMouseMove:{time:true, price:false} }
  });
  SERIES = CHART.addCandlestickSeries({
    upColor:'#00d4aa', downColor:'#ff3b30',
    borderUpColor:'#00d4aa', borderDownColor:'#ff3b30',
    wickUpColor:'#00d4aa', wickDownColor:'#ff3b30',
    priceLineColor:'#ffd479'
  });
  // Follow the container rather than the window: the card is a grid child, so
  // it can change width when the page reflows without the window resizing.
  if(window.ResizeObserver){
    new ResizeObserver(function(){
      if(CHART && el.clientWidth) CHART.resize(el.clientWidth, el.clientHeight);
    }).observe(el);
  }
  drawChart();
}

function drawChart(){
  if(!SERIES || !LAST.bars || !LAST.bars.length) return;
  VIEW = aggregate(LAST.bars, TF);
  SERIES.setData(VIEW);

  // yesterday's close and today's range, as lines -- the levels a day trader
  // is actually watching, rather than decoration
  [HI, LO].forEach(function(l){ if(l) SERIES.removePriceLine(l); });
  HI = LO = null;
  const lv = LAST.levels || {};
  // A price line takes part in autoscaling, so drawing today's high when price
  // is nowhere near it stretches the axis across the whole day's range and
  // squashes an hour of 1-minute candles into a thin band -- destroying the
  // detail the chart exists to show. So a level is only drawn once price is
  // close enough for it to matter; the distance to both is always written
  // underneath, so nothing is hidden, it just stops wrecking the scale.
  // axisLabelVisible is off for the same family of reason: an off-screen line
  // still pins its label to the edge, and the two then overlap in one corner.
  const near = VIEW.length ? VIEW[VIEW.length-1].close : 0;
  const close_enough = function(v){ return near && Math.abs(v-near)/near < 0.006; };
  if(lv.day_high && close_enough(lv.day_high))
    HI = SERIES.createPriceLine({price:lv.day_high, color:'#4a5160',
      lineWidth:1, lineStyle:2, axisLabelVisible:false, title:"today's high"});
  if(lv.day_low && close_enough(lv.day_low))
    LO = SERIES.createPriceLine({price:lv.day_low, color:'#4a5160',
      lineWidth:1, lineStyle:2, axisLabelVisible:false, title:"today's low"});

  CHART.timeScale().fitContent();
  const n = Math.min(VIEW.length, TF === 1 ? 120 : 90);
  if(VIEW.length > n){
    CHART.timeScale().setVisibleLogicalRange({from: VIEW.length - n, to: VIEW.length + 3});
  }
  paintChartLabel();
}

// Keep the forming candle current from the live spot price.
function tickChart(){
  if(!SERIES || LIVEPX === null || !VIEW.length) return;
  const span = TF*60;
  const bucket = Math.floor(Date.now()/1000/span)*span + TZ;
  const last = VIEW[VIEW.length-1];
  if(bucket < last.time) return;            // update() cannot go backwards
  if(bucket === last.time){
    last.close = LIVEPX;
    if(LIVEPX > last.high) last.high = LIVEPX;
    if(LIVEPX < last.low)  last.low  = LIVEPX;
  } else {
    VIEW.push({time:bucket, open:LIVEPX, high:LIVEPX, low:LIVEPX, close:LIVEPX});
  }
  SERIES.update(VIEW[VIEW.length-1]);
}

function setTF(mins, btn){
  TF = mins;
  try{ localStorage.setItem('gw_tf', String(mins)); }catch(e){}
  document.querySelectorAll('.tfbtn').forEach(function(b){ b.classList.remove('on'); });
  if(btn) btn.classList.add('on');
  drawChart();
  tickChart();
}

function paintChartLabel(){
  const el = E('chartnote'); if(!el) return;
  const mins = LAST.built_epoch
    ? Math.max(0, Math.round((Date.now()/1000 - LAST.built_epoch)/60)) : null;
  let s = 'Each candle is ' + (TF===1?'1 minute':TF+' minutes')
    + '. The one on the right is forming now, live. '
    + (mins === null ? '' : 'Earlier candles were last refreshed '
        + (mins < 1 ? 'seconds' : mins + (mins===1?' minute':' minutes')) + ' ago.');
  const lv = LAST.levels || {}, near = VIEW.length ? VIEW[VIEW.length-1].close : 0;
  if(lv.day_high && lv.day_low && near){
    const up = ((lv.day_high-near)/near*100), dn = ((near-lv.day_low)/near*100);
    s += "\nToday's highest was $" + lv.day_high.toFixed(2) + ' ('
       + (up<=0 ? 'we are at it now' : up.toFixed(2)+'% above here')
       + "), lowest was $" + lv.day_low.toFixed(2) + ' ('
       + (dn<=0 ? 'we are at it now' : dn.toFixed(2)+'% below here') + ').';
  }
  if(LAST.basis){
    s += '\nCandle shapes come from gold futures, shifted onto the spot price '
       + '(they normally sit about $' + Math.abs(LAST.basis).toFixed(0)
       + ' apart) so the chart lines up with the number at the top.';
  }
  el.textContent = s;
}
"""

CHART_HTML = """<div class="card" id="chartcard">
  <h2>The chart</h2>
  <div class="filt tfrow">
    <button class="tfbtn" onclick="setTF(1,this)">1 min</button>
    <button class="tfbtn" onclick="setTF(5,this)">5 min</button>
    <button class="tfbtn" onclick="setTF(15,this)">15 min</button>
  </div>
  <div id="chart"></div>
  <div class="sub" id="chartnote"></div>
</div>
"""

CHART_CSS = """
#chartcard h2{margin-bottom:10px}
#chart{width:100%;height:320px}
.tfrow{margin-bottom:10px}
#chartnote{margin-top:9px;font-size:11.5px;color:#5f6368;line-height:1.55;white-space:pre-line}
@media(max-width:640px){ #chart{height:250px} }
"""



ALERTS_JS = r"""
// --------------------------------------------------------------------- alerts
// Deliberately NOT "notify on every update". The site rebuilds every few
// minutes; wiring a notification to that would fire ~288 times a day, and the
// only thing it would reliably produce is people turning notifications off. So
// each alert below is tied to something actually HAPPENING, and each is
// independently switchable.
//
// These fire from the page, so they need it open -- in a tab, or installed to
// the home screen and running in the background. Reaching a locked phone with
// the app closed needs a server to hold subscriptions, which a static site has
// nowhere to put.
var ALERT_DEFAULTS = {move:true, news:true, event:true, level:false, every:false};
var AL = (function(){
  try{ return Object.assign({}, ALERT_DEFAULTS, JSON.parse(localStorage.getItem('gw_alerts')||'{}')); }
  catch(e){ return Object.assign({}, ALERT_DEFAULTS); }
})();
var SEEN = (function(){
  try{ return JSON.parse(localStorage.getItem('gw_seen')||'[]'); }catch(e){ return []; }
})();
var PXHIST = [];        // rolling live-price history, for the move test
var LASTFIRE = {};      // per-category cooldown

function saveAlerts(){ try{ localStorage.setItem('gw_alerts', JSON.stringify(AL)); }catch(e){} }
function saveSeen(){
  SEEN = SEEN.slice(-250);                    // bounded: this is localStorage
  try{ localStorage.setItem('gw_seen', JSON.stringify(SEEN)); }catch(e){}
}

async function toggleAlert(key, el){
  AL[key] = el.checked;
  saveAlerts();
  if(el.checked && !(await ensurePermission())){
    el.checked = false; AL[key] = false; saveAlerts();
  }
  paintAlertState();
}

async function ensurePermission(){
  if(!('Notification' in window)){
    alertNote('This browser cannot show notifications.'); return false;
  }
  if(Notification.permission === 'granted') return true;
  if(Notification.permission === 'denied'){
    alertNote('Notifications are blocked for this site. Turn them back on in your '
            + 'browser settings for this page, then try again.');
    return false;
  }
  const p = await Notification.requestPermission();
  if(p !== 'granted'){ alertNote('Not enabled \u2014 you said no to the permission prompt.'); return false; }
  alertNote('');
  return true;
}

function alertNote(msg){ const el = E('alertnote'); if(el) el.textContent = msg; }

// One place that actually shows a notification, so the cooldown and the
// installed-vs-tab difference are handled once rather than at four call sites.
function fire(category, title, body, cooldownSec){
  if(Notification.permission !== 'granted') return;
  const now = Date.now();
  if(LASTFIRE[category] && now - LASTFIRE[category] < (cooldownSec||300)*1000) return;
  LASTFIRE[category] = now;
  const opts = {body:body, icon:'icon-192.png', badge:'icon-192.png',
                tag:'gw-'+category, renotify:false};
  try{
    if(navigator.serviceWorker && navigator.serviceWorker.ready){
      navigator.serviceWorker.ready.then(function(reg){
        reg.showNotification(title, opts);
      }).catch(function(){ new Notification(title, opts); });
    } else { new Notification(title, opts); }
  }catch(e){ /* some browsers refuse the direct constructor; nothing to do */ }
}

// --- the triggers ----------------------------------------------------------

// A move is only worth waking someone for if it is big FOR THIS HOUR. Gold at
// 3am moves a fraction of what it does when New York opens, so a fixed
// threshold would either scream all afternoon or never fire overnight.
function checkMove(){
  if(!AL.move || LIVEPX === null) return;
  PXHIST.push({t:Date.now(), p:LIVEPX});
  const cut = Date.now() - 3*60*1000;
  while(PXHIST.length && PXHIST[0].t < cut) PXHIST.shift();
  if(PXHIST.length < 6) return;                      // need ~1 min of samples

  const then = PXHIST[0].p, mins = (Date.now() - PXHIST[0].t)/60000;
  if(mins < 1.5) return;
  const bp = Math.abs((LIVEPX - then)/then * 10000);

  const hr = new Date().getUTCHours();               // hourly table is in SGT
  const sgt = (hr + 8) % 24;
  const row = (LAST.hourly||[]).find(function(r){ return r.hour === sgt; });
  const normal = row ? row.avg : 2.0;                // bp per minute
  const expected = normal * mins;
  if(expected <= 0) return;

  if(bp > expected * 3){
    const dir = LIVEPX > then ? 'jumped up' : 'dropped';
    const usd = Math.abs(LIVEPX - then).toFixed(1);
    fire('move', 'Gold ' + dir + ' $' + usd,
         'That is about ' + Math.round(bp/expected) + 'x the usual move for this '
         + 'time of day. Now $' + LIVEPX.toFixed(2) + '.', 420);
  }
}

// A headline is "new" if we have not shown it before -- not if the build is
// new. Those are different things, and confusing them is how you end up
// re-announcing the same story every five minutes.
function checkNews(d){
  if(!AL.news) return;
  const items = (d.pinned || d.news || []).slice(0, 6);
  for(const n of items){
    const id = (n.title||'').slice(0,90);
    if(!id || SEEN.indexOf(id) !== -1) continue;
    SEEN.push(id);
    if(FIRSTLOAD) continue;          // do not dump the backlog on first open
    fire('news', 'Gold news: ' + (n.cat || 'market'),
         (n.title||'') + (n.effect ? '\n\n' + n.effect : ''), 120);
  }
  saveSeen();
}

function checkEvent(d){
  if(!AL.event) return;
  const now = Date.now()/1000;
  for(const c of (d.calendar||[])){
    if(!c.ts || c.ts < now) continue;
    const mins = Math.round((c.ts - now)/60);
    if(mins > 15 || mins < 0) continue;
    const id = 'ev:' + c.ts + c.title;
    if(SEEN.indexOf(id) !== -1) continue;
    SEEN.push(id); saveSeen();
    fire('event', c.title + ' in ' + mins + ' min',
         'Big US number due. Gold often moves sharply the moment it lands.', 60);
  }
}

function checkLevel(){
  if(!AL.level || LIVEPX === null || !LAST.levels) return;
  const lv = LAST.levels;
  if(lv.day_high && LIVEPX > lv.day_high)
    fire('level', 'Gold broke today\u2019s high',
         'Now $' + LIVEPX.toFixed(2) + ', above today\u2019s previous high of $'
         + lv.day_high.toFixed(2) + '.', 900);
  else if(lv.day_low && LIVEPX < lv.day_low)
    fire('level', 'Gold broke today\u2019s low',
         'Now $' + LIVEPX.toFixed(2) + ', below today\u2019s previous low of $'
         + lv.day_low.toFixed(2) + '.', 900);
}

// The literal "tell me about every update" option, off by default and labelled
// for what it is, because the honest version of this feature is the four above.
var LASTBUILD = null;
function checkEvery(d){
  if(!AL.every || !d.built_epoch) return;
  if(LASTBUILD !== null && d.built_epoch !== LASTBUILD && !FIRSTLOAD){
    fire('every', 'Gold Watch updated',
         '$' + (d.price||'') + ' \u00b7 ' + (d.bias||'') + ' \u00b7 '
         + (d.n_news||0) + ' headlines.', 60);
  }
  LASTBUILD = d.built_epoch;
}

var FIRSTLOAD = true;
function runAlerts(d){
  checkNews(d); checkEvent(d); checkEvery(d);
  FIRSTLOAD = false;
}

function paintAlertState(){
  const on = Object.keys(ALERT_DEFAULTS).filter(function(k){ return AL[k]; }).length;
  const el = E('alertstate'); if(!el) return;
  if(!('Notification' in window)) { el.textContent = 'not supported in this browser'; return; }
  el.textContent = Notification.permission === 'granted'
    ? (on ? on + ' alert' + (on>1?'s':'') + ' on' : 'all off')
    : 'not enabled yet';
  el.style.color = (Notification.permission === 'granted' && on) ? '#00d4aa' : '#5f6368';
}

function initAlerts(){
  Object.keys(ALERT_DEFAULTS).forEach(function(k){
    const el = E('al_'+k); if(el && el !== _stub) el.checked = !!AL[k];
  });
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('sw.js').catch(function(){});
  }
  paintAlertState();
}
"""

# Collapsed by default and placed high. Sitting at the bottom of a 12,000px
# page it was technically present and effectively invisible -- nobody scrolls
# that far to switch on a feature they do not know exists. <details> is native,
# so it costs no JS and stays keyboard- and screen-reader-friendly.
ALERTS_HTML = """<div class="card" id="alertcard">
  <details id="aldet">
  <summary><span class="alsum">Tell me when something happens</span>
    <span class="alstate" id="alertstate">not enabled yet</span></summary>
  <div class="sub">Alerts appear on this device. Keep the page open, or add it to
    your home screen so it can run in the background.</div>
  <label class="alrow"><input type="checkbox" id="al_move" onchange="toggleAlert('move',this)">
    <span><b>A big move</b><em>Bigger than normal for this time of day, so it stays quiet overnight and speaks up when New York opens.</em></span></label>
  <label class="alrow"><input type="checkbox" id="al_news" onchange="toggleAlert('news',this)">
    <span><b>Important news</b><em>Only headlines that reach the top of the list, and only once each.</em></span></label>
  <label class="alrow"><input type="checkbox" id="al_event" onchange="toggleAlert('event',this)">
    <span><b>A big report is 15 minutes away</b><em>Jobs numbers, inflation, Fed decisions. Gold usually jumps the second these land.</em></span></label>
  <label class="alrow"><input type="checkbox" id="al_level" onchange="toggleAlert('level',this)">
    <span><b>Today's high or low breaks</b><em>Price goes past where it has been all day.</em></span></label>
  <label class="alrow"><input type="checkbox" id="al_every" onchange="toggleAlert('every',this)">
    <span><b>Every single refresh</b><em>About 288 times a day. Most people regret this one.</em></span></label>
  <div class="err" id="alertnote" style="display:none"></div>
  </details>
</div>
"""

ALERTS_CSS = """
#aldet summary{display:flex;justify-content:space-between;align-items:center;gap:10px;
  cursor:pointer;list-style:none;padding:1px 0}
#aldet summary::-webkit-details-marker{display:none}
#aldet summary::after{content:"\\203a";color:#5f6368;font-size:20px;line-height:1;
  transform:rotate(90deg);transition:transform .18s ease;flex:none}
#aldet[open] summary::after{transform:rotate(-90deg)}
.alsum{font-size:13px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;color:#9aa0a6}
.alstate{font-size:11.5px;color:#5f6368;margin-left:auto;margin-right:4px}
#aldet .sub{margin:11px 0 4px}
.alrow{display:flex;gap:11px;align-items:flex-start;padding:11px 0;
  border-bottom:1px solid #1a1f2a;cursor:pointer}
.alrow:last-of-type{border-bottom:0}
.alrow input{margin-top:3px;width:18px;height:18px;flex:none;accent-color:#00d4aa;cursor:pointer}
.alrow b{display:block;font-size:14px;font-weight:600;color:#e8eaed}
.alrow em{display:block;font-style:normal;font-size:11.5px;color:#5f6368;line-height:1.5;margin-top:2px}
#alertnote:not(:empty){display:block !important}
#alertnote:empty{display:none !important}
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
    # Not from this origin. GitHub Pages serves everything with
    # cache-control: max-age=600 AND ignores the query string when caching, so
    # "data.json?t=<now>" came back x-cache: HIT with an age of two minutes --
    # the buster never busted anything, and the data could be ten minutes old
    # however often it was rebuilt. raw.githubusercontent caches for 300s and is
    # written by the refresh loop every few minutes, so it is the fresher path;
    # the Pages copy stays as the fallback for when it is unreachable.
    swap("fetch('/state.json',{cache:'no-store'})", "fetchData()", "data source")

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
    swap("</style>", KEY_CSS + CHART_CSS + ALERTS_CSS + "</style>", "key css")

    # 9. the chart: markup under the summary line, code before render(), and the
    #    library served from this site rather than a third-party CDN so the page
    #    cannot be broken by someone else's outage
    swap('<div class="card" id="pinned"', CHART_HTML + '<div class="card" id="pinned"',
         "chart markup")
    swap("let CAT='all';", CHART_JS + "\nlet CAT='all';", "chart js")
    swap("</head>", '<script src="lightweight-charts.js"></script></head>', "chart lib")
    # (no leading indent: this line is the OUTPUT of transform 3, which replaced
    #  a column-zero statement, not a nested one)
    swap("tickPrice(); setInterval(tickPrice, 10000); paintKeyState();",
         "tickPrice(); setInterval(tickPrice, 10000); paintKeyState();\n"
         "initChart();\n"
         "document.querySelectorAll('.tfbtn').forEach(function(b){\n"
         "  if(parseInt(b.textContent)===TF) b.classList.add('on'); });",
         "chart boot")
    # redraw when a new build lands, and keep the forming candle live
    swap("  LAST=d;\n", "  LAST=d;\n  initChart(); drawChart();\n", "chart redraw")
    swap("    paintLive();\n  }catch(e)",
         "    paintLive(); tickChart(); checkMove(); checkLevel();\n  }catch(e)",
         "chart live tick")

    # 10. alerts: markup, code, boot, and the per-build checks
    swap('<div class="card" id="pinned"', ALERTS_HTML + '<div class="card" id="pinned"',
         "alerts markup")
    swap("let CAT='all';", ALERTS_JS + "\nlet CAT='all';", "alerts js")
    swap("initChart();\n", "initChart();\n  initAlerts();\n", "alerts boot")
    swap("  LAST=d;\n", "  LAST=d;\n  runAlerts(d);\n", "alerts per build")

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

    # The refresh loop only needs the data. Rebuilding the page every few
    # minutes would mean redeploying the whole site to change one file.
    if "--data-only" in sys.argv:
        print(f"data only: ${state.get('price')}  {state.get('n_news')} headlines",
              flush=True)
        return

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
