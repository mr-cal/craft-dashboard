"""Unit tests for craft_dashboard.git_mirrors.paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from craft_dashboard.git_mirrors.exceptions import UnknownProjectError
from craft_dashboard.git_mirrors.paths import (
    canonical_git_project_name,
    clone_url_for,
    mirror_path_for,
    resolve_allowed_projects,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestResolveAllowedProjects:
    """Tests for building the project-name allowlist."""

    def test_returns_dict_keyed_by_project_name(self) -> None:
        allowed = resolve_allowed_projects(
            craft_projects=["craft-parts", "snapcraft"],
            project_orgs={"craft-parts": "canonical"},
        )
        assert allowed == {
            "craft-parts": "canonical",
            "snapcraft": "canonical",  # falls back to "canonical"
        }


class TestMirrorPathFor:
    """Tests for resolving a project name to its mirror directory."""

    def test_known_project_resolves_under_mirror_dir(self, tmp_path: Path) -> None:
        path = mirror_path_for(
            "craft-parts",
            mirror_dir=tmp_path,
            allowed_projects={"craft-parts": "canonical"},
        )
        assert path == tmp_path / "craft-parts.git"

    def test_unknown_project_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UnknownProjectError):
            mirror_path_for(
                "not-a-real-project",
                mirror_dir=tmp_path,
                allowed_projects={"craft-parts": "canonical"},
            )

    def test_launchpad_view_project_resolves_to_underlying_git_mirror(
        self, tmp_path: Path
    ) -> None:
        """ "snapcraft (launchpad)" is a non-clonable per-source view row for
        the "snapcraft" project (see routes/issues.py's matching
        removesuffix(" (launchpad)") convention); it must resolve to the
        real "snapcraft" mirror rather than raising UnknownProjectError."""
        path = mirror_path_for(
            "snapcraft (launchpad)",
            mirror_dir=tmp_path,
            allowed_projects={"snapcraft": "canonical"},
        )
        assert path == tmp_path / "snapcraft.git"

    def test_unknown_launchpad_view_project_still_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UnknownProjectError):
            mirror_path_for(
                "not-a-real-project (launchpad)",
                mirror_dir=tmp_path,
                allowed_projects={"craft-parts": "canonical"},
            )


class TestCloneUrlFor:
    """Tests for resolving a project name to its clone URL."""

    def test_known_project_resolves_clone_url(self) -> None:
        url = clone_url_for(
            "craft-parts", allowed_projects={"craft-parts": "canonical"}
        )
        assert url == "https://github.com/canonical/craft-parts.git"

    def test_launchpad_view_project_resolves_to_underlying_clone_url(self) -> None:
        url = clone_url_for(
            "snapcraft (launchpad)",
            allowed_projects={"snapcraft": "canonical"},
        )
        assert url == "https://github.com/canonical/snapcraft.git"

    def test_unknown_project_raises(self) -> None:
        with pytest.raises(UnknownProjectError):
            clone_url_for(
                "not-a-real-project", allowed_projects={"craft-parts": "canonical"}
            )


class TestCanonicalGitProjectName:
    """Tests for stripping the Launchpad-view suffix."""

    def test_strips_launchpad_suffix(self) -> None:
        assert canonical_git_project_name("snapcraft (launchpad)") == "snapcraft"

    def test_leaves_normal_names_unchanged(self) -> None:
        assert canonical_git_project_name("craft-parts") == "craft-parts"
