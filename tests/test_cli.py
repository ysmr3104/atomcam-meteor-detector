"""Tests for the CLI."""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from atomcam_meteor.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestCLI:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Automatic meteor detection" in result.output

    def test_run_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_config_validate(self, runner, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("camera:\n  host: test\n")
        result = runner.invoke(cli, ["config", "--validate", "-c", str(config_file)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_config_show(self, runner, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("camera:\n  host: test\n")
        result = runner.invoke(cli, ["config", "-c", str(config_file)])
        assert result.exit_code == 0
        assert "test" in result.output

    @patch("atomcam_meteor.pipeline.Pipeline")
    @patch("atomcam_meteor.services.db.StateDB")
    @patch("atomcam_meteor.services.lock.FileLock")
    def test_run_dry_run(self, mock_lock, mock_db, mock_pipeline, runner, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(f"paths:\n  lock_path: {tmp_path}/test.lock\n  db_path: {tmp_path}/test.db\n")

        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_inst = MagicMock()
        mock_db.from_path.return_value = mock_db_inst

        from atomcam_meteor.pipeline import PipelineResult
        mock_pipeline.return_value.execute.return_value = PipelineResult(
            date_str="20250101", clips_processed=0, detections_found=0,
            composite_path=None, video_path=None, dry_run=True,
        )

        result = runner.invoke(cli, ["run", "--dry-run", "-c", str(config_file)])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()

    def test_serve(self, runner, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            f"paths:\n  download_dir: {tmp_path}/dl\n  output_dir: {tmp_path}/out\n"
        )
        (tmp_path / "dl").mkdir()
        (tmp_path / "out").mkdir()
        with patch("uvicorn.run") as mock_run:
            result = runner.invoke(cli, ["serve", "-c", str(config_file)])
            assert mock_run.called
