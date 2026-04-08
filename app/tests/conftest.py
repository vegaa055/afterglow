import sys
import os

# When pytest is invoked from the repo root as `pytest app/tests/`,
# the app/ directory isn't on sys.path by default.
# This conftest.py lives in app/ and is auto-loaded by pytest,
# adding app/ to sys.path so all app modules are importable directly.
sys.path.insert(0, os.path.dirname(__file__))
