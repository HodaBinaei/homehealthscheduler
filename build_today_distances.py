"""
Extracts today's driving/walking/cycling distance data (keyed by pid_cid, in the correct
hhs Distances schema format -- {"distances": {"pid_cid": {from_location_id, to_location_id,
distance_km, distance_minute}}}) from data_today/distances.json, and saves each mode as its
own file in a "today/" folder.

Run this any time data_today/distances.json is available (i.e. you have a real day export
that includes it, not just patients.json/caregivers.json).
"""
import json, os

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'
DAY_EXPORT_DIR = f'{PROJECT_ROOT}/data_today'
DISTANCES_FILENAME = 'distances.json'  # <- edit if yours has a different name
TODAY_OUTPUT_DIR = f'{PROJECT_ROOT}/today'

os.makedirs(TODAY_OUTPUT_DIR, exist_ok=True)

dist_path = f'{DAY_EXPORT_DIR}/{DISTANCES_FILENAME}'
if not os.path.isfile(dist_path):
    raise SystemExit(f"'{dist_path}' not found -- this script needs your day export's "
                      f"distances.json (with driving/walking/cycling sections) to be in "
                      f"{DAY_EXPORT_DIR}. If you don't have one, this step isn't needed.")

with open(dist_path) as f:
    dist_raw = json.load(f)

date = dist_raw['date']
for mode in ['driving', 'walking', 'cycling']:
    if mode not in dist_raw['distances']:
        print(f"NOTE: '{mode}' not present in {DISTANCES_FILENAME} -- skipped.")
        continue
    # This IS already exactly the correct {"distances": {key: DistanceItem}} shape per the
    # hhs schema -- no reformatting needed, just save each mode's section directly.
    out = dist_raw['distances'][mode]
    out_path = f'{TODAY_OUTPUT_DIR}/{mode}_data.json'
    with open(out_path, 'w') as f:
        json.dump(out, f)
    print(f"Saved {out_path}: {len(out['distances'])} entries")

print(f"\nAll today's distance files saved to {TODAY_OUTPUT_DIR}/ (for {date})")
