"""
Mobile Sync Router — Backend endpoints for mobile app synchronization

Provides bidirectional sync between mobile SQLite and PostgreSQL backend:
- Push corrections from mobile → server
- Pull server updates → mobile  
- Bulk sync operations
- Conflict resolution (server wins by default)
- Sync tokens for incremental updates
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, and_, or_
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID, uuid4
import json

from app.database import get_db
from app.models import RegionCorrection, RegionResponse, OCRResult
from app.middleware.api_key_auth import APIKeyMiddleware

router = APIRouter(prefix="/api/mobile", tags=["mobile"])

# ────────────────────────────────────────────────────────────────────────────
# Pydantic Models for Mobile Sync
# ────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel

class MobileCorrectionItem(BaseModel):
    """A correction submitted from mobile device."""
    local_region_id: str          # Mobile's local UUID
    server_region_id: Optional[str] = None  # Server UUID if known
    document_id: Optional[str] = None       # Server document UUID
    corrected_text: str
    original_text: str
    confidence: float = 0.0
    corrected_at: int             # Unix timestamp (ms)
    user_id: str = "anonymous"
    device_id: Optional[str] = None

class MobileSyncPushRequest(BaseModel):
    """Batch push of corrections from mobile."""
    device_id: str
    last_sync_token: Optional[str] = None
    corrections: List[MobileCorrectionItem]
    documents: Optional[List[Dict[str, Any]]] = None  # New docs metadata

class MobileSyncPullRequest(BaseModel):
    """Request to pull server updates since last sync."""
    device_id: str
    last_sync_token: Optional[str] = None
    since_timestamp: Optional[int] = None  # Unix ms
    user_id: Optional[str] = None
    limit: int = 100

class ServerRegionUpdate(BaseModel):
    """A region update from server to mobile."""
    server_region_id: str
    server_document_id: str
    page_number: int
    bbox: dict
    predicted_text: str
    confidence: float
    corrected_text: Optional[str] = None
    status: str
    corrected_at: Optional[int] = None
    user_id: Optional[str] = None
    updated_at: int

class MobileSyncResponse(BaseModel):
    """Response to a sync request."""
    sync_token: str
    accepted_count: int
    rejected_count: int
    rejected_items: List[Dict[str, Any]]
    server_updates: List[ServerRegionUpdate]
    has_more: bool
    server_timestamp: int

class SyncStatusResponse(BaseModel):
    """Current sync status for a device."""
    device_id: str
    last_sync_at: Optional[str] = None
    pending_corrections: int
    total_documents: int
    total_regions: int
    server_timestamp: int

class BulkCorrectionRequest(BaseModel):
    """Bulk approve/correct multiple regions at once."""
    corrections: List[RegionCorrection]
    user_id: str = "anonymous"
    device_id: Optional[str] = None

# ────────────────────────────────────────────────────────────────────────────
# Sync Token Management
# ────────────────────────────────────────────────────────────────────────────

def generate_sync_token() -> str:
    """Generate a unique sync token for incremental sync."""
    return f"sync_{uuid4().hex}_{int(datetime.utcnow().timestamp() * 1000)}"

def parse_sync_token(token: str) -> Optional[datetime]:
    """Extract timestamp from sync token for incremental queries."""
    try:
        parts = token.split("_")
        if len(parts) >= 3:
            ts_ms = int(parts[-1])
            return datetime.utcfromtimestamp(ts_ms / 1000)
    except (ValueError, IndexError):
        pass
    return None

# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.post("/sync/push", response_model=MobileSyncResponse)
async def push_mobile_corrections(
    request: MobileSyncPushRequest,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """
    Push corrections from mobile device to server.

    - Accepts batch corrections from mobile SQLite
    - Maps local_region_id to server_region_id
    - Creates new regions if server_region_id is missing
    - Returns which items were accepted/rejected
    - Returns server updates for bidirectional sync
    """
    accepted = 0
    rejected = []
    server_updates = []
    sync_token = generate_sync_token()
    now = int(datetime.utcnow().timestamp() * 1000)

    for item in request.corrections:
        try:
            # Case 1: We know the server region ID
            if item.server_region_id:
                result = db.execute(text("""
                    SELECT id, predicted_text, status, corrected_text
                    FROM text_regions
                    WHERE id = :id
                """), {"id": item.server_region_id})
                region = result.fetchone()

                if not region:
                    rejected.append({
                        "local_id": item.local_region_id,
                        "reason": "server_region_id not found",
                        "server_id": item.server_region_id
                    })
                    continue

                # Update existing region
                db.execute(text("""
                    UPDATE text_regions
                    SET corrected_text = :corrected,
                        status = 'corrected',
                        corrected_at = to_timestamp(:ts / 1000.0),
                        user_id = :user,
                        correction_count = correction_count + 1,
                        updated_at = NOW()
                    WHERE id = :id
                """), {
                    "corrected": item.corrected_text,
                    "ts": item.corrected_at,
                    "user": item.user_id,
                    "id": item.server_region_id
                })
                accepted += 1

            # Case 2: No server_region_id — try to find by document + text
            elif item.document_id:
                result = db.execute(text("""
                    SELECT tr.id, tr.predicted_text
                    FROM text_regions tr
                    JOIN pages p ON tr.page_id = p.id
                    WHERE p.document_id = :doc_id
                    AND tr.predicted_text = :predicted
                    LIMIT 1
                """), {
                    "doc_id": item.document_id,
                    "predicted": item.original_text
                })
                region = result.fetchone()

                if region:
                    db.execute(text("""
                        UPDATE text_regions
                        SET corrected_text = :corrected,
                            status = 'corrected',
                            corrected_at = to_timestamp(:ts / 1000.0),
                            user_id = :user,
                            correction_count = correction_count + 1,
                            updated_at = NOW()
                        WHERE id = :id
                    """), {
                        "corrected": item.corrected_text,
                        "ts": item.corrected_at,
                        "user": item.user_id,
                        "id": str(region.id)
                    })
                    accepted += 1
                else:
                    # Create orphan correction (will be linked later)
                    db.execute(text("""
                        INSERT INTO mobile_orphan_corrections
                        (id, device_id, local_region_id, document_id, 
                         predicted_text, corrected_text, corrected_at, user_id, created_at)
                        VALUES (:id, :device, :local_id, :doc_id, :predicted, :corrected, 
                                to_timestamp(:ts / 1000.0), :user, NOW())
                    """), {
                        "id": str(uuid4()),
                        "device": request.device_id,
                        "local_id": item.local_region_id,
                        "doc_id": item.document_id,
                        "predicted": item.original_text,
                        "corrected": item.corrected_text,
                        "ts": item.corrected_at,
                        "user": item.user_id
                    })
                    accepted += 1
            else:
                rejected.append({
                    "local_id": item.local_region_id,
                    "reason": "No server_region_id or document_id provided"
                })

        except Exception as e:
            rejected.append({
                "local_id": item.local_region_id,
                "reason": f"Server error: {str(e)}"
            })

    # Record sync log
    db.execute(text("""
        INSERT INTO mobile_sync_logs (id, device_id, direction, accepted_count, 
                                     rejected_count, sync_token, created_at)
        VALUES (:id, :device, 'push', :accepted, :rejected, :token, NOW())
    """), {
        "id": str(uuid4()),
        "device": request.device_id,
        "accepted": accepted,
        "rejected": len(rejected),
        "token": sync_token
    })

    # Pull server updates for bidirectional sync
    since = parse_sync_token(request.last_sync_token) if request.last_sync_token else None

    query = """
        SELECT tr.id, p.document_id, p.page_number, tr.bbox, tr.predicted_text,
               tr.confidence, tr.corrected_text, tr.status, tr.corrected_at,
               tr.user_id, tr.updated_at
        FROM text_regions tr
        JOIN pages p ON tr.page_id = p.id
        WHERE tr.updated_at > COALESCE(:since, '1970-01-01')
        AND (tr.status = 'corrected' OR tr.status = 'approved' OR tr.status = 'gold_standard')
        ORDER BY tr.updated_at DESC
        LIMIT :limit
    """

    result = db.execute(text(query), {
        "since": since,
        "limit": request.limit
    })

    for row in result:
        server_updates.append(ServerRegionUpdate(
            server_region_id=str(row.id),
            server_document_id=str(row.document_id),
            page_number=row.page_number,
            bbox=row.bbox,
            predicted_text=row.predicted_text,
            confidence=row.confidence,
            corrected_text=row.corrected_text,
            status=row.status,
            corrected_at=int(row.corrected_at.timestamp() * 1000) if row.corrected_at else None,
            user_id=row.user_id,
            updated_at=int(row.updated_at.timestamp() * 1000) if row.updated_at else now
        ))

    db.commit()

    return MobileSyncResponse(
        sync_token=sync_token,
        accepted_count=accepted,
        rejected_count=len(rejected),
        rejected_items=rejected,
        server_updates=server_updates,
        has_more=len(server_updates) == request.limit,
        server_timestamp=now
    )


@router.post("/sync/pull", response_model=MobileSyncResponse)
async def pull_server_updates(
    request: MobileSyncPullRequest,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """
    Pull server updates since last sync.

    Returns all corrections/approvals made on server (or other devices)
    since the provided sync token or timestamp.
    """
    sync_token = generate_sync_token()
    now = int(datetime.utcnow().timestamp() * 1000)

    since = None
    if request.last_sync_token:
        since = parse_sync_token(request.last_sync_token)
    elif request.since_timestamp:
        since = datetime.utcfromtimestamp(request.since_timestamp / 1000)

    # Build query with optional user filter
    user_filter = ""
    params = {"since": since, "limit": request.limit}

    if request.user_id:
        user_filter = "AND (tr.user_id = :user OR tr.reviewer_id = :user)"
        params["user"] = request.user_id

    query = f"""
        SELECT tr.id, p.document_id, p.page_number, tr.bbox, tr.predicted_text,
               tr.confidence, tr.corrected_text, tr.status, tr.corrected_at,
               tr.user_id, tr.reviewer_id, tr.updated_at
        FROM text_regions tr
        JOIN pages p ON tr.page_id = p.id
        WHERE tr.updated_at > COALESCE(:since, '1970-01-01')
        AND (tr.status IN ('corrected', 'approved', 'gold_standard'))
        {user_filter}
        ORDER BY tr.updated_at DESC
        LIMIT :limit
    """

    result = db.execute(text(query), params)

    server_updates = []
    for row in result:
        server_updates.append(ServerRegionUpdate(
            server_region_id=str(row.id),
            server_document_id=str(row.document_id),
            page_number=row.page_number,
            bbox=row.bbox,
            predicted_text=row.predicted_text,
            confidence=row.confidence,
            corrected_text=row.corrected_text,
            status=row.status,
            corrected_at=int(row.corrected_at.timestamp() * 1000) if row.corrected_at else None,
            user_id=row.user_id or row.reviewer_id,
            updated_at=int(row.updated_at.timestamp() * 1000) if row.updated_at else now
        ))

    # Record sync log
    db.execute(text("""
        INSERT INTO mobile_sync_logs (id, device_id, direction, accepted_count, 
                                     sync_token, created_at)
        VALUES (:id, :device, 'pull', :count, :token, NOW())
    """), {
        "id": str(uuid4()),
        "device": request.device_id,
        "count": len(server_updates),
        "token": sync_token
    })
    db.commit()

    return MobileSyncResponse(
        sync_token=sync_token,
        accepted_count=0,
        rejected_count=0,
        rejected_items=[],
        server_updates=server_updates,
        has_more=len(server_updates) == request.limit,
        server_timestamp=now
    )


@router.get("/sync/status/{device_id}", response_model=SyncStatusResponse)
async def get_sync_status(
    device_id: str,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """Get sync status for a specific mobile device."""

    # Last sync
    result = db.execute(text("""
        SELECT created_at FROM mobile_sync_logs
        WHERE device_id = :device
        ORDER BY created_at DESC
        LIMIT 1
    """), {"device": device_id})
    last_sync = result.fetchone()

    # Pending corrections (not yet synced to this device)
    result = db.execute(text("""
        SELECT COUNT(*) as count FROM text_regions
        WHERE status = 'corrected'
        AND updated_at > COALESCE(
            (SELECT MAX(created_at) FROM mobile_sync_logs 
             WHERE device_id = :device AND direction = 'pull'),
            '1970-01-01'
        )
    """), {"device": device_id})
    pending = result.fetchone().count

    # Total documents and regions for this device
    result = db.execute(text("""
        SELECT COUNT(DISTINCT p.document_id) as docs, COUNT(*) as regions
        FROM text_regions tr
        JOIN pages p ON tr.page_id = p.id
        WHERE tr.user_id = :device OR tr.user_id LIKE :device_wild
    """), {"device": device_id, "device_wild": f"%{device_id}%"})
    stats = result.fetchone()

    return SyncStatusResponse(
        device_id=device_id,
        last_sync_at=last_sync.created_at.isoformat() if last_sync else None,
        pending_corrections=pending,
        total_documents=stats.docs,
        total_regions=stats.regions,
        server_timestamp=int(datetime.utcnow().timestamp() * 1000)
    )


@router.post("/sync/bulk-correct")
async def bulk_correct(
    request: BulkCorrectionRequest,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """Bulk correct multiple regions at once (for mobile batch operations)."""
    updated = 0
    failed = []

    for correction in request.corrections:
        try:
            result = db.execute(text("""
                SELECT id, predicted_text FROM text_regions WHERE id = :id
            """), {"id": str(correction.region_id)})
            region = result.fetchone()

            if not region:
                failed.append({"region_id": str(correction.region_id), "reason": "Not found"})
                continue

            db.execute(text("""
                UPDATE text_regions
                SET corrected_text = :corrected,
                    status = 'corrected',
                    corrected_at = NOW(),
                    user_id = :user,
                    correction_count = correction_count + 1,
                    updated_at = NOW()
                WHERE id = :id
            """), {
                "corrected": correction.corrected_text,
                "user": correction.user_id,
                "id": str(correction.region_id)
            })
            updated += 1

        except Exception as e:
            failed.append({"region_id": str(correction.region_id), "reason": str(e)})

    db.commit()

    return {
        "success": updated > 0,
        "updated_count": updated,
        "failed_count": len(failed),
        "failed": failed,
        "sync_token": generate_sync_token()
    }


@router.get("/documents/{document_id}/regions")
async def get_document_regions_for_mobile(
    document_id: UUID,
    include_corrected: bool = True,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    """Get all regions for a document formatted for mobile consumption."""

    status_filter = "" if include_corrected else "AND tr.status = 'pending'"

    result = db.execute(text(f"""
        SELECT tr.id, p.page_number, tr.bbox, tr.predicted_text, 
               tr.confidence, tr.corrected_text, tr.status, tr.script_class,
               tr.is_medical_term, tr.created_at, tr.corrected_at
        FROM text_regions tr
        JOIN pages p ON tr.page_id = p.id
        WHERE p.document_id = :doc_id
        {status_filter}
        ORDER BY p.page_number, tr.reading_order, tr.created_at
    """), {"doc_id": str(document_id)})

    regions = []
    for row in result:
        regions.append({
            "server_region_id": str(row.id),
            "page_number": row.page_number,
            "bbox": row.bbox,
            "predicted_text": row.predicted_text,
            "confidence": row.confidence,
            "corrected_text": row.corrected_text,
            "status": row.status,
            "script_class": row.script_class,
            "is_medical_term": row.is_medical_term,
            "created_at": int(row.created_at.timestamp() * 1000) if row.created_at else None,
            "corrected_at": int(row.corrected_at.timestamp() * 1000) if row.corrected_at else None,
        })

    return {
        "document_id": str(document_id),
        "total_regions": len(regions),
        "pending_count": sum(1 for r in regions if r["status"] == "pending"),
        "regions": regions
    }
