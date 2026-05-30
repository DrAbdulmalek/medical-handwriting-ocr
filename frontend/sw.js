// =============================================================================
// Service Worker — Medical OCR PWA
// Version: v2
// =============================================================================

const CACHE_NAME = 'medical-ocr-v2';
const STATIC_CACHE = 'medical-ocr-static-v2';
const DYNAMIC_CACHE = 'medical-ocr-dynamic-v2';

// Static assets to pre-cache on install
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/dictionary.html',
  '/css/style.css',
  '/css/dictionary.css',
  '/css/mobile.css',
  '/js/app.js',
  '/js/dictionary.js',
  '/js/pwa-bridge.js',
  '/manifest.json',
  '/icons/icon-192x192.png',
  '/icons/icon-512x512.png',
];

// IndexedDB helper for offline storage
// =============================================================================
class OfflineDB {
  constructor(dbName = 'medical-ocr-offline', version = 1) {
    this.dbName = dbName;
    this.version = version;
    this.db = null;
  }

  async open() {
    if (this.db) return this.db;
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.dbName, this.version);

      request.onupgradeneeded = (event) => {
        const db = event.target.result;

        // Store pending corrections for background sync
        if (!db.objectStoreNames.contains('pending-corrections')) {
          const correctionsStore = db.createObjectStore('pending-corrections', {
            keyPath: 'id',
            autoIncrement: true,
          });
          correctionsStore.createIndex('timestamp', 'timestamp', { unique: false });
          correctionsStore.createIndex('status', 'status', { unique: false });
        }

        // Store cached OCR results
        if (!db.objectStoreNames.contains('ocr-results')) {
          const ocrStore = db.createObjectStore('ocr-results', {
            keyPath: 'id',
          });
          ocrStore.createIndex('imageHash', 'imageHash', { unique: true });
          ocrStore.createIndex('timestamp', 'timestamp', { unique: false });
        }

        // Store user preferences offline
        if (!db.objectStoreNames.contains('preferences')) {
          db.createObjectStore('preferences', { keyPath: 'key' });
        }
      };

      request.onsuccess = (event) => {
        this.db = event.target.result;
        resolve(this.db);
      };

      request.onerror = (event) => {
        reject(event.target.error);
      };
    });
  }

  async add(storeName, data) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.add(data);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async getAll(storeName) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const store = tx.objectStore(storeName);
      const request = store.getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async delete(storeName, key) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.delete(key);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }

  async clear(storeName) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const request = store.clear();
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  }
}

const offlineDB = new OfflineDB();

// Install event — pre-cache static assets
// =============================================================================
self.addEventListener('install', (event) => {
  console.log('[SW] Install — caching static assets');
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      console.log('[SW] Caching static assets:', STATIC_ASSETS.length);
      return cache.addAll(STATIC_ASSETS);
    }).then(() => {
      // Initialize IndexedDB
      return offlineDB.open();
    }).then(() => {
      // Activate immediately without waiting for old SW to finish
      return self.skipWaiting();
    })
  );
});

// Activate event — clean up old caches
// =============================================================================
self.addEventListener('activate', (event) => {
  console.log('[SW] Activate — cleaning old caches');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => {
            // Delete old versions of our caches
            return (
              name !== STATIC_CACHE &&
              name !== DYNAMIC_CACHE &&
              name.startsWith('medical-ocr')
            );
          })
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => {
      // Claim all clients immediately
      return self.clients.claim();
    })
  );
});

// Fetch event — strategy router
// =============================================================================
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests and non-http(s) requests
  if (request.method !== 'GET') {
    // For POST/PUT requests to /api/, queue for background sync if offline
    if (url.pathname.startsWith('/api/') && !navigator.onLine) {
      event.respondWith(
        queueForBackgroundSync(request)
      );
    }
    return;
  }

  if (!url.protocol.startsWith('http')) {
    return;
  }

  // Strategy: Network-first for API requests
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Strategy: Cache-first for static assets
  if (
    url.pathname.match(/\.(css|js|png|jpg|jpeg|gif|svg|woff|woff2|ttf|ico|webmanifest)$/) ||
    url.pathname === '/manifest.json'
  ) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Strategy: Stale-while-revalidate for everything else
  event.respondWith(staleWhileRevalidate(request));
});

// =============================================================================
// Fetch Strategies
// =============================================================================

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      // Cache successful API responses in dynamic cache
      const cache = await caches.open(DYNAMIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.log('[SW] Network failed for:', request.url, '- falling back to cache');
    const cached = await caches.match(request);
    if (cached) {
      return cached;
    }
    // Return offline fallback for HTML requests
    if (request.headers.get('accept')?.includes('text/html')) {
      const offlinePage = await caches.match('/index.html');
      return offlinePage || new Response('Offline - Medical OCR', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      });
    }
    return new Response(JSON.stringify({ error: 'Network unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    return cached;
  }
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.log('[SW] Cache-first failed for:', request.url);
    return new Response('Resource not available offline', {
      status: 404,
    });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(DYNAMIC_CACHE);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request)
    .then((response) => {
      if (response.ok) {
        cache.put(request, response.clone());
      }
      return response;
    })
    .catch(() => {
      // If fetch fails, return cached if available
      return cached || new Response('Offline', { status: 503 });
    });

  return cached || fetchPromise;
}

// =============================================================================
// Background Sync Handler
// =============================================================================

self.addEventListener('sync', (event) => {
  console.log('[SW] Background sync event:', event.tag);

  if (event.tag === 'sync-corrections') {
    event.waitUntil(syncPendingCorrections());
  }
});

async function syncPendingCorrections() {
  console.log('[SW] Syncing pending corrections...');
  try {
    const pending = await offlineDB.getAll('pending-corrections');
    console.log('[SW] Found', pending.length, 'pending corrections');

    for (const correction of pending) {
      try {
        const response = await fetch('/api/corrections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(correction.data),
        });

        if (response.ok) {
          await offlineDB.delete('pending-corrections', correction.id);
          console.log('[SW] Synced correction:', correction.id);
        }
      } catch (err) {
        console.error('[SW] Failed to sync correction:', correction.id, err);
      }
    }
  } catch (error) {
    console.error('[SW] Error syncing corrections:', error);
  }
}

async function queueForBackgroundSync(request) {
  try {
    const body = await request.json();
    const correction = {
      data: body,
      timestamp: new Date().toISOString(),
      status: 'pending',
    };
    await offlineDB.add('pending-corrections', correction);
    console.log('[SW] Queued correction for background sync');

    // Register sync if available
    if ('sync' in self.registration) {
      await self.registration.sync.register('sync-corrections');
    }

    return new Response(JSON.stringify({ queued: true, message: 'Will sync when online' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: 'Failed to queue correction' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

// =============================================================================
// Push Notification Handler
// =============================================================================

self.addEventListener('push', (event) => {
  console.log('[SW] Push notification received');
  let data = {
    title: 'Medical OCR',
    body: 'New update available',
    icon: '/icons/icon-192x192.png',
    badge: '/icons/icon-192x192.png',
    vibrate: [100, 50, 100],
    data: { url: '/' },
  };

  if (event.data) {
    try {
      data = { ...data, ...event.data.json() };
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: data.icon,
    badge: data.badge,
    vibrate: data.vibrate,
    data: data.data,
    actions: [
      { action: 'open', title: 'Open' },
      { action: 'dismiss', title: 'Dismiss' },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Handle notification click
self.addEventListener('notificationclick', (event) => {
  console.log('[SW] Notification clicked:', event.action);
  event.notification.close();

  if (event.action === 'dismiss') {
    return;
  }

  const urlToOpen = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // If there's already an open window, focus it
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.navigate(urlToOpen).then(() => client.focus());
        }
      }
      // Otherwise open a new window
      return self.clients.openWindow(urlToOpen);
    })
  );
});

// =============================================================================
// Message Handler (for communication with main thread)
// =============================================================================

self.addEventListener('message', (event) => {
  console.log('[SW] Message received:', event.data);

  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }

  if (event.data && event.data.type === 'GET_CACHE_SIZE') {
    caches.keys().then((names) => {
      Promise.all(
        names.map((name) => caches.open(name).then((cache) => cache.keys().then((keys) => keys.length)))
      ).then((sizes) => {
        const totalSize = sizes.reduce((sum, size) => sum + size, 0);
        self.clients.postMessage({ type: 'CACHE_SIZE', size: totalSize });
      });
    });
  }
});
