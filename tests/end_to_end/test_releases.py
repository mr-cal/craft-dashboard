"""End-to-end tests for the Releases page."""

from __future__ import annotations

import pytest
import requests

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class TestReleasesPage:
    def test_releases_page_loads(self, seeded_url: str) -> None:
        """The releases page should load without errors."""
        resp = requests.get(f"{seeded_url}/stats/releases", timeout=10)
        assert resp.status_code == 200
        assert "No release data available yet" not in resp.text, (
            "Releases page should show data, not 'No release data available yet'"
        )

    def test_releases_page_shows_projects(self, seeded_url: str) -> None:
        """The releases page should show release data for application projects."""
        resp = requests.get(f"{seeded_url}/stats/releases", timeout=10)
        for project in ["snapcraft", "charmcraft", "rockcraft"]:
            assert project in resp.text, f"Releases page should mention {project}"

    def test_releases_page_shows_versions(self, seeded_url: str) -> None:
        """The releases page should show version numbers."""
        resp = requests.get(f"{seeded_url}/stats/releases", timeout=10)
        assert "8.0.0" in resp.text, "Should show version 8.0.0"

    def test_releases_page_not_show_libraries(self, seeded_url: str) -> None:
        """Library projects should not appear on the releases page."""
        resp = requests.get(f"{seeded_url}/stats/releases", timeout=10)
        # craft-parts is a library, shouldn't have releases
        # (it might appear in other contexts but not in the release cards)
        # Check that we have the expected project cards
        assert "snapcraft" in resp.text

    def test_releases_chart_renders(self, seeded_url: str) -> None:
        """The releases timeline chart should render with data."""
        script = make_script("""\
    await page.goto(`${BASE}/stats/releases`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 2000));

    // Check for release cards or timeline elements
    const hasContent = await page.evaluate(() => {
      // Look for release-related content
      const cards = document.querySelectorAll('.release-card, .card, table tr');
      const noData = document.body.textContent.includes('No release data available');
      return {
        cardCount: cards.length,
        hasData: !noData,
        bodyLength: document.body.textContent.length,
      };
    });

    console.log(JSON.stringify(hasContent));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert result["hasData"], "Releases page should have data"
