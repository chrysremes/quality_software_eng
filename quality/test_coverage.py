"""Test coverage analysis: measures how well source modules are covered by tests.

Evaluates:
- Test file existence per source module
- Test-to-source ratio (lines of test code vs lines of source code)
- Test function/class count per module
- Assertion density in test files
- Coverage of source functions by test functions (name-matching heuristic)
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Logical module → test directory mapping
# ---------------------------------------------------------------------------

MODULE_TEST_DIRS = {
    "shared": "tests",  # shared is tested via system/integration tests
    "database_api.app": "database_api/tests",
    "model_inference.app": "model_inference/tests",
    "model_training.utils": "model_training/tests",
}

# Top-level integration/system tests
INTEGRATION_TEST_DIR = "tests"


@dataclass
class ModuleCoverageResult:
    """Test coverage metrics for one logical module."""

    module_name: str
    source_files: int = 0
    source_loc: int = 0
    source_functions: int = 0
    test_files: int = 0
    test_loc: int = 0
    test_functions: int = 0
    test_assertions: int = 0
    covered_function_names: int = 0  # source funcs with a matching test

    @property
    def test_to_source_ratio(self) -> float:
        """Lines of test code per line of source code."""
        return self.test_loc / self.source_loc if self.source_loc else 0.0

    @property
    def function_coverage_pct(self) -> float:
        """Percentage of source functions that have a matching test."""
        return (
            self.covered_function_names / self.source_functions * 100
            if self.source_functions
            else 100.0
        )

    @property
    def assertion_density(self) -> float:
        """Assertions per test function."""
        return (
            self.test_assertions / self.test_functions if self.test_functions else 0.0
        )

    @property
    def has_tests(self) -> bool:
        return self.test_files > 0


@dataclass
class TestCoverageReport:
    modules: Dict[str, ModuleCoverageResult] = field(default_factory=dict)
    total_test_files: int = 0
    total_test_functions: int = 0
    integration_test_files: int = 0
    integration_test_functions: int = 0

    @property
    def avg_test_to_source_ratio(self) -> float:
        ratios = [m.test_to_source_ratio for m in self.modules.values() if m.source_loc > 0]
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def avg_function_coverage(self) -> float:
        coverages = [m.function_coverage_pct for m in self.modules.values() if m.source_functions > 0]
        return sum(coverages) / len(coverages) if coverages else 0.0

    @property
    def modules_without_tests(self) -> List[str]:
        return [n for n, m in self.modules.items() if not m.has_tests]

    @property
    def avg_assertion_density(self) -> float:
        densities = [m.assertion_density for m in self.modules.values() if m.test_functions > 0]
        return sum(densities) / len(densities) if densities else 0.0

    @property
    def overall_coverage_score(self) -> float:
        """Combined coverage score [0, 1]. Higher is better."""
        if not self.modules:
            return 0.0

        # Component 1: all modules have tests (0 or 1 per module)
        modules_with_tests = sum(1 for m in self.modules.values() if m.has_tests)
        existence_score = modules_with_tests / len(self.modules)

        # Component 2: test-to-source ratio (ideal >= 1.0)
        ratio = self.avg_test_to_source_ratio
        ratio_score = min(1.0, ratio)  # cap at 1.0

        # Component 3: function coverage
        func_cov = self.avg_function_coverage / 100.0

        # Component 4: assertion density (ideal >= 2.0 per test function)
        density = self.avg_assertion_density
        density_score = min(1.0, density / 2.0)

        # Component 5: integration tests exist
        integration_score = 1.0 if self.integration_test_functions > 0 else 0.0

        return (
            0.25 * existence_score
            + 0.25 * ratio_score
            + 0.25 * func_cov
            + 0.15 * density_score
            + 0.10 * integration_score
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_loc(source: str) -> int:
    count = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _extract_function_names(tree: ast.AST) -> List[str]:
    """Extract all function/method names from an AST."""
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return names


def _count_assertions(tree: ast.AST) -> int:
    """Count assert statements and common assertion calls."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            # pytest/unittest assertions: assert_*, assertEqual, assertTrue, etc.
            func_name = ""
            if isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                func_name = node.func.id
            if func_name.startswith("assert") or func_name.startswith("Assert"):
                count += 1
    return count


def _match_test_to_source(
    test_names: List[str], source_names: List[str]
) -> int:
    """Heuristic matching: how many source functions have a corresponding test.

    A source function `foo_bar` is considered covered if any test function
    contains `foo_bar` in its name (e.g., `test_foo_bar`, `test_foo_bar_edge_case`).
    """
    covered = 0
    for src_name in source_names:
        if src_name.startswith("_"):
            # Strip leading underscores for matching
            clean = src_name.lstrip("_")
        else:
            clean = src_name
        if not clean:
            continue
        for test_name in test_names:
            if clean in test_name:
                covered += 1
                break
    return covered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_test_coverage(
    source_files: List[Path], project_root: Path
) -> TestCoverageReport:
    """Analyze test coverage by comparing source modules against test directories."""
    from .coupling import LOGICAL_MODULES, _resolve_module

    report = TestCoverageReport()

    # Initialize per-module results
    for mod_name in LOGICAL_MODULES:
        report.modules[mod_name] = ModuleCoverageResult(module_name=mod_name)

    # Gather source file data per module
    module_source_funcs: Dict[str, List[str]] = {m: [] for m in LOGICAL_MODULES}

    for fp in source_files:
        mod = _resolve_module(fp, project_root)
        if mod is None:
            continue
        source = fp.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(fp))
        except SyntaxError:
            continue

        entry = report.modules[mod]
        entry.source_files += 1
        entry.source_loc += _count_loc(source)
        funcs = _extract_function_names(tree)
        entry.source_functions += len(funcs)
        module_source_funcs[mod].extend(funcs)

    # Discover and analyze test files per module
    module_test_funcs: Dict[str, List[str]] = {m: [] for m in LOGICAL_MODULES}

    for mod_name, test_dir_rel in MODULE_TEST_DIRS.items():
        test_dir = project_root / test_dir_rel
        if not test_dir.is_dir():
            continue
        for fp in sorted(test_dir.rglob("*.py")):
            if "__pycache__" in str(fp):
                continue
            if not (fp.name.startswith("test_") or fp.name == "conftest.py"):
                continue

            source = fp.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(fp))
            except SyntaxError:
                continue

            entry = report.modules[mod_name]
            entry.test_files += 1
            entry.test_loc += _count_loc(source)
            test_funcs = [
                n for n in _extract_function_names(tree) if n.startswith("test_")
            ]
            entry.test_functions += len(test_funcs)
            entry.test_assertions += _count_assertions(tree)
            module_test_funcs[mod_name].extend(test_funcs)
            report.total_test_files += 1
            report.total_test_functions += len(test_funcs)

    # Match test functions to source functions
    for mod_name in LOGICAL_MODULES:
        report.modules[mod_name].covered_function_names = _match_test_to_source(
            module_test_funcs[mod_name], module_source_funcs[mod_name]
        )

    # Integration / system tests
    integ_dir = project_root / INTEGRATION_TEST_DIR
    if integ_dir.is_dir():
        for fp in sorted(integ_dir.rglob("test_*.py")):
            if "__pycache__" in str(fp):
                continue
            source = fp.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(fp))
            except SyntaxError:
                continue
            test_funcs = [
                n for n in _extract_function_names(tree) if n.startswith("test_")
            ]
            report.integration_test_files += 1
            report.integration_test_functions += len(test_funcs)
            report.total_test_files += 1
            report.total_test_functions += len(test_funcs)

    return report
