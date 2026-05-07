#!/usr/bin/env sh
set -eu

REPO_URL="${PRAXILE_REPO_URL:-https://github.com/Praxile-Alpha/Praxile.git}"
PYTHON_BIN="${PYTHON:-python3}"
INSTALL_METHOD="${PRAXILE_INSTALL_METHOD:-auto}"

echo "Praxile installer"
echo "Repository: ${REPO_URL}"

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required to install Praxile from GitHub." >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "error: ${PYTHON_BIN} was not found. Set PYTHON=/path/to/python3.11+ and retry." >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(
        f"error: Praxile requires Python 3.11 or newer; found {sys.version.split()[0]}"
    )
PY

install_with_pipx() {
  if ! command -v pipx >/dev/null 2>&1; then
    return 1
  fi
  echo "Installing Praxile with pipx..."
  pipx install --force "git+${REPO_URL}"
}

install_with_pip() {
  echo "Installing Praxile with ${PYTHON_BIN} -m pip..."
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    "${PYTHON_BIN}" -m pip install --upgrade "git+${REPO_URL}"
  else
    "${PYTHON_BIN}" -m pip install --user --upgrade "git+${REPO_URL}"
  fi
}

case "${INSTALL_METHOD}" in
  auto)
    if ! install_with_pipx; then
      install_with_pip
    fi
    ;;
  pipx)
    install_with_pipx
    ;;
  pip)
    install_with_pip
    ;;
  *)
    echo "error: PRAXILE_INSTALL_METHOD must be auto, pipx, or pip." >&2
    exit 1
    ;;
esac

echo
echo "Praxile installed."
echo
echo "Next steps:"
echo "  cd /path/to/your/code-project"
echo "  praxile init"
echo "  praxile setup"
echo "  praxile doctor --online"
echo "  praxile run \"Fix a small bug\" --test-command \"python -m pytest\""
echo
echo "This installer only installs the CLI. It does not create or modify .praxile/ state."
