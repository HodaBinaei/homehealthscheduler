import csv, sys, json, datetime
from collections import defaultdict, Counter

PROJECT_ROOT = '/home/hoda/Desktop/homehealthscheduler_local_pipeline'

csv.field_size_limit(sys.maxsize)

CSV_PATH = f'{PROJECT_ROOT}/data/VisitExport.csv'
USERS_PATH = f'{PROJECT_ROOT}/data/users-new.json'
CLIENTS_PATH = f'{PROJECT_ROOT}/data/clients-new.json'

WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']

# Reasons that represent a genuine OPERATIONAL failure to fulfil an existing requirement --
# these are the ones relevant to "did we, as an agency, actually cover this call".
OPERATIONAL_CANCEL_REASONS = {
    'VNR', 'Missed call', 'Missed Call', 'Cancelled Less than 12h',
    'Cancelled with less than 24 hours notice', 'Covered  By Another Agency',
}
# Reasons that reflect the CLIENT not needing the visit that day, unrelated to staffing --
# excluded entirely from the cancellation-rate denominator (would otherwise wrongly inflate
# "unreliable slot" for clients who are simply often hospitalised, etc).
EXCUSED_REASONS = {
    'Hospital', 'Holiday', 'Bank Holiday', 'Respite',
    'ZzzCoronavirus – Financial Reasons', 'ZzzCoronavirus – Hospitalised',
    'ZzzCoronavirus – Shielding',
}
PLACEHOLDER_PREFIX = '*'  # *Ryan Moira, *Management..., *Temporary Roster -- not real carers

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
        active_carers[key] = f"{first} {last}".strip()

with open(CLIENTS_PATH) as f:
    clients = json.load(f)['client']
active_clients = {}
for c in clients:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        key = norm(f"{last}, {first}")
        active_clients[key] = f"{first} {last}".strip()

import re
def strip_loc_suffix(name):
    return re.sub(r'\s*\([^)]*\)', '', name or '')

# slot key: (client, weekday, half_hour_bucket) -> counts + planned-carer breakdown for
# operationally-cancelled occurrences (for the fairness/concentration check)
slot_data = defaultdict(lambda: {
    'fulfilled': [], 'op_cancelled': [], 'excused': 0, 'op_cancel_planned': Counter(),
})

total = 0
with open(CSV_PATH, newline='', encoding='utf-8', errors='replace') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if (row.get('Actual Service Type Description') or '') != 'Personal Care':
            continue
        loc_raw = (row.get('Service Location Name') or '').strip()
        if not loc_raw:
            continue
        loc_key_raw = norm(loc_raw)
        loc_key_clean = norm(strip_loc_suffix(loc_raw))
        client_display = active_clients.get(loc_key_raw) or active_clients.get(loc_key_clean)
        if not client_display:
            continue  # not an active client -- out of scope

        req_start = (row.get('Service Requirement Start Date And Time') or '').strip()
        try:
            req_dt = datetime.datetime.strptime(req_start, '%d/%m/%Y %H:%M:%S')
        except Exception:
            continue
        wd = WEEKDAYS[req_dt.weekday()]
        half_hour = (req_dt.hour * 60 + req_dt.minute) // 30 * 30
        slot_key = (client_display, wd, half_hour)

        cancel_desc = (row.get('Cancellation Description') or '').strip()
        emp = (row.get('Actual Employee Name') or '').strip()
        emp_key = norm(emp)

        total += 1
        rec = slot_data[slot_key]

        if cancel_desc in EXCUSED_REASONS:
            rec['excused'] += 1
        elif cancel_desc in OPERATIONAL_CANCEL_REASONS or emp.startswith(PLACEHOLDER_PREFIX):
            rec['op_cancelled'].append(req_dt.date())
            planned = (row.get('Planned Employee Name') or '').strip()
            if planned and not planned.startswith(PLACEHOLDER_PREFIX):
                rec['op_cancel_planned'][planned] += 1
        elif not cancel_desc and emp_key in active_carers:
            rec['fulfilled'].append((req_dt.date(), active_carers[emp_key]))
        # else: blank employee + blank cancellation, or other edge case -- ignore (rare)

print(f"Total Personal Care rows scanned (active clients): {total}")
print(f"Distinct (client, weekday, slot) keys: {len(slot_data)}")

# ---------------------------------------------------------------------------
# Classify each slot
# ---------------------------------------------------------------------------
ALWAYS_CANCELLED_THRESHOLD = 0.90
NEVER_CANCELLED_THRESHOLD = 0.05
MIN_OCCURRENCES = 3  # need at least this many (fulfilled+op_cancelled) to classify meaningfully

results = []
for (client, wd, half_hour), rec in slot_data.items():
    n_fulfilled = len(rec['fulfilled'])
    n_cancelled = len(rec['op_cancelled'])
    denom = n_fulfilled + n_cancelled
    if denom < MIN_OCCURRENCES:
        classification = 'Insufficient history'
        rate = n_cancelled / denom if denom else None
    else:
        rate = n_cancelled / denom
        if rate >= ALWAYS_CANCELLED_THRESHOLD:
            classification = 'Always cancelled'
        elif rate <= NEVER_CANCELLED_THRESHOLD:
            classification = 'Never cancelled'
        else:
            classification = 'Occasionally cancelled'

    fairness_flag = ''
    if classification == 'Occasionally cancelled' and rec['op_cancel_planned']:
        # was cancellation concentrated on ONE planned carer's shifts, or spread across
        # several different carers who were meant to cover it?
        top_carer, top_n = rec['op_cancel_planned'].most_common(1)[0]
        total_planned_known = sum(rec['op_cancel_planned'].values())
        if total_planned_known >= 2 and top_n / total_planned_known >= 0.7:
            fairness_flag = f"Concentrated: {top_n}/{total_planned_known} cancellations were planned-carer {top_carer}"
        elif len(rec['op_cancel_planned']) >= 2:
            fairness_flag = f"Spread across {len(rec['op_cancel_planned'])} different planned carers -- looks fair"

    results.append({
        'client': client, 'weekday': wd, 'time': f"{half_hour//60:02d}:{half_hour%60:02d}",
        'fulfilled': n_fulfilled, 'op_cancelled': n_cancelled, 'excused': rec['excused'],
        'cancellation_rate': rate, 'classification': classification, 'fairness_flag': fairness_flag,
    })

counts = Counter(r['classification'] for r in results)
print("Classification counts:", dict(counts))

with open(f'{PROJECT_ROOT}/cancellation_analysis.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
print("Saved cancellation_analysis.json")
