"""End-to-end tests for the Engagement (forum activity) page.

Mirrors the structure of test_trends.py: verifies charts render with real
data for each tracked forum, the loading spinner disappears, category
checkboxes toggle chart datasets, and default categories are pre-checked
on load.
"""

from __future__ import annotations

import pytest

from tests.end_to_end.helpers import make_script, run_puppeteer
from tests.end_to_end.seed_data import FORUM_CATEGORIES, FORUMS

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


# ---------------------------------------------------------------------------
# Tests: Loading spinner
# ---------------------------------------------------------------------------
class TestSpinner:
    def test_spinner_disappears_after_load(self, seeded_url: str) -> None:
        """The loading spinner should disappear once charts are initialized."""
        script = make_script("""\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});
    console.log(JSON.stringify({"spinner_hidden": true}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert result["spinner_hidden"] is True


# ---------------------------------------------------------------------------
# Tests: chart rendering for all forums
# ---------------------------------------------------------------------------
_FETCH_ALL_CHARTS_SCRIPT = """\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    const result = {};
    for (const forum of FORUM_NAMES) {
      const canvas = await page.$(`#engagement-${forum}-chart`);
      if (!canvas) { result[forum] = null; continue; }
      result[forum] = await page.evaluate((c) => {
        const chart = Chart.getChart(c);
        if (!chart) return null;
        return {
          type: chart.config.type,
          labels: chart.data.labels,
          datasets: chart.data.datasets.map(d => ({label: d.label, data: d.data})),
        };
      }, canvas);
    }
    console.log(JSON.stringify(result));
"""


def _get_all_charts(base_url: str) -> dict:
    forum_names_json = "[" + ", ".join(f"'{f}'" for f in FORUMS) + "]"
    script = make_script(
        _FETCH_ALL_CHARTS_SCRIPT.replace("FORUM_NAMES", forum_names_json)
    )
    return run_puppeteer(script, base_url=base_url, timeout=45)


class TestChartRendering:
    def test_all_forum_charts_render_with_data(self, seeded_url: str) -> None:
        """Each configured forum should have a line chart with data."""
        data = _get_all_charts(seeded_url)
        for forum in FORUMS:
            chart = data.get(forum)
            assert chart is not None, f"{forum} chart missing"
            assert chart["type"] == "line", f"{forum} chart should be line type"
            assert len(chart["labels"]) > 0, f"{forum} chart has no month labels"
            assert len(chart["datasets"]) > 0, f"{forum} chart has no datasets"

    def test_default_categories_are_shown_by_default(self, seeded_url: str) -> None:
        """On first load, only 'all categories' (plus any per-forum default
        categories) should be visible as datasets — other categories start
        unchecked.
        """
        data = _get_all_charts(seeded_url)
        for forum in FORUMS:
            labels = {ds["label"] for ds in data[forum]["datasets"]}
            assert "all categories" in labels, (
                f"{forum} should show 'all categories' by default"
            )
            # Non-default categories should not be shown yet.
            assert "features" not in labels, (
                f"{forum} should not default-show 'features'"
            )
            assert "docs" not in labels, f"{forum} should not default-show 'docs'"


# ---------------------------------------------------------------------------
# Tests: category checkbox toggling
# ---------------------------------------------------------------------------
class TestCategoryToggling:
    def test_toggling_all_categories_checkbox_removes_dataset(
        self, seeded_url: str
    ) -> None:
        """Unchecking 'all categories' should remove the 'all categories' dataset."""
        script = make_script("""\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    const getLabels = async () => page.evaluate(() => {
      const chart = Chart.getChart(document.getElementById('engagement-snapcraft-chart'));
      return chart ? chart.data.datasets.map(d => d.label) : [];
    });

    const before = await getLabels();

    const allCategoriesCb = await page.$('#engagement-snapcraft-all-categories');
    await allCategoriesCb.click();
    await new Promise(r => setTimeout(r, 500));

    const after = await getLabels();

    console.log(JSON.stringify({before, after}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert "all categories" in result["before"]
        assert "all categories" not in result["after"]

    def test_checking_a_category_adds_a_dataset(self, seeded_url: str) -> None:
        """Checking an individual (non-default) category checkbox should add
        its dataset to the chart.
        """
        script = make_script("""\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    const getLabels = async () => page.evaluate(() => {
      const chart = Chart.getChart(document.getElementById('engagement-snapcraft-chart'));
      return chart ? chart.data.datasets.map(d => d.label) : [];
    });

    const before = await getLabels();

    const categoryCb = await page.$('#engagement-snapcraft-category-features');
    await categoryCb.click();
    await new Promise(r => setTimeout(r, 500));

    const after = await getLabels();

    console.log(JSON.stringify({before, after}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert "features" not in result["before"]
        assert "features" in result["after"]


# ---------------------------------------------------------------------------
# Tests: category checkboxes list every discovered category
# ---------------------------------------------------------------------------
class TestCategoryCheckboxes:
    def test_all_forum_categories_have_checkboxes(self, seeded_url: str) -> None:
        """Every category cached in forum_backfill_state should render as a
        checkbox."""
        script = make_script("""\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    const ids = CATEGORY_IDS;
    const result = {};
    for (const id of ids) {
      result[id] = !!(await page.$(`#${id}`));
    }
    console.log(JSON.stringify(result));
""")
        category_ids = [
            f"engagement-snapcraft-category-{category}" for category in FORUM_CATEGORIES
        ]
        category_ids_json = "[" + ", ".join(f"'{c}'" for c in category_ids) + "]"
        script = script.replace("CATEGORY_IDS", category_ids_json)
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        for category_id in category_ids:
            assert result[category_id] is True, f"checkbox {category_id} should exist"


# ---------------------------------------------------------------------------
# Tests: nav link presence
# ---------------------------------------------------------------------------
class TestNavigation:
    def test_engagement_link_present_in_nav(self, seeded_url: str) -> None:
        """The Engagement page should be reachable from the main nav."""
        script = make_script("""\
    await page.goto(`${BASE}/`, {waitUntil: 'networkidle0', timeout: 30000});
    const link = await page.$('a[href="/engagement/forums"]');
    console.log(JSON.stringify({has_link: !!link}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert result["has_link"] is True
