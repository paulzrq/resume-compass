"""Reload a local module when a running Streamlit process retains older code."""
import hashlib
import importlib
from pathlib import Path


def load_current_module(name):
    source = Path(__file__).parent / (name + '.py')
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    module = importlib.import_module(name)
    if getattr(module, '_resume_source_fingerprint', None) != fingerprint:
        importlib.invalidate_caches()
        module = importlib.reload(module)
        module._resume_source_fingerprint = fingerprint
    return module
