from fastapi import APIRouter, Depends, Query, Header
from typing import List, Optional

from backend.app.dictionary_client import (
    DictionaryManager, verify_dictionary_access, get_dictionary_manager
)

router = APIRouter(prefix="/dictionaries", tags=["dictionaries"])

@router.get("/status")
async def dictionary_status():
    manager = get_dictionary_manager()
    return manager.get_status()

@router.get("/search", dependencies=[Depends(verify_dictionary_access)])
async def search_dictionary(
    term: str = Query(..., min_length=1),
    dictionaries: Optional[List[str]] = Query(None),
    manager: DictionaryManager = Depends(verify_dictionary_access)
):
    results = manager.search_term(term, dictionaries)
    return {
        "term": term,
        "results_found": len(results),
        "results": [{"term": r.term, "dictionary": r.dictionary, 
                     "definition": r.definition, "language": r.language} 
                    for r in results]
    }

@router.post("/validate", dependencies=[Depends(verify_dictionary_access)])
async def validate_term(
    term: str,
    manager: DictionaryManager = Depends(verify_dictionary_access)
):
    return manager.validate_medical_term(term)

@router.get("/list", dependencies=[Depends(verify_dictionary_access)])
async def list_dictionaries(
    manager: DictionaryManager = Depends(verify_dictionary_access)
):
    try:
        contents = manager.repo.get_contents(".")
        dictionaries = [{"name": c.name, "path": c.path, "type": "directory"} 
                       for c in contents if c.type == "dir"]
        return {"repository": f"{manager.REPO_OWNER}/{manager.REPO_NAME}", 
                "dictionaries": dictionaries}
    except Exception as e:
        return {"error": str(e)}
