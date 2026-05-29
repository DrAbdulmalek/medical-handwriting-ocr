#!/usr/bin/env python3
"""
Arabic Dictionaries Client
Optional integration with DrAbdulmalek/arabic-dictionaries-collection
Requires valid GitHub token for access.
"""

import os
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

import requests
from github import Github
from github.GithubException import GithubException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DictionaryEntry:
    """Single dictionary entry"""
    term: str
    dictionary: str
    definition: Optional[str]
    source: str
    language: str


class DictionaryManager:
    """
    Manages access to Arabic dictionaries repository.
    Token is optional - system works without it but with limited features.
    """

    REPO_OWNER = "DrAbdulmalek"
    REPO_NAME = "arabic-dictionaries-collection"
    CACHE_TTL_HOURS = 24

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv('DICTIONARY_REPO_TOKEN')
        self.enabled = self._validate_token()

        self.github: Optional[Github] = None
        self.repo = None
        self._cache: Dict[str, Dict] = {}
        self._cache_dir = Path("./data/dictionaries")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        if self.enabled:
            self._initialize_github()

        self._load_local_cache()

        logger.info(f"DictionaryManager: enabled={self.enabled}")

    def _validate_token(self) -> bool:
        """Validate token format"""
        if not self.token:
            logger.info("No dictionary token provided - running without dictionary features")
            return False

        if not self.token.startswith('ghp_'):
            logger.warning("Invalid token format. Expected ghp_ prefix.")
            return False

        if len(self.token) < 20:
            logger.warning("Token appears too short.")
            return False

        return True

    def _initialize_github(self):
        """Initialize GitHub client"""
        try:
            self.github = Github(self.token)
            self.repo = self.github.get_repo(f"{self.REPO_OWNER}/{self.REPO_NAME}")
            logger.info(f"Connected to dictionary repository")
        except GithubException as e:
            logger.error(f"GitHub connection failed: {e}")
            self.enabled = False

    def _load_local_cache(self):
        """Load cached data"""
        cache_file = self._cache_dir / "cache_metadata.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                last_update = datetime.fromisoformat(metadata.get('last_update', '2000-01-01'))
                if datetime.now() - last_update < timedelta(hours=self.CACHE_TTL_HOURS):
                    self._cache = metadata.get('entries', {})
                    logger.info(f"Loaded {len(self._cache)} cached entries")
            except Exception as e:
                logger.warning(f"Cache load failed: {e}")

    def _save_local_cache(self):
        """Save cache"""
        cache_file = self._cache_dir / "cache_metadata.json"
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'last_update': datetime.now().isoformat(),
                    'entry_count': len(self._cache),
                    'entries': self._cache
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def get_status(self) -> Dict:
        """Get integration status"""
        if not self.enabled:
            return {
                'enabled': False,
                'reason': 'No valid token',
                'setup_instructions': 'Set DICTIONARY_REPO_TOKEN or pass token header',
                'features_limited': True
            }

        try:
            contents = list(self.repo.get_contents("."))
            dictionaries = [c.name for c in contents if c.type == 'dir']

            return {
                'enabled': True,
                'repository': self.repo.full_name,
                'dictionaries_available': len(dictionaries),
                'dictionary_names': dictionaries,
                'cached_entries': len(self._cache),
                'last_sync': self._get_last_sync_time(),
                'token_valid': True
            }
        except Exception as e:
            return {
                'enabled': False,
                'error': str(e),
                'token_valid': False
            }

    def search_term(self, term: str, dictionaries: Optional[List[str]] = None) -> List[DictionaryEntry]:
        """Search for term"""
        if not self.enabled:
            return []

        cache_key = hashlib.md5(term.encode()).hexdigest()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if datetime.now() - datetime.fromisoformat(cached['timestamp']) < timedelta(hours=self.CACHE_TTL_HOURS):
                return [DictionaryEntry(**e) for e in cached['results']]

        results = []
        try:
            contents = self.repo.get_contents(".")
            for content in contents:
                if content.type != 'dir':
                    continue
                if dictionaries and content.name not in dictionaries:
                    continue

                dict_results = self._search_in_dictionary(content.path, term)
                results.extend(dict_results)

            self._cache[cache_key] = {
                'timestamp': datetime.now().isoformat(),
                'results': [self._entry_to_dict(e) for e in results]
            }
            self._save_local_cache()

        except Exception as e:
            logger.error(f"Search failed: {e}")

        return results

    def _search_in_dictionary(self, dict_path: str, term: str) -> List[DictionaryEntry]:
        """Search within dictionary"""
        results = []
        try:
            contents = self.repo.get_contents(dict_path)
            for file in contents:
                if file.type != 'file' or not file.name.endswith('.json'):
                    continue

                content = self._download_file(file.download_url)
                if content:
                    entries = self._search_in_content(content, term, dict_path)
                    results.extend(entries)
        except Exception as e:
            logger.warning(f"Search failed for {dict_path}: {e}")

        return results

    def _download_file(self, url: str) -> Optional[Dict]:
        """Download JSON file"""
        try:
            headers = {'Authorization': f'token {self.token}'}
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Download failed: {e}")
            return None

    def _search_in_content(self, content, term: str, source: str) -> List[DictionaryEntry]:
        """Search in content"""
        results = []
        if isinstance(content, list):
            for entry in content:
                if self._term_matches(entry, term):
                    results.append(self._create_entry(entry, source))
        elif isinstance(content, dict):
            for key, value in content.items():
                if self._normalize(key) == self._normalize(term):
                    results.append(DictionaryEntry(
                        term=key, dictionary=source,
                        definition=str(value) if not isinstance(value, dict) else json.dumps(value, ensure_ascii=False),
                        source=source, language='ar'
                    ))
        return results

    def _term_matches(self, entry: Dict, term: str) -> bool:
        """Check match"""
        return ('term' in entry and self._normalize(entry['term']) == self._normalize(term)) or \
               ('word' in entry and self._normalize(entry['word']) == self._normalize(term))

    def _normalize(self, text: str) -> str:
        """Normalize Arabic text"""
        normalized = text.strip()
        normalized = normalized.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
        normalized = normalized.replace('ة', 'ه')
        return normalized

    def _create_entry(self, data: Dict, source: str) -> DictionaryEntry:
        """Create entry"""
        return DictionaryEntry(
            term=data.get('term', data.get('word', '')),
            dictionary=source,
            definition=data.get('definition', data.get('meaning')),
            source=source, language=data.get('language', 'ar')
        )

    def _entry_to_dict(self, entry: DictionaryEntry) -> Dict:
        """Convert to dict"""
        return {
            'term': entry.term, 'dictionary': entry.dictionary,
            'definition': entry.definition, 'source': entry.source,
            'language': entry.language
        }

    def validate_medical_term(self, term: str) -> Dict:
        """Validate medical term"""
        if not self.enabled:
            return {'validated': False, 'reason': 'dictionaries_disabled'}

        results = self.search_term(term, dictionaries=['medical', 'anatomy'])
        return {
            'validated': len(results) > 0,
            'term': term,
            'matches_found': len(results),
            'dictionaries_checked': ['medical', 'anatomy'],
            'suggestions': [r.term for r in results[:5]]
        }

    def _get_last_sync_time(self) -> Optional[str]:
        """Get last sync"""
        cache_file = self._cache_dir / "cache_metadata.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f).get('last_update')
            except:
                pass
        return None


# Singleton
_dictionary_manager: Optional[DictionaryManager] = None

def get_dictionary_manager() -> DictionaryManager:
    global _dictionary_manager
    if _dictionary_manager is None:
        _dictionary_manager = DictionaryManager()
    return _dictionary_manager


# FastAPI dependency
from fastapi import Depends, HTTPException, Header

async def verify_dictionary_access(
    x_dictionary_token: Optional[str] = Header(None)
) -> DictionaryManager:
    """Verify dictionary access"""
    token = x_dictionary_token or os.getenv('DICTIONARY_REPO_TOKEN')
    manager = DictionaryManager(token=token)

    if not manager.enabled:
        raise HTTPException(
            status_code=403,
            detail={
                'error': 'Dictionary access not configured',
                'message': 'Valid DICTIONARY_REPO_TOKEN required',
                'setup_url': 'https://github.com/settings/tokens'
            }
        )

    return manager
