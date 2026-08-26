"""Unit tests for craft_dashboard.commit_scanner.parsing."""

from __future__ import annotations

from craft_dashboard.commit_scanner.parsing import (
    BareRef,
    LaunchpadRef,
    QualifiedRef,
    extract_references,
)


class TestExtractQualifiedRefs:
    """canonical/<repo>#N and https://github.com/.../issues/N are exact."""

    def test_slash_hash_form(self) -> None:
        refs = extract_references("Fixes canonical/craft-parts#567 in the pull step")
        assert QualifiedRef(project="craft-parts", external_id="567") in refs.qualified

    def test_url_form(self) -> None:
        refs = extract_references(
            "See https://github.com/canonical/craft-parts/issues/567 for context"
        )
        assert QualifiedRef(project="craft-parts", external_id="567") in refs.qualified

    def test_url_form_pull_request(self) -> None:
        refs = extract_references(
            "Closes https://github.com/canonical/rockcraft/pull/42"
        )
        assert QualifiedRef(project="rockcraft", external_id="42") in refs.qualified

    def test_no_false_positive_on_plain_hash(self) -> None:
        refs = extract_references("Version 1.2#3 released")
        assert refs.qualified == []


class TestExtractBareRefs:
    """Bare #N is repo-scoped for exact matching, weak for cross-repo."""

    def test_bare_hash_is_extracted(self) -> None:
        refs = extract_references("Fixes #1234 by rewriting the handler")
        assert BareRef(external_id="1234") in refs.bare

    def test_bare_ref_is_never_returned_as_qualified(self) -> None:
        """A bare #1234 must never be conflated with a cross-repo exact match."""
        refs = extract_references("Fixes #1234")
        assert refs.qualified == []
        assert refs.bare == [BareRef(external_id="1234")]

    def test_qualified_ref_is_not_duplicated_as_bare(self) -> None:
        refs = extract_references("Fixes canonical/craft-parts#567")
        assert refs.bare == []
        assert len(refs.qualified) == 1


class TestExtractLaunchpadRefs:
    """LP: #N is cross-source (Launchpad), not cross-repo."""

    def test_lp_ref_is_extracted(self) -> None:
        refs = extract_references("LP: #2012345 - fix snap confinement")
        assert LaunchpadRef(external_id="2012345") in refs.launchpad

    def test_lp_ref_lowercase_variant(self) -> None:
        refs = extract_references("lp: #2012345")
        assert LaunchpadRef(external_id="2012345") in refs.launchpad

    def test_lp_ref_is_not_also_a_bare_ref(self) -> None:
        """The #N inside an LP: ref must not double-signal as a bare ref."""
        refs = extract_references("LP: #2012345 - fix snap confinement")
        assert refs.launchpad == [LaunchpadRef(external_id="2012345")]
        assert refs.bare == []


class TestExtractReferencesCombined:
    """A single commit message can carry all three kinds of ref at once."""

    def test_mixed_message(self) -> None:
        message = (
            "Fix pull-step crash (canonical/craft-parts#567), also relates to "
            "#42 and LP: #2012345"
        )
        refs = extract_references(message)
        assert QualifiedRef(project="craft-parts", external_id="567") in refs.qualified
        assert BareRef(external_id="42") in refs.bare
        assert LaunchpadRef(external_id="2012345") in refs.launchpad
        # The LP number must not leak into bare, and the qualified #567 must
        # not either — bare carries *only* the genuine bare #42.
        assert refs.bare == [BareRef(external_id="42")]
