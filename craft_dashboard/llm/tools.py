"""Native tool-calling schemas for the deep-evaluation tool set.

Every tool is read-only and bare-mirror-safe (see
plans/36-deep-evaluation-design.md section 5). ``ref`` is optional on every
repo-scoped tool and defaults, at dispatch time (craft_dashboard.llm.
tool_dispatch), to the pinned HEAD SHA `/api/eval/next` supplied for that
repo — never to a branch name or "latest", so recorded evidence stays
reproducible.
"""

from typing import Any

_REPOS_ARRAY = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Project names to search, e.g. ['craft-parts', 'snapcraft']. Omit to "
        "search only this issue's own project by default; pass other project "
        "names explicitly to search across apps/libraries."
    ),
}

_REF_PROPERTY = {
    "type": "string",
    "description": (
        "A 40-character git commit SHA to pin this call to. Omit to use "
        "this repo's pinned HEAD SHA from this evaluation's round-1 baseline."
    ),
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "git_log_search",
            "description": (
                "Search commit messages and diff content for a query string "
                "across one or more projects. Two modes: --grep (message text) "
                "is default; use pickaxe=true for -S (diff content) search. "
                "Capped at 50 matching commits per call -- if truncated, narrow "
                "your query or the `repos` list for more precise results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for."},
                    "repos": _REPOS_ARRAY,
                    "pickaxe": {
                        "type": "boolean",
                        "description": "If true, search diff content (git log -S) instead of commit messages.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log_path",
            "description": "Return the commit history touching a specific file path in a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'craft-parts'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative file path within the repo.",
                    },
                    "ref": _REF_PROPERTY,
                },
                "required": ["project", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the content of a file at a specific commit in a "
                "project. Returns the whole file by default; pass "
                "start_line/end_line (1-indexed, inclusive) to read only a "
                "slice of a large file instead of paying for its entire "
                "content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'craft-parts'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative file path within the repo.",
                    },
                    "ref": _REF_PROPERTY,
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "1-indexed inclusive first line to return. "
                            "Omit to read from the beginning of the file."
                        ),
                    },
                    "end_line": {
                        "type": "integer",
                        "description": (
                            "1-indexed inclusive last line to return. "
                            "Omit to read to the end of the file."
                        ),
                    },
                },
                "required": ["project", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_repo",
            "description": (
                "Search for a literal pattern across one or more projects' source "
                "at a specific commit. Returns matching lines with file:line "
                "prefixes, capped at 50 matches per call -- if truncated, narrow "
                "your pattern or the `repos` list for more precise results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Literal text pattern to search for.",
                    },
                    "repos": _REPOS_ARRAY,
                    "ref": _REF_PROPERTY,
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_layout",
            "description": (
                "Return a depth-2 directory listing with per-directory file "
                "counts for a project, to discover what paths exist before "
                "reading or grepping specific files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'craft-parts'.",
                    },
                    "ref": _REF_PROPERTY,
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "related_issues",
            "description": (
                "Semantically search the existing issue corpus (all 18 projects) "
                "for issues similar to a free-text query, by embedding cosine "
                "similarity over past evaluation summaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Free-text description of what to search for.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_detail",
            "description": (
                "Look up an issue by reference. Accepts a qualified reference "
                "('canonical/craft-parts#567') which resolves to exactly one "
                "issue, or a bare reference ('#123') which returns every "
                "candidate issue across all 18 projects sharing that number, "
                "for you to disambiguate using context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "'owner/project#N', 'project#N', or bare '#N'.",
                    },
                },
                "required": ["ref"],
            },
        },
    },
]
