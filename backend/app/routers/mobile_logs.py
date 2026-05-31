"""
Mobile Logs Endpoint — Receive and analyze logs from mobile devices
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel
from uuid import UUID, uuid4
import json

from app.database import get_db

router = APIRouter(prefix="/api/mobile", tags=["mobile-logs"])

# ─── Pydantic Models ───

class MobileLogEntry(BaseModel):
    id: str
    timestamp: int
    level: str
    category: str
    message: str
    details: Optional[Dict[str, Any]] = None
    stackTrace: Optional[str] = None
    sessionId: str
    userId: Optional[str] = None
    deviceId: Optional[str] = None
    screen: Optional[str] = None
    memoryUsage: Optional[float] = None
    networkStatus: Optional[str] = None
    appVersion: Optional[str] = None

class DeviceInfo(BaseModel):
    platform: str
    osVersion: str
    model: str
    manufacturer: str
    screenWidth: int
    screenHeight: int
    language: str
    timezone: str
    batteryLevel: Optional[float] = None

class MobileLogsUpload(BaseModel):
    sessionId: str
    deviceInfo: DeviceInfo
    logs: List[MobileLogEntry]

class LogAnalytics(BaseModel):
    totalLogs: int
    errorCount: int
    warningCount: int
    fatalCount: int
    topErrors: List[Dict[str, Any]]
    sessionsToday: int
    activeDevices: int
    avgSessionDuration: float
    crashRate: float

# ─── Endpoints ───

@router.post("/logs")
async def upload_mobile_logs(
    data: MobileLogsUpload,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """
    Receive logs from mobile devices for centralized monitoring.
    """
    accepted = 0
    rejected = 0

    for log in data.logs:
        try:
            db.execute(text("""
                INSERT INTO mobile_logs (
                    id, session_id, device_id, user_id, timestamp, level, category,
                    message, details, stack_trace, screen, memory_usage, 
                    network_status, app_version, device_info, created_at
                ) VALUES (
                    :id, :session_id, :device_id, :user_id, 
                    to_timestamp(:timestamp / 1000.0), :level, :category,
                    :message, :details, :stack_trace, :screen, :memory_usage,
                    :network_status, :app_version, :device_info, NOW()
                )
            """), {
                "id": log.id,
                "session_id": log.sessionId,
                "device_id": log.deviceId,
                "user_id": log.userId,
                "timestamp": log.timestamp,
                "level": log.level,
                "category": log.category,
                "message": log.message,
                "details": json.dumps(log.details) if log.details else None,
                "stack_trace": log.stackTrace,
                "screen": log.screen,
                "memory_usage": log.memoryUsage,
                "network_status": log.networkStatus,
                "app_version": log.appVersion,
                "device_info": json.dumps(data.deviceInfo.dict()),
            })
            accepted += 1
        except Exception as e:
            rejected += 1
            print(f"Failed to insert log {log.id}: {e}")

    db.commit()

    return {
        "success": True,
        "accepted": accepted,
        "rejected": rejected,
        "session_id": data.sessionId,
    }


@router.get("/logs/analytics")
async def get_log_analytics(
    days: int = 7,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """
    Get analytics from mobile logs.
    """
    since = datetime.utcnow() - timedelta(days=days)

    # Total logs
    result = db.execute(text("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN level = 'error' THEN 1 ELSE 0 END) as errors,
               SUM(CASE WHEN level = 'warn' THEN 1 ELSE 0 END) as warnings,
               SUM(CASE WHEN level = 'fatal' THEN 1 ELSE 0 END) as fatals
        FROM mobile_logs
        WHERE created_at > :since
    """), {"since": since})
    row = result.fetchone()

    # Top errors
    result = db.execute(text("""
        SELECT message, category, COUNT(*) as count,
               MAX(created_at) as last_occurrence
        FROM mobile_logs
        WHERE level IN ('error', 'fatal')
        AND created_at > :since
        GROUP BY message, category
        ORDER BY count DESC
        LIMIT 10
    """), {"since": since})
    top_errors = [
        {
            "message": r.message,
            "category": r.category,
            "count": r.count,
            "last_occurrence": r.last_occurrence.isoformat() if r.last_occurrence else None,
        }
        for r in result
    ]

    # Sessions today
    result = db.execute(text("""
        SELECT COUNT(DISTINCT session_id) as sessions
        FROM mobile_logs
        WHERE created_at > CURRENT_DATE
    """))
    sessions_today = result.fetchone().sessions

    # Active devices
    result = db.execute(text("""
        SELECT COUNT(DISTINCT device_id) as devices
        FROM mobile_logs
        WHERE created_at > :since
    """), {"since": since})
    active_devices = result.fetchone().devices

    # Crash rate (fatal / total)
    total = row.total or 1
    crash_rate = (row.fatals or 0) / total * 100

    return {
        "period_days": days,
        "total_logs": row.total,
        "error_count": row.errors,
        "warning_count": row.warnings,
        "fatal_count": row.fatals,
        "top_errors": top_errors,
        "sessions_today": sessions_today,
        "active_devices": active_devices,
        "crash_rate_percent": round(crash_rate, 2),
    }


@router.get("/logs/search")
async def search_logs(
    query: Optional[str] = None,
    level: Optional[str] = None,
    category: Optional[str] = None,
    device_id: Optional[str] = None,
    user_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """
    Search and filter mobile logs.
    """
    conditions = ["1=1"]
    params = {}

    if query:
        conditions.append("(message ILIKE :query OR details ILIKE :query)")
        params["query"] = f"%{query}%"
    if level:
        conditions.append("level = :level")
        params["level"] = level
    if category:
        conditions.append("category = :category")
        params["category"] = category
    if device_id:
        conditions.append("device_id = :device_id")
        params["device_id"] = device_id
    if user_id:
        conditions.append("user_id = :user_id")
        params["user_id"] = user_id
    if since:
        conditions.append("created_at >= :since")
        params["since"] = since
    if until:
        conditions.append("created_at <= :until")
        params["until"] = until

    where_clause = " AND ".join(conditions)

    result = db.execute(text(f"""
        SELECT * FROM mobile_logs
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset})

    logs = []
    for row in result:
        logs.append({
            "id": row.id,
            "session_id": row.session_id,
            "timestamp": int(row.timestamp.timestamp() * 1000) if row.timestamp else None,
            "level": row.level,
            "category": row.category,
            "message": row.message,
            "details": json.loads(row.details) if row.details else None,
            "device_id": row.device_id,
            "user_id": row.user_id,
            "app_version": row.app_version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # Count total
    count_result = db.execute(text(f"""
        SELECT COUNT(*) as total FROM mobile_logs WHERE {where_clause}
    """), params)
    total = count_result.fetchone().total

    return {
        "logs": logs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
