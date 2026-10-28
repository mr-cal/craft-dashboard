"""Unit tests for CI log/annotation parsers."""

from craft_dashboard.collectors.ci.parsers import (
    ParsedFailure,
    detect_infra_error,
    has_structured_parser_hint,
    parse_check_annotations,
    parse_go_failures,
    parse_linter_and_doc_failures,
    parse_log_tail,
    parse_pytest_failures,
    parse_spread_failures,
)


class TestParsePytestFailures:
    def test_extracts_path_and_test_name(self):
        log = (
            "=========================== short test summary info ============================\n"
            "FAILED tests/unit/test_foo.py::test_bar - AssertionError: assert 1 == 2\n"
        )
        failures = parse_pytest_failures(log)
        assert failures == [
            ParsedFailure(
                test_type="pytest",
                signature="tests/unit/test_foo.py::test_bar",
                error_message="AssertionError: assert 1 == 2",
            )
        ]

    def test_handles_parametrized_test_names(self):
        log = "FAILED tests/unit/test_foo.py::test_bar[param1-param2] - ValueError\n"
        failures = parse_pytest_failures(log)
        assert (
            failures[0].signature == "tests/unit/test_foo.py::test_bar[param1-param2]"
        )

    def test_ignores_non_failed_lines(self):
        log = "PASSED tests/unit/test_foo.py::test_bar\nsome other output\n"
        assert parse_pytest_failures(log) == []

    def test_flags_environmental_message(self):
        log = "FAILED tests/unit/test_foo.py::test_bar - Errno 101: Network is unreachable\n"
        failures = parse_pytest_failures(log)
        assert failures[0].is_environmental is True


class TestParseGoFailures:
    def test_associates_test_with_following_package_summary(self):
        log = (
            "=== RUN   TestFoo\n"
            "--- FAIL: TestFoo (0.01s)\n"
            "    foo_test.go:12: some message\n"
            "FAIL\n"
            "FAIL\tgithub.com/canonical/craft-cli/cmd\t0.123s\n"
        )
        failures = parse_go_failures(log)
        assert failures == [
            ParsedFailure(
                test_type="go", signature="github.com/canonical/craft-cli/cmd.TestFoo"
            )
        ]

    def test_multiple_failures_before_one_summary_line(self):
        log = (
            "--- FAIL: TestFoo (0.01s)\n"
            "--- FAIL: TestBar (0.02s)\n"
            "FAIL\tgithub.com/canonical/craft-cli/cmd\t0.123s\n"
        )
        failures = parse_go_failures(log)
        signatures = {f.signature for f in failures}
        assert signatures == {
            "github.com/canonical/craft-cli/cmd.TestFoo",
            "github.com/canonical/craft-cli/cmd.TestBar",
        }

    def test_truncated_tail_without_package_summary_falls_back_to_bare_name(self):
        log = "--- FAIL: TestFoo (0.01s)\n"
        failures = parse_go_failures(log)
        assert failures == [ParsedFailure(test_type="go", signature="TestFoo")]

    def test_no_failures_returns_empty_list(self):
        assert (
            parse_go_failures("ok  \tgithub.com/canonical/craft-cli/cmd\t0.1s\n") == []
        )


class TestParseSpreadFailures:
    def test_extracts_suite_task_and_strips_run_id(self):
        log = (
            "Error executing tests/spread/core24/project-variables (oct271016-731736) "
            ": exit status 1\n"
        )
        failures = parse_spread_failures(log)
        assert failures == [
            ParsedFailure(
                test_type="spread",
                signature="tests/spread/core24/project-variables",
                error_message="exit status 1",
            )
        ]

    def test_keeps_variant_suffix(self):
        log = "Error executing core26/build-and-run-hello:maven (run-id-1)\n"
        failures = parse_spread_failures(log)
        assert failures[0].signature == "core26/build-and-run-hello:maven"

    def test_detects_environmental_failure(self):
        log = (
            "Error executing tests/spread/core24/foo (run-id) : "
            "LXDError: Failed to start container\n"
        )
        failures = parse_spread_failures(log)
        assert failures[0].is_environmental is True


class TestDetectInfraError:
    def test_matches_known_infra_patterns(self):
        assert detect_infra_error("LXDError: Failed to start '...': forkstart")
        assert detect_infra_error("Errno 101: Network is unreachable")
        assert detect_infra_error("context deadline exceeded")

    def test_does_not_match_assertion_errors(self):
        assert not detect_infra_error("AssertionError: assert 1 == 2")

    def test_matches_github_actions_cancellation_and_timeout_boilerplate(self):
        # These are the actual messages observed on production annotations --
        # GitHub's own job/step cancellation and timeout text, not the
        # underlying tool failing. A sibling matrix cell failing or a newer
        # push superseding a run is CI-infra noise, not a code regression.
        assert detect_infra_error(
            "The action 'Run tests' has timed out after 3 minutes."
        )
        assert detect_infra_error("The operation was canceled.")
        assert detect_infra_error(
            "The strategy configuration was canceled because "
            "'build (ubuntu-22.04)' failed"
        )
        assert detect_infra_error(
            "Canceling since a higher priority waiting request for "
            "refs/pull/123/merge exists"
        )

    def test_does_not_match_generic_exit_code_message(self):
        # "Process completed with exit code N" is GitHub's generic marker
        # for *any* failing step, including real test/tool failures, so it
        # must not be treated as infra noise -- it carries no signal either
        # way about whether the failure is environmental.
        assert not detect_infra_error("Process completed with exit code 1.")


class TestParseLinterAndDocFailures:
    def test_extracts_known_tool_failure(self):
        log = "docs-lint / linkcheck failed: broken link in README.md\n"
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1
        assert failures[0].test_type == "linter"
        assert failures[0].signature.startswith("docs-lint:")

    def test_deduplicates_identical_lines(self):
        log = "ruff check error\nruff check error\n"
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1

    def test_ignores_unknown_tool_output(self):
        log = "some completely unrelated log line\n"
        assert parse_linter_and_doc_failures(log) == []

    def test_ruff_signature_drops_line_and_column(self):
        # Two different violations of the same rule on the same file, but
        # at different lines (e.g. code shifted between runs), must collapse
        # to one signature rather than being counted as two distinct errors.
        log = (
            "src/foo.py:12:5: E501 line too long (92 > 88 characters)\n"
            "src/foo.py:40:1: E501 line too long (95 > 88 characters)\n"
        )
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1
        assert failures[0].signature == "ruff: src/foo.py E501"

    def test_ruff_signature_falls_back_when_unparseable(self):
        log = "ruff failed with a non-standard error format\n"
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1
        assert failures[0].signature.startswith("ruff: ruff failed")

    def test_osv_scanner_signature_uses_vulnerability_id(self):
        # Two scans flagging the same advisory in a differently-formatted
        # table row must still collapse to one signature.
        log = (
            "osv-scanner: | GHSA-wq4v-7hhh-8vh4 | foo-package | 1.2.3 |\n"
            "osv-scanner: GHSA-wq4v-7hhh-8vh4 found again in scan output\n"
        )
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1
        assert failures[0].signature == "osv-scanner: GHSA-wq4v-7hhh-8vh4"

    def test_osv_scanner_signature_falls_back_when_no_id_found(self):
        log = "osv-scanner: no vulnerabilities data available\n"
        failures = parse_linter_and_doc_failures(log)
        assert len(failures) == 1
        assert failures[0].signature.startswith("osv-scanner: osv-scanner")

    def test_osv_scanner_ignores_github_actions_boilerplate(self):
        # Real production noise: every line of a job *named* osv-scanner
        # literally contains that substring (snap install confirmation,
        # command echo, env var dump), but none of it is an actual finding.
        log = (
            "##[group]Run sudo snap install osv-scanner\n"
            "sudo snap install osv-scanner\n"
            "osv-scanner 2.3.8 from someone installed\n"
            "  OSV_EXTRA_ARGS: --config=osv-scanner.toml\n"
            "osv-scanner scan --recursive --allow-no-lockfiles . \n"
            "Complete job name: Security scan / OSV-scanner\n"
        )
        assert parse_linter_and_doc_failures(log) == []


class TestParseCheckAnnotations:
    def test_builds_signature_from_path_and_line(self):
        annotations = [
            {
                "path": "tests/unit/test_foo.py",
                "start_line": 42,
                "message": "assert 1 == 2",
                "annotation_level": "failure",
            }
        ]
        failures = parse_check_annotations(annotations)
        assert failures == [
            ParsedFailure(
                test_type="annotation",
                signature="tests/unit/test_foo.py:42",
                error_message="assert 1 == 2",
            )
        ]

    def test_skips_non_failure_annotations(self):
        annotations = [
            {
                "path": "foo.py",
                "start_line": 1,
                "message": "note",
                "annotation_level": "notice",
            }
        ]
        assert parse_check_annotations(annotations) == []


class TestParseLogTail:
    def test_dispatches_by_job_name_keyword(self):
        log = "FAILED tests/unit/test_foo.py::test_bar - AssertionError\n"
        failures = parse_log_tail(log, job_name="unit-tests (3.12)")
        assert any(f.test_type == "pytest" for f in failures)

    def test_falls_back_to_all_parsers_for_unrecognized_job_name(self):
        log = "Error executing tests/spread/core24/foo (run-id)\n"
        failures = parse_log_tail(log, job_name="some-custom-job")
        assert any(f.test_type == "spread" for f in failures)

    def test_strips_github_actions_log_timestamps_before_parsing(self):
        # GitHub's job-log API prefixes every real line with a timestamp;
        # without stripping it first, pytest's "^FAILED" anchor never
        # matches and nothing is ever parsed as a real test failure.
        log = (
            "2026-10-28T19:45:13.0019151Z FAILED tests/unit/test_foo.py::"
            "test_bar - AssertionError: assert 1 == 2\n"
        )
        failures = parse_log_tail(log, job_name="unit-tests (3.12)")
        assert failures == [
            ParsedFailure(
                test_type="pytest",
                signature="tests/unit/test_foo.py::test_bar",
                error_message="AssertionError: assert 1 == 2",
            )
        ]

    def test_timestamped_unrecognized_linter_line_still_dedupes_across_runs(self):
        # A tool with no dedicated stable-signature extractor (e.g. vale)
        # falls back to the raw line; if the leading timestamp weren't
        # stripped first, two runs hitting the same error would never
        # collapse into one signature since the timestamp differs each time.
        run_one = "2026-10-28T19:45:13.0019151Z vale: style violation found\n"
        run_two = "2026-10-28T20:05:41.1187322Z vale: style violation found\n"
        sig_one = parse_log_tail(run_one, job_name="qa")[0].signature
        sig_two = parse_log_tail(run_two, job_name="qa")[0].signature
        assert sig_one == sig_two


class TestHasStructuredParserHint:
    def test_matches_pytest_job_name(self):
        assert has_structured_parser_hint("unit-tests (3.12)")

    def test_matches_spread_job_name(self):
        assert has_structured_parser_hint(
            "integration-spread-tests (openstack:ubuntu-24.04-64)"
        )

    def test_matches_go_job_name(self):
        assert has_structured_parser_hint("go-tests")

    def test_matches_linter_and_security_scan_job_names(self):
        # These have a dedicated log parser (parse_linter_and_doc_failures)
        # even though they aren't a test framework -- their Check Run
        # Annotations carry no real diagnostic content in production (see
        # has_structured_parser_hint's docstring), so the log is the only
        # useful signal.
        assert has_structured_parser_hint("lint / files")
        assert has_structured_parser_hint("Security scan / OSV-scanner")
        assert has_structured_parser_hint("Check policy")
        assert has_structured_parser_hint("TICS / TICS")

    def test_does_not_match_generic_build_job_names(self):
        assert not has_structured_parser_hint("Snap")
        assert not has_structured_parser_hint("Build artefacts")
