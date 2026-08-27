# Phase 5 scoring bake-off

## Per-model roll-up
| Model | Items | Completion rate | Mean rounds | Mean cost_usd | Median cost_usd | Stdev cost_usd | Mean wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen/qwen3.8-27b | 5 | 0% | 6.00 | 0.0418 | 0.0373 | 0.0139 | 69.22 |

## Per-issue comparisons

| Issue | Model | Old scores | New scores | Related work | Rounds | Tools | Prompt tokens | Completion tokens | Cost_usd | Wall seconds | Status |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| snapcraft#6381 | qwen/qwen3.8-27b | complexity=45, confidence=90, staleness=5, support_request=5 | - | - | 6 | related_issues, git_log_search, grep_repo, issue_detail, git_log_search, grep_repo, read_file, git_log_search, git_log_path, git_log_search, read_file, git_log_search, repo_layout, git_log_search | 73256 | 1203 | 0.0324 | 42.70 | error: max rounds reached (6) |
| debcraft#41 | qwen/qwen3.8-27b | complexity=25, confidence=90, staleness=75, support_request=5 | - | - | 6 | repo_layout, grep_repo, git_log_search, grep_repo, read_file, read_file, read_file, grep_repo, read_file, grep_repo | 159339 | 1122 | 0.0660 | 40.42 | error: token ceiling exceeded (120000) |
| snapcraft (launchpad)#1861614 | qwen/qwen3.8-27b | complexity=10, confidence=85, staleness=95, support_request=10 | - | - | 6 | grep_repo, grep_repo, grep_repo, grep_repo, read_file, read_file, git_log_search, git_log_path, read_file, related_issues | 76658 | 1132 | 0.0335 | 53.81 | error: max rounds reached (6) |
| snapcraft-rocks#111 | qwen/qwen3.8-27b | complexity=50, confidence=80, staleness=75, support_request=15 | - | - | 6 | repo_layout, grep_repo, git_log_search, related_issues, git_log_search, read_file, issue_detail, git_log_search, git_log_search, related_issues, git_log_search, git_log_search, git_log_search, read_file, read_file, git_log_search | 98556 | 2415 | 0.0398 | 93.92 | error: max rounds reached (6) |
| craft-parts#766 | qwen/qwen3.8-27b | complexity=85, confidence=85, staleness=75, support_request=0 | - | - | 6 | repo_layout, git_log_path, read_file, related_issues, read_file, git_log_search, issue_detail, git_log_search, git_log_search, grep_repo, git_log_search, git_log_search | 67527 | 4029 | 0.0373 | 115.25 | error: max rounds reached (6) |

## Scoring backfill projection

- Sample size: 5
- Mean cost per item: 0.0418
- Median cost per item: 0.0373
- Stdev cost per item: 0.0139
- Min/Max cost per item: 0.0324 / 0.0660
- Projected total for 2,269 items: 94.8763
- Caveats: Point estimates are insufficient: review sample size, variance, and prompt-size skew before authorizing any backfill.

## MAX_TOOL_ROUNDS calibration
Evaluated against current cap = 6.

| Rounds used | Count |
| --- | ---: |
| 6 | 5 |

Current recommendation: keep MAX_TOOL_ROUNDS at 6 until sweep data is available.
