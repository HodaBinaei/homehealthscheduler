"""
Diagnostic: traces exactly why a specific (prid, crid) pair got the weight it did.
Run this in the same folder as build_full_recompute_real_day.py, after that script has
already run once (so output/patient.json exists).

Usage: python3 diagnose_weight.py <prid> <crid>
Example: python3 diagnose_weight.py 342 554
"""
import sys, json

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'

prid_target = sys.argv[1] if len(sys.argv) > 1 else '342'
crid_target = sys.argv[2] if len(sys.argv) > 2 else '554'

# Reuse the full foundation from the real script, unmodified, up to (not including) the
# feasibility-building loop -- so this sees EXACTLY the same data/logic as the real run.
lines = open('build_full_recompute_real_day.py').readlines()
cut_idx = next(i for i, l in enumerate(lines)
               if l.strip() == 'for p in patients:' and 'find_slot_raw_scores' in ''.join(lines[i:i + 15]))
exec(''.join(lines[:cut_idx]))

with open('data_today/patients.json') as f:
    orig_patients = json.load(f)['patients']
with open('data_today/caregivers.json') as f:
    orig_caregivers = json.load(f)['caregivers']
with open('output/patient.json') as f:
    out_patients = json.load(f)

p_orig = next((p for p in orig_patients if p['prid'] == prid_target), None)
c_orig = next((c for c in orig_caregivers if c['crid'] == crid_target), None)
p_out = next((p for p in out_patients if p['prid'] == prid_target), None)

if not p_orig or not c_orig:
    print(f"prid {prid_target} or crid {crid_target} not found in today's data.")
    sys.exit(1)

client = f"{p_orig.get('name','')} {p_orig.get('lastname','')}".strip()
carer = f"{c_orig.get('name','')} {c_orig.get('lastname','')}".strip()
print(f"prid {prid_target} = client '{client}'")
print(f"crid {crid_target} = carer '{carer}'")
print(f"Our recomputed start_time_soft for this prid: {p_out['request_window']['start_time_soft']}")
start_minute = p_out['request_window']['start_time_soft']

raw_here = find_slot_raw_scores(client, TARGET_WEEKDAY, start_minute)
print(f"\nAll historical carers found for this slot (client='{client}', weekday={TARGET_WEEKDAY}, "
      f"near minute {start_minute}):")
for c, r in sorted(raw_here.items(), key=lambda x: -x[1]):
    print(f"  {c}: raw={r:.5f}")

if not raw_here:
    print("  (none found -- this pair's weight must be coming from the geographic 'extend' "
          "fallback, not slot history)")
else:
    main_carer = max(raw_here, key=raw_here.get)
    print(f"\nHistorical top ('main') carer for this slot: {main_carer}")

    carers_today = set()
    for c in orig_caregivers:
        carers_today.add(f"{c.get('name','')} {c.get('lastname','')}".strip())
    print(f"Is main carer working today? {main_carer in carers_today}")

    raw_today = {c: r for c, r in raw_here.items() if c in carers_today}
    print(f"\nCarers from this slot's history who ARE working today: {list(raw_today.keys())}")
    print(f"(if only 1 name appears here besides an absent main, that carer legitimately gets 1.0 -- "
          f"if 2+ names appear, none of them should be 1.0 unless one of them IS the main carer)")

fp_row = None
with open('output/crid_prid_feasible.json') as f:
    for row in json.load(f):
        if row['prid'] == prid_target and row['crid'] == crid_target:
            fp_row = row
            break
print(f"\nActual computed weight for this pair: {fp_row}")
