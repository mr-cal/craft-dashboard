"""Exhaustive end-to-end tests for the Issues & PR Trends page.

Tests all 7 author-group view combinations, snapshot chart view switching,
date range filtering, project toggling, spinner behavior, and median age
correctness.
"""

from __future__ import annotations

import pytest
import requests

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


# ---------------------------------------------------------------------------
# Helper: fetch all chart data for a given view configuration
# ---------------------------------------------------------------------------
_FETCH_CHART_DATA_SCRIPT = """\
    await page.goto(`${BASE}/stats/trends`, {waitUntil: 'networkidle0', timeout: 30000});

    // Wait for spinner to disappear
    await page.waitForFunction(() => {
      const el = document.getElementById('trends-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    // Set view checkboxes
    const views = VIEWS_CONFIG;
    const maintainers = await page.$('#view-maintainers');
    const contributors = await page.$('#view-contributors');
    const bots = await page.$('#view-bots');

    const mChecked = await page.evaluate(el => el.checked, maintainers);
    const cChecked = await page.evaluate(el => el.checked, contributors);
    const bChecked = await page.evaluate(el => el.checked, bots);

    if (views.maintainers !== mChecked) await maintainers.click();
    if (views.contributors !== cChecked) await contributors.click();
    if (views.bots !== bChecked) await bots.click();

    await new Promise(r => setTimeout(r, 800));

    // Collect data from all charts
    const result = {};
    for (const id of ['issues-chart', 'median-age-chart', 'closed-chart',
                       'snapshot-open-chart', 'snapshot-age-chart', 'snapshot-closed-chart']) {
      const canvas = await page.$(`#${id}`);
      if (!canvas) { result[id] = null; continue; }
      result[id] = await page.evaluate((c) => {
        const chart = Chart.getChart(c);
        if (!chart) return null;
        return {
          type: chart.config.type,
          labels: chart.data.labels,
          datasets: chart.data.datasets.map(d => ({
            label: d.label,
            data: d.data,
            hidden: d.hidden || false,
          })),
        };
      }, canvas);
    }

    console.log(JSON.stringify(result));
"""


def _get_chart_data(
    base_url: str, *, maintainers: bool, contributors: bool, bots: bool
) -> dict:
    """Fetch chart data with a specific view configuration."""
    views_json = (
        f"{{maintainers: {str(maintainers).lower()}, "
        f"contributors: {str(contributors).lower()}, "
        f"bots: {str(bots).lower()}}}"
    )
    script = make_script(_FETCH_CHART_DATA_SCRIPT.replace("VIEWS_CONFIG", views_json))
    return run_puppeteer(script, base_url=base_url, timeout=45)


# ---------------------------------------------------------------------------
# Tests: Loading spinner
# ---------------------------------------------------------------------------
class TestSpinner:
    def test_spinner_disappears_after_load(self, seeded_url: str) -> None:
        """The loading spinner should disappear once charts are initialized."""
        script = make_script("""\
    await page.goto(`${BASE}/stats/trends`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('trends-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});
    console.log(JSON.stringify({"spinner_hidden": true}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert result["spinner_hidden"] is True


# ---------------------------------------------------------------------------
# Tests: All 7 view combinations
# ---------------------------------------------------------------------------
_VIEW_COMBOS = [
    (True, True, True, "all-three"),
    (True, True, False, "maintainers+contributors"),
    (True, False, True, "maintainers+bots"),
    (False, True, True, "contributors+bots"),
    (True, False, False, "maintainers-only"),
    (False, True, False, "contributors-only"),
    (False, False, True, "bots-only"),
]


class TestViewCombinations:
    @pytest.mark.parametrize(
        ("maintainers", "contributors", "bots", "desc"),
        _VIEW_COMBOS,
        ids=[c[3] for c in _VIEW_COMBOS],
    )
    def test_charts_render_for_view(
        self,
        seeded_url: str,
        maintainers: bool,
        contributors: bool,
        bots: bool,
        desc: str,
    ) -> None:
        """Each view combination should render all charts with data."""
        data = _get_chart_data(
            seeded_url,
            maintainers=maintainers,
            contributors=contributors,
            bots=bots,
        )
        # All 6 charts should exist
        for chart_id in [
            "issues-chart",
            "median-age-chart",
            "closed-chart",
            "snapshot-open-chart",
            "snapshot-age-chart",
            "snapshot-closed-chart",
        ]:
            assert data.get(chart_id) is not None, f"{chart_id} missing for view {desc}"

        # Line charts should have data points
        for chart_id in ["issues-chart", "median-age-chart"]:
            chart = data[chart_id]
            assert len(chart["labels"]) > 0, f"{chart_id} has no labels"
            assert len(chart["datasets"]) > 0, f"{chart_id} has no datasets"

        # Snapshot charts should have data
        for chart_id in [
            "snapshot-open-chart",
            "snapshot-age-chart",
            "snapshot-closed-chart",
        ]:
            chart = data[chart_id]
            assert len(chart["labels"]) > 0, f"{chart_id} has no labels"
            assert len(chart["datasets"]) > 0, f"{chart_id} has no datasets"

    def test_snapshot_values_differ_between_all_and_bots(self, seeded_url: str) -> None:
        """Snapshot 'Open Issues & PRs' must show different values for all vs bots."""
        all_data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        bots_data = _get_chart_data(
            seeded_url, maintainers=False, contributors=False, bots=True
        )

        all_issues = all_data["snapshot-open-chart"]["datasets"][0]["data"]
        bots_issues = bots_data["snapshot-open-chart"]["datasets"][0]["data"]

        assert all_issues != bots_issues, (
            f"Snapshot open issues should differ between all-3 and bots-only: "
            f"all={all_issues}, bots={bots_issues}"
        )

    def test_snapshot_values_differ_between_all_and_internal(
        self, seeded_url: str
    ) -> None:
        """Snapshot 'Open Issues & PRs' must show different values for all vs internal."""
        all_data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        internal_data = _get_chart_data(
            seeded_url, maintainers=True, contributors=False, bots=False
        )

        all_issues = all_data["snapshot-open-chart"]["datasets"][0]["data"]
        internal_issues = internal_data["snapshot-open-chart"]["datasets"][0]["data"]

        assert all_issues != internal_issues, (
            f"Snapshot open issues should differ between all-3 and internal: "
            f"all={all_issues}, internal={internal_issues}"
        )

    def test_none_view_shows_empty_charts(self, seeded_url: str) -> None:
        """When no view checkboxes are selected, charts should be empty."""
        data = _get_chart_data(
            seeded_url, maintainers=False, contributors=False, bots=False
        )
        # Line charts should have no datasets or empty data
        for chart_id in ["issues-chart", "median-age-chart", "closed-chart"]:
            chart = data[chart_id]
            total_points = sum(
                len([p for p in ds["data"] if p is not None])
                for ds in chart["datasets"]
            )
            assert total_points == 0, (
                f"{chart_id} should be empty with no views selected, "
                f"but has {total_points} data points"
            )


# ---------------------------------------------------------------------------
# Tests: Snapshot chart closed-year view switching
# ---------------------------------------------------------------------------
class TestSnapshotClosedChart:
    def test_closed_chart_differs_between_views(self, seeded_url: str) -> None:
        """The closed-year snapshot chart should show different values per view."""
        all_data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        external_data = _get_chart_data(
            seeded_url, maintainers=False, contributors=True, bots=False
        )

        all_closed = all_data["snapshot-closed-chart"]["datasets"][0]["data"]
        ext_closed = external_data["snapshot-closed-chart"]["datasets"][0]["data"]

        assert all_closed != ext_closed, (
            f"Closed chart should differ between all and external: "
            f"all={all_closed}, external={ext_closed}"
        )


# ---------------------------------------------------------------------------
# Tests: Project toggling
# ---------------------------------------------------------------------------
class TestProjectToggling:
    def test_toggling_projects_changes_line_charts(self, seeded_url: str) -> None:
        """Checking/unchecking project checkboxes should add/remove datasets."""
        script = make_script("""\
    await page.goto(`${BASE}/stats/trends`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('trends-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    // Initially all-projects is checked. Count datasets.
    const getDatasetCount = async (chartId) => {
      return page.evaluate((id) => {
        const chart = Chart.getChart(document.getElementById(id));
        return chart ? chart.data.datasets.filter(d => !d.hidden).length : 0;
      }, chartId);
    };

    const initial = await getDatasetCount('issues-chart');

    // Check snapcraft
    const snapcraft = await page.$('#open-issues-snapcraft');
    if (snapcraft) {
      await snapcraft.click();
      await new Promise(r => setTimeout(r, 500));
    }

    const afterToggle = await getDatasetCount('issues-chart');

    console.log(JSON.stringify({
      initial_datasets: initial,
      after_toggle_datasets: afterToggle,
      changed: initial !== afterToggle,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        assert result["changed"], (
            f"Expected dataset count to change after toggling project: "
            f"initial={result['initial_datasets']}, after={result['after_toggle_datasets']}"
        )


# ---------------------------------------------------------------------------
# Tests: Date range filtering
# ---------------------------------------------------------------------------
class TestDateRange:
    def test_date_range_limits_data(self, seeded_url: str) -> None:
        """Changing the date range should affect the number of data points."""
        script = make_script("""\
    await page.goto(`${BASE}/stats/trends`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('trends-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    // Get initial label count
    const getLabels = async () => {
      return page.evaluate(() => {
        const chart = Chart.getChart(document.getElementById('issues-chart'));
        return chart ? chart.data.labels.length : 0;
      });
    };

    const initial = await getLabels();

    // Set a narrower date range
    const startInput = await page.$('#date-start');
    const endInput = await page.$('#date-end');
    if (startInput && endInput) {
      await page.evaluate((el) => { el.value = '2024-06-01'; el.dispatchEvent(new Event('change')); }, startInput);
      await page.evaluate((el) => { el.value = '2024-06-15'; el.dispatchEvent(new Event('change')); }, endInput);
      await new Promise(r => setTimeout(r, 800));
    }

    const filtered = await getLabels();

    console.log(JSON.stringify({
      initial_labels: initial,
      filtered_labels: filtered,
      reduced: filtered < initial || initial === 0,
    }));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        if result["initial_labels"] > 0:
            assert result["reduced"], (
                f"Date filtering should reduce data points: "
                f"initial={result['initial_labels']}, filtered={result['filtered_labels']}"
            )


# ---------------------------------------------------------------------------
# Tests: Median age correctness
# ---------------------------------------------------------------------------
class TestMedianAge:
    def test_median_age_values_are_positive(self, seeded_url: str) -> None:
        """Median age chart should show positive values, not zeros or negatives."""
        data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        chart = data["median-age-chart"]
        # Get the first visible dataset's data
        for ds in chart["datasets"]:
            points = [p for p in ds["data"] if p is not None]
            if points:
                assert all(p >= 0 for p in points), (
                    f"Median age has negative values in dataset '{ds['label']}': "
                    f"{[p for p in points if p < 0]}"
                )
                assert any(p > 0 for p in points), (
                    f"Median age is all zeros in dataset '{ds['label']}'"
                )

    def test_median_age_no_artificial_drops(self, seeded_url: str) -> None:
        """Median age should not have sudden large drops (>50%) between adjacent points."""
        data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        chart = data["median-age-chart"]
        for ds in chart["datasets"]:
            points = [p for p in ds["data"] if p is not None and p > 0]
            for i in range(1, len(points)):
                if points[i - 1] > 0:
                    drop_pct = (points[i - 1] - points[i]) / points[i - 1]
                    assert drop_pct < 0.5, (
                        f"Median age dropped >50% in dataset '{ds['label']}' "
                        f"at index {i}: {points[i - 1]} -> {points[i]} ({drop_pct:.0%})"
                    )


# ---------------------------------------------------------------------------
# Tests: Chart type verification
# ---------------------------------------------------------------------------
class TestChartTypes:
    def test_line_charts_are_line_type(self, seeded_url: str) -> None:
        """Trend line charts should be line type."""
        data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        for chart_id in ["issues-chart", "median-age-chart", "closed-chart"]:
            assert data[chart_id]["type"] == "line", (
                f"{chart_id} should be line chart, got {data[chart_id]['type']}"
            )

    def test_snapshot_charts_are_bar_type(self, seeded_url: str) -> None:
        """Snapshot charts should be bar type."""
        data = _get_chart_data(
            seeded_url, maintainers=True, contributors=True, bots=True
        )
        for chart_id in [
            "snapshot-open-chart",
            "snapshot-age-chart",
            "snapshot-closed-chart",
        ]:
            assert data[chart_id]["type"] == "bar", (
                f"{chart_id} should be bar chart, got {data[chart_id]['type']}"
            )


# ---------------------------------------------------------------------------
# Tests: Data alignment
# ---------------------------------------------------------------------------
class TestDataAlignment:
    def test_all_projects_dates_are_superset(self, seeded_url: str) -> None:
        """When multiple projects are selected, dates should be unified."""
        script = make_script("""\
    await page.goto(`${BASE}/stats/trends`, {waitUntil: 'networkidle0', timeout: 30000});
    await page.waitForFunction(() => {
      const el = document.getElementById('trends-loading');
      return !el || el.style.display === 'none';
    }, {timeout: 15000});

    // Check snapcraft and charmcraft
    const snap = await page.$('#open-issues-snapcraft');
    const charm = await page.$('#open-issues-charmcraft');
    if (snap) await snap.click();
    if (charm) await charm.click();
    await new Promise(r => setTimeout(r, 800));

    const chartData = await page.evaluate(() => {
      const chart = Chart.getChart(document.getElementById('issues-chart'));
      if (!chart) return null;
      return {
        labels: chart.data.labels,
        datasets: chart.data.datasets.map(d => ({
          label: d.label,
          dataLength: d.data.length,
        })),
      };
    });

    console.log(JSON.stringify(chartData));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)
        if result and result.get("datasets"):
            # All datasets should have same length as labels
            label_count = len(result["labels"])
            for ds in result["datasets"]:
                assert ds["dataLength"] == label_count, (
                    f"Dataset '{ds['label']}' has {ds['dataLength']} points "
                    f"but chart has {label_count} labels"
                )


# ---------------------------------------------------------------------------
# Tests: Cache busting
# ---------------------------------------------------------------------------
class TestCacheBusting:
    def test_trends_js_has_cache_bust_param(self, seeded_url: str) -> None:
        """The trends.js script tag should have a cache-busting query param."""
        resp = requests.get(f"{seeded_url}/stats/trends", timeout=10)
        assert "trends.js?v=" in resp.text, "trends.js should have cache-busting param"
