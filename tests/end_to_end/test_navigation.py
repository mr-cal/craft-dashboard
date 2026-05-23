"""End-to-end tests for navigation, health, and basic page rendering."""

from __future__ import annotations

import pytest
import requests

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class TestHealth:
    def test_health_endpoint(self, seeded_url: str) -> None:
        """The /health endpoint should return OK."""
        resp = requests.get(f"{seeded_url}/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"


class TestNavigation:
    def test_nav_links_present(self, seeded_url: str) -> None:
        """The navigation bar should have links to all main pages."""
        script = make_script("""\
    await page.goto(`${BASE}/`, {waitUntil: 'networkidle0', timeout: 30000});

    const links = await page.evaluate(() => {
      const navLinks = document.querySelectorAll('nav a, .navbar a, header a');
      return Array.from(navLinks).map(a => ({
        text: a.textContent.trim(),
        href: a.getAttribute('href'),
      }));
    });

    console.log(JSON.stringify({links: links}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        hrefs = [l["href"] for l in result["links"]]
        # Should have links to major sections
        assert any("/issues" in h for h in hrefs), "Should have link to Issues page"
        assert any("/stats" in h for h in hrefs), "Should have link to Stats page"

    def test_navigate_between_pages(self, seeded_url: str) -> None:
        """Navigation between pages should work without errors."""
        pages = ["/", "/issues", "/stats/trends", "/stats/releases", "/stats/dependencies"]
        for page_path in pages:
            resp = requests.get(f"{seeded_url}{page_path}", timeout=10)
            assert resp.status_code == 200, (
                f"Page {page_path} returned {resp.status_code}"
            )


class TestDashboardPage:
    def test_dashboard_loads(self, seeded_url: str) -> None:
        """The main dashboard page should load."""
        resp = requests.get(f"{seeded_url}/", timeout=10)
        assert resp.status_code == 200

    def test_dashboard_has_content(self, seeded_url: str) -> None:
        """The dashboard should show project information."""
        resp = requests.get(f"{seeded_url}/", timeout=10)
        # Dashboard should mention at least one project
        assert "snapcraft" in resp.text.lower() or "charmcraft" in resp.text.lower() or "craft" in resp.text.lower()


class TestDependenciesPage:
    def test_dependencies_page_loads(self, seeded_url: str) -> None:
        """The dependencies page should load."""
        resp = requests.get(f"{seeded_url}/stats/dependencies", timeout=10)
        assert resp.status_code == 200

    def test_dependencies_data_endpoint(self, seeded_url: str) -> None:
        """The dependencies data API should return data."""
        resp = requests.get(f"{seeded_url}/stats/dependencies/data", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestErrorPages:
    def test_404_page(self, seeded_url: str) -> None:
        """Non-existent pages should return a 404 page."""
        resp = requests.get(f"{seeded_url}/nonexistent-page-xyz", timeout=10)
        assert resp.status_code == 404


class TestTrendsDataAPI:
    def test_trends_all_data_returns_json(self, seeded_url: str) -> None:
        """The trends all-data API should return valid JSON."""
        resp = requests.get(f"{seeded_url}/stats/trends/all-data", timeout=15)
        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        assert "snapshot" in data
        assert "order" in data

    def test_trends_all_data_has_projects(self, seeded_url: str) -> None:
        """The trends data should include seeded projects."""
        resp = requests.get(f"{seeded_url}/stats/trends/all-data", timeout=15)
        data = resp.json()
        project_names = list(data["projects"].keys())
        assert "snapcraft" in project_names
        assert "charmcraft" in project_names
        assert "rockcraft" in project_names

    def test_trends_all_data_has_snapshot(self, seeded_url: str) -> None:
        """The trends data should include snapshot data for each project."""
        resp = requests.get(f"{seeded_url}/stats/trends/all-data", timeout=15)
        data = resp.json()
        for project in ["snapcraft", "charmcraft", "rockcraft"]:
            assert project in data["snapshot"], f"Snapshot missing for {project}"
            snap = data["snapshot"][project]
            assert "open_issues" in snap
            assert "internal_open_issues" in snap
            assert "bots_open_issues" in snap
            assert "bots_closed_issues_year" in snap
            assert "internal_closed_issues_year" in snap

    def test_trends_snapshot_internal_values_consistent(self, seeded_url: str) -> None:
        """Internal + external + bots should equal total for open issues."""
        resp = requests.get(f"{seeded_url}/stats/trends/all-data", timeout=15)
        data = resp.json()
        for project in ["snapcraft", "charmcraft", "rockcraft"]:
            snap = data["snapshot"][project]
            total_issues = snap["open_issues"]
            parts = snap["nm_open_issues"] + snap["internal_open_issues"] + snap["bots_open_issues"]
            assert total_issues == parts, (
                f"{project}: open_issues={total_issues} != "
                f"external({snap['nm_open_issues']}) + internal({snap['internal_open_issues']}) "
                f"+ bots({snap['bots_open_issues']}) = {parts}"
            )
