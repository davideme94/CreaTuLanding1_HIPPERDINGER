const VERSION='alerta-zona-shell-v2';
const SHELL=['/','/index.html','/manifest.webmanifest','/assets/favicon-v2.png','/assets/icon-192-v2.png','/assets/icon-512-v2.png','/assets/maskable-512-v2.png','/assets/apple-touch-icon-v2.png'];
self.addEventListener('install',event=>{
  event.waitUntil(caches.open(VERSION).then(cache=>cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener('activate',event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(url.origin!==location.origin || event.request.method!=='GET' || url.pathname.startsWith('/api/'))return;
  event.respondWith(fetch(event.request).then(res=>{
    if(res.ok) { const copy=res.clone();caches.open(VERSION).then(cache=>cache.put(event.request,copy)); }
    return res;
  }).catch(()=>caches.match(event.request)));
});
