// Service worker for Gold Watch.
//
// It deliberately caches NOTHING. Its only job is to exist, so that an
// installed home-screen app can call showNotification() -- which on some
// platforms is only available through a registration, not from the page.
//
// No caching is a decision, not an omission. This page's whole value is that
// its numbers are current; a cache-first worker serving yesterday's data.json
// from disk would be worse than the site being briefly unreachable, and
// "remember to bump CACHE_NAME on every deploy" is a rule that gets forgotten
// exactly once and then ships stale prices to everyone.
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });

self.addEventListener('notificationclick', function(e){
  e.notification.close();
  e.waitUntil(clients.matchAll({type:'window', includeUncontrolled:true}).then(function(ws){
    for(const w of ws){ if('focus' in w) return w.focus(); }
    if(clients.openWindow) return clients.openWindow('./');
  }));
});
