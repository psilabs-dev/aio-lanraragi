"""
Benchmarking tools (for pytest runs at `tests/benchmark/`).

Not (exactly) related to search_benchmark.py, but useful for reference.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

@dataclass
class TimingRecord:
    name: str
    iteration: int
    elapsed_s: float

@dataclass
class BenchmarkCollector:
    """
    Collects timing records during a benchmark session and writes JSON output.
    """
    label: str
    records: list[TimingRecord] = field(default_factory=list)

    def record(self, name: str, iteration: int, elapsed_s: float):
        self.records.append(TimingRecord(name=name, iteration=iteration, elapsed_s=elapsed_s))

    def to_dict(self) -> dict:
        by_name: dict[str, list[float]] = {}
        for r in self.records:
            by_name.setdefault(r.name, []).append(r.elapsed_s)

        benchmarks = {}
        for name, times in by_name.items():
            sorted_times = sorted(times)
            n = len(sorted_times)
            benchmarks[name] = {
                "samples":  n,
                "times_s":  times,
                "min_s":    sorted_times[0],
                "max_s":    sorted_times[-1],
                "mean_s":   sum(sorted_times) / n,
                "p50_s":    sorted_times[(n - 1) // 2],
                "p95_s":    sorted_times[min(int(n * 0.95), n - 1)],
                "p99_s":    sorted_times[min(int(n * 0.99), n - 1)],
            }

        return {
            "label": self.label,
            "benchmarks": benchmarks,
        }

    def write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        LOGGER.info(f"Benchmark results written to {path}")
