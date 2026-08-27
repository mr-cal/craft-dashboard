# Phase 5 scoring bake-off

## Per-model roll-up
| Model | Items | Completion rate | Mean rounds | Mean cost_usd | Median cost_usd | Stdev cost_usd | Mean wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen/qwen3.8-27b | 5 | 100% | 2.00 | 0.0057 | 0.0044 | 0.0043 | 12.45 |

## Per-issue comparisons

| Issue | Model | Old scores | New scores | Related work | Rounds | Tools | Prompt tokens | Completion tokens | Cost_usd | Wall seconds | Status |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| snapcraft#6381 | qwen/qwen3.8-27b | complexity=45, confidence=90, staleness=5, support_request=5 | complexity=0, confidence=0, impact=0, staleness=0, support_request=0 | - | 2 | issue_detail, related_issues, git_log_search | 13626 | 439 | 0.0044 | 13.71 | completed |
| debcraft#41 | qwen/qwen3.8-27b | complexity=25, confidence=90, staleness=75, support_request=5 | complexity=0, confidence=0, impact=0, staleness=0, support_request=0 | - | 2 | repo_layout, git_log_search, related_issues | 4497 | 313 | 0.0026 | 15.10 | completed |
| snapcraft (launchpad)#1861614 | qwen/qwen3.8-27b | complexity=10, confidence=85, staleness=95, support_request=10 | complexity=0, confidence=0, impact=0, staleness=0, support_request=0 | - | 2 | grep_repo, grep_repo, related_issues | 7967 | 335 | 0.0040 | 7.70 | completed |
| snapcraft-rocks#111 | qwen/qwen3.8-27b | complexity=50, confidence=80, staleness=75, support_request=15 | complexity=0, confidence=0, impact=0, staleness=0, support_request=0 | - | 2 | repo_layout, related_issues, issue_detail | 30258 | 671 | 0.0133 | 16.32 | completed |
| craft-parts#766 | qwen/qwen3.8-27b | complexity=85, confidence=85, staleness=75, support_request=0 | complexity=0, confidence=0, impact=0, staleness=0, support_request=0 | - | 2 | repo_layout, related_issues | 9272 | 254 | 0.0044 | 9.41 | completed |

## Scoring backfill projection

- Sample size: 5
- Mean cost per item: 0.0057
- Median cost per item: 0.0044
- Stdev cost per item: 0.0043
- Min/Max cost per item: 0.0026 / 0.0133
- Projected total for 2,269 items: 12.9944
- Caveats: Point estimates are insufficient: review sample size, variance, and prompt-size skew before authorizing any backfill.

## MAX_TOOL_ROUNDS calibration
Evaluated against current cap = 6.

| Rounds used | Count |
| --- | ---: |
| 2 | 5 |

Current recommendation: keep MAX_TOOL_ROUNDS at 6 until sweep data is available.
