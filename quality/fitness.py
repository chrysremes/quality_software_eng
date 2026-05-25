"""Fitness function that combines all quality metrics into a single [0, 10] score.

Weights (from requirements):
    Cohesion:            1.5
    Coupling:            1.5
    Connascence:         1
    Quality (Docs):      1
    Logical Components:  1.5
    Performance:         2
    Test Coverage:       1.5
                        ---
    Total weight:       10

Each dimension is first normalised to [0, 1] (1 = best), then the weighted
sum is computed to produce a final score in [0, 10].
"""

from dataclasses import dataclass, field
from typing import Dict

from .cohesion import CohesionReport
from .connascence import ConnascenceReport
from .coupling import CouplingReport
from .doc_quality import QualityReport
from .logical_components import LogicalComponentsReport
from .performance import PerformanceReport
from .test_coverage import TestCoverageReport


@dataclass
class DimensionScore:
    """Score for a single quality dimension."""

    name: str
    raw_score: float  # [0, 1]
    weight: float
    weighted_score: float  # raw_score * weight
    details: Dict[str, float] = field(default_factory=dict)


@dataclass
class FitnessResult:
    dimensions: Dict[str, DimensionScore] = field(default_factory=dict)

    @property
    def total_score(self) -> float:
        """Overall fitness score in [0, 10]."""
        return round(sum(d.weighted_score for d in self.dimensions.values()), 2)

    @property
    def max_possible(self) -> float:
        return sum(d.weight for d in self.dimensions.values())

    @property
    def grade(self) -> str:
        s = self.total_score
        if s >= 9.0:
            return "A+"
        if s >= 8.0:
            return "A"
        if s >= 7.0:
            return "B"
        if s >= 6.0:
            return "C"
        if s >= 5.0:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# Dimension scorers — each returns a value in [0, 1] (higher = better)
# ---------------------------------------------------------------------------


def _score_cohesion(report: CohesionReport) -> DimensionScore:
    """Score cohesion from LCOM and cyclomatic complexity.

    - LCOM: avg closer to 0 is better → score = 1 - avg_lcom
    - CC avg: ideally ≤ 5 (score=1), degrades linearly to 0 at 50
    - CC violations (>10): penalty per violation
    - CC critical violations (>50): heavy penalty
    """
    lcom_score = 1.0 - report.avg_lcom

    avg_cc = report.avg_complexity
    if avg_cc <= 5:
        cc_avg_score = 1.0
    elif avg_cc <= 10:
        cc_avg_score = 1.0 - (avg_cc - 5) / 10  # linear 1.0 → 0.5
    elif avg_cc <= 50:
        cc_avg_score = 0.5 - (avg_cc - 10) / 80  # linear 0.5 → 0.0
    else:
        cc_avg_score = 0.0

    # Penalty for violations
    n_violations = len(report.complexity_violations)
    n_critical = len(report.critical_complexity_violations)
    violation_penalty = min(0.3, n_violations * 0.03 + n_critical * 0.1)

    raw = max(0.0, (0.5 * lcom_score + 0.5 * cc_avg_score) - violation_penalty)
    raw = min(1.0, raw)

    return DimensionScore(
        name="Cohesion",
        raw_score=round(raw, 4),
        weight=1.5,
        weighted_score=round(raw * 1.5, 4),
        details={
            "avg_lcom": round(report.avg_lcom, 4),
            "avg_cyclomatic_complexity": round(avg_cc, 2),
            "max_cyclomatic_complexity": report.max_complexity,
            "violations_over_10": n_violations,
            "critical_over_50": n_critical,
            "lcom_sub_score": round(lcom_score, 4),
            "cc_avg_sub_score": round(cc_avg_score, 4),
        },
    )


def _score_coupling(report: CouplingReport) -> DimensionScore:
    """Score coupling from instability, abstractness, and distance.

    - Distance from main sequence: 0 ideal, 1 worst → score = 1 - avg_distance
    - Pain/uselessness zones: penalty per module in bad zone
    - Balanced instability (not all 0 or all 1): bonus
    """
    dist_score = 1.0 - report.avg_distance
    zone_penalty = (
        len(report.pain_zone_modules) * 0.15
        + len(report.uselessness_zone_modules) * 0.10
    )
    raw = max(0.0, dist_score - zone_penalty)
    raw = min(1.0, raw)

    return DimensionScore(
        name="Coupling",
        raw_score=round(raw, 4),
        weight=1.5,
        weighted_score=round(raw * 1.5, 4),
        details={
            "avg_instability": round(report.avg_instability, 4),
            "avg_abstractness": round(report.avg_abstractness, 4),
            "avg_distance_from_main_seq": round(report.avg_distance, 4),
            "pain_zone_modules": len(report.pain_zone_modules),
            "uselessness_zone_modules": len(report.uselessness_zone_modules),
        },
    )


def _score_connascence(report: ConnascenceReport) -> DimensionScore:
    """Score connascence: fewer issues is better, high-severity issues penalised more.

    Scoring: start at 1.0, subtract per issue:
      low: -0.005, medium: -0.01, high: -0.03
    """
    score = 1.0
    for issue in report.issues:
        if issue.severity == "high":
            score -= 0.03
        elif issue.severity == "medium":
            score -= 0.01
        else:
            score -= 0.005
    raw = max(0.0, min(1.0, score))

    return DimensionScore(
        name="Connascence",
        raw_score=round(raw, 4),
        weight=1,
        weighted_score=round(raw * 1, 4),
        details={
            "total_issues": report.total,
            "high_severity": report.high_severity_count,
            "by_kind": report.count_by_kind,
        },
    )


def _score_quality(report: QualityReport) -> DimensionScore:
    """Score documentation and typing quality."""
    raw = report.overall_score
    return DimensionScore(
        name="Quality (Docs)",
        raw_score=round(raw, 4),
        weight=1,
        weighted_score=round(raw * 1, 4),
        details={
            "docstring_coverage": round(report.docstring_coverage, 4),
            "module_docstring_coverage": round(report.module_docstring_coverage, 4),
            "type_annotation_coverage": round(report.type_annotation_coverage, 4),
        },
    )


def _score_logical_components(report: LogicalComponentsReport) -> DimensionScore:
    """Score logical component design.

    - Demeter violations: penalty per violation
    - Mixed responsibilities: penalty per module
    - Temporal coupling risks: penalty per risk
    """
    score = 1.0
    score -= min(0.3, report.demeter_violation_count * 0.02)
    score -= report.mixed_responsibility_count * 0.1
    score -= len(report.temporal_coupling_risks) * 0.05
    raw = max(0.0, min(1.0, score))

    return DimensionScore(
        name="Logical Components",
        raw_score=round(raw, 4),
        weight=1.5,
        weighted_score=round(raw * 1.5, 4),
        details={
            "demeter_violations": report.demeter_violation_count,
            "mixed_responsibility_modules": report.mixed_responsibility_count,
            "temporal_coupling_risks": len(report.temporal_coupling_risks),
        },
    )


def _score_performance(report: PerformanceReport) -> DimensionScore:
    """Score performance characteristics.

    Primary runtime signals:
    - API latency score (inversely proportional to time_exec_penalty)
    - Execution-time deviation score (outlier-aware)
    - Memory deviation score (outlier-aware)

    Deviation signals are intentionally capped at 20% combined influence.
    """
    # Fallback static score if runtime measurements are unavailable.
    avg_len = report.avg_function_length
    if avg_len <= 15:
        static_len_score = 1.0
    elif avg_len <= 30:
        static_len_score = 1.0 - (avg_len - 15) / 30
    elif avg_len <= 50:
        static_len_score = 0.5 - (avg_len - 30) / 40
    else:
        static_len_score = 0.0
    static_penalty = min(0.3, report.long_function_count * 0.05)
    static_score = max(0.0, min(1.0, static_len_score - static_penalty))

    if report.runtime_measurements_count > 0:
        runtime_score = report.overall_runtime_performance_score
        # Blend in a small static component to avoid overfitting to sparse probes.
        raw = (0.9 * runtime_score) + (0.1 * static_score)
    else:
        raw = static_score

    raw = max(0.0, min(1.0, raw))

    details = {
        "total_loc": report.total_loc,
        "avg_function_length": round(report.avg_function_length, 2),
        "long_functions_over_50_lines": report.long_function_count,
        "total_imports": report.total_imports,
        "runtime_measurements_count": report.runtime_measurements_count,
        "api_measurements_count": report.api_measurements_count,
        "api_time_penalty": round(report.api_time_penalty, 6),
        "api_time_score": round(report.api_time_score, 6),
        "time_deviation_score": round(report.execution_time_deviation_score, 6),
        "memory_deviation_score": round(report.memory_deviation_score, 6),
        "runtime_performance_score": round(report.overall_runtime_performance_score, 6),
    }
    if report.time_outlier_metrics:
        details["time_outliers"] = report.time_outlier_metrics
    if report.memory_outlier_metrics:
        details["memory_outliers"] = report.memory_outlier_metrics
    if report.import_time_seconds is not None:
        details["import_time_seconds"] = report.import_time_seconds
    if report.peak_memory_mb is not None:
        details["peak_memory_mb"] = report.peak_memory_mb

    return DimensionScore(
        name="Performance",
        raw_score=round(raw, 4),
        weight=2,
        weighted_score=round(raw * 2, 4),
        details=details,
    )


def _score_test_coverage(report: TestCoverageReport) -> DimensionScore:
    """Score test coverage quality.

    Uses the report's composite score covering:
    - Test existence per module
    - Test-to-source ratio
    - Function-level coverage (name matching)
    - Assertion density
    - Integration test presence
    """
    raw = report.overall_coverage_score

    return DimensionScore(
        name="Test Coverage",
        raw_score=round(raw, 4),
        weight=1.5,
        weighted_score=round(raw * 1.5, 4),
        details={
            "total_test_files": report.total_test_files,
            "total_test_functions": report.total_test_functions,
            "avg_test_to_source_ratio": round(report.avg_test_to_source_ratio, 4),
            "avg_function_coverage_pct": round(report.avg_function_coverage, 2),
            "avg_assertion_density": round(report.avg_assertion_density, 2),
            "modules_without_tests": report.modules_without_tests,
            "integration_test_files": report.integration_test_files,
            "integration_test_functions": report.integration_test_functions,
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fitness(
    cohesion: CohesionReport,
    coupling: CouplingReport,
    connascence: ConnascenceReport,
    quality: QualityReport,
    logical: LogicalComponentsReport,
    performance: PerformanceReport,
    test_coverage: TestCoverageReport,
) -> FitnessResult:
    """Compute the overall fitness score combining all dimensions."""
    result = FitnessResult()
    result.dimensions["cohesion"] = _score_cohesion(cohesion)
    result.dimensions["coupling"] = _score_coupling(coupling)
    result.dimensions["connascence"] = _score_connascence(connascence)
    result.dimensions["quality"] = _score_quality(quality)
    result.dimensions["logical_components"] = _score_logical_components(logical)
    result.dimensions["performance"] = _score_performance(performance)
    result.dimensions["test_coverage"] = _score_test_coverage(test_coverage)
    return result
