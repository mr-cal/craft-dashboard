"""End-to-end tests for issue detail related-work provenance."""

from __future__ import annotations

import pytest

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class TestIssueDetailRelatedWork:
    def test_issue_detail_shows_related_work_and_triage_indicator(
        self, seeded_url: str
    ) -> None:
        script = make_script("""\
    await page.goto(`${BASE}/issues/rockcraft/2001`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    const detail = await page.evaluate(() => {
      const panel = Array.from(document.querySelectorAll('section.issue-detail-card'))
        .find(section => section.textContent.includes('Related work'));
      const panelText = panel ? panel.textContent.replace(/\\s+/g, ' ').trim() : '';
      const link = panel ? panel.querySelector('a[href="/issues/rockcraft/2002"]') : null;
      return {
        has_panel: !!panel,
        panel_text: panelText,
        link_href: link ? link.getAttribute('href') : null,
        link_text: link ? link.textContent.trim() : null,
      };
    });

    await page.goto(`${BASE}/issues?project=rockcraft`, {waitUntil: 'networkidle0', timeout: 30000});
    await new Promise(r => setTimeout(r, 1000));

    const table = await page.evaluate(() => {
      const row = Array.from(document.querySelectorAll('table tbody tr'))
        .find(tr => tr.textContent.includes('#2001'));
      const indicator = row ? row.querySelector('.related-work-indicator') : null;
      return {
        has_row: !!row,
        indicator_text: indicator ? indicator.textContent.trim() : null,
      };
    });

    console.log(JSON.stringify({detail, table}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=30)

        assert result["detail"]["has_panel"], (
            "Expected related work panel on detail page"
        )
        assert "Likely Fixed By" in result["detail"]["panel_text"]
        assert "rockcraft#2" in result["detail"]["panel_text"]
        assert "80%" in result["detail"]["panel_text"]
        assert result["detail"]["link_href"] == "/issues/rockcraft/2002"
        assert result["detail"]["link_text"] == "rockcraft#2"
        assert result["table"]["has_row"], "Expected rockcraft issue row on triage page"
        assert result["table"]["indicator_text"] == "Related work"
