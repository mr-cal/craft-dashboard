"""Release tag and branch selection for GitHub release collection."""

import re

HOTFIX_BRANCH_RE = re.compile(r"^hotfix/(\d+)\.(\d+)$")

_HOTFIX_VERSION_COMPONENTS = 2


def parse_version(tag: str) -> tuple[int, ...] | None:
    """Parse a version tag like '4.2.1' or 'v4.2.1' into a tuple."""
    clean = tag.lstrip("v")
    try:
        return tuple(int(p) for p in clean.split("."))
    except ValueError:
        return None


def select_branches_to_track(branch_names: list[str]) -> list[str]:
    """Return main plus every hotfix/X.Y branch, in that order.

    All hotfix branches are tracked with no version filter so the Hotfixes
    page can show a complete picture.
    """
    branches = ["main"]
    branches.extend(name for name in branch_names if HOTFIX_BRANCH_RE.match(name))
    return branches


def select_best_tag(branch_name: str, tags: list[str]) -> str | None:
    """Return the highest-version tag belonging to a branch.

    Args:
        branch_name: 'main' or a 'hotfix/X.Y' branch name.
        tags: Candidate release tag names.

    Returns:
        The best matching tag, or None when the branch has no matching tag or
        is not a recognized hotfix branch.

    """
    best_tag: str | None = None
    best_ver: tuple[int, ...] = ()

    if branch_name == "main":
        for tag in tags:
            ver = parse_version(tag)
            if ver and ver > best_ver:
                best_ver = ver
                best_tag = tag
        return best_tag

    match = HOTFIX_BRANCH_RE.match(branch_name)
    if not match:
        return None
    hf_major, hf_minor = int(match.group(1)), int(match.group(2))
    for tag in tags:
        ver = parse_version(tag)
        if (
            ver
            and len(ver) >= _HOTFIX_VERSION_COMPONENTS
            and ver[0] == hf_major
            and ver[1] == hf_minor
        ) and ver > best_ver:
            best_ver = ver
            best_tag = tag
    return best_tag
