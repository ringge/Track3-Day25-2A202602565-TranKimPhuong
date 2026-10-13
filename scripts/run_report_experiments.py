"""Reproduce the Part F experiments without changing submission code/config.

Run: python scripts/run_report_experiments.py --seed 25
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.client import ResilientClient
from gameday_lab.factory import build_client
from gameday_lab.sim.config import LabConfig, load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario


class UnfilteredClient(ResilientClient):
    """Intentionally unsafe variant used only for the compatibility experiment."""

    def ladder(self, task: str) -> list[str]:
        return list(self.order)


def measure(cfg: LabConfig, scenarios: tuple[str, ...], seed: int,
            unfiltered: bool = False, client_name: str = "resilient") -> dict[str, Any]:
    results = {}
    for name in scenarios:
        client = (UnfilteredClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=1)
                  if unfiltered else build_client(client_name, cfg))
        metrics = run_scenario(cfg, name, client, seed).metrics()
        checks = evaluate_checks(metrics, cfg.scenarios[name].checks)
        results[name] = {"metrics": metrics, "checks": checks, "pass": all(checks.values())}
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=25)
    parser.add_argument("--out", type=Path, default=Path("reports/experiments/part_f.json"))
    args = parser.parse_args()
    cfg = load_config()
    experiments: dict[str, Any] = {}

    def record(name: str, trial: LabConfig, scenarios: tuple[str, ...],
               unfiltered: bool = False, client_name: str = "resilient") -> None:
        results = measure(trial, scenarios, args.seed, unfiltered, client_name)
        experiments[name] = {"client": trial.client, "cache": trial.cache,
                             "unfiltered_tools": unfiltered, "results": results}
        for scenario, result in results.items():
            m = result["metrics"]
            fields = ("availability", "latency_p95_s", "amplification", "retry_ratio",
                      "recovery_s", "incompatible_routes")
            if client_name == "cached":
                fields = ("faq_availability", "cache_hit_ratio", "wrong_answers", "outdated_answers",
                          "stale_served", "cost_per_good_turn")
            print(name, scenario, "PASS" if result["pass"] else "FAIL", {key: m[key] for key in fields})

    record("baseline", cfg, tuple(cfg.scenarios))
    budget_scenarios = ("G2_overload_storm", "G3_brownout")
    for breaker_enabled in (False, True):
        for budget_enabled in (True, False):
            settings = deepcopy(cfg.client)
            settings["max_attempts_per_provider"] = 5
            if not breaker_enabled:
                settings["breaker"]["min_calls"] = 1_000_000_000
            if not budget_enabled:
                settings.update(retry_budget_ratio=100, retry_budget_max_tokens=1.0e9,
                                retry_budget_min_per_s=1.0e6)
            name = f"breaker_{'on' if breaker_enabled else 'off'}_budget_{'on' if budget_enabled else 'off'}"
            record(name, replace(cfg, client=settings), budget_scenarios)

    settings = {**cfg.client, "attempt_timeout_s": 30.0}
    record("timeout_30", replace(cfg, client=settings), ("G4_slow_region", "G6_provider_outage"))
    settings = {**cfg.client, "attempt_timeout_s": 8.0}
    record("timeout_8", replace(cfg, client=settings), ("G2_overload_storm",))
    record("unfiltered_tools", cfg, ("G6_provider_outage",), unfiltered=True)

    cache_cfg = load_config(scenarios_path="configs/scenarios_cache.yaml")
    cache_scenarios = ("C1_faq_outage",)
    for threshold in (0.80, cfg.cache["similarity_threshold"], 0.97):
        settings = {**cache_cfg.cache, "similarity_threshold": threshold}
        record(f"cache_threshold_{threshold:.2f}", replace(cache_cfg, cache=settings),
               cache_scenarios, client_name="cached")
    for ttl in (30, 60, 300):
        settings = {**cache_cfg.cache, "ttl_s": ttl}
        record(f"cache_ttl_{ttl}", replace(cache_cfg, cache=settings), cache_scenarios, client_name="cached")
    settings = {**cache_cfg.cache, "max_stale_s": 0}
    record("cache_no_stale", replace(cache_cfg, cache=settings), cache_scenarios, client_name="cached")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"seed": args.seed, "client_rng_seed": 1,
                                    "experiments": experiments}, indent=2) + "\n", encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
