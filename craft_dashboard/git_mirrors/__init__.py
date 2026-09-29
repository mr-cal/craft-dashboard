"""Read-only bare git mirror access for cross-repo evidence gathering.

Mirrors are cloned with ``git clone --mirror`` (no working tree, ever) under
``Settings.mirror_dir``. The eval worker reads them read-only; the commit
scanner is the sole writer via ``git fetch``.
"""
