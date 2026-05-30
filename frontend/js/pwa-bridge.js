/**
 * PWA Bridge — Medical OCR
 *
 * Provides a unified interface for PWA capabilities including:
 * - Install prompt handling
 * - Offline detection and management
 * - Camera access for mobile OCR scanning
 * - Background sync for corrections
 * - Push notifications
 * - Haptic feedback
 * - Battery status monitoring
 */

// =============================================================================
// PWABridge Class
// =============================================================================

class PWABridge {
  constructor() {
    this._deferredPrompt = null;
    this._isInstalled = false;
    this._isOnline = navigator.onLine;
    this._cameraStream = null;
    this._correctionQueue = [];
    this._registration = null;
    this._batteryStatus = null;
    this._listeners = new Map();

    this._init();
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------

  async _init() {
    await this.checkInstallStatus();
    this.setupInstallPrompt();
    this.setupOfflineDetection();
    console.log('[PWA Bridge] Initialized — online:', this._isOnline, 'installed:', this._isInstalled);
  }

  // ---------------------------------------------------------------------------
  // Install Status & Prompt
  // ---------------------------------------------------------------------------

  async checkInstallStatus() {
    // Check if running as installed PWA
    if (window.matchMedia('(display-mode: standalone)').matches) {
      this._isInstalled = true;
    } else if (window.navigator.standalone === true) {
      // iOS Safari standalone
      this._isInstalled = true;
    }

    // Check for existing service worker registration
    if ('serviceWorker' in navigator) {
      this._registration = await navigator.serviceWorker.getRegistration();
    }

    return this._isInstalled;
  }

  setupInstallPrompt() {
    // Capture the beforeinstallprompt event
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      this._deferredPrompt = e;
      console.log('[PWA Bridge] Install prompt captured');
      this._emit('install-available', { canInstall: true });
    });

    // Listen for successful installation
    window.addEventListener('appinstalled', () => {
      this._isInstalled = true;
      this._deferredPrompt = null;
      console.log('[PWA Bridge] App installed successfully');
      this._emit('installed', { installed: true });
    });
  }

  async promptInstall() {
    if (!this._deferredPrompt) {
      console.log('[PWA Bridge] No install prompt available');
      return { success: false, reason: 'prompt_not_available' };
    }

    this._deferredPrompt.prompt();
    const { outcome } = await this._deferredPrompt.userChoice;
    this._deferredPrompt = null;

    if (outcome === 'accepted') {
      this._isInstalled = true;
      console.log('[PWA Bridge] User accepted install prompt');
      return { success: true, outcome };
    }

    console.log('[PWA Bridge] User dismissed install prompt');
    return { success: false, outcome };
  }

  get isInstalled() {
    return this._isInstalled;
  }

  get canInstall() {
    return this._deferredPrompt !== null;
  }

  // ---------------------------------------------------------------------------
  // Offline Detection
  // ---------------------------------------------------------------------------

  setupOfflineDetection() {
    window.addEventListener('online', () => {
      this._isOnline = true;
      console.log('[PWA Bridge] Network status: online');
      this._emit('online', { online: true });
      this._syncWhenOnline();
    });

    window.addEventListener('offline', () => {
      this._isOnline = false;
      console.log('[PWA Bridge] Network status: offline');
      this._emit('offline', { online: false });
    });

    // Listen for connection quality changes
    if ('connection' in navigator) {
      const connection = navigator.connection;
      connection.addEventListener('change', () => {
        this._emit('connection-change', {
          type: connection.effectiveType,
          downlink: connection.downlink,
          rtt: connection.rtt,
          saveData: connection.saveData,
        });
      });
    }
  }

  get isOnline() {
    return this._isOnline;
  }

  // ---------------------------------------------------------------------------
  // Camera Access
  // ---------------------------------------------------------------------------

  async setupCameraAccess(constraints = {}) {
    const defaultConstraints = {
      video: {
        facingMode: 'environment', // Back camera for document scanning
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        ...constraints.video,
      },
      audio: false,
    };

    try {
      // Check if camera API is available
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('Camera API not available');
      }

      // Stop existing stream if any
      if (this._cameraStream) {
        this._stopCamera();
      }

      const stream = await navigator.mediaDevices.getUserMedia(defaultConstraints);
      this._cameraStream = stream;

      console.log('[PWA Bridge] Camera access granted');
      this._emit('camera-ready', { stream });

      return { success: true, stream };
    } catch (error) {
      console.error('[PWA Bridge] Camera access error:', error);
      this._emit('camera-error', { error: error.message });

      return {
        success: false,
        error: error.message,
        code: error.name, // NotAllowedError, NotFoundError, etc.
      };
    }
  }

  showCameraPreview(stream, containerSelector) {
    const container = document.querySelector(containerSelector);
    if (!container) {
      console.error('[PWA Bridge] Container not found:', containerSelector);
      return;
    }

    // Clear previous content
    container.innerHTML = '';

    // Create video element
    const video = document.createElement('video');
    video.srcObject = stream;
    video.setAttribute('autoplay', '');
    video.setAttribute('playsinline', '');
    video.setAttribute('muted', '');
    video.style.width = '100%';
    video.style.height = '100%';
    video.style.objectFit = 'cover';
    container.appendChild(video);

    // Create capture button overlay
    const captureBtn = document.createElement('button');
    captureBtn.className = 'camera-capture-btn';
    captureBtn.innerHTML = `
      <svg width="64" height="64" viewBox="0 0 64 64" fill="none">
        <circle cx="32" cy="32" r="28" stroke="white" stroke-width="3"/>
        <circle cx="32" cy="32" r="22" fill="white"/>
      </svg>
    `;
    captureBtn.style.cssText = `
      position: absolute;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: none;
      border: none;
      cursor: pointer;
      padding: 0;
      z-index: 10;
    `;

    // Create close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'camera-close-btn';
    closeBtn.innerHTML = `
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
      </svg>
    `;
    closeBtn.style.cssText = `
      position: absolute;
      top: 16px;
      right: 16px;
      background: rgba(0,0,0,0.5);
      border: none;
      border-radius: 50%;
      cursor: pointer;
      padding: 8px;
      z-index: 10;
    `;
    closeBtn.addEventListener('click', () => this._stopCamera());

    // Container must have position: relative
    container.style.position = 'relative';
    container.appendChild(captureBtn);
    container.appendChild(closeBtn);

    return {
      video,
      capture: () => this._captureFrame(video),
      close: () => this._stopCamera(),
    };
  }

  _captureFrame(video) {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);

    // Convert to blob
    return new Promise((resolve) => {
      canvas.toBlob((blob) => {
        resolve({
          blob,
          dataUrl: canvas.toDataURL('image/jpeg', 0.85),
          width: canvas.width,
          height: canvas.height,
        });
      }, 'image/jpeg', 0.85);
    });
  }

  _stopCamera() {
    if (this._cameraStream) {
      this._cameraStream.getTracks().forEach((track) => track.stop());
      this._cameraStream = null;
    }
    this._emit('camera-stopped', {});
  }

  // ---------------------------------------------------------------------------
  // Correction Queue (Offline Sync)
  // ---------------------------------------------------------------------------

  async queueCorrection(correction) {
    const queueItem = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2),
      data: correction,
      timestamp: new Date().toISOString(),
      status: 'pending',
    };

    // Store in IndexedDB for persistence
    if ('indexedDB' in window) {
      try {
        const db = await this._openOfflineDB();
        if (db) {
          const tx = db.transaction('pending-corrections', 'readwrite');
          tx.objectStore('pending-corrections').add(queueItem);
          await new Promise((resolve, reject) => {
            tx.oncomplete = resolve;
            tx.onerror = reject;
          });
        }
      } catch (err) {
        console.error('[PWA Bridge] Failed to queue correction:', err);
      }
    }

    // Also keep in memory
    this._correctionQueue.push(queueItem);

    console.log('[PWA Bridge] Correction queued:', queueItem.id);

    // If online, try to sync immediately
    if (this._isOnline) {
      this._emit('queue-updated', { pending: this._correctionQueue.length });
      return this._syncCorrection(queueItem);
    }

    // Register background sync if available
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      const reg = await navigator.serviceWorker.ready;
      reg.sync.register('sync-corrections');
    }

    this._emit('queue-updated', { pending: this._correctionQueue.length });
    return { queued: true, id: queueItem.id };
  }

  async _syncCorrection(item) {
    try {
      const response = await fetch('/api/corrections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(item.data),
      });

      if (response.ok) {
        // Remove from queue
        this._correctionQueue = this._correctionQueue.filter((c) => c.id !== item.id);

        // Remove from IndexedDB
        try {
          const db = await this._openOfflineDB();
          if (db) {
            const tx = db.transaction('pending-corrections', 'readwrite');
            tx.objectStore('pending-corrections').delete(item.id);
          }
        } catch (_) {
          // Ignore DB errors
        }

        this._emit('correction-synced', { id: item.id });
        return { success: true, id: item.id };
      }

      return { success: false, status: response.status };
    } catch (error) {
      console.error('[PWA Bridge] Sync failed:', error);
      return { success: false, error: error.message };
    }
  }

  async _syncWhenOnline() {
    if (this._correctionQueue.length === 0) return;

    console.log('[PWA Bridge] Syncing queued corrections...');
    for (const item of [...this._correctionQueue]) {
      await this._syncCorrection(item);
    }
  }

  async _openOfflineDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open('medical-ocr-offline', 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  get pendingCorrectionCount() {
    return this._correctionQueue.length;
  }

  // ---------------------------------------------------------------------------
  // Push Notifications
  // ---------------------------------------------------------------------------

  async subscribeToNotifications() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      console.log('[PWA Bridge] Push notifications not supported');
      return { success: false, reason: 'not_supported' };
    }

    try {
      const registration = await navigator.serviceWorker.ready;

      // Check existing subscription
      const existingSubscription = await registration.pushManager.getSubscription();
      if (existingSubscription) {
        console.log('[PWA Bridge] Already subscribed to notifications');
        return { success: true, alreadySubscribed: true };
      }

      // Request permission
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        console.log('[PWA Bridge] Notification permission denied');
        return { success: false, permission };
      }

      // Create subscription
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        // VAPID public key should come from server
        applicationServerKey: this._urlBase64ToUint8Array(
          'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkOs-GvVl3gTHkTjcUG0C1mGHbDCNXKvY4m0wpNyQ' // Replace with actual VAPID key
        ),
      });

      // Send subscription to server
      await fetch('/api/notifications/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subscription),
      });

      console.log('[PWA Bridge] Subscribed to push notifications');
      this._emit('notification-subscribed', {});
      return { success: true };
    } catch (error) {
      console.error('[PWA Bridge] Push subscription error:', error);
      return { success: false, error: error.message };
    }
  }

  _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  // ---------------------------------------------------------------------------
  // Haptic Feedback
  // ---------------------------------------------------------------------------

  hapticFeedback(pattern = 'light') {
    if (!('vibrate' in navigator)) {
      console.log('[PWA Bridge] Haptic feedback not supported');
      return false;
    }

    const patterns = {
      light: 10,
      medium: 50,
      heavy: 100,
      success: [50, 100, 50],
      error: [100, 50, 100, 50, 100],
      notification: [100, 50, 100],
      tap: 10,
    };

    const vibrationPattern = patterns[pattern] || pattern;
    return navigator.vibrate(vibrationPattern);
  }

  // ---------------------------------------------------------------------------
  // Battery Status
  // ---------------------------------------------------------------------------

  async getBatteryStatus() {
    if (!('getBattery' in navigator)) {
      return { supported: false };
    }

    try {
      const battery = await navigator.getBattery();

      this._batteryStatus = {
        charging: battery.charging,
        chargingTime: battery.chargingTime === Infinity ? null : battery.chargingTime,
        dischargingTime: battery.dischargingTime === Infinity ? null : battery.dischargingTime,
        level: Math.round(battery.level * 100),
      };

      // Listen for battery changes
      battery.addEventListener('chargingchange', () => {
        this._batteryStatus.charging = battery.charging;
        this._emit('battery-change', this._batteryStatus);
      });

      battery.addEventListener('levelchange', () => {
        this._batteryStatus.level = Math.round(battery.level * 100);
        this._emit('battery-change', this._batteryStatus);
      });

      return { supported: true, ...this._batteryStatus };
    } catch (error) {
      console.error('[PWA Bridge] Battery API error:', error);
      return { supported: false, error: error.message };
    }
  }

  // ---------------------------------------------------------------------------
  // Service Worker Registration
  // ---------------------------------------------------------------------------

  async registerServiceWorker(swPath = '/sw.js') {
    if (!('serviceWorker' in navigator)) {
      console.log('[PWA Bridge] Service workers not supported');
      return { success: false, reason: 'not_supported' };
    }

    try {
      this._registration = await navigator.serviceWorker.register(swPath, {
        scope: '/',
      });

      // Listen for updates
      this._registration.addEventListener('updatefound', () => {
        const newWorker = this._registration.installing;
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'activated') {
            this._emit('sw-updated', { newWorker: true });
          }
        });
      });

      console.log('[PWA Bridge] Service worker registered:', this._registration.scope);
      return { success: true, scope: this._registration.scope };
    } catch (error) {
      console.error('[PWA Bridge] Service worker registration error:', error);
      return { success: false, error: error.message };
    }
  }

  // ---------------------------------------------------------------------------
  // Event System
  // ---------------------------------------------------------------------------

  on(event, callback) {
    if (!this._listeners.has(event)) {
      this._listeners.set(event, []);
    }
    this._listeners.get(event).push(callback);
    return this;
  }

  off(event, callback) {
    if (!this._listeners.has(event)) return this;
    if (callback) {
      const listeners = this._listeners.get(event);
      const index = listeners.indexOf(callback);
      if (index > -1) listeners.splice(index, 1);
    } else {
      this._listeners.delete(event);
    }
    return this;
  }

  _emit(event, data) {
    const listeners = this._listeners.get(event) || [];
    for (const callback of listeners) {
      try {
        callback(data);
      } catch (err) {
        console.error(`[PWA Bridge] Error in event listener "${event}":`, err);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Feature Detection
  // ---------------------------------------------------------------------------

  getFeatures() {
    return {
      serviceWorker: 'serviceWorker' in navigator,
      pushNotifications: 'PushManager' in window,
      backgroundSync: 'SyncManager' in window,
      camera: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      haptic: 'vibrate' in navigator,
      battery: 'getBattery' in navigator,
      share: 'share' in navigator,
      clipboard: !!(navigator.clipboard),
      indexedDB: 'indexedDB' in window,
      fileSystem: 'showOpenFilePicker' in window,
      speechRecognition: 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window,
    };
  }
}

// =============================================================================
// Singleton Instance
// =============================================================================

const pwaBridge = new PWABridge();

export { pwaBridge };
