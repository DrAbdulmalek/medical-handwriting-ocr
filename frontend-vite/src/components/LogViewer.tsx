import React, { useState, useEffect, useCallback, useRef } from 'react';
import { logger, LogEntry, LogLevel, LogCategory } from '../services/logger';
import { Filesystem, Directory, Encoding } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import toast from 'react-hot-toast';

interface LogViewerProps {
  onClose: () => void;
}

const LEVEL_COLORS: Record<LogLevel, string> = {
  debug: '#6b7280',
  info: '#3b82f6',
  warn: '#f59e0b',
  error: '#ef4444',
  fatal: '#dc2626',
};

const LEVEL_BG: Record<LogLevel, string> = {
  debug: '#f3f4f6',
  info: '#eff6ff',
  warn: '#fffbeb',
  error: '#fef2f2',
  fatal: '#fef2f2',
};

const CATEGORY_ICONS: Record<LogCategory, string> = {
  app: '📱',
  ocr: '🔍',
  sync: '🔄',
  db: '🗄️',
  network: '🌐',
  auth: '🔐',
  ui: '🎨',
  camera: '📷',
  pdf: '📄',
  dictionary: '📚',
  performance: '⚡',
};

export const LogViewer: React.FC<LogViewerProps> = ({ onClose }) => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([]);
  const [selectedLevel, setSelectedLevel] = useState<LogLevel | 'all'>('all');
  const [selectedCategory, setSelectedCategory] = useState<LogCategory | 'all'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [isAutoScroll, setIsAutoScroll] = useState(true);
  const [sessionId, setSessionId] = useState('');
  const logEndRef = useRef<HTMLDivElement>(null);
  const refreshInterval = useRef<ReturnType<typeof setInterval>>();

  const refreshLogs = useCallback(() => {
    const allLogs = logger.getLogs({
      level: selectedLevel === 'all' ? undefined : selectedLevel,
      category: selectedCategory === 'all' ? undefined : selectedCategory,
      search: searchQuery || undefined,
      limit: 500,
    });
    setLogs(allLogs);
    setFilteredLogs(allLogs);
    setStats(logger.getStats());
  }, [selectedLevel, selectedCategory, searchQuery]);

  useEffect(() => {
    refreshLogs();
    refreshInterval.current = setInterval(refreshLogs, 3000);
    return () => clearInterval(refreshInterval.current);
  }, [refreshLogs]);

  useEffect(() => {
    if (isAutoScroll && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [filteredLogs, isAutoScroll]);

  const formatTime = (timestamp: number): string => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('ar-SA', { 
      hour: '2-digit', 
      minute: '2-digit', 
      second: '2-digit',
      fractionalSecondDigits: 3 
    });
  };

  const formatDuration = (ms: number): string => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  };

  const handleExport = async (format: 'json' | 'csv' | 'txt') => {
    try {
      const filename = await logger.exportLogs(format);
      toast.success(`تم تصدير اللوغ: ${filename}`);

      // Share the file
      await Share.share({
        title: 'MedOCR Logs',
        text: `Logs exported: ${filename}`,
        url: `file://${filename}`,
        dialogTitle: 'مشاركة ملف اللوغ',
      });
    } catch (err) {
      toast.error('فشل تصدير اللوغ');
      logger.error('app', 'Export logs failed', err as Error);
    }
  };

  const handleClear = async () => {
    if (confirm('هل أنت متأكد من حذف جميع السجلات؟')) {
      await logger.clearLogs();
      refreshLogs();
      toast.success('تم حذف السجلات');
    }
  };

  const handleUpload = async () => {
    try {
      await logger.uploadLogs(true);
      toast.success('تم رفع السجلات للسيرفر');
    } catch {
      toast.error('فشل رفع السجلات');
    }
  };

  const getLevelCount = (level: LogLevel): number => {
    return stats[`level_${level}`] || 0;
  };

  return (
    <div className="log-viewer-overlay" dir="rtl">
      <div className="log-viewer-container">
        {/* Header */}
        <div className="log-viewer-header">
          <div className="header-left">
            <h2>📋 سجل الأحداث (Logs)</h2>
            <span className="log-count">{filteredLogs.length} سجل</span>
          </div>
          <div className="header-actions">
            <button className="btn-icon" onClick={() => setIsAutoScroll(!isAutoScroll)} title="تمرير تلقائي">
              {isAutoScroll ? '⏸️' : '▶️'}
            </button>
            <button className="btn-icon" onClick={refreshLogs} title="تحديث">
              🔄
            </button>
            <button className="btn-icon" onClick={handleUpload} title="رفع للسيرفر">
              ☁️
            </button>
            <div className="dropdown">
              <button className="btn-icon">📥</button>
              <div className="dropdown-menu">
                <button onClick={() => handleExport('json')}>تصدير JSON</button>
                <button onClick={() => handleExport('csv')}>تصدير CSV</button>
                <button onClick={() => handleExport('txt')}>تصدير نص</button>
              </div>
            </div>
            <button className="btn-icon danger" onClick={handleClear} title="حذف الكل">
              🗑️
            </button>
            <button className="btn-close" onClick={onClose}>✕</button>
          </div>
        </div>

        {/* Stats Bar */}
        <div className="log-stats-bar">
          <div className="stat-item debug">
            <span className="stat-dot" style={{ background: LEVEL_COLORS.debug }} />
            <span className="stat-label">Debug</span>
            <span className="stat-value">{getLevelCount('debug')}</span>
          </div>
          <div className="stat-item info">
            <span className="stat-dot" style={{ background: LEVEL_COLORS.info }} />
            <span className="stat-label">Info</span>
            <span className="stat-value">{getLevelCount('info')}</span>
          </div>
          <div className="stat-item warn">
            <span className="stat-dot" style={{ background: LEVEL_COLORS.warn }} />
            <span className="stat-label">Warn</span>
            <span className="stat-value">{getLevelCount('warn')}</span>
          </div>
          <div className="stat-item error">
            <span className="stat-dot" style={{ background: LEVEL_COLORS.error }} />
            <span className="stat-label">Error</span>
            <span className="stat-value">{getLevelCount('error')}</span>
          </div>
          <div className="stat-item fatal">
            <span className="stat-dot" style={{ background: LEVEL_COLORS.fatal }} />
            <span className="stat-label">Fatal</span>
            <span className="stat-value">{getLevelCount('fatal')}</span>
          </div>
          <div className="stat-item total">
            <span className="stat-label">الإجمالي</span>
            <span className="stat-value">{stats.total || 0}</span>
          </div>
        </div>

        {/* Filters */}
        <div className="log-filters">
          <div className="filter-group">
            <label>المستوى:</label>
            <select value={selectedLevel} onChange={(e) => setSelectedLevel(e.target.value as LogLevel | 'all')}>
              <option value="all">الكل</option>
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warn">Warn</option>
              <option value="error">Error</option>
              <option value="fatal">Fatal</option>
            </select>
          </div>
          <div className="filter-group">
            <label>الفئة:</label>
            <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value as LogCategory | 'all')}>
              <option value="all">الكل</option>
              <option value="app">App</option>
              <option value="ocr">OCR</option>
              <option value="sync">Sync</option>
              <option value="db">Database</option>
              <option value="network">Network</option>
              <option value="auth">Auth</option>
              <option value="ui">UI</option>
              <option value="camera">Camera</option>
              <option value="pdf">PDF</option>
              <option value="dictionary">Dictionary</option>
              <option value="performance">Performance</option>
            </select>
          </div>
          <div className="filter-group search">
            <input
              type="text"
              placeholder="بحث في السجلات..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {/* Log List */}
        <div className="log-list">
          {filteredLogs.length === 0 ? (
            <div className="log-empty">لا توجد سجلات</div>
          ) : (
            filteredLogs.map((log) => (
              <div
                key={log.id}
                className={`log-item ${log.level} ${expandedLog === log.id ? 'expanded' : ''}`}
                style={{ background: LEVEL_BG[log.level] }}
                onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
              >
                <div className="log-row">
                  <span className="log-time">{formatTime(log.timestamp)}</span>
                  <span className="log-level" style={{ color: LEVEL_COLORS[log.level] }}>
                    {log.level.toUpperCase()}
                  </span>
                  <span className="log-category">
                    {CATEGORY_ICONS[log.category]} {log.category}
                  </span>
                  <span className="log-message">{log.message}</span>
                  {log.details?.duration && (
                    <span className="log-duration">{formatDuration(log.details.duration)}</span>
                  )}
                </div>

                {expandedLog === log.id && (
                  <div className="log-details">
                    <div className="detail-row">
                      <strong>ID:</strong> {log.id}
                    </div>
                    <div className="detail-row">
                      <strong>Session:</strong> {log.sessionId}
                    </div>
                    {log.userId && (
                      <div className="detail-row">
                        <strong>User:</strong> {log.userId}
                      </div>
                    )}
                    {log.screen && (
                      <div className="detail-row">
                        <strong>Screen:</strong> {log.screen}
                      </div>
                    )}
                    {log.memoryUsage && (
                      <div className="detail-row">
                        <strong>Memory:</strong> {log.memoryUsage} MB
                      </div>
                    )}
                    {log.networkStatus && (
                      <div className="detail-row">
                        <strong>Network:</strong> {log.networkStatus}
                      </div>
                    )}
                    {log.details && (
                      <div className="detail-row">
                        <strong>Details:</strong>
                        <pre>{JSON.stringify(log.details, null, 2)}</pre>
                      </div>
                    )}
                    {log.stackTrace && (
                      <div className="detail-row">
                        <strong>Stack Trace:</strong>
                        <pre className="stack-trace">{log.stackTrace}</pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
      </div>
    </div>
  );
};
