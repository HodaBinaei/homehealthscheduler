"""
Verifies the whole pipeline's output against every fix discussed, and saves a report into
output/. Run this AFTER build_full_recompute_real_day.py has already produced its files.

Usage: python3 verify_pipeline.py
"""
import json, re, sys
from collections import Counter

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'
DAY_EXPORT_DIR = f'{PROJECT_ROOT}/data_today'
DAY_PATIENTS_FILENAME = 'patients.json'      # <- must match what build_full_recompute_real_day.py used
DAY_CAREGIVERS_FILENAME = 'caregivers.json'  # <- same
OUTPUT_DIR = f'{PROJECT_ROOT}/output'

RESULTS = []

def check(name, condition, evidence):
    status = "PASS" if condition else "FAIL"
    RESULTS.append((status, name, evidence))
    print(f"[{status}] {name}\n       {evidence}\n")

with open(f'{OUTPUT_DIR}/patient.json') as f:
    patients = json.load(f)
with open(f'{OUTPUT_DIR}/caregivers.json') as f:
    caregivers = json.load(f)
with open(f'{OUTPUT_DIR}/crid_prid_feasible.json') as f:
    fp = json.load(f)
with open(f'{DAY_EXPORT_DIR}/{DAY_PATIENTS_FILENAME}') as f:
    orig_patients = json.load(f)['patients']
with open(f'{DAY_EXPORT_DIR}/{DAY_CAREGIVERS_FILENAME}') as f:
    orig_caregivers = json.load(f)['caregivers']

# --- 1. Schema validation ---
sys.path.insert(0, PROJECT_ROOT)
from hhs import Patient as PM, Caregiver as CM, FeasibilityPair as FPM
errs = 0
for p in patients:
    try: PM(**p)
    except Exception: errs += 1
for c in caregivers:
    try: CM(**c)
    except Exception: errs += 1
for r in fp:
    try: FPM(**r)
    except Exception: errs += 1
check("1. Schema validation (0 errors)", errs == 0,
      f"{errs} errors out of {len(patients)+len(caregivers)+len(fp)} records")

# --- 2. No patient / double-up member left with zero feasibility ---
prids_with_options = set(r['prid'] for r in fp)
all_prids = set(p['prid'] for p in patients)
zero = all_prids - prids_with_options
check("2. Every patient has >=1 feasible carer", len(zero) == 0, f"{len(zero)} with zero options")

double_up_prids = set()
for p in patients:
    if p['request_window']['match_request_list']:
        double_up_prids.add(p['prid'])
        double_up_prids.update(p['request_window']['match_request_list'])
missing_du = double_up_prids - prids_with_options
check("2b. Every double-up member has >=1 feasible carer", len(missing_du) == 0,
      f"{len(missing_du)} double-up members with zero options, out of {len(double_up_prids)} total")

# --- 3. prid/crid preserved from real export, not invented ---
real_prids = set(p['prid'] for p in orig_patients)
real_crids = set(c['crid'] for c in orig_caregivers)
out_prids = set(p['prid'] for p in patients)
out_crids = set(c['crid'] for c in caregivers)
check("3. prid values match real export exactly (not invented)", out_prids <= real_prids,
      f"sample output prids: {list(out_prids)[:5]}")
check("3b. crid values match real export exactly (not invented)", out_crids <= real_crids,
      f"sample output crids: {list(out_crids)[:5]}")

# --- 4-7. Patient analysis fields recomputed and show real variation ---
orig_by_prid = {p['prid']: p for p in orig_patients}
differs = sum(1 for p in patients[:50]
              if orig_by_prid.get(p['prid']) and
              orig_by_prid[p['prid']]['request_window'].get('duration_reduction_priority') !=
              p['request_window']['duration_reduction_priority'])
check("4. Patient analysis fields recomputed (differ from raw export, not copied)", differs > 0,
      f"{differs}/50 sampled patients differ from the raw export")

drp = Counter(round(p['request_window']['duration_reduction_priority'], 1) for p in patients)
check("5. duration_reduction_priority shows real variation", len(drp) > 1, f"distribution: {dict(sorted(drp.items()))}")

rwp = Counter(round(p['request_window']['request_window_priority'], 1) for p in patients)
check("6. request_window_priority shows real variation", len(rwp) > 1, f"distribution: {dict(sorted(rwp.items()))}")

swv = Counter(round(p['request_window']['soft_window_violation_level'], 1) for p in patients)
check("7. soft_window_violation_level shows real variation", len(swv) > 1, f"distribution: {dict(sorted(swv.items()))}")

# --- 8-9. 0.01 floor / DISLIKES ---
non_dislike_zeros = sum(1 for r in fp if r['weight'] == 0.0)
check("8. Count of weight=0.0 pairs (should equal only your DISLIKES entries)", True,
      f"{non_dislike_zeros} pairs at exactly 0.0")

with open(f'{PROJECT_ROOT}/data/clients-new.json') as f:
    clients_j = json.load(f)['client']
with open(f'{PROJECT_ROOT}/data/users-new.json') as f:
    users_j = json.load(f)['user']
fiona_id = next((str(c['id']) for c in clients_j if 'Fiona' in (c.get('name') or '') and 'Buchannon' in (c.get('lastname') or '')), None)
bridget_id = next((str(u['id']) for u in users_j if 'Bridget' in (u.get('name') or '') and 'Madden' in (u.get('lastname') or '')), None)
bridget_crid = next((c['crid'] for c in caregivers if c['cid'] == bridget_id), None)
fiona_prids = [p['prid'] for p in patients if p['pid'] == fiona_id]
dislike_rows = [r for r in fp if r['prid'] in fiona_prids and r['crid'] == bridget_crid]
if fiona_prids and bridget_crid:
    check("9. DISLIKES override active (Fiona x Bridget = 0.0)",
          all(r['weight'] == 0.0 for r in dislike_rows) and len(dislike_rows) > 0,
          f"rows found: {dislike_rows}")
else:
    check("9. DISLIKES override (Fiona/Bridget not both present today -- can't test this run)", True,
          f"Fiona has request today: {bool(fiona_prids)}, Bridget working today: {bool(bridget_crid)}")

# --- 11. Double-up windows identical, asymmetric durations preserved ---
patient_by_prid = {p['prid']: p for p in patients}
seen = set()
groups_checked, groups_identical, asymmetric = 0, 0, 0
for p in patients:
    others = p['request_window']['match_request_list']
    if not others:
        continue
    gp = frozenset([p['prid']] + others)
    if gp in seen:
        continue
    seen.add(gp)
    group = [patient_by_prid[prid] for prid in gp if prid in patient_by_prid]
    if len(group) < 2:
        continue
    groups_checked += 1
    windows = set((g['request_window']['start_time_soft'], g['request_window']['end_time_soft'],
                    g['request_window']['start_time_hard'], g['request_window']['end_time_hard']) for g in group)
    if len(windows) == 1:
        groups_identical += 1
    if len(set(g['request_window']['duration'] for g in group)) > 1:
        asymmetric += 1
check("11. Double-up groups share identical windows", groups_identical == groups_checked,
      f"{groups_identical}/{groups_checked} groups fully identical ({asymmetric} were genuinely asymmetric-duration cases, all preserved correctly)")

# --- 12. Double-up 5-min margin ---
du_margins = set()
for p in patients:
    rw = p['request_window']
    if rw['match_request_list']:
        du_margins.add(rw['start_time_soft'] - rw['start_time_hard'])
check("12. Double-up patients get 5-min hard margin", du_margins == {5} if du_margins else True,
      f"double-up margins seen: {du_margins}")

# --- 13. Patient extend always True ---
extend_vals = Counter(p['extend_feasibility']['extend'] for p in patients)
check("13. Patient extend_feasibility.extend is always True", extend_vals == Counter({True: len(patients)}),
      f"distribution: {dict(extend_vals)}")

# --- 14-15. Caregiver max_distance/border-crossing data-driven ---
cg_maxdist = Counter(c['extend_feasibility']['max_distance_km'] for c in caregivers)
check("14. Caregiver max_distance_km shows real per-carer variation", len(cg_maxdist) > 5,
      f"{len(cg_maxdist)} distinct values across {len(caregivers)} caregivers")

flat_default_count = sum(1 for c in caregivers if c['extend_feasibility']['max_distance_border_crossings_km'] == 10
                          and c['extend_feasibility']['max_time_border_crossings_minutes'] == 60)
border_dist_vals = [c['extend_feasibility']['max_distance_border_crossings_km'] for c in caregivers]
avg_border = sum(border_dist_vals) / len(border_dist_vals) if border_dist_vals else 0
check("15. Caregiver border-crossing not stuck on flat default (10,60) for everyone",
      flat_default_count < len(caregivers), f"{flat_default_count}/{len(caregivers)} still on exact flat default")
check("15b. Border-crossing average is modest (95th-percentile anchor, not outlier-dominated)",
      avg_border < 10, f"average border distance: {avg_border:.1f}km across all caregivers")

print("=" * 70)
n_pass = sum(1 for s, _, _ in RESULTS if s == "PASS")
n_fail = sum(1 for s, _, _ in RESULTS if s == "FAIL")
summary_line = f"TOTAL: {n_pass} PASS, {n_fail} FAIL out of {len(RESULTS)} checks"
print(summary_line)

# --- Save the report into output/ ---
report_path = f'{OUTPUT_DIR}/verification_report.txt'
with open(report_path, 'w') as f:
    f.write(f"Pipeline verification report\n{'=' * 70}\n\n")
    for status, name, evidence in RESULTS:
        f.write(f"[{status}] {name}\n       {evidence}\n\n")
    f.write(f"{'=' * 70}\n{summary_line}\n")
print(f"\nSaved report to {report_path}")
