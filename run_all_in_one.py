"""
homehealthscheduler -- full pipeline in one file.

Runs everything from raw data through the phase-2 scheduling files:
  1. Build the roster from your visit export (Service Requirement anchored)
  2. Compute each carer's own presence window
  3. Extract real driving distances (carer x client)
  4. Analyse cancellation patterns
  5. Build the carer-relative foundation (set roster, flexibility, geographic reach)
  6. Build the extend_feasibility classifier (ported from a prior, more rigorous version
     of this pipeline -- Wilson confidence intervals on off-routine conversion, temporal
     span rules, dynamic caseload-deficit gating)
  7. Read today_patients.csv / today_carers.csv and build patient/caregivers/feasibility
     JSON for that day, validated against hhs.py

Just edit the CONFIG section below to point at your files, then:
    python3 run_all_in_one.py
"""
import csv, sys, json, math, re, datetime
from collections import defaultdict, Counter

csv.field_size_limit(sys.maxsize)

# =============================================================================
# CONFIG -- edit these paths and the target day
# =============================================================================
import os
PROJECT_ROOT = os.environ.get(
    'HHS_PROJECT_ROOT',
    os.path.dirname(os.path.abspath(__file__)),
)
CSV_PATH = f'{PROJECT_ROOT}/data/VisitExport.csv'
USERS_PATH = f'{PROJECT_ROOT}/data/users-new.json'
CLIENTS_PATH = f'{PROJECT_ROOT}/data/clients-new.json'
DRIVING_DATA_PATH = f'{PROJECT_ROOT}/data/driving_data.json'
TODAY_PATIENTS_PATH = f'{PROJECT_ROOT}/data_today/today_patients.csv'
TODAY_CARERS_PATH = f'{PROJECT_ROOT}/data_today/today_carers.csv'
OUTPUT_DIR = f'{PROJECT_ROOT}/output'
HHS_SCHEMA_PATH = PROJECT_ROOT  # folder containing hhs.py

TARGET_DATE = datetime.date(2026, 8, 2)
TARGET_WEEKDAY = TARGET_DATE.strftime('%A')

# Dislikes: (client, carer) pairs that must NEVER be assigned together. Defined once, here,
# and used in two places: stripped from the roster entirely (below, right after roster
# building) so their shared history never distorts anyone ELSE's statistics (other carers'
# consistency %, the extend_feasibility classifier's "regular carer" determination for this
# client, etc.), and re-applied as a final weight=0.0 safety net right before the feasibility
# file is written (in case the geographic "extend" fallback would otherwise reintroduce this
# pairing on a short-staffed day). Add more pairs here as needed -- this is the only place a
# dislike should be declared.
DISLIKES = [
    ('Fiona Buchannon (DS)', 'Bridget Madden'),
]

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
TIME_GAP_MINUTES = 90
DAILY_FALLBACK_MIN_ACTIVE_DAYS = 5
MAX_SCHEMA_MINUTE = (23 * 60 + 59) * 2  # schema's actual max (2 days' worth) -- allows overnight shifts

import os
os.makedirs(OUTPUT_DIR, exist_ok=True)


def norm(s):
    if s is None:
        return ''
    return ' '.join(s.strip().split()).lower()


def strip_loc_suffix(name):
    return re.sub(r'\s*\([^)]*\)', '', name or '')


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


print("=" * 70)
print("STEP 1: Build roster from visit export (Service Requirement anchored)")
print("=" * 70)

with open(USERS_PATH) as f:
    users = json.load(f)['user']
active_carers = {}  # normalized "lastname, firstname" -> display name
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        active_carers[norm(f"{last}, {first}")] = f"{first} {last}".strip()
print(f"Active caregivers: {len(active_carers)}")

with open(CLIENTS_PATH) as f:
    clients_json = json.load(f)['client']
active_clients = {}
for c in clients_json:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        active_clients[norm(f"{last}, {first}")] = f"{first} {last}".strip()
print(f"Active clients: {len(active_clients)}")

roster = defaultdict(lambda: defaultdict(list))  # carer -> weekday -> [visit dict]
carer_dates = defaultdict(set)  # carer's own presence window (any client, active or not)

total = kept = not_personal_care = cancelled = 0
unmatched_carer, unmatched_client = set(), set()

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

        req_start_str = (row.get('Service Requirement Start Date And Time') or '').strip()
        req_end_str = (row.get('Service Requirement End Date And Time') or '').strip()
        try:
            req_start_dt = datetime.datetime.strptime(req_start_str, '%d/%m/%Y %H:%M:%S')
            req_end_dt = datetime.datetime.strptime(req_end_str, '%d/%m/%Y %H:%M:%S')
        except Exception:
            continue
        carer_dates[active_carers[emp_key]].add(req_start_dt.date())

        loc_key_raw = norm(loc_name)
        loc_key_clean = norm(strip_loc_suffix(loc_name))
        client_display = active_clients.get(loc_key_raw) or active_clients.get(loc_key_clean)
        if not client_display:
            unmatched_client.add(loc_name)
            continue

        actual_start_str = (row.get('Actual Start Date And Time') or '').strip()
        actual_end_str = (row.get('Actual End Date And Time') or '').strip()
        try:
            actual_start_dt = datetime.datetime.strptime(actual_start_str, '%d/%m/%Y %H:%M:%S')
            actual_end_dt = datetime.datetime.strptime(actual_end_str, '%d/%m/%Y %H:%M:%S')
        except Exception:
            actual_start_dt = actual_end_dt = None

        weekday = WEEKDAYS[req_start_dt.weekday()]
        carer_display = active_carers[emp_key]
        roster[carer_display][weekday].append({
            'client': client_display, 'start_dt': req_start_dt, 'end_dt': req_end_dt,
            'actual_start_dt': actual_start_dt, 'actual_end_dt': actual_end_dt,
        })
        kept += 1

print(f"Total rows: {total}  Not Personal Care: {not_personal_care}  Cancelled: {cancelled}  Kept: {kept}")
print(f"Unmatched carers: {len(unmatched_carer)}  Unmatched clients: {len(unmatched_client)}")

carer_presence = {c: (min(d), max(d)) for c, d in carer_dates.items()}
print(f"Computed presence window for {len(carer_presence)} carers")

# --- merge "split" visits (1x long visit some weeks, N contiguous shorter visits other
# weeks, same total window) into one synthetic visit -- see 1_build_roster.py's original
# comment for full rationale. Only fires when the exact window is seen BOTH ways.
MERGE_GAP_TOLERANCE_MIN = 15


def merge_split_visits(roster):
    merged_count = 0
    for carer, wd_map in roster.items():
        for wd, visits in wd_map.items():
            by_date = defaultdict(list)
            for v in visits:
                by_date[v['start_dt'].date()].append(v)
            windows_single, windows_multi = set(), set()
            for date, day_visits in by_date.items():
                clients_today = defaultdict(list)
                for v in day_visits:
                    clients_today[v['client']].append(v)
                for client, cvisits in clients_today.items():
                    cvisits.sort(key=lambda v: v['start_dt'])
                    if len(cvisits) == 1:
                        windows_single.add((client, (cvisits[0]['start_dt'].strftime('%H:%M'),
                                                      cvisits[0]['end_dt'].strftime('%H:%M'))))
                    else:
                        contiguous = all(
                            abs((cvisits[i + 1]['start_dt'] - cvisits[i]['end_dt']).total_seconds() / 60) <= MERGE_GAP_TOLERANCE_MIN
                            for i in range(len(cvisits) - 1))
                        if contiguous:
                            windows_multi.add((client, (cvisits[0]['start_dt'].strftime('%H:%M'),
                                                         cvisits[-1]['end_dt'].strftime('%H:%M'))))
            eligible = windows_single & windows_multi
            if not eligible:
                continue
            new_visits = []
            for date, day_visits in by_date.items():
                clients_today = defaultdict(list)
                for v in day_visits:
                    clients_today[v['client']].append(v)
                for client, cvisits in clients_today.items():
                    cvisits.sort(key=lambda v: v['start_dt'])
                    if len(cvisits) > 1:
                        window = (cvisits[0]['start_dt'].strftime('%H:%M'), cvisits[-1]['end_dt'].strftime('%H:%M'))
                        if (client, window) in eligible:
                            first, last = cvisits[0], cvisits[-1]
                            new_visits.append({
                                'client': client, 'start_dt': first['start_dt'], 'end_dt': last['end_dt'],
                                'actual_start_dt': first['actual_start_dt'], 'actual_end_dt': last['actual_end_dt'],
                            })
                            merged_count += 1
                            continue
                    new_visits.extend(cvisits)
            wd_map[wd] = new_visits
    return merged_count


merged = merge_split_visits(roster)
print(f"Split-visit merge: {merged} multi-segment days merged")

# Strip DISLIKES pairs from the roster entirely -- their shared history (if any) must not
# influence ANY downstream statistic, not just today's final feasibility weight.
_dislike_removed = 0
for dislike_client, dislike_carer in DISLIKES:
    wd_map = roster.get(dislike_carer)
    if not wd_map:
        continue
    for wd, visits in wd_map.items():
        before = len(visits)
        wd_map[wd] = [v for v in visits if v['client'] != dislike_client]
        _dislike_removed += before - len(wd_map[wd])
if _dislike_removed:
    print(f"Removed {_dislike_removed} historical visit(s) for DISLIKES pairs "
          f"(so they never influence anyone else's statistics)")

print("\n" + "=" * 70)
print("STEP 2: Extract real driving distances (carer x client)")
print("=" * 70)

carer_id_by_name = {}
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        carer_id_by_name[f"{first} {last}".strip()] = str(u.get('id'))

client_id_by_name = {}
for c in clients_json:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        client_id_by_name[f"{first} {last}".strip()] = str(c.get('id'))

with open(DRIVING_DATA_PATH) as f:
    _driving = json.load(f)
_dist = _driving['distance']
_dur = _driving.get('duration', {})
carer_client_km = {}
carer_client_min = {}
for carer_name, cid in carer_id_by_name.items():
    for client_name, did in client_id_by_name.items():
        key1, key2 = f"{cid}_{did}", f"{did}_{cid}"
        d = _dist.get(key1) or _dist.get(key2)
        t = _dur.get(key1) or _dur.get(key2)
        if d is not None:
            carer_client_km[(carer_name, client_name)] = float(d)
        if t is not None:
            carer_client_min[(carer_name, client_name)] = float(t)
del _driving, _dist, _dur
print(f"Matched {len(carer_client_km)} carer-client distance pairs, {len(carer_client_min)} travel-time pairs")

carer_info = {}
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        carer_info[display] = {
            'id': str(u.get('id')), 'gender': (u.get('gender') or 'prefer_not_to_say').lower(),
            'lat': u.get('latitude'), 'lon': u.get('longitude'), 'postcode': u.get('postcode'),
            'travel_method': (u.get('travel_method') or 'Car'),
        }

client_info = {}
client_dates = {}
for c in clients_json:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        client_info[display] = {
            'id': str(c.get('id')), 'gender': (c.get('gender') or 'prefer_not_to_say').lower(),
            'lat': c.get('latitude'), 'lon': c.get('longitude'), 'postcode': c.get('postcode'),
            'service_priority': c.get('service_priority') or 'Medium',
        }
        sd = c.get('start_date')
        ed = c.get('end_date') or c.get('termination_date')
        client_dates[display] = (
            datetime.date.fromisoformat(sd) if sd else None,
            datetime.date.fromisoformat(ed) if ed else None,
        )


def haversine_km(p1, p2):
    lat1, lon1 = p1
    lat2, lon2 = p2
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def travel_km(carer, client):
    d = carer_client_km.get((carer, client))
    if d is not None:
        return d
    ci, cj = carer_info.get(carer), client_info.get(client)
    if ci and cj and ci['lat'] and cj['lat']:
        return haversine_km((ci['lat'], ci['lon']), (cj['lat'], cj['lon']))
    return None


print("\n" + "=" * 70)
print("STEP 3: Analyse cancellation patterns")
print("=" * 70)

OPERATIONAL_CANCEL_REASONS = {
    'VNR', 'Missed call', 'Missed Call', 'Cancelled Less than 12h',
    'Cancelled with less than 24 hours notice', 'Covered  By Another Agency',
}
EXCUSED_REASONS = {
    'Hospital', 'Holiday', 'Bank Holiday', 'Respite',
    'ZzzCoronavirus – Financial Reasons', 'ZzzCoronavirus – Hospitalised', 'ZzzCoronavirus – Shielding',
}
PLACEHOLDER_PREFIX = '*'

slot_cancel_data = defaultdict(lambda: {'fulfilled': 0, 'op_cancelled': 0, 'excused': 0, 'op_cancel_planned': Counter()})

with open(CSV_PATH, newline='', encoding='utf-8', errors='replace') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if (row.get('Actual Service Type Description') or '') != 'Personal Care':
            continue
        loc_raw = (row.get('Service Location Name') or '').strip()
        if not loc_raw:
            continue
        client_display = active_clients.get(norm(loc_raw)) or active_clients.get(norm(strip_loc_suffix(loc_raw)))
        if not client_display:
            continue
        req_start = (row.get('Service Requirement Start Date And Time') or '').strip()
        try:
            req_dt = datetime.datetime.strptime(req_start, '%d/%m/%Y %H:%M:%S')
        except Exception:
            continue
        wd = WEEKDAYS[req_dt.weekday()]
        half_hour = (req_dt.hour * 60 + req_dt.minute) // 30 * 30
        rec = slot_cancel_data[(client_display, wd, half_hour)]

        cancel_desc = (row.get('Cancellation Description') or '').strip()
        emp = (row.get('Actual Employee Name') or '').strip()
        if cancel_desc in EXCUSED_REASONS:
            rec['excused'] += 1
        elif cancel_desc in OPERATIONAL_CANCEL_REASONS or emp.startswith(PLACEHOLDER_PREFIX):
            rec['op_cancelled'] += 1
            planned = (row.get('Planned Employee Name') or '').strip()
            if planned and not planned.startswith(PLACEHOLDER_PREFIX):
                rec['op_cancel_planned'][planned] += 1
        elif not cancel_desc and norm(emp) in active_carers:
            rec['fulfilled'] += 1

cancellation_lookup = {}
for (client, wd, half_hour), rec in slot_cancel_data.items():
    denom = rec['fulfilled'] + rec['op_cancelled']
    if denom < 3:
        classification, rate = 'Insufficient history', (rec['op_cancelled'] / denom if denom else None)
    else:
        rate = rec['op_cancelled'] / denom
        if rate >= 0.90:
            classification = 'Always cancelled'
        elif rate <= 0.05:
            classification = 'Never cancelled'
        else:
            classification = 'Occasionally cancelled'
    fairness_flag = ''
    if classification == 'Occasionally cancelled' and rec['op_cancel_planned']:
        top_carer, top_n = rec['op_cancel_planned'].most_common(1)[0]
        total_known = sum(rec['op_cancel_planned'].values())
        if total_known >= 2 and top_n / total_known >= 0.7:
            fairness_flag = f"Concentrated: {top_n}/{total_known} cancellations were planned-carer {top_carer}"
    cancellation_lookup[(client, wd, half_hour)] = {
        'classification': classification, 'cancellation_rate': rate, 'fairness_flag': fairness_flag,
    }

print(f"Classified {len(cancellation_lookup)} (client, weekday, slot) combinations")


def find_cancellation_record(client, wd, start_minute):
    bucket = (start_minute // 30) * 30
    for cand in (bucket, bucket - 30, bucket + 30):
        rec = cancellation_lookup.get((client, wd, cand))
        if rec:
            return rec
    return None


print("\n" + "=" * 70)
print("STEP 4: Build carer-relative foundation")
print("=" * 70)

carer_weekday_active_weeks = defaultdict(set)
carer_active_days = defaultdict(set)


def isoweek(d):
    y, w, _ = d.isocalendar()
    return (y, w)


for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            carer_weekday_active_weeks[(carer, wd)].add(isoweek(v['start_dt'].date()))
            carer_active_days[carer].add(v['start_dt'].date())


def cluster_by_time(occ):
    occ_sorted = sorted(occ, key=lambda o: o[0].hour * 60 + o[0].minute)
    clusters, cur = [], [occ_sorted[0]]
    for prev, c in zip(occ_sorted, occ_sorted[1:]):
        if (c[0].hour * 60 + c[0].minute) - (prev[0].hour * 60 + prev[0].minute) > TIME_GAP_MINUTES:
            clusters.append(cur); cur = [c]
        else:
            cur.append(c)
    clusters.append(cur)
    return clusters


def classify_relative(carer, wd, occurrences, daily_fallback=None):
    dates = sorted(set(o.date() for o in occurrences))
    observed_start, observed_end = dates[0], dates[-1]
    active_weeks_all = carer_weekday_active_weeks[(carer, wd)]
    win_start_wk, win_end_wk = isoweek(observed_start), isoweek(observed_end)
    active_weeks_in_window = sorted(w for w in active_weeks_all if win_start_wk <= w <= win_end_wk)
    hit_weeks = set(isoweek(d) for d in dates)
    n_active = len(active_weeks_in_window)
    n_hits = len(set(active_weeks_in_window) & hit_weeks)
    if n_active < 3:
        pattern = 'Insufficient history'
        if daily_fallback is not None:
            daily_ratio, _ = daily_fallback
            pattern = 'Weekly' if daily_ratio >= 0.75 else 'Occasional'
    else:
        ratio = n_hits / n_active
        if ratio >= 0.75:
            pattern = 'Weekly'
        elif ratio >= 0.35:
            idx = {w: i for i, w in enumerate(active_weeks_in_window)}
            hit_positions = sorted(idx[w] for w in active_weeks_in_window if w in hit_weeks)
            if len(hit_positions) >= 2:
                steps = [b - a for a, b in zip(hit_positions, hit_positions[1:])]
                alt_like = sum(1 for s in steps if s == 2) / len(steps)
            else:
                alt_like = 0
            pattern = 'Fortnightly' if alt_like >= 0.5 else 'Occasional'
        else:
            pattern = 'Occasional'
    return pattern


set_roster = defaultdict(set)  # (carer, weekday) -> set of client names (Weekly only)
per_carer_client_slot_pattern = defaultdict(list)  # (carer, weekday, client) -> [(pattern, median_minute)]

for carer, wd_map in roster.items():
    per_client_tagged = defaultdict(list)
    for wd, visits in wd_map.items():
        for v in visits:
            per_client_tagged[v['client']].append((v['start_dt'], wd))
    for client, tagged in per_client_tagged.items():
        for cluster in cluster_by_time([(dt,) for dt, wd in tagged]):
            cluster_set = set(cluster)
            members = [(dt, wd) for dt, wd in tagged if (dt,) in cluster_set]
            dates_all = [dt for dt, wd in members]
            span_start, span_end = min(d.date() for d in dates_all), max(d.date() for d in dates_all)
            weekdays_covered = set(wd for _, wd in members)
            active_days = [d for d in carer_active_days.get(carer, set()) if span_start <= d <= span_end]
            daily_fallback = None
            if len(weekdays_covered) >= 4 and len(active_days) >= DAILY_FALLBACK_MIN_ACTIVE_DAYS:
                n_dates = len(set(d.date() for d in dates_all))
                daily_fallback = (n_dates / len(active_days), len(active_days))
            by_wd = defaultdict(list)
            for dt, wd in members:
                by_wd[wd].append(dt)
            for wd, dts in by_wd.items():
                pattern = classify_relative(carer, wd, dts, daily_fallback=daily_fallback)
                median_minute = sorted(d.hour * 60 + d.minute for d in dts)[len(dts) // 2]
                per_carer_client_slot_pattern[(carer, wd, client)].append((pattern, median_minute))
                if pattern == 'Weekly':
                    set_roster[(carer, wd)].add(client)

print(f"Set roster slots: {sum(len(v) for v in set_roster.values())}")

carer_does_extra = defaultdict(bool)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        roster_clients = set_roster.get((carer, wd), set())
        for v in visits:
            if v['client'] not in roster_clients:
                carer_does_extra[carer] = True
print(f"Carers who ever do extra visits: {sum(carer_does_extra.values())} / {len(carer_info)}")

carer_all_visited_clients = defaultdict(set)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            carer_all_visited_clients[carer].add(v['client'])

carer_search_radius = {}
for carer, cl in carer_all_visited_clients.items():
    dists = sorted(d for d in (travel_km(carer, c) for c in cl) if d is not None)
    if not dists:
        continue
    n = len(dists)
    median = (dists[n // 2 - 1] + dists[n // 2]) / 2 if n % 2 == 0 else dists[n // 2]
    carer_search_radius[carer] = median + 5
print(f"Carers with a computed search radius: {len(carer_search_radius)}")

_breadths = sorted(len(cl) for cl in carer_all_visited_clients.values())
MEDIAN_BREADTH = _breadths[len(_breadths) // 2] if _breadths else 1


def concentration_factor(carer):
    breadth = max(len(carer_all_visited_clients.get(carer, set())), 1)
    factor = 1 + 0.15 * math.log2(MEDIAN_BREADTH / breadth)
    return max(0.7, min(factor, 1.3))


print(f"Median carer breadth (distinct clients ever visited): {MEDIAN_BREADTH}")

# Client-side slot history: (client, weekday) -> list of clusters, each a list of (carer, dt)
# pooled across ALL carers who've ever covered that time-of-day slot. Also builds a parallel
# actual-time structure (same cluster indices) for the request-window derivation in step 7.
client_slot_history = defaultdict(list)
client_slot_actual_times = defaultdict(list)  # (client, weekday, idx) -> [(actual_start, actual_end)]
client_slot_durations = defaultdict(list)     # (client, weekday, idx) -> [(req_duration_min, actual_duration_min)]
client_visits_by_wd = defaultdict(lambda: defaultdict(list))
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            client_visits_by_wd[v['client']][wd].append(
                (carer, v['start_dt'], v['end_dt'], v['actual_start_dt'], v['actual_end_dt']))

# Visits are grouped into recurring slots using Service Requirement time (the stable,
# intended schedule -- actual times drift day to day and wouldn't cluster cleanly into "this
# is the same recurring visit"). But every per-carer STATISTIC computed from a slot (last
# visit date, recency, on-time variance, duration compression) uses the ACTUAL date/time --
# when the visit really happened, not when it was scheduled to. A visit can genuinely land
# on a different calendar day than its requirement (done late one night, early the next
# morning, etc.); anchoring recency/consistency on the requirement date alone risks working
# off a slightly wrong or stale reference point. Each cluster member carries dt (requirement,
# used for the slot's time-of-day identity/clustering) and actual_dt (used for every
# date-based calculation downstream).
for client, wd_map in client_visits_by_wd.items():
    for wd, tagged in wd_map.items():
        occ = [(dt,) for carer, dt, e_dt, a_s, a_e in tagged]
        for idx, cluster_occ in enumerate(cluster_by_time(occ)):
            cluster_times = set(cluster_occ)
            members = [(carer, dt, a_s) for carer, dt, e_dt, a_s, a_e in tagged if (dt,) in cluster_times]
            actual_pairs = [(a_s, a_e) for carer, dt, e_dt, a_s, a_e in tagged
                             if (dt,) in cluster_times and a_s is not None and a_e is not None]
            duration_pairs = [((e_dt - dt).total_seconds() / 60, (a_e - a_s).total_seconds() / 60)
                               for carer, dt, e_dt, a_s, a_e in tagged
                               if (dt,) in cluster_times and a_s is not None and a_e is not None]
            client_slot_history[(client, wd)].append(members)
            client_slot_actual_times[(client, wd, idx)] = actual_pairs
            client_slot_durations[(client, wd, idx)] = duration_pairs

print("\n" + "=" * 70)
print("STEP 5: Build extend_feasibility classifier")
print("=" * 70 + "\n" +
      "Ported from a prior, more rigorous version of this pipeline (found via conversation\n"
      "search, verified against its actual code). Adaptations noted inline where this\n"
      "pipeline's own established concepts (geographic search radius, concentration_factor)\n"
      "were reused in place of pieces from the original whose exact source wasn't available\n"
      "(e.g. 'routing key' local-area membership -> carer_search_radius).")

# ---- extend_feasibility classifier ----
# extend = True for: (a) new carers (not enough tenure yet to judge), (b) carers who take on
# new/off-routine patients at a rate at or above the peer median (compared against the
# population of other carers, not a fixed number -- there is no "right" absolute count),
# (c) carers who are geographically isolated (very few other active clients near her at all,
# so restricting her wouldn't give the solver any real alternative regardless of her own
# pattern). "Routine" = any client where she has a Weekly-classified slot (see weight=2
# in the feasibility step below) -- the SAME definition drives both the weight=2 rule and
# this classifier, deliberately, since they're the same underlying concept (her fixed
# clients vs everyone else).
NEW_CARER_TENURE_DAYS = 90
CASELOAD_WINDOW_DAYS = 182  # the "per 6 months" period the off-routine rate is normalized to

carer_routine_clients = defaultdict(set)
for (carer, wd, client), slots in per_carer_client_slot_pattern.items():
    for pattern, median_minute in slots:
        if pattern == 'Weekly':
            carer_routine_clients[carer].add(client)

carer_off_routine_rate = {}
for carer, all_clients in carer_all_visited_clients.items():
    off_routine = all_clients - carer_routine_clients.get(carer, set())
    presence = carer_presence.get(carer)
    tenure_days = (presence[1] - presence[0]).days if presence else 0
    if tenure_days < NEW_CARER_TENURE_DAYS:
        carer_off_routine_rate[carer] = None  # too new to judge -- handled as extend=True below
    else:
        carer_off_routine_rate[carer] = len(off_routine) / max(tenure_days / CASELOAD_WINDOW_DAYS, 0.1)

_established_rates = sorted(r for r in carer_off_routine_rate.values() if r is not None)
MEDIAN_OFF_ROUTINE_RATE = _established_rates[len(_established_rates) // 2] if _established_rates else 0.0
print(f"Median off-routine (new-patient) rate across established carers: "
      f"{MEDIAN_OFF_ROUTINE_RATE:.2f} per {CASELOAD_WINDOW_DAYS}-day period")

# "Nobody around her" means genuinely nowhere left to expand -- NOT just a small raw count
# of nearby clients. A carer with only 16 reachable clients but who's visited just 1 of them
# had 15 real, untapped opportunities -- that's a closed pattern, not isolation. True
# isolation needs BOTH: (a) few total clients reachable to her at all (bottom quartile
# across carers), AND (b) she's already covering most of that small pool (so there's
# realistically nothing left to reach). Either alone is not enough.
carer_nearby_total_count = {}
carer_nearby_coverage_ratio = {}
for carer in carer_all_visited_clients:
    radius = carer_search_radius.get(carer)
    if radius is None:
        continue
    visited = carer_all_visited_clients.get(carer, set())
    nearby = [cl for cl in client_info if (d := travel_km(carer, cl)) is not None and d <= radius]
    total = len(nearby)
    covered = sum(1 for cl in nearby if cl in visited)
    carer_nearby_total_count[carer] = total
    carer_nearby_coverage_ratio[carer] = (covered / total) if total > 0 else 0.0

_nearby_totals = sorted(carer_nearby_total_count.values())
ISOLATED_TOTAL_THRESHOLD = _nearby_totals[len(_nearby_totals) // 4] if _nearby_totals else 0  # bottom quartile
ISOLATED_COVERAGE_THRESHOLD = 0.5  # must already cover at least half of her small nearby pool
print(f"Isolated-carer thresholds: <= {ISOLATED_TOTAL_THRESHOLD} total nearby clients "
      f"AND >= {ISOLATED_COVERAGE_THRESHOLD:.0%} of them already visited")

confirmed_picky = set()  # carers whose own routine-based rate lands them closed, for reporting
for carer, rate in carer_off_routine_rate.items():
    if rate is not None and rate < MEDIAN_OFF_ROUTINE_RATE:
        confirmed_picky.add(carer)


def carer_extend_feasibility_flag(carer):
    if (carer_nearby_total_count.get(carer, 0) <= ISOLATED_TOTAL_THRESHOLD
            and carer_nearby_coverage_ratio.get(carer, 0) >= ISOLATED_COVERAGE_THRESHOLD):
        return True  # nobody around her to be selective about
    rate = carer_off_routine_rate.get(carer)
    if rate is None:
        return True  # new carer -- not enough tenure to judge
    return rate >= MEDIAN_OFF_ROUTINE_RATE




print("\n" + "=" * 70)
print(f"STEP 6: {TARGET_DATE} ({TARGET_WEEKDAY}) -- read today's patients/carers")
print("=" * 70)


def to_minutes(dt, base_date=TARGET_DATE):
    day_offset = (dt.date() - base_date).days
    return day_offset * 1440 + dt.hour * 60 + dt.minute


def _parse_hhmm(s, base_date):
    hh, mm = s.strip().split(':')
    return datetime.datetime.combine(base_date, datetime.time(int(hh), int(mm)))


day_requirements = []
with open(TODAY_PATIENTS_PATH, newline='') as f:
    for row in csv.DictReader(f):
        client = row['client_name'].strip()
        start_dt = _parse_hhmm(row['start_time'], TARGET_DATE)
        end_dt = _parse_hhmm(row['end_time'], TARGET_DATE)
        if end_dt <= start_dt:
            end_dt += datetime.timedelta(days=1)
        day_requirements.append({'client': client, 'start_dt': start_dt, 'end_dt': end_dt})
print(f"{len(day_requirements)} patient requirements read from {TODAY_PATIENTS_PATH}")

carer_shift_input = {}
with open(TODAY_CARERS_PATH, newline='') as f:
    for row in csv.DictReader(f):
        carer = row['carer_name'].strip()
        s_dt = _parse_hhmm(row['shift_start'], TARGET_DATE)
        e_dt = _parse_hhmm(row['shift_end'], TARGET_DATE)
        if e_dt <= s_dt:
            e_dt += datetime.timedelta(days=1)
        carer_shift_input[carer] = (s_dt, e_dt)
print(f"{len(carer_shift_input)} carers read from {TODAY_CARERS_PATH}")

by_client = defaultdict(list)
for v in day_requirements:
    by_client[v['client']].append(v)


def overlaps(a, b):
    return a['start_dt'] < b['end_dt'] and b['start_dt'] < a['end_dt']


requests = []
for client, reqs in by_client.items():
    reqs_sorted = sorted(reqs, key=lambda v: v['start_dt'])
    used = [False] * len(reqs_sorted)
    for i, v in enumerate(reqs_sorted):
        if used[i]:
            continue
        group = [v]
        used[i] = True
        for j in range(i + 1, len(reqs_sorted)):
            if not used[j] and overlaps(v, reqs_sorted[j]):
                group.append(reqs_sorted[j])
                used[j] = True
        requests.append({'client': client, 'members': group})

n_double_ups = sum(1 for r in requests if len(r['members']) > 1)
print(f"Physical requests: {len(requests)} ({n_double_ups} double-ups)")

daily_carers_all = defaultdict(set)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            daily_carers_all[v['start_dt'].date()].add(carer)

same_weekday_dates = [d for d in daily_carers_all if WEEKDAYS[d.weekday()] == TARGET_WEEKDAY]
recent_same_weekday = [d for d in same_weekday_dates if d >= TARGET_DATE - datetime.timedelta(weeks=8)]
avg_carers_recent = (sum(len(daily_carers_all[d]) for d in recent_same_weekday) / len(recent_same_weekday)
                      if recent_same_weekday else len(carer_shift_input))
today_carers_n = len(carer_shift_input)
staffing_ratio = today_carers_n / avg_carers_recent if avg_carers_recent else 1.0
day_is_short_staffed = staffing_ratio < 0.85
print(f"Today's carers: {today_carers_n}, recent-{TARGET_WEEKDAY}-average: {avg_carers_recent:.1f}, "
      f"ratio: {staffing_ratio:.2f} -> {'SHORT-STAFFED' if day_is_short_staffed else 'normal/adequate'}")


def gender_enum(g):
    g = (g or '').lower()
    return g if g in ('male', 'female') else 'prefer_not_to_say'


PRIORITY_BY_SERVICE_PRIORITY = {'Medium': 0.5, 'High': 0.75, 'Very High': 0.9}

print("\n" + "=" * 70)
print("STEP 7: Build PATIENT.JSON (soft window from ACTUAL historical times)")
print("=" * 70)

patients = []
prid_counter = defaultdict(int)
visit_to_prid = {}
long_visit_flags = []

for req in requests:
    client = req['client']
    info = client_info.get(client)
    if not info:
        continue
    member_prids = []
    for v in req['members']:
        prid_counter[client] += 1
        prid = f"{info['id']}_{to_minutes(v['start_dt']):04d}_{prid_counter[client]}"
        visit_to_prid[id(v)] = prid
        member_prids.append(prid)

    for v, prid in zip(req['members'], member_prids):
        start_min = to_minutes(v['start_dt'])
        end_min = to_minutes(v['end_dt'])
        raw_duration = end_min - start_min
        SCHEMA_MAX_DURATION = 8 * 60
        if raw_duration > SCHEMA_MAX_DURATION:
            long_visit_flags.append((prid, raw_duration))
            duration = SCHEMA_MAX_DURATION
            end_min = start_min + duration
        else:
            duration = max(raw_duration, 15)
        # min_duration must satisfy the schema's own hard rule: min_duration >= 15 AND
        # duration - min_duration >= 10 (i.e. duration >= 25 whenever any min_duration is
        # reported at all). For a genuinely short visit (15-20min, common in home care --
        # a quick check-in/medication call), the true need is shorter than 25min, but the
        # schema has no way to represent that without a duration floor: min_duration stays
        # at the true floor (15), and duration is padded up to the minimum the schema allows
        # (25) rather than reporting an internally-inconsistent record. This previously
        # produced 32 real schema violations on a day with many short visits -- caught by
        # actually validating against hhs.py, not assumed correct.
        min_duration = 15
        if duration - min_duration < 10:
            duration = min_duration + 10
        others = [p for p in member_prids if p != prid]

        # Soft window: find this exact slot's historical ACTUAL times (not the Requirement
        # time just entered for today) -- 25th percentile of actual starts, 75th percentile
        # of actual ends, per the field-by-field reference from the ported pipeline. Falls
        # back to today's own requirement start/end if this slot has no actual-time history
        # yet (brand new client/slot).
        best_key, best_diff = None, None
        for cidx, cluster in enumerate(client_slot_history.get((client, TARGET_WEEKDAY), [])):
            minutes = [dt.hour * 60 + dt.minute for _, dt, _ in cluster]
            med = sorted(minutes)[len(minutes) // 2]
            diff = abs(med - start_min)
            if diff <= TIME_GAP_MINUTES and (best_diff is None or diff < best_diff):
                best_key, best_diff = (client, TARGET_WEEKDAY, cidx), diff

        actual_pairs = client_slot_actual_times.get(best_key, []) if best_key else []
        if actual_pairs:
            actual_start_mins = [to_minutes(a_s) % 1440 for a_s, a_e in actual_pairs]
            actual_end_mins = [to_minutes(a_e) % 1440 for a_s, a_e in actual_pairs]
            p25_start = percentile(actual_start_mins, 25)
            p75_end = percentile(actual_end_mins, 75)
            start_soft = round(p25_start / 5) * 5
            end_soft = max(round(p75_end / 5) * 5, start_soft + duration)
        else:
            start_soft, end_soft = start_min, end_min
            if end_soft - start_soft < duration:
                end_soft = start_soft + duration

        hard_buffer = 20
        start_hard = max(min(start_soft - 15, start_soft - hard_buffer), 0)
        end_hard = min(max(end_soft + 15, end_soft + hard_buffer), MAX_SCHEMA_MINUTE)

        # duration_reduction_priority: how much this slot's real visits have historically run
        # shorter than their own requirement duration (mean actual/requirement ratio). A slot
        # that's reliably compressed in practice tolerates further reduction well; one that
        # always runs full-length (or over) does not.
        duration_pairs = client_slot_durations.get(best_key, []) if best_key else []
        if duration_pairs:
            ratios = [a / r for r, a in duration_pairs if r > 0]
            mean_ratio = sum(ratios) / len(ratios) if ratios else 1.0
            duration_reduction_priority = round(min(max(1 - mean_ratio, 0.0), 1.0), 2)
        else:
            duration_reduction_priority = 0.3  # no history -- neutral default

        # request_window_priority: driven directly by this slot's own operational
        # cancellation rate (VNR / Missed call / Covered by another agency / assigned to a
        # Management-Moira-Temporary placeholder) -- a slot that reliably happens gets high
        # priority; one that's frequently cancelled gets low priority, since committing solver
        # resources to it is less likely to pay off. Client's stated service_priority is used
        # only as a fallback when there's no cancellation history for this slot at all.
        cancel_rec = find_cancellation_record(client, TARGET_WEEKDAY, start_min)
        if cancel_rec and cancel_rec['cancellation_rate'] is not None:
            request_window_priority = round(min(max(1 - cancel_rec['cancellation_rate'], 0.05), 0.95), 2)
        else:
            request_window_priority = PRIORITY_BY_SERVICE_PRIORITY.get(info['service_priority'], 0.5)

        # soft_window_violation_level: how strictly this visit actually happens on time --
        # the standard deviation of its real historical ACTUAL start times. A slot whose
        # actual start barely varies gets a LOW violation_level (per the schema: higher value
        # = less penalty, so low = strict/high-penalty, correctly enforcing tight timing on a
        # reliably punctual visit). A slot whose actual start historically wanders gets a HIGH
        # violation_level (more tolerance, since drifting is normal for it).
        if actual_pairs and len(actual_start_mins) >= 2:
            mean_m = sum(actual_start_mins) / len(actual_start_mins)
            variance = sum((m - mean_m) ** 2 for m in actual_start_mins) / len(actual_start_mins)
            std_dev = math.sqrt(variance)
            soft_window_violation_level = round(min(max(std_dev / 90, 0.0), 1.0), 2)
        else:
            soft_window_violation_level = 0.5  # no/thin history -- neutral default

        all_carers_this_client_wd = set()
        for cluster in client_slot_history.get((client, TARGET_WEEKDAY), []):
            all_carers_this_client_wd.update(c for c, dt, a_s in cluster)
        rotates = len(all_carers_this_client_wd) >= 3

        patient = {
            'pid': info['id'], 'prid': prid, 'gender': gender_enum(info['gender']),
            'location_id': info['id'],
            'location': {'latitude': info['lat'], 'longitude': info['lon'], 'postcode': info['postcode']},
            'request_window': {
                'start_time_hard': start_hard, 'end_time_hard': end_hard,
                'start_time_soft': start_soft, 'end_time_soft': end_soft,
                'duration': duration, 'min_duration': min_duration,
                'duration_reduction_priority': duration_reduction_priority,
                'request_window_priority': request_window_priority,
                'soft_window_violation_level': soft_window_violation_level,
                'match_request_list': others,
            },
            'extend_feasibility': {
                # Per the other chat's own design, patient-side extend is a flat default --
                # extension permission is a CAREGIVER-side concept (whether she's allowed to
                # travel outside her usual range), not something a patient's own record
                # should conditionally restrict. Always True; only max_distance_km/
                # max_time_minutes vary, reflecting whether this slot has historically
                # rotated across multiple carers or stayed with one.
                'extend': True,
                'max_distance_km': 15.0 if rotates else 5.0,
                'max_time_minutes': 45 if rotates else 20,
                'max_distance_border_crossings_km': 10,
                'max_time_border_crossings_minutes': 60,
            },
            '_slot_key': best_key,  # internal only -- stripped before writing; lets the
                                     # double-up tightening pass look up this exact visit's
                                     # own historical actual-duration data
        }
        patients.append(patient)

print(f"Built {len(patients)} patient records")
if long_visit_flags:
    print(f"NOTE: {len(long_visit_flags)} visit(s) exceeded the schema's 8h max duration, capped:")
    for prid, raw_dur in long_visit_flags:
        print(f"   {prid}: actual {raw_dur / 60:.1f}h -> capped to 8h")

# ---------------------------------------------------------------------------
# Tighten double-up windows so the solver can't schedule them sequentially.
#
# Some double-ups are a genuine partial overlap, not a full one -- e.g. a 30min task nested
# inside a 90min visit, not two 90min visits at once. Forcing every member to the SAME
# duration (an earlier version of this fix) would wrongly stretch the 30min leg out to
# 90min. Instead: share the same WINDOW across the group (sized to the longest member's own
# duration), but leave every member's own `duration` exactly as computed -- untouched. The
# longest member's duration then exactly fills the shared window (zero slack), so THEY are
# pinned to occupy the whole window; any other member placed anywhere inside that same
# window necessarily overlaps them, however short their own visit is and wherever within
# the window the solver puts it. This holds regardless of whether the true historical
# overlap is at the start, middle, or end of the longer visit.
# ---------------------------------------------------------------------------
patient_by_prid = {p['prid']: p for p in patients}
seen_groups = set()
tightened = 0
for p in patients:
    others = p['request_window']['match_request_list']
    if not others:
        continue
    group_prids = frozenset([p['prid']] + others)
    if group_prids in seen_groups:
        continue
    seen_groups.add(group_prids)
    group = [patient_by_prid[prid] for prid in group_prids if prid in patient_by_prid]
    if len(group) < 2:
        continue

    # Window size = MEAN of the biggest member's own historical ACTUAL durations, not just
    # its computed `duration` field -- a more direct, real-world-grounded anchor. Falls back
    # to the `duration` field if this slot has no actual-duration history. Never allowed to
    # come out SMALLER than any member's own duration -- the mean actual duration can
    # legitimately be shorter than the stated duration, but the schema requires the window
    # to fit every member's own duration regardless.
    biggest = max(group, key=lambda g: g['request_window']['duration'])
    dur_pairs = client_slot_durations.get(biggest.get('_slot_key'), []) if biggest.get('_slot_key') else []
    if dur_pairs:
        window_span = round(sum(a for r, a in dur_pairs) / len(dur_pairs))
    else:
        window_span = biggest['request_window']['duration']
    window_span = max(window_span, biggest['request_window']['duration'])
    if window_span - 15 < 10:
        window_span = 25
    group_start_soft = min(g['request_window']['start_time_soft'] for g in group)
    group_end_soft = group_start_soft + window_span
    # Hard window margin: the schema now allows a tighter 5-minute margin specifically for
    # match_request_list (double-up) requests -- the general 15-minute minimum only applies
    # to ordinary single visits.
    group_start_hard = max(group_start_soft - 5, 0)
    group_end_hard = min(group_end_soft + 5, MAX_SCHEMA_MINUTE)

    for g in group:
        g['request_window']['start_time_soft'] = group_start_soft
        g['request_window']['end_time_soft'] = group_end_soft
        g['request_window']['start_time_hard'] = group_start_hard
        g['request_window']['end_time_hard'] = group_end_hard
        # duration is NOT touched -- each member keeps its own correct visit length. But
        # min_duration IS tightened to the closest the schema legally allows (duration - 10,
        # its own hard floor of 15) -- the schema mandates AT LEAST a 10-minute compression
        # gap no matter what, so a solver willing to compress a double-up leg down to its
        # min_duration can still open up slack in an otherwise-tight shared window. This
        # doesn't remove that risk (the schema won't allow eliminating it entirely) but
        # minimizes it to the legal floor.
        g['request_window']['min_duration'] = max(g['request_window']['duration'] - 10, 15)
    tightened += len(group)

if tightened:
    print(f"Tightened {tightened} double-up patient windows across {len(seen_groups)} groups "
          f"(forced identical, minimally-wide windows so they can't be scheduled sequentially)")

print("\n" + "=" * 70)
print("STEP 8: Build CAREGIVERS.JSON (extend_feasibility from the ported classifier)")
print("=" * 70)

def weighted_percentile(values, weights, pct):
    """Weighted percentile (pct in [0,100]); values/weights are parallel lists."""
    if not values:
        return None
    pairs = sorted(zip(values, weights))
    total_weight = sum(w for v, w in pairs)
    if total_weight <= 0:
        return pairs[-1][0]
    target = total_weight * (pct / 100.0)
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


def data_driven_extend_feasibility(carer, min_observations=1):
    """Ported from the '3 year history data' chat's own data_driven_extend_feasibility --
    see build_full_recompute_real_day.py for the full rationale. Recency-weighted 90th
    percentile of her own home->client distances/times, border-crossing = the gap between
    her furthest-ever trip and that normal range. min_observations=1: even a single real
    trip is more informative than a flat default. Returns None only if she has literally
    zero distance-matched visits at all."""
    obs = []
    for wd, visits in roster.get(carer, {}).items():
        for v in visits:
            client = v['client']
            d = carer_client_km.get((carer, client))
            t = carer_client_min.get((carer, client))
            if d is None or t is None:
                continue
            days_since = max((TARGET_DATE - v['start_dt'].date()).days, 0)
            obs.append((d, t, days_since))
    if len(obs) < min_observations:
        return None

    dists = [o[0] for o in obs]
    times = [o[1] for o in obs]
    weights = [0.5 ** (o[2] / 180.0) for o in obs]

    max_distance_km = min(max(weighted_percentile(dists, weights, 90), 0.0), 100.0)
    max_time_minutes = int(round(min(max(weighted_percentile(times, weights, 90), 0), 120)))
    # Border-crossing anchor: 95th percentile, not the literal single furthest-ever trip --
    # see build_full_recompute_real_day.py for the full rationale (one rare outlier journey
    # can otherwise inflate the whole border allowance to an unrepresentative number).
    p95_distance = weighted_percentile(dists, weights, 95)
    p95_time = weighted_percentile(times, weights, 95)
    border_distance_km = int(round(min(max(p95_distance - max_distance_km, 0), 50)))
    border_time_minutes = int(round(min(max(p95_time - max_time_minutes, 0), 120)))

    return {
        'max_distance_km': round(max_distance_km, 2),
        'max_time_minutes': max_time_minutes,
        'max_distance_border_crossings_km': border_distance_km,
        'max_time_border_crossings_minutes': border_time_minutes,
    }


carers_today = sorted(carer_shift_input.keys())
caregivers = []
carer_to_crid = {}

for carer in carers_today:
    info = carer_info.get(carer)
    if not info or not info['lat']:
        print(f"WARNING: '{carer}' in {TODAY_CARERS_PATH} doesn't match an active carer with a "
              f"home coordinate -- skipped.")
        continue
    shift_start_dt, shift_end_dt = carer_shift_input[carer]
    shift_start = max(to_minutes(shift_start_dt), 0)
    shift_end = min(to_minutes(shift_end_dt), MAX_SCHEMA_MINUTE)

    crid = f"{info['id']}_{TARGET_DATE.isoformat()}"
    carer_to_crid[carer] = crid

    is_flexible = carer_does_extra.get(carer, False)
    extend = carer_extend_feasibility_flag(carer)

    data_driven = data_driven_extend_feasibility(carer)
    if data_driven:
        max_dist = data_driven['max_distance_km']
        max_time = data_driven['max_time_minutes']
        border_dist = data_driven['max_distance_border_crossings_km']
        border_time = data_driven['max_time_border_crossings_minutes']
    else:
        radius = carer_search_radius.get(carer, 10.0)
        if extend:
            max_dist, max_time = radius, 45
        else:
            max_dist = radius if day_is_short_staffed else 2.0
            max_time = 30 if day_is_short_staffed else 10
        border_dist, border_time = 10, 60

    travel_mode_map = {'Car': 'driving', 'Walk': 'walking'}
    caregivers.append({
        'cid': info['id'], 'crid': crid, 'gender': gender_enum(info['gender']),
        'travel_mode': travel_mode_map.get(info['travel_method'], 'driving'),
        'location_id': info['id'],
        'location': {'latitude': info['lat'], 'longitude': info['lon'], 'postcode': info['postcode']},
        'current_location_id': info['id'], 'start_location_id': info['id'], 'end_location_id': info['id'],
        'shift': {'start_time': shift_start, 'end_time': shift_end},
        'extend_feasibility': {
            'extend': extend, 'max_distance_km': round(max_dist, 1), 'max_time_minutes': int(max_time),
            'max_distance_border_crossings_km': int(border_dist), 'max_time_border_crossings_minutes': int(border_time),
        },
        'caregiver_usage_priority': 0.75 if is_flexible else 0.5,
    })

print(f"Built {len(caregivers)} caregiver records")
print(f"  Flexible (does extras): {sum(1 for c in carers_today if carer_does_extra.get(c))}")
print(f"  Classifier-flagged picky (extend=False): {len(confirmed_picky)}")

print("\n" + "=" * 70)
print("STEP 9: Build CRID_PRID_FEASIBILITY.JSON")
print("=" * 70)

WINDOW_DAYS = 112
STATUS_FACTORS = {'Current Primary': 1.0, 'Support / Relief': 0.5, 'Former / Relief': 0.2}


def identify_carer_status(overall_pct, days_since_last_visit):
    if days_since_last_visit > 50:
        return "Former / Relief"
    if overall_pct >= 40:
        return "Current Primary"
    return "Support / Relief"


customer_totals = Counter()
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            customer_totals[v['client']] += 1

slot_raw = {}  # key -> {carer: raw_score} -- UNNORMALIZED. Normalizing here (across ALL
# historical carers, dead or alive, working today or not) would let an absent regular
# carer's dominant historical score keep suppressing everyone else's weight even on a day
# she isn't working at all. Instead this stays raw, and normalization happens per request
# in the feasibility loop below, against only the carers actually working that day -- so
# if the regular is off and there's a carer who's genuinely covered this slot before (even
# a handful of times), she correctly becomes the best AVAILABLE option today, not an
# artificially deflated one.
slot_clusters_by_client_wd = defaultdict(list)

for (client, wd), clusters in client_slot_history.items():
    total_cust_visits = customer_totals.get(client, 0)
    if total_cust_visits <= 0:
        continue
    calls_per_day = total_cust_visits / float(WINDOW_DAYS)
    freq_factor = 1 + math.log1p(calls_per_day)

    for idx, cluster in enumerate(clusters):
        slot_total = len(cluster)
        by_carer = defaultdict(list)
        for carer, dt, a_s in cluster:
            # Use the ACTUAL date when we have it (when the visit really happened), falling
            # back to the requirement date only if actual is missing -- e.g. not yet run.
            by_carer[carer].append(a_s.date() if a_s is not None else dt.date())
        minutes = [dt.hour * 60 + dt.minute for _, dt, a_s in cluster]
        median_minute = sorted(minutes)[len(minutes) // 2]
        key = (client, wd, idx)
        slot_clusters_by_client_wd[(client, wd)].append((median_minute, key))

        raw_by_carer = {}
        for carer, dates in by_carer.items():
            last_visit = max(dates)
            days_since_last_visit = max((TARGET_DATE - last_visit).days, 0)
            overall_pct = round((len(dates) / slot_total) * 100, 1)
            status = identify_carer_status(overall_pct, days_since_last_visit)
            consistency = overall_pct / 100.0
            recency_decay = math.exp(-days_since_last_visit / 21.0)
            status_factor = STATUS_FACTORS.get(status, 0.3)
            raw = consistency * freq_factor * recency_decay * status_factor * concentration_factor(carer)
            raw_by_carer[carer] = raw

        slot_raw[key] = raw_by_carer

print(f"Computed carer affinity raw scores for {len(slot_raw)} historical (client, weekday, slot) "
      f"combinations across {len(customer_totals)} clients")


def find_slot_raw_scores(client, wd, start_minute):
    """Best-matching slot's RAW (unnormalized) carer scores, if any."""
    best_key, best_diff = None, None
    for median_minute, key in slot_clusters_by_client_wd.get((client, wd), []):
        diff = abs(median_minute - start_minute)
        if diff <= TIME_GAP_MINUTES and (best_diff is None or diff < best_diff):
            best_key, best_diff = key, diff
    return slot_raw.get(best_key, {})


# weight = 2 means "the ONLY valid assignment" to the solver, not just "strongly preferred" --
# so it must be reserved for genuine exclusivity, not just high consistency. A carer whose
# long shift has plenty of spare room, and who does see other people even occasionally, must
# NOT get 2 for a routine slot -- that would tell the solver she can never be considered for
# anyone else, hiding real, lower-priority options for other patients she actually could take.
# weight = 2 requires Weekly consistency for this exact slot PLUS at least one of:
#   (a) on every historical occurrence of this slot, it was the ONLY client she saw that
#       day (the visit consumes her whole working day -- e.g. a 12h/18h call), or
#   (b) across her entire history, she has NEVER visited anyone outside her routine at all
#       (fully exclusive, e.g. a carer with exactly one client, ever).
# Weekly-but-not-exclusive carers fall through to the normal formula-based weight instead --
# which, given her real consistency, is usually still high, just not a hard lock.
carer_clients_by_date = defaultdict(set)
for _carer, _wd_map in roster.items():
    for _wd, _visits in _wd_map.items():
        for _v in _visits:
            carer_clients_by_date[(_carer, _v['start_dt'].date())].add(_v['client'])


def carer_slot_consumes_whole_day(carer, client, wd):
    dates = [v['start_dt'].date() for v in roster.get(carer, {}).get(wd, []) if v['client'] == client]
    if not dates:
        return False
    return all(carer_clients_by_date.get((carer, d), set()) == {client} for d in dates)


def carer_fully_exclusive(carer):
    routine = carer_routine_clients.get(carer, set())
    all_clients = carer_all_visited_clients.get(carer, set())
    return len(all_clients - routine) == 0


def carer_is_weekly_for_slot(carer, client, wd, start_minute):
    """Broad check: is she Weekly-classified for this exact slot at all (regardless of
    exclusivity)? Used to separate the routine (1.0/2.0) pool from the occasional pool."""
    for pattern, median_minute in per_carer_client_slot_pattern.get((carer, wd, client), []):
        if pattern == 'Weekly' and abs(median_minute - start_minute) <= TIME_GAP_MINUTES:
            return True
    return False


def carer_deserves_weight_2(carer, client, wd, start_minute):
    """Stricter check: Weekly for this slot AND exclusive -- the 2.0 tier specifically."""
    if not carer_is_weekly_for_slot(carer, client, wd, start_minute):
        return False
    return carer_slot_consumes_whole_day(carer, client, wd) or carer_fully_exclusive(carer)


feasibility_pairs = []
seen_pairs = set()


def add_pair(prid, crid, weight):
    key = (prid, crid)
    if key in seen_pairs:
        return
    seen_pairs.add(key)
    feasibility_pairs.append({'prid': prid, 'crid': crid, 'weight': round(min(max(weight, 0.0), 2.0), 2)})


# A feasibility pair exists ONLY for a carer and patient who have real history together --
# no invented geographic-proximity fallback. A patient nobody has ever seen may legitimately
# end up with zero feasibility pairs here; that's a correct outcome of this definition, not
# a gap to paper over.
#
# Three tiers per slot:
#   2.0 -- Weekly for this exact slot AND exclusive (whole-day-consuming, or she's never
#          seen anyone outside her routine at all).
#   1.0 -- Weekly for this exact slot but NOT exclusive -- her real, regular go-to, fixed at
#          1.0 rather than run through the formula, but she genuinely has spare capacity for
#          others too.
#   <1.0 -- everyone else with real history for this slot (occasional/substitute carers),
#          scored by the existing formula, normalized to land strictly below whichever
#          routine (1.0 or 2.0) carer is present today. If NO routine carer is present today
#          at all, this group falls back to the substitute-boost logic instead.
for req in requests:
    client = req['client']
    if client not in client_info:
        continue
    for v in req['members']:
        prid = visit_to_prid[id(v)]
        start_minute = to_minutes(v['start_dt'])

        raw_here = find_slot_raw_scores(client, TARGET_WEEKDAY, start_minute)
        raw_today = {c: r for c, r in raw_here.items() if c in carer_to_crid}

        weekly_all = {c for c in raw_here if carer_is_weekly_for_slot(c, client, TARGET_WEEKDAY, start_minute)}
        weekly_today = weekly_all & set(raw_today.keys())
        weight_2_today = {c for c in weekly_today
                           if carer_slot_consumes_whole_day(c, client, TARGET_WEEKDAY) or carer_fully_exclusive(c)}
        weight_1_today = weekly_today - weight_2_today

        others_today = {c: r for c, r in raw_today.items() if c not in weekly_all}
        others_here = {c: r for c, r in raw_here.items() if c not in weekly_all}

        if weight_1_today or weight_2_today:
            # A routine carer (fixed 1.0 or 2.0) is present today -- everyone else
            # normalizes strictly below her.
            norm_base = max(raw_here[c] for c in (weight_1_today | weight_2_today))
        else:
            # No routine carer present today -- fall back to substitute-boost among the
            # occasional/non-weekly pool only.
            if others_here:
                main_other = max(others_here, key=others_here.get)
                overall_max_other = others_here[main_other]
            else:
                main_other, overall_max_other = None, 0.0
            if len(others_here) <= 1 or main_other in others_today:
                norm_base = max(others_today.values(), default=0.0)
            elif len(others_today) <= 1:
                norm_base = max(others_today.values(), default=0.0)
            else:
                norm_base = overall_max_other

        # Floor at 0.01 for anyone with real history, capped at 0.99 -- per the schema,
        # weight=0.0 means "dislike, don't assign" (a hard exclusion), and weights strictly
        # between 0.0 and 1.0 or exactly 2.0 are the only valid values -- nothing can tie or
        # exceed the fixed 1.0 (routine) tier.
        weights_here = {
            c: min(max(round(r / norm_base, 4), 0.01), 0.99) if norm_base > 0 else 0.0
            for c, r in others_today.items()
        }

        for carer in weight_2_today:
            crid = carer_to_crid.get(carer)
            if crid:
                add_pair(prid, crid, 2.0)
        for carer in weight_1_today:
            crid = carer_to_crid.get(carer)
            if crid:
                add_pair(prid, crid, 1.0)
        for carer, w in weights_here.items():
            crid = carer_to_crid.get(carer)
            if crid:
                add_pair(prid, crid, w)

# --- Manual override: dislikes (final safety net). weight=0 per the schema means "dislike,
# don't assign" -- a hard exclusion. Nobody gets 0 unless explicitly listed in DISLIKES
# (declared once, near CONFIG at the top of this file) -- this is the ONLY place a 0.0
# weight is allowed to originate (everywhere else, real history gets at least a 0.01 floor).
# Their shared history was already stripped from the roster entirely back in Step 1, so this
# is a belt-and-suspenders catch for the geographic "extend" fallback, which doesn't consult
# history at all and could otherwise reintroduce this pairing on a short-staffed day.
for dislike_client, dislike_carer in DISLIKES:
    dislike_prids = {p['prid'] for p in patients if p['pid'] == client_info.get(dislike_client, {}).get('id')}
    dislike_crid = carer_to_crid.get(dislike_carer)
    if not dislike_prids:
        print(f"NOTE: dislike '{dislike_client}' x '{dislike_carer}' -- no patient requests "
              f"for this client today, nothing to apply.")
        continue
    if not dislike_crid:
        print(f"NOTE: dislike '{dislike_client}' x '{dislike_carer}' -- carer isn't working "
              f"today, nothing to apply.")
        continue
    feasibility_pairs = [fp for fp in feasibility_pairs
                          if not (fp['prid'] in dislike_prids and fp['crid'] == dislike_crid)]
    seen_pairs = {k for k in seen_pairs if not (k[0] in dislike_prids and k[1] == dislike_crid)}
    for prid in dislike_prids:
        add_pair(prid, dislike_crid, 0.0)
    print(f"Applied dislike: '{dislike_client}' x '{dislike_carer}' -> weight 0.0 "
          f"({len(dislike_prids)} request(s))")

print(f"Built {len(feasibility_pairs)} feasibility pairs across {len(patients)} patient requests")
avg_options = len(feasibility_pairs) / len(patients) if patients else 0
print(f"Average feasible caregivers per request: {avg_options:.1f}")

# QA: every patient request must have at least one feasible carer, and every double-up
# member must be present.
prids_with_options = set(r['prid'] for r in feasibility_pairs)
all_prids = set(p['prid'] for p in patients)
zero = all_prids - prids_with_options
print(f"Patient requests with ZERO feasible carers: {len(zero)}" + (f"  {list(zero)[:5]}" if zero else ""))
double_up_prids = set()
for req in requests:
    if len(req['members']) > 1:
        for v in req['members']:
            double_up_prids.add(visit_to_prid[id(v)])
missing_double_up = double_up_prids - prids_with_options
print(f"Double-up members with ZERO feasible carers: {len(missing_double_up)}")

weight_counts = Counter(round(p['weight'], 2) for p in feasibility_pairs)
print("Weight distribution (sample):", dict(sorted(weight_counts.items())[-10:]))

# strip the internal-only tracking field before validation/output -- not part of the schema
for p in patients:
    p.pop('_slot_key', None)

print("\n" + "=" * 70)
print("STEP 10: Validate against hhs.py schema and write output")
print("=" * 70)

sys.path.insert(0, HHS_SCHEMA_PATH)
from hhs import Patient as PatientModel, Caregiver as CaregiverModel, FeasibilityPair as FeasibilityPairModel

validation_errors = []
for p in patients:
    try:
        PatientModel(**p)
    except Exception as e:
        validation_errors.append(('patient', p.get('prid'), str(e)[:200]))
for c in caregivers:
    try:
        CaregiverModel(**c)
    except Exception as e:
        validation_errors.append(('caregiver', c.get('crid'), str(e)[:200]))
for fp in feasibility_pairs:
    try:
        FeasibilityPairModel(**fp)
    except Exception as e:
        validation_errors.append(('feasibility', (fp.get('prid'), fp.get('crid')), str(e)[:200]))

print(f"Schema validation: {len(validation_errors)} errors out of "
      f"{len(patients) + len(caregivers) + len(feasibility_pairs)} records")
for e in validation_errors[:10]:
    print(' ', e)

with open(f'{OUTPUT_DIR}/patient.json', 'w') as f:
    json.dump(patients, f, indent=2)
with open(f'{OUTPUT_DIR}/caregivers.json', 'w') as f:
    json.dump(caregivers, f, indent=2)
with open(f'{OUTPUT_DIR}/crid_prid_feasible.json', 'w') as f:
    json.dump(feasibility_pairs, f, indent=2)

print(f"\nSaved patient.json, caregivers.json, crid_prid_feasible.json to {OUTPUT_DIR}/ "
      f"(for {TARGET_DATE.isoformat()})")
