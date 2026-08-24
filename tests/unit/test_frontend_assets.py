"""Tests for frontend asset configuration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestFrontendAssets:
    def test_trends_js_supports_dark_mode_chart_updates(self) -> None:
        """The trends script adapts chart colors when the theme changes,
        via the shared chart-common.js module it imports."""
        trends_contents = (ROOT / "craft_dashboard/static/js/trends.js").read_text()
        common_contents = (
            ROOT / "craft_dashboard/static/js/chart-common.js"
        ).read_text()

        assert "chart-common.js" in trends_contents
        assert (
            'document.documentElement.classList.contains("is-dark-theme")'
            in common_contents
        )
        assert "Chart.defaults.color" in common_contents
        assert "Chart.defaults.borderColor" in common_contents
        assert "MutationObserver" in common_contents
        assert "dataset.theme" in common_contents

    def test_custom_css_includes_dark_mode_overrides(self) -> None:
        """The custom stylesheet overrides Vanilla defaults for dark mode."""
        contents = (ROOT / "craft_dashboard/static/css/custom.css").read_text()

        assert "html.is-dark-theme .p-tabs__link" in contents
        assert "html.is-dark-theme .p-tabs__link:hover" in contents
        assert 'table[role="grid"] col.col-age       { width: 60px; }' in contents
        assert "html.is-dark-theme th" in contents
        assert "html.is-dark-theme td" in contents
        assert "html.is-dark-theme input" in contents
        assert "html.is-dark-theme select" in contents
        assert "html.is-dark-theme a" in contents
        assert "html.is-dark-theme h1" in contents
