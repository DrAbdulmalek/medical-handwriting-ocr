import { Preferences } from '@capacitor/preferences';
import { Filesystem, Directory, Encoding } from '@capacitor/filesystem';
import { Network } from '@capacitor/network';

// ─── Types ───

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'fatal';
export type LogCategory = 
  | 'app' 
  | 'ocr' 
  | 'sync' 
  | 'db' 
  | 'network' 
  | 'auth' 
  | 'ui' 
  | 'camera' 
  | 'pdf' 
  | 'dictionary' 
  | 'performance';

export interface LogEntry {
  id: string;
  timestamp: number;        // Unix ms
  level: LogLevel;
  category: LogCategory;
  message: string;
  details?: Record<string, any>;
  stackTrace?: string;
  sessionId: string;
  userId?: string;
  deviceId?: string;
  screen?: string;
  memoryUsage?: number;     // MB
  networkStatus?: 'online' | 'offline';
  appVersion?: string;
}

export interface SessionInfo {
  id: string;
  startTime: number;
  endTime?: number;
  deviceInfo: DeviceInfo;
  appVersion: string;
  userId?: string;
}

export interface DeviceInfo {
  platform: string;         // android | ios | web
  osVersion: string;
  model: string;
  manufacturer: string;
  screenWidth: number;
  screenHeight: number;
  language: string;
  timezone: string;
  batteryLevel?: number;
}

// ─── Configuration ───

const LOG_CONFIG = {
  maxLogEntries: 5000,           // Maximum entries in memory
  maxLogAgeDays: 7,              // Auto-delete logs older than 7 days
  logToConsole: __DEV__,        // Log to console in development
  logToFile: true,              // Save to filesystem
  logToServer: false,           // Upload to server (configurable)
  minLevelForFile: 'debug' as LogLevel,
  minLevelForServer: 'error' as LogLevel,
  batchSize: 50,                // Upload batch size
  logFileName: 'medocr-logs.json',
  sessionFileName: 'medocr-sessions.json',
};

// ─── Session Management ───

class SessionManager {
  private currentSession: SessionInfo | null = null;

  async startSession(userId?: string): Promise<SessionInfo> {
    const deviceInfo = await this.getDeviceInfo();
    const session: SessionInfo = {
      id: `sess_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      startTime: Date.now(),
      deviceInfo,
      appVersion: import.meta.env.VITE_APP_VERSION || '1.0.0',
      userId,
    };

    this.currentSession = session;
    await this.saveSession(session);

    logger.info('app', 'Session started', { sessionId: session.id, deviceInfo });
    return session;
  }

  async endSession(): Promise<void> {
    if (this.currentSession) {
      this.currentSession.endTime = Date.now();
      await this.saveSession(this.currentSession);
      logger.info('app', 'Session ended', { 
        duration: this.currentSession.endTime - this.currentSession.startTime 
      });
    }
  }

  getCurrentSession(): SessionInfo | null {
    return this.currentSession;
  }

  private async getDeviceInfo(): Promise<DeviceInfo> {
    const { Device } = await import('@capacitor/device');
    const info = await Device.getInfo();
    const battery = await Device.getBatteryInfo();
    const language = await Device.getLanguageCode();

    return {
      platform: info.platform || 'web',
      osVersion: info.osVersion || 'unknown',
      model: info.model || 'unknown',
      manufacturer: info.manufacturer || 'unknown',
      screenWidth: window.innerWidth,
      screenHeight: window.innerHeight,
      language: language.value || 'ar',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      batteryLevel: battery.batteryLevel ? battery.batteryLevel * 100 : undefined,
    };
  }

  private async saveSession(session: SessionInfo): Promise<void> {
    try {
      const { value } = await Preferences.get({ key: 'medocr_sessions' });
      const sessions: SessionInfo[] = value ? JSON.parse(value) : [];

      // Remove old sessions
      const cutoff = Date.now() - (LOG_CONFIG.maxLogAgeDays * 24 * 60 * 60 * 1000);
      const filtered = sessions.filter(s => s.startTime > cutoff);

      filtered.push(session);
      await Preferences.set({ 
        key: 'medocr_sessions', 
        value: JSON.stringify(filtered) 
      });
    } catch (err) {
      console.error('Failed to save session:', err);
    }
  }
}

// ─── Logger Core ───

class Logger {
  private logs: LogEntry[] = [];
  private sessionManager = new SessionManager();
  private initialized = false;
  private flushTimer: ReturnType<typeof setInterval> | null = null;

  async initialize(userId?: string): Promise<void> {
    if (this.initialized) return;

    await this.sessionManager.startSession(userId);
    await this.loadLogsFromFile();
    this.setupErrorHandlers();
    this.startPeriodicFlush();

    this.initialized = true;
    this.info('app', 'Logger initialized', { config: LOG_CONFIG });
  }

  // ─── Public Logging Methods ───

  debug(category: LogCategory, message: string, details?: Record<string, any>): void {
    this.log('debug', category, message, details);
  }

  info(category: LogCategory, message: string, details?: Record<string, any>): void {
    this.log('info', category, message, details);
  }

  warn(category: LogCategory, message: string, details?: Record<string, any>): void {
    this.log('warn', category, message, details);
  }

  error(category: LogCategory, message: string, error?: Error, details?: Record<string, any>): void {
    this.log('error', category, message, { 
      ...details, 
      errorName: error?.name,
      errorMessage: error?.message,
    }, error?.stack);
  }

  fatal(category: LogCategory, message: string, error?: Error, details?: Record<string, any>): void {
    this.log('fatal', category, message, {
      ...details,
      errorName: error?.name,
      errorMessage: error?.message,
    }, error?.stack);

    // Immediately flush on fatal
    this.flushToFile();
    this.uploadLogs(true);
  }

  // ─── Performance Logging ───

  startTimer(label: string, category: LogCategory = 'performance'): () => void {
    const start = performance.now();
    return () => {
      const duration = performance.now() - start;
      this.info(category, `Timer: ${label}`, { duration: Math.round(duration), unit: 'ms' });
    };
  }

  logMemoryUsage(category: LogCategory = 'performance'): void {
    if ('memory' in performance) {
      const mem = (performance as any).memory;
      this.info(category, 'Memory usage', {
        usedJSHeapSize: Math.round(mem.usedJSHeapSize / 1024 / 1024),
        totalJSHeapSize: Math.round(mem.totalJSHeapSize / 1024 / 1024),
        jsHeapSizeLimit: Math.round(mem.jsHeapSizeLimit / 1024 / 1024),
        unit: 'MB',
      });
    }
  }

  // ─── Network Logging ───

  async logNetworkRequest(
    method: string,
    url: string,
    status: number,
    duration: number,
    error?: string
  ): Promise<void> {
    const level: LogLevel = error ? 'error' : status >= 400 ? 'warn' : 'debug';
    this.log(level, 'network', `${method} ${url}`, {
      status,
      duration: Math.round(duration),
      error,
      unit: 'ms',
    });
  }

  // ─── Core Log Method ───

  private log(
    level: LogLevel,
    category: LogCategory,
    message: string,
    details?: Record<string, any>,
    stackTrace?: string
  ): void {
    const session = this.sessionManager.getCurrentSession();
    const networkStatus = Network.getStatus();

    const entry: LogEntry = {
      id: `log_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      timestamp: Date.now(),
      level,
      category,
      message,
      details,
      stackTrace,
      sessionId: session?.id || 'unknown',
      userId: session?.userId,
      deviceId: session?.deviceInfo.model,
      screen: window.location.pathname,
      memoryUsage: (performance as any).memory?.usedJSHeapSize 
        ? Math.round((performance as any).memory.usedJSHeapSize / 1024 / 1024) 
        : undefined,
      networkStatus: networkStatus.then ? undefined : 'offline',
      appVersion: session?.appVersion,
    };

    // Add to memory
    this.logs.push(entry);

    // Trim if too many
    if (this.logs.length > LOG_CONFIG.maxLogEntries) {
      this.logs = this.logs.slice(-LOG_CONFIG.maxLogEntries);
    }

    // Console output (development)
    if (LOG_CONFIG.logToConsole) {
      const emoji = { debug: '🔍', info: 'ℹ️', warn: '⚠️', error: '❌', fatal: '💀' };
      const color = { debug: '#6b7280', info: '#3b82f6', warn: '#f59e0b', error: '#ef4444', fatal: '#dc2626' };
      console.log(
        `%c${emoji[level]} [${level.toUpperCase()}] [${category}] ${message}`,
        `color: ${color[level]}; font-weight: ${level === 'error' || level === 'fatal' ? 'bold' : 'normal'}`,
        details || ''
      );
      if (stackTrace) console.error(stackTrace);
    }

    // Immediate file write for errors
    if (level === 'error' || level === 'fatal') {
      this.flushToFile();
    }
  }

  // ─── Error Handlers ───

  private setupErrorHandlers(): void {
    // Global error handler
    window.addEventListener('error', (event) => {
      this.fatal('app', 'Uncaught error', event.error, {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        message: event.message,
      });
    });

    // Unhandled promise rejections
    window.addEventListener('unhandledrejection', (event) => {
      this.error('app', 'Unhandled promise rejection', 
        event.reason instanceof Error ? event.reason : new Error(String(event.reason)),
        { reason: String(event.reason) }
      );
    });

    // React error boundary fallback
    (window as any).__medocrLogError = (error: Error, info: any) => {
      this.error('ui', 'React error boundary', error, { componentStack: info?.componentStack });
    };
  }

  // ─── File Operations ───

  private async loadLogsFromFile(): Promise<void> {
    try {
      const result = await Filesystem.readFile({
        path: LOG_CONFIG.logFileName,
        directory: Directory.Documents,
        encoding: Encoding.UTF8,
      });

      const data = JSON.parse(result.data as string);
      this.logs = data.logs || [];

      // Clean old logs
      const cutoff = Date.now() - (LOG_CONFIG.maxLogAgeDays * 24 * 60 * 60 * 1000);
      this.logs = this.logs.filter(l => l.timestamp > cutoff);

      this.info('app', `Loaded ${this.logs.length} logs from file`);
    } catch {
      // File doesn't exist yet
      this.logs = [];
    }
  }

  private async flushToFile(): Promise<void> {
    if (this.logs.length === 0) return;

    try {
      const data = {
        exportedAt: Date.now(),
        sessionId: this.sessionManager.getCurrentSession()?.id,
        logCount: this.logs.length,
        logs: this.logs,
      };

      await Filesystem.writeFile({
        path: LOG_CONFIG.logFileName,
        data: JSON.stringify(data, null, 2),
        directory: Directory.Documents,
        encoding: Encoding.UTF8,
      });
    } catch (err) {
      console.error('Failed to flush logs:', err);
    }
  }

  // ─── Periodic Flush ───

  private startPeriodicFlush(): void {
    // Flush every 30 seconds
    this.flushTimer = setInterval(() => {
      this.flushToFile();
    }, 30000);

    // Flush on app pause/background
    document.addEventListener('pause', () => this.flushToFile());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.flushToFile();
    });
  }

  // ─── Log Retrieval ───

  getLogs(options?: {
    level?: LogLevel;
    category?: LogCategory;
    since?: number;
    until?: number;
    limit?: number;
    search?: string;
  }): LogEntry[] {
    let filtered = [...this.logs];

    if (options?.level) {
      filtered = filtered.filter(l => l.level === options.level);
    }
    if (options?.category) {
      filtered = filtered.filter(l => l.category === options.category);
    }
    if (options?.since) {
      filtered = filtered.filter(l => l.timestamp >= options.since);
    }
    if (options?.until) {
      filtered = filtered.filter(l => l.timestamp <= options.until);
    }
    if (options?.search) {
      const search = options.search.toLowerCase();
      filtered = filtered.filter(l => 
        l.message.toLowerCase().includes(search) ||
        JSON.stringify(l.details).toLowerCase().includes(search)
      );
    }

    // Sort newest first
    filtered.sort((a, b) => b.timestamp - a.timestamp);

    if (options?.limit) {
      filtered = filtered.slice(0, options.limit);
    }

    return filtered;
  }

  getStats(): Record<string, number> {
    const stats: Record<string, number> = {};

    this.logs.forEach(l => {
      stats[`level_${l.level}`] = (stats[`level_${l.level}`] || 0) + 1;
      stats[`cat_${l.category}`] = (stats[`cat_${l.category}`] || 0) + 1;
    });

    stats.total = this.logs.length;
    stats.sessions = new Set(this.logs.map(l => l.sessionId)).size;

    return stats;
  }

  // ─── Log Export ───

  async exportLogs(format: 'json' | 'csv' | 'txt' = 'json'): Promise<string> {
    const logs = this.getLogs();
    let content: string;
    let filename: string;

    switch (format) {
      case 'csv':
        content = this.toCSV(logs);
        filename = `medocr-logs-${Date.now()}.csv`;
        break;
      case 'txt':
        content = this.toText(logs);
        filename = `medocr-logs-${Date.now()}.txt`;
        break;
      default:
        content = JSON.stringify({
          exportedAt: new Date().toISOString(),
          appVersion: this.sessionManager.getCurrentSession()?.appVersion,
          totalLogs: logs.length,
          logs,
        }, null, 2);
        filename = `medocr-logs-${Date.now()}.json`;
    }

    await Filesystem.writeFile({
      path: filename,
      data: content,
      directory: Directory.Documents,
      encoding: Encoding.UTF8,
    });

    return filename;
  }

  private toCSV(logs: LogEntry[]): string {
    const headers = ['timestamp', 'level', 'category', 'message', 'sessionId', 'screen', 'details'];
    const rows = logs.map(l => [
      new Date(l.timestamp).toISOString(),
      l.level,
      l.category,
      `"${l.message.replace(/"/g, '""')}"`,
      l.sessionId,
      l.screen || '',
      l.details ? JSON.stringify(l.details).replace(/"/g, '""') : '',
    ]);
    return [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
  }

  private toText(logs: LogEntry[]): string {
    return logs.map(l => 
      `[${new Date(l.timestamp).toISOString()}] [${l.level.toUpperCase()}] [${l.category}] ${l.message}` +
      (l.details ? `\n  Details: ${JSON.stringify(l.details)}` : '') +
      (l.stackTrace ? `\n  Stack: ${l.stackTrace}` : '')
    ).join('\n\n');
  }

  // ─── Server Upload ───

  async uploadLogs(immediate = false): Promise<void> {
    if (!LOG_CONFIG.logToServer) return;

    const network = await Network.getStatus();
    if (!network.connected) {
      this.warn('network', 'Cannot upload logs — offline');
      return;
    }

    const logsToUpload = immediate 
      ? this.logs.filter(l => l.level === 'error' || l.level === 'fatal')
      : this.logs.slice(-LOG_CONFIG.batchSize);

    if (logsToUpload.length === 0) return;

    try {
      const { mobileAuth } = await import('./mobileAuth');
      const client = mobileAuth.getClient();

      await client.post('/api/mobile/logs', {
        sessionId: this.sessionManager.getCurrentSession()?.id,
        deviceInfo: this.sessionManager.getCurrentSession()?.deviceInfo,
        logs: logsToUpload,
      });

      this.info('sync', `Uploaded ${logsToUpload.length} logs to server`);
    } catch (err) {
      this.warn('sync', 'Failed to upload logs', { error: String(err) });
    }
  }

  // ─── Cleanup ───

  async clearLogs(): Promise<void> {
    this.logs = [];
    await Filesystem.deleteFile({
      path: LOG_CONFIG.logFileName,
      directory: Directory.Documents,
    }).catch(() => {});
    this.info('app', 'All logs cleared');
  }

  async destroy(): Promise<void> {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
    }
    await this.flushToFile();
    await this.sessionManager.endSession();
  }
}

// ─── Singleton ───

export const logger = new Logger();

// ─── React Hook ───

import { useState, useEffect, useCallback } from 'react';

export function useLogger() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});

  const refresh = useCallback(() => {
    setLogs(logger.getLogs({ limit: 100 }));
    setStats(logger.getStats());
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { logs, stats, refresh, logger };
}
