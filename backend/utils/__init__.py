"""
Utils Package
=============
Re-exports from utils.py for backwards compatibility.
"""
import os
import importlib.util

# Load utils.py directly since we're now a package that shadows it
_utils_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils.py")
_spec = importlib.util.spec_from_file_location("utils_module", _utils_path)
_utils_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_utils_module)

# Re-export all functions
set_cache_collection = _utils_module.set_cache_collection
get_cached_data = _utils_module.get_cached_data
set_cached_data = _utils_module.set_cached_data
fuzzy_match_player = _utils_module.fuzzy_match_player
