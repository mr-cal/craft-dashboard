"""CI log/annotation collector subpackage: parsing, not collection.

``parsers.py`` holds pure functions only (no network, no DB session) so they
stay trivially unit-testable against recorded log/annotation samples. The
collector itself (``collectors/ci_collector.py``, one level up) is what
calls these against live GitHub API responses.
"""
