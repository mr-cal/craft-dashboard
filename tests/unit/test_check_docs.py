"""Tests for scripts.check_docs."""

from pathlib import Path

from scripts.check_docs import RULES, scan_text


class TestCheckDocs:
    """Tests for documentation drift detection."""

    def test_clean_document_passes(self) -> None:
        """A present-tense document without volatile details has no violations."""
        text = """# Operations\n\nRun the collector from the repository root.\n"""

        assert scan_text(Path("README.md"), text) == []

    def test_temporal_narrative_is_detected(self) -> None:
        """Temporal migration wording is reported."""
        violations = scan_text(Path("docs/example.md"), "This no longer runs here.")

        assert [(v.rule, v.match) for v in violations] == [
            ("temporal narrative", "no longer")
        ]

    def test_project_management_reference_is_detected(self) -> None:
        """Phase and task references are reported."""
        violations = scan_text(Path("docs/example.md"), "Phase 6 and Task 12")

        assert [(v.rule, v.match) for v in violations] == [
            ("project-management reference", "Phase 6"),
            ("project-management reference", "Task 12"),
        ]

    def test_bare_ipv4_address_is_detected(self) -> None:
        """Bare IPv4 addresses are reported."""
        violations = scan_text(Path("docs/example.md"), "ssh root@203.0.113.10")

        assert [(v.rule, v.match) for v in violations] == [
            ("bare IPv4 address", "203.0.113.10")
        ]

    def test_volatile_measurements_are_detected(self) -> None:
        """Large approximate counts and memory measurements are reported."""
        violations = scan_text(Path("docs/example.md"), "~2,000 items and 307MB RAM")

        assert [(v.rule, v.match) for v in violations] == [
            ("volatile measurement", "~2,000"),
            ("volatile measurement", "307MB"),
        ]

    def test_quoted_examples_are_ignored(self) -> None:
        """Quoted examples of banned phrases can document the rules."""
        text = 'Avoid "no longer" in docs and use `Phase 1` only as an example.'

        assert scan_text(Path("agents.md"), text) == []

    def test_rule_names_are_unique(self) -> None:
        """Rule names stay stable for readable output."""
        names = [rule.name for rule in RULES]

        assert len(names) == len(set(names))
