import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from remote_shell import conda_env_path_command, conda_run_command  # noqa: E402


def test_conda_env_path_command_prefers_env_bin_fast_path():
    command = conda_env_path_command("ReLL", "cd /repo && ./burner --help")

    assert 'env_dir="$base/envs/$CONDA_ENV_NAME"' in command
    assert 'export PATH="$env_dir/bin:$PATH"' in command
    assert "bash -c" in command
    assert "conda run -n" in command
    assert command.index('export PATH="$env_dir/bin:$PATH"') < command.index("conda run -n")


def test_conda_env_path_command_no_conda_uses_plain_bash():
    command = conda_env_path_command(None, "cd /repo && ./burner --help")
    assert command.startswith("bash -lc ")
    assert "conda" not in command


def test_conda_run_command_no_conda_uses_plain_bash():
    command = conda_run_command(None, "cd /repo && python3 script.py")
    assert command.startswith("bash -lc ")
    assert "conda" not in command
