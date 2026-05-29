import pytest
from backend.app.dictionary_client import DictionaryManager

class TestDictionaryManager:
    def test_without_token(self):
        manager = DictionaryManager(token=None)
        assert manager.enabled == False
        status = manager.get_status()
        assert status['enabled'] == False

    def test_invalid_token_format(self):
        manager = DictionaryManager(token="invalid_token")
        assert manager.enabled == False

    def test_status_without_token(self):
        manager = DictionaryManager()
        status = manager.get_status()
        assert 'setup_instructions' in status
