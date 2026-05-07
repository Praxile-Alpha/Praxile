from __future__ import annotations

__version__ = "0.1.0"

PRAXILE_DIR = ".praxile"
CONFIG_FILE = "config.json"

DEFAULT_EXCLUDES = {
    ".git",
    ".praxile",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "coverage",
}

SENSITIVE_GLOBS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*id_rsa*",
    "**/*id_dsa*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "*id_rsa*",
    "*id_ed25519*",
    "**/.aws/**",
    ".aws/**",
    "**/.ssh/**",
    ".ssh/**",
    "**/secrets.*",
    "**/*secret*",
    "**/*credential*",
]

DANGEROUS_COMMAND_PATTERNS = [
    "rm -rf",
    "rm -fr",
    "sudo ",
    "su ",
    "dd if=",
    "mkfs",
    "diskutil erase",
    "chmod -R",
    "chown -R",
    "git reset",
    "git clean",
    "shutdown",
    "reboot",
    "curl | sh",
    "curl|sh",
    "wget | sh",
    "wget|sh",
    ":(){",
]

DEFAULT_ALLOWED_COMMAND_PREFIXES = [
    "python -m pytest",
    "pytest",
    "python -m unittest",
    "npm test",
    "npm run test",
    "npm run lint",
    "npm run build",
    "pnpm test",
    "pnpm run test",
    "pnpm run lint",
    "pnpm run build",
    "yarn test",
    "yarn lint",
    "yarn build",
    "bun test",
    "bun run test",
    "bun run lint",
    "bun run build",
    "cargo test",
    "go test",
    "git status",
    "git diff",
]
