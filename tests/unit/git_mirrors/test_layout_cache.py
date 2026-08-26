"""Unit tests for craft_dashboard.git_mirrors.layout_cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

from craft_dashboard.git_mirrors import layout_cache

if TYPE_CHECKING:
    import pathlib


class TestLayoutCache:
    async def test_second_call_same_key_does_not_recompute(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str], monkeypatch
    ) -> None:
        calls = {"n": 0}

        async def counting_repo_layout(mirror, *, ref):
            calls["n"] += 1
            return {"src/": 2}

        monkeypatch.setattr(layout_cache, "repo_layout", counting_repo_layout)
        layout_cache.clear_layout_cache()

        sha = sample_repo_shas[-1]
        first = await layout_cache.cached_repo_layout(
            "sample-project", bare_mirror, ref=sha
        )
        second = await layout_cache.cached_repo_layout(
            "sample-project", bare_mirror, ref=sha
        )

        assert first == second == {"src/": 2}
        assert calls["n"] == 1  # computed once, served from cache the second time

    async def test_different_sha_recomputes(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str], monkeypatch
    ) -> None:
        calls = {"n": 0}

        async def counting_repo_layout(mirror, *, ref):
            calls["n"] += 1
            return {"src/": calls["n"]}

        monkeypatch.setattr(layout_cache, "repo_layout", counting_repo_layout)
        layout_cache.clear_layout_cache()

        await layout_cache.cached_repo_layout("p", bare_mirror, ref=sample_repo_shas[0])
        await layout_cache.cached_repo_layout(
            "p", bare_mirror, ref=sample_repo_shas[-1]
        )
        assert calls["n"] == 2  # a different (project, sha) key recomputes

    async def test_cache_is_bounded_and_evicts_lru(
        self, bare_mirror: pathlib.Path, monkeypatch
    ) -> None:
        async def repo_layout(mirror, *, ref):
            return {"x/": 1}

        monkeypatch.setattr(layout_cache, "repo_layout", repo_layout)
        monkeypatch.setattr(layout_cache, "_MAX_CACHE_ENTRIES", 2)
        layout_cache.clear_layout_cache()

        # Insert 3 distinct keys into a size-2 cache; the oldest is evicted.
        for i in range(3):
            await layout_cache.cached_repo_layout("p", bare_mirror, ref=f"{i:040x}")
        assert layout_cache.cache_size() == 2
