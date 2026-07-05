#!/usr/bin/env python3
"""
UMLS/SNOMED-CT Client for English Medical Terminology
"""

import os
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class UMLSConcept:
    cui: str
    name: str
    semantic_types: List[str]
    definitions: List[str]
    synonyms: List[str]
    is_finding: bool
    is_disorder: bool
    is_procedure: bool


class UMLSClient:
    API_BASE = "https://uts-ws.nlm.nih.gov/rest"
    AUTH_URL = "https://utslogin.nlm.nih.gov/cas/v1/api-key"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('UMLS_API_KEY')
        self.ticket_granting_ticket: Optional[str] = None

        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

        if self.api_key:
            self._authenticate()

    def _authenticate(self):
        try:
            response = self.session.post(self.AUTH_URL, data={'apikey': self.api_key})
            response.raise_for_status()

            from html.parser import HTMLParser
            class TGTExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.tgt = None
                def handle_starttag(self, tag, attrs):
                    if tag == 'form':
                        for attr, value in attrs:
                            if attr == 'action':
                                self.tgt = value

            extractor = TGTExtractor()
            extractor.feed(response.text)
            self.ticket_granting_ticket = extractor.tgt
            logger.info("UMLS authentication successful")
        except Exception as e:
            logger.error(f"UMLS auth failed: {e}")
            self.api_key = None

    def _get_service_ticket(self) -> Optional[str]:
        if not self.ticket_granting_ticket:
            return None
        try:
            response = self.session.post(self.ticket_granting_ticket, 
                data={'service': 'http://umlsks.nlm.nih.gov'})
            response.raise_for_status()
            return response.text.strip()
        except Exception as e:
            logger.error(f"Service ticket failed: {e}")
            return None

    def search_term(self, term: str, search_type: str = 'words') -> List[UMLSConcept]:
        if not self.api_key:
            return []

        ticket = self._get_service_ticket()
        if not ticket:
            return []

        try:
            url = f"{self.API_BASE}/search/current"
            params = {
                'ticket': ticket,
                'string': quote(term),
                'searchType': search_type,
                'returnIdType': 'concept',
                'pageNumber': 1,
                'pageSize': 25
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            results = data.get('result', {}).get('results', [])

            concepts = []
            for result in results:
                concept = self._get_concept_details(result['ui'])
                if concept:
                    concepts.append(concept)
            return concepts

        except Exception as e:
            logger.error(f"UMLS search failed: {e}")
            return []

    def _get_concept_details(self, cui: str) -> Optional[UMLSConcept]:
        ticket = self._get_service_ticket()
        if not ticket:
            return None

        try:
            url = f"{self.API_BASE}/content/current/CUI/{cui}"
            response = self.session.get(url, params={'ticket': ticket}, timeout=30)
            response.raise_for_status()

            data = response.json()
            result = data.get('result', {})

            sem_types = [st.get('name') for st in result.get('semanticTypes', [])]
            sem_type_ids = {st.get('uri', '').split('/')[-1] for st in result.get('semanticTypes', [])}

            return UMLSConcept(
                cui=cui,
                name=result.get('name', ''),
                semantic_types=sem_types,
                definitions=self._get_definitions(cui),
                synonyms=self._get_synonyms(cui),
                is_finding='T033' in sem_type_ids,
                is_disorder='T047' in sem_type_ids,
                is_procedure='T060' in sem_type_ids or 'T061' in sem_type_ids
            )
        except Exception as e:
            logger.error(f"Concept details failed: {e}")
            return None

    def _get_definitions(self, cui: str) -> List[str]:
        ticket = self._get_service_ticket()
        if not ticket:
            return []
        try:
            url = f"{self.API_BASE}/content/current/CUI/{cui}/definitions"
            response = self.session.get(url, params={'ticket': ticket}, timeout=30)
            data = response.json()
            return [r.get('value', '') for r in data.get('result', [])]
        except:
            return []

    def _get_synonyms(self, cui: str) -> List[str]:
        ticket = self._get_service_ticket()
        if not ticket:
            return []
        try:
            url = f"{self.API_BASE}/content/current/CUI/{cui}/atoms"
            response = self.session.get(url, params={'ticket': ticket, 'pageSize': 100}, timeout=30)
            data = response.json()
            return list(set(r.get('name', '') for r in data.get('result', [])))
        except:
            return []

    def validate_medical_term(self, term: str) -> Dict:
        concepts = self.search_term(term, search_type='exact')
        if not concepts:
            return {'valid': False, 'term': term, 'message': 'Not found in UMLS'}

        best = concepts[0]
        return {
            'valid': True,
            'term': term,
            'cui': best.cui,
            'preferred_name': best.name,
            'semantic_types': best.semantic_types,
            'is_disorder': best.is_disorder,
            'is_procedure': best.is_procedure,
            'definitions': best.definitions[:3],
            'synonyms': best.synonyms[:10]
        }

    def cross_language_map(self, term: str, source_lang: str = 'ENG', target_lang: str = 'ARA') -> List[Dict]:
        concepts = self.search_term(term, search_type='exact')
        mappings = []

        for concept in concepts:
            arabic_atoms = [s for s in concept.synonyms if any('\u0600' <= c <= '\u06FF' for c in s)]
            if arabic_atoms:
                mappings.append({
                    'english': concept.name,
                    'arabic': arabic_atoms[0],
                    'cui': concept.cui,
                    'semantic_types': concept.semantic_types
                })

        return mappings


_umls_client: Optional[UMLSClient] = None

def get_umls_client() -> UMLSClient:
    global _umls_client
    if _umls_client is None:
        _umls_client = UMLSClient()
    return _umls_client
