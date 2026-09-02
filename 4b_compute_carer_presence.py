import csv, sys, json
from datetime import datetime
from collections import defaultdict

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'

csv.field_size_limit(sys.maxsize)

CSV_PATH = f'{PROJECT_ROOT}/data/VisitExport.csv'
USERS_PATH = f'{PROJECT_ROOT}/data/users-new.json'

def norm(s):
    if s is None:
        return ''
    return ' '.join(s.strip().split()).lower()

with open(USERS_PATH) as f:
    users = json.load(f)['user']

active_carers = {}
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        key = norm(f"{last}, {first}")
        display = f"{first} {last}".strip()
        active_carers[key] = display

# Carer's OWN presence window: first/last Personal Care, non-cancelled visit date,
# across ALL clients (active or not) -- this is "as far as we have data" for that carer,
# not limited by whether their clients are still active today.
carer_dates = defaultdict(set)

with open(CSV_PATH, newline='', encoding='utf-8', errors='replace') as f:
    reader = csv.DictReader(f)
    for row in reader:
        svc_type = (row.get('Actual Service Type Description') or '').strip()
        if svc_type != 'Personal Care':
            continue
        cancel_desc = (row.get('Cancellation Description') or '').strip()
        if cancel_desc:
            continue
        emp_name = (row.get('Actual Employee Name') or '').strip()
        if not emp_name:
            continue
        emp_key = norm(emp_name)
        if emp_key not in active_carers:
            continue
        # Anchor presence on the Requirement date too, consistent with the roster itself.
        start_str = (row.get('Service Requirement Start Date And Time') or '').strip()
        if not start_str:
            continue
        try:
            d = datetime.strptime(start_str, '%d/%m/%Y %H:%M:%S').date()
        except Exception:
            continue
        carer_dates[active_carers[emp_key]].add(d)

carer_presence = {carer: (min(dates), max(dates)) for carer, dates in carer_dates.items()}

import pickle
with open(f'{PROJECT_ROOT}/carer_presence.pkl', 'wb') as f:
    pickle.dump(carer_presence, f)

print(f"Computed presence window for {len(carer_presence)} carers")
sample = list(carer_presence.items())[:5]
for c, (a, b) in sample:
    print(f"  {c}: {a} -> {b}")
