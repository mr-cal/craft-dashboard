"""Unit tests for the _tag_on_main helper in the GitHub collector."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from craft_dashboard.collectors.github import _tag_on_main


def _make_compare(ahead_by: int = 5, behind_by: int = 0) -> SimpleNamespace:
    return SimpleNamespace(ahead_by=ahead_by, behind_by=behind_by)


class TestTagOnMain:
    def test_returns_true_when_behind_by_is_zero(self) -> None:
        repo = MagicMock()
        repo.compare.return_value = _make_compare(ahead_by=3, behind_by=0)

        assert _tag_on_main(repo, "4.1.0") is True
        repo.compare.assert_called_once_with("4.1.0", "main")

    def test_returns_false_when_behind_by_is_nonzero(self) -> None:
        repo = MagicMock()
        repo.compare.return_value = _make_compare(ahead_by=0, behind_by=2)

        assert _tag_on_main(repo, "4.1.0") is False

    def test_returns_false_on_exception(self) -> None:
        repo = MagicMock()
        repo.compare.side_effect = Exception("API error")

        assert _tag_on_main(repo, "4.1.0") is False
