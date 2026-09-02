#!/usr/bin/env python3
"""
run_pipeline.py -- runs the whole homehealthscheduler pipeline in the correct order.

Usage:
    python3 run_pipeline.py                  # core pipeline only (needed for phase-2 day files)
    python3 run_pipeline.py --all-reports     # core pipeline + all the Excel workbooks
    python3 run_pipeline.py --day-analysis    # core pipeline + the human-readable day analysis reports
    python3 run_pipeline.py --all             # everything

Stops immediately if any step fails, showing that step's actual error output --
it never carries on to a later step whose input the failed step was supposed to produce.
"""
import subprocess
import sys
import time

# Each stage: (script, human description). Stages run strictly in this order; within a
# stage the scripts are independent of each other and always run in the listed order too.
CORE_STAGE = [
    ("1_build_roster.py", "Build the core roster from your visit export"),
    ("4b_compute_carer_presence.py", "Compute each carer's own presence window"),
    ("12_build_distance_matrix.py", "Extract real driving distances (carer x client)"),
    ("15_build_cancellation_analysis.py", "Analyse cancellation patterns"),
]

PHASE2_STAGE = [
    ("13_build_hhs_day_files.py", "Build patient/caregivers/feasibility JSON for the target day"),
]

REPORTS_STAGE = [
    ("5_build_carer_relative_output_v3.py", "Carer_Weekday_Roster_v3.xlsx"),
    ("6_build_permanent_clients.py", "Carer_Permanent_Clients.xlsx"),
    ("8_build_client_weekday_visits.py", "Client_Weekday_Visits.xlsx"),
    ("9_build_repeating_roster.py", "Carer_Repeating_Roster_2mo.xlsx"),
    ("10_build_roster_coverage.py", "Carer_Roster_Coverage.xlsx"),
    ("11_build_geographic_coverage.py", "Carer_Geographic_Coverage.xlsx"),
    ("16_build_cancellation_report.py", "Cancellation_Pattern_Analysis.xlsx"),
]

DAY_ANALYSIS_STAGE = [
    ("14_build_day_analysis.py", "Human-readable day analysis (single day)"),
    ("17_build_day_analysis_week.py", "Human-readable day analysis (full week)"),
]


def run_step(script, description):
    print(f"\n{'=' * 70}")
    print(f"  {script}")
    print(f"  {description}")
    print(f"{'=' * 70}")
    t0 = time.time()
    result = subprocess.run([sys.executable, script], capture_output=False)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"\n!!! FAILED: {script} (exit code {result.returncode}, after {elapsed:.1f}s) !!!")
        print("Stopping here -- later steps depend on this one's output and would fail too.")
        sys.exit(1)
    print(f"--- done in {elapsed:.1f}s ---")


def main():
    args = sys.argv[1:]
    run_reports = "--all-reports" in args or "--all" in args
    run_day_analysis = "--day-analysis" in args or "--all" in args

    plan = list(CORE_STAGE)
    if run_reports:
        plan += REPORTS_STAGE
    plan += PHASE2_STAGE
    if run_day_analysis:
        plan += DAY_ANALYSIS_STAGE

    print(f"Running {len(plan)} steps:")
    for script, desc in plan:
        print(f"  - {script}: {desc}")

    overall_start = time.time()
    for script, desc in plan:
        run_step(script, desc)

    total = time.time() - overall_start
    print(f"\n{'=' * 70}")
    print(f"  ALL {len(plan)} STEPS COMPLETED SUCCESSFULLY in {total / 60:.1f} min")
    print(f"  Check the ./output/ folder for the generated files.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
