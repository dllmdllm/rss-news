// Service worker for 新聞快訊
// Strategy:
//   - HTML / articles.json : network-first (fall back to cache when offline)
//   - content/*.json, images, js, css : stale-while-revalidate

const CACHE   = "rss-news-v21";
const SHELL   = [
  "./",
  "./index.html",
  "./article.html",
  "./manifest.json",
  "./vendor/fuse.min.js",
  "./js/common.js",
  "./js/index.js",
  "./js/article.js",
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => null)
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

function cacheKey(req) {
  const url = new URL(req.url);
  url.search = "";
  return url.toString();
}

function networkFirst(req) {
  const key = cacheKey(req);
  return fetch(req)
    .then(res => {
      if (res && res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(key, clone)).catch(() => null);
      }
      return res;
    })
    .catch(() => caches.match(key));
}

function staleWhileRevalidate(req) {
  const key = cacheKey(req);
  return caches.match(key).then(cached => {
    const network = fetch(req).then(res => {
      if (res && res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(key, clone)).catch(() => null);
      }
      return res;
    }).catch(() => cached);
    return cached || network;
  });
}

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const isHtml  = req.destination === "document" || url.pathname.endsWith(".html");
  const isIndex = url.pathname.endsWith("/articles.json");

  if (isHtml || isIndex) {
    event.respondWith(networkFirst(req));
  } else if (url.origin === location.origin) {
    event.respondWith(staleWhileRevalidate(req));
  }
});
