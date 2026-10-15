// Servis calisani: uygulama kabugu onbellekte, veri her zaman once agdan.
// Surum her yayinda degisir (dc028711b3), eski onbellek temizlenir.
const CACHE = 'fiyat-takip-dc028711b3';
const SHELL = ['./', './app.js?v=dc028711b3', './app.css?v=dc028711b3', './manifest.webmanifest',
               './icons/icon-192.png', './icons/apple-touch-icon.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Yazi tipi: once onbellek (degismez, cevrimdisinda da gorunsun)
  if (url.hostname.endsWith('fonts.googleapis.com') || url.hostname.endsWith('fonts.gstatic.com')) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    })));
    return;
  }
  // GitHub API ve diger kaynaklar asla onbelleklenmez
  if (!req.url.startsWith(self.registration.scope)) return;

  // Surumlu dosyalar degismez: onbellekten ver
  if (url.searchParams.has('v')) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }
  // Sayfa ve veri: once ag, olmazsa son kopya
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req, { ignoreSearch: true }).then((hit) => hit || caches.match('./')))
  );
});
