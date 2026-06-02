from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
import subprocess
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUALITY_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff-check", (sys.executable, "-m", "ruff", "check", ".")),
    ("ruff-format", (sys.executable, "-m", "ruff", "format", "--check", ".")),
    ("mypy", (sys.executable, "-m", "mypy", "--config-file", "pyproject.toml")),
)
FORBIDDEN_IMPORT_PREFIXES: dict[str, str] = {
    "dirac.memory.tools": "memory",
    "dirac.provider.fake": "provider",
    "dirac.provider.ollama": "provider",
    "dirac.provider.openai_compatible": "provider",
    "dirac.provider.openrouter": "provider",
}


def pytest_sessionstart(session: pytest.Session) -> None:
    del session

    failures: list[str] = []
    for check_name, command in QUALITY_COMMANDS:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(_render_process_failure(check_name, command, result))

    duplicate_modules = _find_duplicate_module_bodies()
    if duplicate_modules:
        failures.append(duplicate_modules)

    forbidden_imports = _find_forbidden_cross_owner_imports()
    if forbidden_imports:
        failures.append(forbidden_imports)

    if failures:
        rendered_failures = "\n\n".join(failures)
        raise pytest.UsageError(
            "Quality gate failed. Fix lint, typing, and encapsulation violations before running tests.\n\n"
            f"{rendered_failures}"
        )


def _iter_gate_python_files() -> list[Path]:
    files: list[Path] = [PROJECT_ROOT / "bot.py", PROJECT_ROOT / "doctor.py"]
    files.extend((PROJECT_ROOT / "dirac").rglob("*.py"))
    return sorted(file_path for file_path in files if file_path.is_file())


def _owner_for_path(file_path: Path) -> str:
    relative = file_path.relative_to(PROJECT_ROOT)
    if relative.parts[0] != "dirac" or len(relative.parts) < 2:
        return "root"
    return relative.parts[1]


def _find_duplicate_module_bodies() -> str:
    fingerprints: dict[str, list[Path]] = defaultdict(list)

    for file_path in _iter_gate_python_files():
        if file_path.name == "__init__.py":
            continue

        source = file_path.read_text(encoding="utf-8")
        if source.count("\n") < 4:
            continue

        parsed = ast.parse(source, filename=str(file_path))
        fingerprint = ast.dump(parsed, annotate_fields=False, include_attributes=False)
        fingerprints[fingerprint].append(file_path)

    duplicates = [paths for paths in fingerprints.values() if len(paths) > 1]
    if not duplicates:
        return ""

    lines = [
        "[duplicate-module-bodies] Found AST-identical Python modules. "
        "Do not duplicate module implementations across owners.",
    ]
    for group in duplicates:
        listed_paths = ", ".join(
            sorted(str(candidate.relative_to(PROJECT_ROOT)) for candidate in group)
        )
        lines.append(f"- {listed_paths}")
    return "\n".join(lines)


def _iter_imported_module_names(parsed: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []

    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.module, node.lineno))
            imports.extend(
                (f"{node.module}.{alias.name}", node.lineno)
                for alias in node.names
                if alias.name != "*"
            )

    return imports


def _find_forbidden_cross_owner_imports() -> str:
    violations: list[str] = []

    for file_path in _iter_gate_python_files():
        importer_owner = _owner_for_path(file_path)
        parsed = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

        for imported_module, line_number in _iter_imported_module_names(parsed):
            for prefix, owning_module in FORBIDDEN_IMPORT_PREFIXES.items():
                if not (
                    imported_module == prefix
                    or imported_module.startswith(f"{prefix}.")
                ):
                    continue
                if importer_owner == owning_module:
                    continue
                relative_path = file_path.relative_to(PROJECT_ROOT)
                violations.append(
                    f"- {relative_path}:{line_number} imports '{imported_module}' (owned by dirac.{owning_module}); "
                    "depend on boundary contracts instead."
                )

    if not violations:
        return ""

    header = "[encapsulation-imports] Forbidden cross-owner concrete imports detected:"
    return "\n".join([header, *sorted(violations)])


def _render_process_failure(
    check_name: str,
    command: tuple[str, ...],
    result: subprocess.CompletedProcess[str],
) -> str:
    command_text = " ".join(command)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    output = "\n".join(part for part in (stdout, stderr) if part)
    rendered_output = output or "(no output)"
    return (
        f"[{check_name}] command failed with exit code {result.returncode}: {command_text}\n"
        f"{rendered_output}"
    )
