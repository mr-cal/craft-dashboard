from click.testing import CliRunner
from craft_dashboard.cli import main


def test_smoketest() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
