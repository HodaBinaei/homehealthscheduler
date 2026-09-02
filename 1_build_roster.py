import csv, sys, json
from collections import defaultdict
from datetime import datetime

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'

csv.field_size_limit(sys.maxsize)

CSV_PATH = f'{PROJECT_ROOT}/data/VisitExport.csv'
USERS_PATH = f'{PROJECT_ROOT}/data/users-new.json'
CLIENTS_PATH = f'{PROJECT_ROOT}/data/clients-new.json'

def norm(s):
    if s is None:
        return ''
    return ' '.join(s.strip().split()).lower()

# --- load active carers ---
with open(USERS_PATH) as f:
    users = json.load(f)['user']

active_carers = {}  # normalized "lastname, firstname" -> display name
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        key = norm(f"{last}, {first}")
        display = f"{first} {last}".strip()
        active_carers[key] = display

print(f"Active caregivers: {len(active_carers)}")

# --- load active clients ---
with open(CLIENTS_PATH) as f:
    clients = json.load(f)['client']

active_clients = {}
for c in clients:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        key = norm(f"{last}, {first}")
        display = f"{first} {last}".strip()
        active_clients[key] = display

print(f"Active clients: {len(active_clients)}")

# --- stream CSV ---
WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']

# carer -> weekday -> list of visit dicts
roster = defaultdict(lambda: defaultdict(list))

total = 0
kept = 0
unmatched_carer = set()
unmatched_client = set()
not_personal_care = 0
cancelled = 0

def strip_loc_suffix(name):
    # "Walsh (Oughterard), Patrick" -> remove the parenthetical branch tag before matching
    import re
    return re.sub(r'\s*\([^)]*\)', '', name or '')

with open(CSV_PATH, newline='', encoding='utf-8', errors='replace') as f:
    reader = csv.DictReader(f)
    for row in reader:
        total += 1

        svc_type = (row.get('Actual Service Type Description') or '').strip()
        if svc_type != 'Personal Care':
            not_personal_care += 1
            continue

        cancel_desc = (row.get('Cancellation Description') or '').strip()
        if cancel_desc:
            cancelled += 1
            continue

        emp_name = (row.get('Actual Employee Name') or '').strip()
        loc_name = (row.get('Service Location Name') or '').strip()
        if not emp_name or not loc_name:
            continue

        emp_key = norm(emp_name)
        if emp_key not in active_carers:
            unmatched_carer.add(emp_name)
            continue

        loc_key_raw = norm(loc_name)
        loc_key_clean = norm(strip_loc_suffix(loc_name))
        client_display = None
        if loc_key_raw in active_clients:
            client_display = active_clients[loc_key_raw]
        elif loc_key_clean in active_clients:
            client_display = active_clients[loc_key_clean]
        else:
            unmatched_client.add(loc_name)
            continue

        # Roster is anchored on the Service Requirement time (the scheduled/rostered slot),
        # not the Actual time (when the visit really happened) -- Actual is kept alongside
        # for variance/punctuality notes downstream.
        req_start_str = (row.get('Service Requirement Start Date And Time') or '').strip()
        req_end_str = (row.get('Service Requirement End Date And Time') or '').strip()
        actual_start_str = (row.get('Actual Start Date And Time') or '').strip()
        actual_end_str = (row.get('Actual End Date And Time') or '').strip()

        try:
            req_start_dt = datetime.strptime(req_start_str, '%d/%m/%Y %H:%M:%S')
            req_end_dt = datetime.strptime(req_end_str, '%d/%m/%Y %H:%M:%S')
        except Exception:
            continue  # no usable requirement time -> can't roster this visit

        try:
            actual_start_dt = datetime.strptime(actual_start_str, '%d/%m/%Y %H:%M:%S')
            actual_end_dt = datetime.strptime(actual_end_str, '%d/%m/%Y %H:%M:%S')
        except Exception:
            actual_start_dt = None
            actual_end_dt = None

        weekday = WEEKDAYS[req_start_dt.weekday()]
        carer_display = active_carers[emp_key]

        roster[carer_display][weekday].append({
            'client': client_display,
            'start': req_start_dt.strftime('%d/%m/%Y %H:%M:%S'),
            'end': req_end_dt.strftime('%d/%m/%Y %H:%M:%S'),
            'start_dt': req_start_dt,
            'end_dt': req_end_dt,
            'actual_start_dt': actual_start_dt,
            'actual_end_dt': actual_end_dt,
            'date': req_start_dt.strftime('%d/%m/%Y'),
        })
        kept += 1

print(f"Total rows: {total}")
print(f"Not Personal Care: {not_personal_care}")
print(f"Cancelled (non-empty cancellation desc): {cancelled}")
print(f"Kept (matched active carer+client): {kept}")
print(f"Unmatched carer names (sample 15): {list(unmatched_carer)[:15]}  total={len(unmatched_carer)}")
print(f"Unmatched client/location names (sample 15): {list(unmatched_client)[:15]}  total={len(unmatched_client)}")
print(f"Carers with at least one visit: {len(roster)}")

# --- merge "split" visits into their single-visit equivalent ---
# Some care needs are delivered flexibly: on some dates as one long visit, on others as
# several back-to-back shorter visits covering the exact same time window (e.g. a 12-hour
# cover slot done as one 09:00-21:00 visit some weeks, and as two 09:00-15:00 / 15:00-21:00
# visits other weeks). Left alone, downstream weekday/time-of-day clustering treats the
# second half of the split days as a totally separate, much less consistent "slot" from the
# single-visit days -- even though coverage was actually continuous every time. This merges
# back-to-back same-carer/same-client segments on a given date into one synthetic visit
# ONLY when that exact window (start-end) is also seen covered by a single visit on other
# dates for the same (carer, client, weekday) -- so it only fires for genuine alternating
# 1-visit/N-visit patterns, never for two visits that just happen to be scheduled close
# together but are otherwise unrelated.
MERGE_GAP_TOLERANCE_MIN = 15

def merge_split_visits(roster):
    merged_count = 0
    affected_keys = set()
    for carer, wd_map in roster.items():
        for wd, visits in wd_map.items():
            by_date = defaultdict(list)
            for v in visits:
                by_date[v['start_dt'].date()].append(v)

            # first pass: find which (window_start, window_end) appear BOTH as a single
            # visit on some date AND as multiple contiguous visits on another date
            windows_single = set()
            windows_multi = set()
            for date, day_visits in by_date.items():
                day_visits_sorted = sorted(day_visits, key=lambda v: v['start_dt'])
                clients_today = defaultdict(list)
                for v in day_visits_sorted:
                    clients_today[v['client']].append(v)
                for client, cvisits in clients_today.items():
                    if len(cvisits) == 1:
                        window = (cvisits[0]['start_dt'].strftime('%H:%M'),
                                  cvisits[0]['end_dt'].strftime('%H:%M'))
                        windows_single.add((client, window))
                    else:
                        contiguous = all(
                            abs((cvisits[i + 1]['start_dt'] - cvisits[i]['end_dt']).total_seconds() / 60)
                            <= MERGE_GAP_TOLERANCE_MIN
                            for i in range(len(cvisits) - 1)
                        )
                        if contiguous:
                            window = (cvisits[0]['start_dt'].strftime('%H:%M'),
                                      cvisits[-1]['end_dt'].strftime('%H:%M'))
                            windows_multi.add((client, window))

            eligible_windows = windows_single & windows_multi
            if not eligible_windows:
                continue

            # second pass: actually merge multi-segment days whose window qualifies
            new_visits = []
            for date, day_visits in by_date.items():
                clients_today = defaultdict(list)
                for v in day_visits:
                    clients_today[v['client']].append(v)
                for client, cvisits in clients_today.items():
                    cvisits.sort(key=lambda v: v['start_dt'])
                    if len(cvisits) > 1:
                        window = (cvisits[0]['start_dt'].strftime('%H:%M'),
                                  cvisits[-1]['end_dt'].strftime('%H:%M'))
                        if (client, window) in eligible_windows:
                            first, last = cvisits[0], cvisits[-1]
                            merged = {
                                'client': client,
                                'start': first['start'], 'end': last['end'],
                                'start_dt': first['start_dt'], 'end_dt': last['end_dt'],
                                'actual_start_dt': first['actual_start_dt'],
                                'actual_end_dt': last['actual_end_dt'],
                                'date': first['date'],
                            }
                            new_visits.append(merged)
                            merged_count += 1
                            affected_keys.add((carer, client, wd))
                            continue
                    new_visits.extend(cvisits)
            wd_map[wd] = new_visits
    return merged_count, affected_keys

merged_count, affected_keys = merge_split_visits(roster)
print(f"Split-visit merge: {merged_count} multi-segment days merged into single visits")
print(f"Affected (carer, client, weekday) combos: {sorted(affected_keys)}")

# save intermediate as json-serializable pickle for next step
import pickle
with open(f'{PROJECT_ROOT}/roster_data.pkl', 'wb') as f:
    pickle.dump(dict(roster), f)
print("Saved roster_data.pkl")
