from __future__ import annotations

import shlex


def conda_run_command(conda_env: str, inner_command: str) -> str:
    env = shlex.quote(conda_env)
    inner = shlex.quote(inner_command)
    script = f"""
if command -v conda >/dev/null 2>&1; then
  conda run -n {env} bash -lc {inner}
elif [ -x "$HOME/miniconda3/bin/conda" ]; then
  "$HOME/miniconda3/bin/conda" run -n {env} bash -lc {inner}
elif [ -x "$HOME/anaconda3/bin/conda" ]; then
  "$HOME/anaconda3/bin/conda" run -n {env} bash -lc {inner}
elif [ -x "$HOME/miniforge3/bin/conda" ]; then
  "$HOME/miniforge3/bin/conda" run -n {env} bash -lc {inner}
elif [ -x "$HOME/mambaforge/bin/conda" ]; then
  "$HOME/mambaforge/bin/conda" run -n {env} bash -lc {inner}
elif [ -x "/opt/conda/bin/conda" ]; then
  "/opt/conda/bin/conda" run -n {env} bash -lc {inner}
else
  echo "conda not found on remote PATH or common install paths" >&2
  echo "checked: PATH, ~/miniconda3, ~/anaconda3, ~/miniforge3, ~/mambaforge, /opt/conda" >&2
  exit 127
fi
""".strip()
    return f"bash -lc {shlex.quote(script)}"


def conda_env_path_command(conda_env: str, inner_command: str) -> str:
    env = shlex.quote(conda_env)
    inner = shlex.quote(inner_command)
    script = f"""
CONDA_ENV_NAME={env}
for base in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge" "/opt/conda"; do
  env_dir="$base/envs/$CONDA_ENV_NAME"
  if [ -x "$env_dir/bin/python" ]; then
    export CONDA_PREFIX="$env_dir"
    export PATH="$env_dir/bin:$PATH"
    bash -c {inner}
    exit $?
  fi
done

if command -v conda >/dev/null 2>&1; then
  conda run -n "$CONDA_ENV_NAME" bash -lc {inner}
elif [ -x "$HOME/miniconda3/bin/conda" ]; then
  "$HOME/miniconda3/bin/conda" run -n "$CONDA_ENV_NAME" bash -lc {inner}
elif [ -x "$HOME/anaconda3/bin/conda" ]; then
  "$HOME/anaconda3/bin/conda" run -n "$CONDA_ENV_NAME" bash -lc {inner}
elif [ -x "$HOME/miniforge3/bin/conda" ]; then
  "$HOME/miniforge3/bin/conda" run -n "$CONDA_ENV_NAME" bash -lc {inner}
elif [ -x "$HOME/mambaforge/bin/conda" ]; then
  "$HOME/mambaforge/bin/conda" run -n "$CONDA_ENV_NAME" bash -lc {inner}
elif [ -x "/opt/conda/bin/conda" ]; then
  "/opt/conda/bin/conda" run -n "$CONDA_ENV_NAME" bash -lc {inner}
else
  echo "conda environment or conda executable not found" >&2
  echo "checked env paths under: ~/miniconda3, ~/anaconda3, ~/miniforge3, ~/mambaforge, /opt/conda" >&2
  exit 127
fi
""".strip()
    return f"bash -lc {shlex.quote(script)}"
