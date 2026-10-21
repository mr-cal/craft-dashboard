"""Tests for scripts.check_docs."""

from pathlib import Path

from scripts.check_docs import RULES, heading_to_slug, scan_links, scan_text


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

    def test_heading_to_slug(self) -> None:
        """Headings are converted to GitHub-compatible slugs."""
        assert heading_to_slug("Database tables") == "database-tables"
        assert heading_to_slug("`craft-dashboard`") == "craft-dashboard"
        assert heading_to_slug("CLI & Options") == "cli-options"

    def test_scan_links_valid_anchor(self, tmp_path: Path) -> None:
        """Valid local anchors in the same file do not produce violations."""
        file = tmp_path / "doc.md"
        content = "## Setup\n\nSee [setup](#setup) section.\n"
        file.write_text(content)

        assert scan_links(file, content) == []

    def test_scan_links_broken_anchor(self, tmp_path: Path) -> None:
        """Broken local anchors are reported."""
        file = tmp_path / "doc.md"
        content = "## Setup\n\nSee [config](#config) section.\n"
        file.write_text(content)

        violations = scan_links(file, content)
        assert len(violations) == 1
        assert violations[0].rule == "broken anchor"
        assert violations[0].match == "#config"

    def test_scan_links_cross_file_valid_and_broken(self, tmp_path: Path) -> None:
        """Cross-file links validate target existence and anchors."""
        target = tmp_path / "target.md"
        target.write_text("# Target Doc\n\n## Sub Section\n")

        source = tmp_path / "source.md"
        valid_content = "See [target](target.md#sub-section).\n"
        assert scan_links(source, valid_content) == []

        broken_link_content = "See [missing](nonexistent.md).\n"
        violations = scan_links(source, broken_link_content)
        assert len(violations) == 1
        assert violations[0].rule == "broken link"
        assert violations[0].match == "nonexistent.md"

        broken_anchor_content = "See [target](target.md#missing-anchor).\n"
        violations = scan_links(source, broken_anchor_content)
        assert len(violations) == 1
        assert violations[0].rule == "broken anchor"
        assert violations[0].match == "target.md#missing-anchor"

    def test_scan_links_ignores_code_blocks_and_external(self, tmp_path: Path) -> None:
        """Links inside fenced code blocks and external links are ignored."""
        file = tmp_path / "doc.md"
        content = (
            "See [docs](https://example.com/docs).\n"
            "```markdown\n[broken](missing.md#missing)\n```\n"
        )
        file.write_text(content)

        assert scan_links(file, content) == []
