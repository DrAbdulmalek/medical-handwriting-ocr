from fastapi import APIRouter, Depends, Query, HTTPException
from app.umls_client import UMLSClient, get_umls_client

router = APIRouter(prefix="/umls", tags=["umls"])

@router.get("/search")
async def search_umls(
    term: str = Query(..., min_length=1),
    search_type: str = Query("words"),
    client: UMLSClient = Depends(get_umls_client)
):
    if not client.api_key:
        raise HTTPException(503, "UMLS not configured")

    concepts = client.search_term(term, search_type=search_type)
    return {
        "term": term,
        "results_found": len(concepts),
        "results": [{"cui": c.cui, "name": c.name, "semantic_types": c.semantic_types,
                     "is_disorder": c.is_disorder, "is_procedure": c.is_procedure} 
                    for c in concepts]
    }

@router.get("/validate")
async def validate_medical_term(
    term: str = Query(..., min_length=1),
    client: UMLSClient = Depends(get_umls_client)
):
    if not client.api_key:
        raise HTTPException(503, "UMLS not configured")
    return client.validate_medical_term(term)

@router.get("/cross-language")
async def cross_language_map(
    term: str = Query(..., min_length=1),
    client: UMLSClient = Depends(get_umls_client)
):
    if not client.api_key:
        raise HTTPException(503, "UMLS not configured")
    return {"english_term": term, "mappings": client.cross_language_map(term)}
