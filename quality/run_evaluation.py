#!/usr/bin/env python3
"""Code quality evaluation pipeline — CI-ready entry point.

Usage:
    python -m quality.run_evaluation [--json] [--threshold SCORE] [--output FILE]

Exit codes:
    0  — fitness score >= threshold (default 6.0)
    1  — fitness score < threshold (quality gate failed)
    2  — analysis error
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

from .cohesion import analyze_cohesion
from .connascence import analyze_connascence
from .coupling import LOGICAL_MODULES, analyze_coupling
from .doc_quality import analyze_quality
from .fitness import FitnessResult, compute_fitness
from .logical_components import analyze_logical_components
from .performance import analyze_performance
from .test_coverage import analyze_test_coverage

# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

# Directories to scan (relative to project root)
SOURCE_DIRS = [
    "shared",
    "database_api/app",
    "database_api/main.py",
    "model_inference/app",
    "model_inference/main.py",
    "model_training/utils",
]

EXCLUDE_PATTERNS = {"__pycache__", ".pyc", "test_", "conftest"}


def discover_source_files(project_root: Path) -> List[Path]:
    """Discover Python source files to analyze."""
    files: List[Path] = []
    for entry in SOURCE_DIRS:
        target = project_root / entry
        if target.is_file() and target.suffix == ".py":
            files.append(target)
        elif target.is_dir():
            for fp in sorted(target.rglob("*.py")):
                if any(pat in fp.name for pat in EXCLUDE_PATTERNS):
                    continue
                if any(pat in str(fp) for pat in ("__pycache__",)):
                    continue
                files.append(fp)
    return files


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _format_text_report(result: FitnessResult, elapsed: float, file_count: int) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("  CODE QUALITY EVALUATION REPORT")
    lines.append("=" * 72)
    lines.append(f"  Files analyzed : {file_count}")
    lines.append(f"  Analysis time  : {elapsed:.2f}s")
    lines.append("")

    for key, dim in result.dimensions.items():
        bar_filled = int(dim.raw_score * 20)
        bar = "#" * bar_filled + "-" * (20 - bar_filled)
        lines.append(
            f"  {dim.name:<22} [{bar}] "
            f"{dim.raw_score:.2f} x{dim.weight} = {dim.weighted_score:.2f}"
        )
        for dk, dv in dim.details.items():
            lines.append(f"    {dk}: {dv}")
        lines.append("")

    lines.append("-" * 72)
    lines.append(
        f"  FITNESS SCORE: {result.total_score:.2f} / {result.max_possible:.0f}"
        f"   Grade: {result.grade}"
    )
    lines.append("-" * 72)
    return "\n".join(lines)


def _format_markdown_report(result: FitnessResult, elapsed: float, file_count: int) -> str:
    """Generate a beautiful markdown report."""
    lines = []
    
    # Header
    grade_emoji = {
        "A": "🟢",
        "B": "🟡",
        "C": "🟠",
        "D": "🔴",
        "F": "⚫",
    }.get(result.grade, "⚪")
    
    lines.append("# Code Quality Evaluation Report")
    lines.append("")
    lines.append(
        f"**Fitness Score:** {result.total_score:.2f} / {result.max_possible:.0f} "
        f"{grade_emoji} **Grade: {result.grade}**"
    )
    lines.append("")
    
    # Metadata
    lines.append("## Metadata")
    lines.append(f"- **Files analyzed:** {file_count}")
    lines.append(f"- **Analysis time:** {elapsed:.2f}s")
    lines.append("")
    
    # Detailed dimensions
    lines.append("## Quality Dimensions")
    lines.append("")
    
    for key, dim in result.dimensions.items():
        # Progress bar
        bar_filled = int(dim.raw_score * 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        scaled_score = dim.raw_score * 10
        percentage = scaled_score * 10
        
        lines.append(f"### {dim.name}")
        lines.append(f"`{bar}` {scaled_score:.1f}/10 ({percentage:.0f}%)")
        lines.append("")
        lines.append(f"- **Score:** {dim.raw_score:.2f}")
        lines.append(f"- **Weight:** {dim.weight}x")
        lines.append(f"- **Weighted Score:** {dim.weighted_score:.2f}")
        lines.append("")
        
        if dim.details:
            lines.append("**Details:**")
            for dk, dv in dim.details.items():
                lines.append(f"- {dk}: {dv}")
            lines.append("")
    
    # Summary
    lines.append("## Summary")
    summary_items = [
        f"📊 **Total Score:** {result.total_score:.2f}",
        f"🎯 **Maximum Possible:** {result.max_possible:.0f}",
        f"📈 **Completion:** {((result.total_score / result.max_possible) * 100):.1f}%",
        f"🏆 **Grade:** {result.grade}",
    ]
    for item in summary_items:
        lines.append(f"- {item}")
    
    return "\n".join(lines)


def _build_json_report(
    result: FitnessResult, elapsed: float, file_count: int
) -> dict:
    dimensions = {}
    for key, dim in result.dimensions.items():
        dimensions[key] = {
            "name": dim.name,
            "raw_score": dim.raw_score,
            "weight": dim.weight,
            "weighted_score": dim.weighted_score,
            "details": dim.details,
        }
    return {
        "fitness_score": result.total_score,
        "max_possible": result.max_possible,
        "grade": result.grade,
        "files_analyzed": file_count,
        "analysis_time_seconds": round(elapsed, 2),
        "dimensions": dimensions,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Code quality fitness evaluation")
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of text"
    )
    parser.add_argument(
        "--markdown", "--md", action="store_true", help="Output markdown report"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=6.0,
        help="Minimum fitness score to pass (default: 6.0)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write report to file instead of stdout",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Project root directory (default: auto-detect)",
    )
    args = parser.parse_args()

    # Determine project root
    if args.project_root:
        project_root = Path(args.project_root).resolve()
    else:
        # Auto-detect: walk up from this file until we find docker-compose.yml
        candidate = Path(__file__).resolve().parent.parent
        if (candidate / "docker-compose.yml").exists():
            project_root = candidate
        else:
            project_root = Path.cwd()

    # Discover files
    source_files = discover_source_files(project_root)
    if not source_files:
        print("ERROR: No source files found.", file=sys.stderr)
        return 2

    start = time.perf_counter()

    # Run all analyzers
    cohesion_report = analyze_cohesion(source_files)
    coupling_report = analyze_coupling(source_files, project_root)
    connascence_report = analyze_connascence(source_files)
    quality_report = analyze_quality(source_files)
    logical_report = analyze_logical_components(source_files, project_root)
    performance_report = analyze_performance(source_files, project_root)
    coverage_report = analyze_test_coverage(source_files, project_root)

    # Compute fitness
    result = compute_fitness(
        cohesion=cohesion_report,
        coupling=coupling_report,
        connascence=connascence_report,
        quality=quality_report,
        logical=logical_report,
        performance=performance_report,
        test_coverage=coverage_report,
    )

    elapsed = time.perf_counter() - start

    # Format output
    if args.json:
        report_data = _build_json_report(result, elapsed, len(source_files))
        output = json.dumps(report_data, indent=2)
    elif args.markdown:
        output = _format_markdown_report(result, elapsed, len(source_files))
    else:
        output = _format_text_report(result, elapsed, len(source_files))

    # Write output
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(output)

    # CI gate
    if result.total_score < args.threshold:
        print(
            f"\nQUALITY GATE FAILED: {result.total_score:.2f} < {args.threshold:.1f}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
