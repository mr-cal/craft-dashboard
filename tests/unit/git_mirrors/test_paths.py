"""Unit tests for craft_dashboard.git_mirrors.paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from craft_dashboard.git_mirrors.exceptions import UnknownProjectError
from craft_dashboard.git_mirrors.paths import mirror_path_for, resolve_allowed_projects

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
