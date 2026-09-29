"""End-to-end tests for keyboard accessibility of the issue filters."""

from __future__ import annotations

import pytest

from tests.end_to_end.helpers import make_script, run_puppeteer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
]


class TestMultiselectKeyboard:
    """The multiselect trigger is a div, so its keyboard behaviour is ours."""

    def test_enter_opens_and_escape_closes(self, seeded_url: str) -> None:
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});

    const trigger = await page.$('.multiselect__input-wrap');
    await trigger.focus();

    await page.keyboard.press('Enter');
    await new Promise(r => setTimeout(r, 200));
    const afterEnter = await page.evaluate(() => {
      const wrap = document.querySelector('.multiselect__input-wrap');
      const dropdown = document.querySelector('.multiselect__dropdown');
      return {
        expanded: wrap.getAttribute('aria-expanded'),
        hidden: dropdown.classList.contains('u-hide'),
        controls: wrap.getAttribute('aria-controls'),
        dropdown_id: dropdown.id,
      };
    });

    await page.keyboard.press('Escape');
    await new Promise(r => setTimeout(r, 200));
    const afterEscape = await page.evaluate(() => {
      const wrap = document.querySelector('.multiselect__input-wrap');
      const dropdown = document.querySelector('.multiselect__dropdown');
      return {
        expanded: wrap.getAttribute('aria-expanded'),
        hidden: dropdown.classList.contains('u-hide'),
        focus_returned: document.activeElement === wrap,
      };
    });

    console.log(JSON.stringify({after_enter: afterEnter, after_escape: afterEscape}));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=40)

        opened = result["after_enter"]
        assert opened["expanded"] == "true", "Enter did not open the dropdown"
        assert opened["hidden"] is False, "Dropdown stayed hidden after Enter"
        assert opened["controls"], "combobox is missing aria-controls"
        assert opened["controls"] == opened["dropdown_id"], (
            "aria-controls does not point at the dropdown"
        )

        closed = result["after_escape"]
        assert closed["expanded"] == "false", "Escape did not close the dropdown"
        assert closed["hidden"] is True, "Dropdown stayed visible after Escape"
        assert closed["focus_returned"] is True, (
            "Escape did not return focus to the trigger"
        )

    def test_arrow_down_opens(self, seeded_url: str) -> None:
        script = make_script("""\
    await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});

    const trigger = await page.$('.multiselect__input-wrap');
    await trigger.focus();
    await page.keyboard.press('ArrowDown');
    await new Promise(r => setTimeout(r, 200));

    const state = await page.evaluate(() => {
      const wrap = document.querySelector('.multiselect__input-wrap');
      return {expanded: wrap.getAttribute('aria-expanded')};
    });

    console.log(JSON.stringify(state));
""")
        result = run_puppeteer(script, base_url=seeded_url, timeout=40)
        assert result["expanded"] == "true", "ArrowDown did not open the dropdown"
