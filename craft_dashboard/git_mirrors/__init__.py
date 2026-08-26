"""Read-only bare git mirror access for cross-repo evidence gathering.

Mirrors are cloned with ``git clone --mirror`` (no working tree, ever) under
``Settings.mirror_dir``. The eval worker reads them read-only; the commit
scanner (Phase 3) is the sole writer via ``git fetch``. See
``plans/36-deep-evaluation-design.md`` sections 2 and 5.
"""
