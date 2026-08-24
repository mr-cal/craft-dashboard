"""End-to-end tests for the Engagement (forum activity) page.

Mirrors the structure of test_trends.py: verifies charts render with real
data for each tracked forum, the loading spinner disappears, tag checkboxes
toggle chart datasets, and default tags are pre-checked on load.
"""

from __future__ import annotations

import pytest

from tests.end_to_end.helpers import make_script, run_puppeteer
from tests.end_to_end.seed_data import FORUM_TAGS, FORUMS

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

    def test_default_tags_are_shown_by_default(self, seeded_url: str) -> None:
        """On first load, only default tags ('bug', 'question') plus 'all tags'
        should be visible as datasets — other tags start unchecked.
        """
        data = _get_all_charts(seeded_url)
        for forum in FORUMS:
            labels = {ds["label"] for ds in data[forum]["datasets"]}
            assert "all tags" in labels, f"{forum} should show 'all tags' by default"
            # Non-default tags (feature, docs) should not be shown yet.
            assert "feature" not in labels, f"{forum} should not default-show 'feature'"
            assert "docs" not in labels, f"{forum} should not default-show 'docs'"


# ---------------------------------------------------------------------------
# Tests: tag checkbox toggling
# ---------------------------------------------------------------------------
class TestTagToggling:
    def test_toggling_all_tags_checkbox_removes_dataset(self, seeded_url: str) -> None:
        """Unchecking 'all tags' should remove the 'all tags' dataset."""
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

    const allTagsCb = await page.$('#engagement-snapcraft-all-tags');
    await allTagsCb.click();
    await new Promise(r => setTimeout(r, 500));

    const after = await getLabels();

    console.log(JSON.stringify({before, after}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert "all tags" in result["before"]
        assert "all tags" not in result["after"]

    def test_checking_a_tag_adds_a_dataset(self, seeded_url: str) -> None:
        """Checking an individual (non-default) tag checkbox should add its
        dataset to the chart.
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

    const tagCb = await page.$('#engagement-snapcraft-tag-feature');
    await tagCb.click();
    await new Promise(r => setTimeout(r, 500));

    const after = await getLabels();

    console.log(JSON.stringify({before, after}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert "feature" not in result["before"]
        assert "feature" in result["after"]


# ---------------------------------------------------------------------------
# Tests: tag checkboxes list every discovered tag
# ---------------------------------------------------------------------------
class TestTagCheckboxes:
    def test_all_forum_tags_have_checkboxes(self, seeded_url: str) -> None:
        """Every tag cached in forum_tags should render as a checkbox."""
        script = make_script("""\
    await page.goto(`${BASE}/engagement/forums`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('engagement-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    const ids = TAG_IDS;
    const result = {};
    for (const id of ids) {
      result[id] = !!(await page.$(`#${id}`));
    }
    console.log(JSON.stringify(result));
""")
        tag_ids = [f"engagement-snapcraft-tag-{tag}" for tag in FORUM_TAGS]
        tag_ids_json = "[" + ", ".join(f"'{t}'" for t in tag_ids) + "]"
        script = script.replace("TAG_IDS", tag_ids_json)
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        for tag_id in tag_ids:
            assert result[tag_id] is True, f"checkbox {tag_id} should exist"


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
