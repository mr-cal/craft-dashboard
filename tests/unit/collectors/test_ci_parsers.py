"""Unit tests for CI log/annotation parsers."""

from craft_dashboard.collectors.ci.parsers import (
    ParsedFailure,
    detect_infra_error,
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
