const CACHE_VERSION = 'v2';
const STATIC_CACHE_NAME = `yamtrack-static-${CACHE_VERSION}`;

const STATIC_ASSETS = [
  '/static/css/main.css',
  '/static/favicon/android-chrome-192x192.png',
  '/static/favicon/android-chrome-512x512.png',
  '/static/favicon/android-chrome-192x192-maskable.png',
  '/static/favicon/android-chrome-512x512-maskable.png',
  '/static/favicon/apple-touch-icon.png',
  '/static/fonts/roboto-flex.woff2'
];

const CACHEABLE_DESTINATIONS = new Set(['style', 'script', 'font', 'image']);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames
          .filter((cacheName) => cacheName.startsWith('yamtrack-static-') && cacheName !== STATIC_CACHE_NAME)
          .map((cacheName) => caches.delete(cacheName))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  if (request.method !== 'GET') {
    return;
  }

  const requestUrl = new URL(request.url);
  const isStaticAsset = requestUrl.origin === self.location.origin && requestUrl.pathname.startsWith('/static/');

  if (!isStaticAsset || !CACHEABLE_DESTINATIONS.has(request.destination)) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      return fetch(request).then((networkResponse) => {
        if (!networkResponse || networkResponse.status !== 200) {
          return networkResponse;
        }

        const responseToCache = networkResponse.clone();
        caches.open(STATIC_CACHE_NAME).then((cache) => cache.put(request, responseToCache));

        return networkResponse;
      });
    })
  );
});
