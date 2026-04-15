"""Eval test configuration.

Register the 'eval' marker so pytest doesn't warn about unknown markers.
These tests are excluded from normal test runs.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "eval: marks eval tests (requires OpenAI API)")
