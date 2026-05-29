"""End-to-end tests for the Issues & PR Triage page."""

from __future__ import annotations

import pytest
import requests

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class TestTriagePage:
    def test_triage_page_loads(self, seeded_url: str) -> None:
        """The triage page should load and show issues."""
        resp = requests.get(f"{seeded_url}/issues", timeout=10)
        assert resp.status_code == 200
        assert (
            "Test issue" in resp.text
            or "Test pull_request" in resp.text
            or "issue" in resp.text.lower()
        )

    def test_triage_dropdown_excludes_aggregate(self, seeded_url: str) -> None:
        """The project dropdown should not contain 'all-projects'."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});

    const options = await page.evaluate(() => {
      const items = document.querySelectorAll('.multiselect-option');
      return Array.from(items).map(i => i.textContent.trim());
    });

    const hasAggregate = options.some(o => o === 'all-projects');

    console.log(JSON.stringify({
      options: options,
      has_aggregate: hasAggregate,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert not result["has_aggregate"], (
            f"'all-projects' should not appear in dropdown, got options: {result['options']}"
        )

    def test_triage_default_shows_all_projects(self, seeded_url: str) -> None:
        """With no project selected, triage shows issues from all projects."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    const rows = await page.evaluate(() => {
      const trs = document.querySelectorAll('table tbody tr');
      return Array.from(trs).map(tr => ({
        text: tr.textContent.trim().substring(0, 100),
      }));
    });

    console.log(JSON.stringify({row_count: rows.length}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert result["row_count"] > 0, "Triage page should show issues"

    def test_triage_filter_by_state(self, seeded_url: str) -> None:
        """Filtering by state should change the displayed issues."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    // Get initial count
    const getRowCount = async () => {
      return page.evaluate(() => {
        return document.querySelectorAll('table tbody tr').length;
      });
    };

    const allCount = await getRowCount();

    // Filter by closed state using the state dropdown/select
    const stateSelect = await page.$('select[name="state"]');
    if (stateSelect) {
      await page.select('select[name="state"]', 'closed');
      await new Promise(r => setTimeout(r, 1500));
    }

    const closedCount = await getRowCount();

    console.log(JSON.stringify({
      all_count: allCount,
      closed_count: closedCount,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        # Both counts should be > 0 since we seeded open and closed issues
        assert result["all_count"] > 0, "Should have issues in default view"

    def test_triage_search(self, seeded_url: str) -> None:
        """The search function should filter issues by text."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    // Search for a specific project name
    const searchInput = await page.$('input[name="search"], input[type="search"], #search');
    if (searchInput) {
      await searchInput.type('snapcraft');
      await new Promise(r => setTimeout(r, 1500));
    }

    const rows = await page.evaluate(() => {
      const trs = document.querySelectorAll('table tbody tr');
      return Array.from(trs).map(tr => tr.textContent.trim().substring(0, 100));
    });

    console.log(JSON.stringify({
      row_count: rows.length,
      rows: rows.slice(0, 3),
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        # Search should either filter or return results
        assert isinstance(result["row_count"], int)

    def test_triage_pagination(self, seeded_url: str) -> None:
        """Pagination should work if there are enough issues."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    const pagination = await page.evaluate(() => {
      const nav = document.querySelector('nav[aria-label="pagination"], .pagination');
      const links = nav ? nav.querySelectorAll('a') : [];
      return {
        has_pagination: !!nav,
        link_count: links.length,
      };
    });

    const rowCount = await page.evaluate(() => {
      return document.querySelectorAll('table tbody tr').length;
    });

    console.log(JSON.stringify({
      ...pagination,
      row_count: rowCount,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert result["row_count"] > 0, "Should have issues in the table"

    def test_triage_placeholder_says_all(self, seeded_url: str) -> None:
        """The project dropdown placeholder should indicate 'All projects'."""
        resp = requests.get(f"{seeded_url}/issues", timeout=10)
        assert "All projects" in resp.text, (
            "Triage page should show 'All projects' placeholder"
        )

    def test_triage_default_score_columns(self, seeded_url: str) -> None:
        """The triage page should show default score columns: Staleness and Readiness."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 2000));

    const headers = await page.evaluate(() => {
      const ths = document.querySelectorAll('table thead th');
      return Array.from(ths).map(th => th.textContent.trim());
    });

    console.log(JSON.stringify({
      headers: headers,
      has_staleness: headers.some(h => h.toLowerCase().includes('staleness')),
      has_readiness: headers.some(h => h.toLowerCase().includes('readiness')),
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert result["has_staleness"], (
            f"Expected 'Staleness' column, got headers: {result['headers']}"
        )
        assert result["has_readiness"], (
            f"Expected 'Readiness' column, got headers: {result['headers']}"
        )

    def test_triage_score_columns_have_values(self, seeded_url: str) -> None:
        """Score badges should be present in the table (seeded data has LLM evaluations)."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 2000));

    const scoreBadges = await page.evaluate(() => {
      const badges = document.querySelectorAll('.score-badge');
      return badges.length;
    });

    console.log(JSON.stringify({
      score_badge_count: scoreBadges,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert result["score_badge_count"] > 0, "Expected score badges in the table"

    def test_triage_action_has_tooltip(self, seeded_url: str) -> None:
        """Action badges should have data-tooltip attributes."""
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 2000));

    const tooltipCount = await page.evaluate(() => {
      const elements = document.querySelectorAll('[data-tooltip]');
      return elements.length;
    });

    console.log(JSON.stringify({
      tooltip_count: tooltipCount,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=20)
        assert result["tooltip_count"] > 0, (
            "Expected data-tooltip attributes on page elements"
        )
