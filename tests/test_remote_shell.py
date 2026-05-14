import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "UI" / "backend"))

from remote_shell import conda_env_path_command  # noqa: E402


def test_conda_env_path_command_prefers_env_bin_fast_path():
    command = conda_env_path_command("ReLL", "cd /repo && ./burner --help")

    assert 'env_dir="$base/envs/$CONDA_ENV_NAME"' in command
    assert 'export PATH="$env_dir/bin:$PATH"' in command
    assert "bash -c" in command
    assert "conda run -n" in command
    assert command.index('export PATH="$env_dir/bin:$PATH"') < command.index("conda run -n")
