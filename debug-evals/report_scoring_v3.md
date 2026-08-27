# Phase 5 scoring bake-off

## Per-model roll-up
| Model | Items | Completion rate | Mean rounds | Mean cost_usd | Median cost_usd | Stdev cost_usd | Mean wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen/qwen3.8-27b | 5 | 80% | 6.00 | 0.0447 | 0.0468 | 0.0104 | 117.45 |

## Per-issue comparisons

| Issue | Model | Old scores | New scores | Related work | Rounds | Tools | Prompt tokens | Completion tokens | Cost_usd | Wall seconds | Status |
| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |
| snapcraft#6381 | qwen/qwen3.8-27b | complexity=45, confidence=90, staleness=5, support_request=5 | complexity=80, confidence=70, impact=50, staleness=15, support_request=10 | issue:snapcraft#4236 (75); commit:ab2c13576 (70); commit:57cd1d97b (60); file:snapcraft:spread.yaml (70); commit:31fb7ec8b (55) | 6 | issue_detail, git_log_search, grep_repo, repo_layout, related_issues, git_log_search, grep_repo, read_file, git_log_search, git_log_search, issue_detail, git_log_search | 71981 | 4161 | 0.0325 | 101.51 | completed |
| debcraft#41 | qwen/qwen3.8-27b | complexity=25, confidence=90, staleness=75, support_request=5 | - | - | 5 | repo_layout, grep_repo, grep_repo, git_log_search, read_file, read_file, read_file, grep_repo | 155321 | 696 | 0.0373 | 28.02 | error: token ceiling exceeded (120000) |
| snapcraft (launchpad)#1861614 | qwen/qwen3.8-27b | complexity=10, confidence=85, staleness=95, support_request=10 | complexity=10, confidence=90, impact=35, staleness=80, support_request=10 | file:craft-parts:craft_parts/sources/tar_source.py (95); file:craft-parts:craft_parts/sources/sources.py (85); file:craft-parts:tests/unit/sources/test_sources.py (80); commit:craft-parts:b4a320fe (60) | 7 | grep_repo, grep_repo, read_file, read_file, read_file, git_log_search, related_issues, git_log_path, git_log_search, git_log_search, git_log_search | 99133 | 5276 | 0.0476 | 125.21 | completed |
| snapcraft-rocks#111 | qwen/qwen3.8-27b | complexity=50, confidence=80, staleness=75, support_request=15 | complexity=65, confidence=65, impact=60, staleness=15, support_request=20 | commit:craft-parts@b7455277 (80); commit:rockcraft@55ade214 (55); commit:rockcraft@86153dcc (45) | 7 | repo_layout, issue_detail, git_log_search, related_issues, read_file, grep_repo, git_log_search, git_log_search, related_issues, git_log_search, git_log_search, git_log_search, git_log_search, git_log_path, related_issues, git_log_search, git_log_search, git_log_search | 119198 | 6593 | 0.0594 | 184.77 | completed |
| craft-parts#766 | qwen/qwen3.8-27b | complexity=85, confidence=85, staleness=75, support_request=0 | complexity=32, confidence=90, impact=70, staleness=25, support_request=15 | file:craft_parts/executor/step_handler.py (92); issue:snapcraft#4835 (80); pull_request:craft-parts#1617 (68); commit:0c1e7feb (60) | 5 | read_file, git_log_search, git_log_path, issue_detail, read_file, git_log_search, grep_repo, git_log_search | 64811 | 8167 | 0.0468 | 147.77 | completed |

## Scoring backfill projection

- Sample size: 5
- Mean cost per item: 0.0447
- Median cost per item: 0.0468
- Stdev cost per item: 0.0104
- Min/Max cost per item: 0.0325 / 0.0594
- Projected total for 2,269 items: 101.4634
- Caveats: Point estimates are insufficient: review sample size, variance, and prompt-size skew before authorizing any backfill.

## MAX_TOOL_ROUNDS calibration
Evaluated against current cap = 8.

| Rounds used | Count |
| --- | ---: |
| 5 | 2 |
| 6 | 1 |
| 7 | 2 |

Current recommendation: keep MAX_TOOL_ROUNDS at 8 until sweep data is available.
