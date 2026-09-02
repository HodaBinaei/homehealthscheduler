import pickle, json, math, datetime
from collections import defaultdict, Counter
import os
os.makedirs('./output', exist_ok=True)

TARGET_DATE = datetime.date(2026, 8, 2)   # Sunday
TARGET_WEEKDAY = 'Sunday'
WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
TIME_GAP_MINUTES = 90
DAILY_FALLBACK_MIN_ACTIVE_DAYS = 5

def norm(s):
    if s is None:
        return ''
    return ' '.join(s.strip().split()).lower()

# ---------------------------------------------------------------------------
# Load everything we've already built
# ---------------------------------------------------------------------------
with open('./roster_data.pkl', 'rb') as f:
    roster = pickle.load(f)
with open('./carer_presence.pkl', 'rb') as f:
    carer_presence = pickle.load(f)
with open('./carer_client_distances.pkl', 'rb') as f:
    carer_client_km = pickle.load(f)

with open('./users-new__4_.json') as f:
    users_json = json.load(f)['user']
with open('./clients-new__5_.json') as f:
    clients_json = json.load(f)['client']

carer_info = {}   # display name -> dict
for u in users_json:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        carer_info[display] = {
            'id': str(u.get('id')),
            'gender': (u.get('gender') or 'prefer_not_to_say').lower(),
            'lat': u.get('latitude'), 'lon': u.get('longitude'),
            'postcode': u.get('postcode'),
            'travel_method': (u.get('travel_method') or 'Car'),
            'stated_extended_feasibility': u.get('extended_feasibility'),
        }

client_info = {}
for c in clients_json:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        client_info[display] = {
            'id': str(c.get('id')),
            'gender': (c.get('gender') or 'prefer_not_to_say').lower(),
            'lat': c.get('latitude'), 'lon': c.get('longitude'),
            'postcode': c.get('postcode'),
            'service_priority': c.get('service_priority') or 'Medium',
            'stated_extended_feasibility': c.get('extended_feasibility'),
        }

def haversine_km(p1, p2):
    lat1, lon1 = p1; lat2, lon2 = p2
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1); dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def travel_km(carer, client):
    d = carer_client_km.get((carer, client))
    if d is not None:
        return d
    ci, cj = carer_info.get(carer), client_info.get(client)
    if ci and cj and ci['lat'] and cj['lat']:
        return haversine_km((ci['lat'], ci['lon']), (cj['lat'], cj['lon']))
    return None

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

def isoweek(d):
    y, w, _ = d.isocalendar()
    return (y, w)

# ---------------------------------------------------------------------------
# Rebuild carer-relative Weekly/set-roster classification (same logic as the
# main roster workbook), plus each carer's own flexibility profile and
# geographic search radius (median travel + 5km), plus a client-side view of
# "who has ever covered this exact slot" for feasibility candidates.
# ---------------------------------------------------------------------------
carer_weekday_active_weeks = defaultdict(set)
carer_active_days = defaultdict(set)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            if v['start_dt']:
                carer_weekday_active_weeks[(carer, wd)].add(isoweek(v['start_dt'].date()))
                carer_active_days[carer].add(v['start_dt'].date())

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
        pattern, ratio = 'Insufficient history', (n_hits / n_active if n_active else 0)
        if daily_fallback is not None:
            daily_ratio, _ = daily_fallback
            ratio = daily_ratio
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
    return pattern, ratio

# set_roster[(carer, weekday)] -> set of client names (Weekly only)
# per_carer_client_slot_pattern[(carer, weekday, client)] -> list of (pattern, ratio, median_minute)
# one entry per distinct time-of-day slot (so a morning + evening visit to the same client
# are correctly treated as two separate slots, matching the main roster workbook's methodology).
set_roster = defaultdict(set)
per_carer_client_slot_pattern = defaultdict(list)
for carer, wd_map in roster.items():
    per_client_tagged = defaultdict(list)
    for wd, visits in wd_map.items():
        for v in visits:
            if v['start_dt']:
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
                pattern, ratio = classify_relative(carer, wd, dts, daily_fallback=daily_fallback)
                median_minute = sorted(d.hour * 60 + d.minute for d in dts)[len(dts) // 2]
                per_carer_client_slot_pattern[(carer, wd, client)].append((pattern, ratio, median_minute))
                if pattern == 'Weekly':
                    set_roster[(carer, wd)].add(client)

# Each carer's flexibility profile: does she EVER do a visit outside her own set roster?
carer_does_extra = defaultdict(bool)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        roster_clients = set_roster.get((carer, wd), set())
        for v in visits:
            if v['client'] not in roster_clients:
                carer_does_extra[carer] = True

# Each carer's own geographic search radius: median travel distance across everywhere she
# actually visits (roster + extra), + 5km -- same methodology as Carer_Roster_Coverage.xlsx.
carer_all_visited_clients = defaultdict(set)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            carer_all_visited_clients[carer].add(v['client'])

carer_search_radius = {}
for carer, clients in carer_all_visited_clients.items():
    dists = [travel_km(carer, c) for c in clients]
    dists = sorted(d for d in dists if d is not None)
    if not dists:
        continue
    n = len(dists)
    median = (dists[n // 2 - 1] + dists[n // 2]) / 2 if n % 2 == 0 else dists[n // 2]
    carer_search_radius[carer] = median + 5

# Concentration factor: a carer's total caseload breadth (how many distinct clients she's
# EVER visited) changes how much confidence a single-slot historical ratio deserves. A carer
# with a tiny, focused caseload showing up on a client's slot is a strong signal -- that
# client is a big share of everything she does. The same nominal ratio from a carer spread
# across hundreds of clients (e.g. Vera: 305 distinct clients vs a median of ~32) is a much
# thinner, more diluted signal -- any one relationship is a small fraction of her attention.
# Below-median breadth boosts weight (up to +30%); above-median dampens it (down to -30%).
import math
_breadths = sorted(len(cl) for cl in carer_all_visited_clients.values())
MEDIAN_BREADTH = _breadths[len(_breadths) // 2] if _breadths else 1

def concentration_factor(carer):
    breadth = max(len(carer_all_visited_clients.get(carer, set())), 1)
    factor = 1 + 0.15 * math.log2(MEDIAN_BREADTH / breadth)
    return max(0.7, min(factor, 1.3))

print(f"Median carer breadth (distinct clients ever visited): {MEDIAN_BREADTH}")

# Client-side slot history: for (client, weekday), pooled clusters across ALL carers who have
# EVER done that slot, with each carer's own pattern/ratio for it -- these are the natural
# feasibility candidates for a visit in that slot, beyond just "whoever did it today".
client_slot_history = defaultdict(list)  # (client, weekday) -> list of clusters; each cluster:
                                          # list of (carer, dt) tuples at a shared time-of-day
client_visits_by_wd = defaultdict(lambda: defaultdict(list))  # client -> weekday -> [(carer, dt)]
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            if v['start_dt']:
                client_visits_by_wd[v['client']][wd].append((carer, v['start_dt']))

for client, wd_map in client_visits_by_wd.items():
    for wd, tagged in wd_map.items():
        occ = [(dt,) for carer, dt in tagged]
        for cluster_occ in cluster_by_time(occ):
            cluster_times = set(cluster_occ)
            members = [(carer, dt) for carer, dt in tagged if (dt,) in cluster_times]
            client_slot_history[(client, wd)].append(members)

print(f"Set roster slots: {sum(len(v) for v in set_roster.values())}")
print(f"Carers who ever do extra visits: {sum(carer_does_extra.values())} / {len(carer_all_visited_clients)}")
print(f"Carers with a computed search radius: {len(carer_search_radius)}")

# ---------------------------------------------------------------------------
# Read TODAY'S actual input -- who needs a visit today, and who's working today.
# This is data YOU provide (not pulled from history), since this script's whole point is
# to take a real day's patient/carer list and use HISTORY to work out feasibility for it --
# not to reproduce a day that's already in the CSV export.
#
# today_patients.csv columns: client_name,start_time,end_time   (start/end as HH:MM)
# today_carers.csv columns:   carer_name,shift_start,shift_end  (as HH:MM)
#
# A client needing a double-up (two carers at once) just gets TWO rows in
# today_patients.csv with the same client_name and overlapping times -- the script detects
# the overlap and links them automatically, same as before.
# ---------------------------------------------------------------------------
import csv as _csv

def _parse_hhmm(s, base_date):
    hh, mm = s.strip().split(':')
    return datetime.datetime.combine(base_date, datetime.time(int(hh), int(mm)))

day_requirements = []  # list of dicts: client, start_dt, end_dt  (NO carer -- not yet assigned)
with open('./today_patients.csv', newline='') as f:
    for row in _csv.DictReader(f):
        client = row['client_name'].strip()
        start_dt = _parse_hhmm(row['start_time'], TARGET_DATE)
        end_dt = _parse_hhmm(row['end_time'], TARGET_DATE)
        if end_dt <= start_dt:  # crosses midnight
            end_dt += datetime.timedelta(days=1)
        day_requirements.append({'client': client, 'start_dt': start_dt, 'end_dt': end_dt})

print(f"\n{TARGET_DATE} ({TARGET_WEEKDAY}): {len(day_requirements)} patient requirements read from today_patients.csv")

carer_shift_input = {}  # carer -> (shift_start_dt, shift_end_dt)
with open('./today_carers.csv', newline='') as f:
    for row in _csv.DictReader(f):
        carer = row['carer_name'].strip()
        s_dt = _parse_hhmm(row['shift_start'], TARGET_DATE)
        e_dt = _parse_hhmm(row['shift_end'], TARGET_DATE)
        if e_dt <= s_dt:
            e_dt += datetime.timedelta(days=1)
        carer_shift_input[carer] = (s_dt, e_dt)

print(f"{len(carer_shift_input)} carers read from today_carers.csv")

# Group same-client requirements into physical "requests": overlapping requirements for the
# same client are a double-up (one request needing 2 carers); everything else is its own
# single request.
by_client = defaultdict(list)
for v in day_requirements:
    by_client[v['client']].append(v)

def overlaps(a, b):
    return a['start_dt'] < b['end_dt'] and b['start_dt'] < a['end_dt']

requests = []  # each: {'client':..., 'members': [requirement,...]} -- members>1 means double-up
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

# ---------------------------------------------------------------------------
# Day-level staffing context: compare today's carer headcount (from today_carers.csv) to
# the average for this weekday over the recent (last 8 weeks) HISTORY -- drives the overall
# extend_feasibility bias.
# ---------------------------------------------------------------------------
daily_carers_all = defaultdict(set)
daily_visit_count_all = defaultdict(int)
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            if v['start_dt']:
                d = v['start_dt'].date()
                daily_carers_all[d].add(carer)
                daily_visit_count_all[d] += 1

same_weekday_dates = [d for d in daily_visit_count_all if WEEKDAYS[d.weekday()] == TARGET_WEEKDAY]
recent_same_weekday = [d for d in same_weekday_dates if d >= TARGET_DATE - datetime.timedelta(weeks=8)]
if recent_same_weekday:
    avg_carers_recent = sum(len(daily_carers_all[d]) for d in recent_same_weekday) / len(recent_same_weekday)
else:
    avg_carers_recent = len(carer_shift_input)

today_carers = len(carer_shift_input)
staffing_ratio = today_carers / avg_carers_recent if avg_carers_recent else 1.0
day_is_short_staffed = staffing_ratio < 0.85

print(f"Today's carers: {today_carers}, recent-{TARGET_WEEKDAY}-average: {avg_carers_recent:.1f}, "
      f"ratio: {staffing_ratio:.2f} -> {'SHORT-STAFFED' if day_is_short_staffed else 'normal/adequate'}")

# ---------------------------------------------------------------------------
# Helper: minutes since midnight
# ---------------------------------------------------------------------------
def to_minutes(dt, base_date=TARGET_DATE):
    """Minutes since midnight of base_date -- extends past 1440 for a visit that runs into
    the next calendar day (e.g. an overnight/live-in shift), which the schema explicitly
    supports (its max value is 2 days' worth of minutes)."""
    day_offset = (dt.date() - base_date).days
    return day_offset * 1440 + dt.hour * 60 + dt.minute

MAX_SCHEMA_MINUTE = (23 * 60 + 59) * 2  # schema's actual max (2 days' worth) -- allows overnight shifts

def gender_enum(g):
    g = (g or '').lower()
    if g in ('male', 'female'):
        return g
    return 'prefer_not_to_say'

PRIORITY_BY_SERVICE_PRIORITY = {'Medium': 0.5, 'High': 0.75, 'Very High': 0.9}

# Load the cancellation-pattern analysis (client, weekday, half-hour-bucket) -> classification.
# Never-cancelled slots are firm commitments (raise priority, lower tolerance for violation);
# always-cancelled slots are rarely real (lower priority); occasional ones stay near baseline.
try:
    with open('./cancellation_analysis.json') as f:
        _cancel_rows = json.load(f)
    cancellation_lookup = {}
    for r in _cancel_rows:
        hh, mm = r['time'].split(':')
        half_hour = int(hh) * 60 + int(mm)
        cancellation_lookup[(r['client'], r['weekday'], half_hour)] = r
except FileNotFoundError:
    cancellation_lookup = {}

def find_cancellation_record(client, wd, start_minute):
    bucket = (start_minute // 30) * 30
    for cand in (bucket, bucket - 30, bucket + 30):
        rec = cancellation_lookup.get((client, wd, cand))
        if rec:
            return rec
    return None

# ---------------------------------------------------------------------------
# Build PATIENT.JSON -- one Patient (request) per visit; double-up members
# reference each other via match_request_list.
# ---------------------------------------------------------------------------
patients = []
prid_counter = defaultdict(int)  # client -> running count of prids used, for uniqueness
visit_to_prid = {}  # id(visit dict) -> prid, so feasibility building can look it up
long_visit_flags = []

for req in requests:
    client = req['client']
    info = client_info.get(client)
    if not info:
        continue  # no id/coords -- can't build a valid Patient record
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
        SCHEMA_MAX_DURATION = 8 * 60  # schema's DURATION_MAXIMUM
        if raw_duration > SCHEMA_MAX_DURATION:
            # a genuine overnight/live-in style visit (e.g. 15:00 -> next day 09:00) doesn't
            # fit this system's per-visit request model (max 8h) -- cap it and flag it rather
            # than silently truncating without comment.
            long_visit_flags.append((prid, raw_duration))
            duration = SCHEMA_MAX_DURATION
            end_min = start_min + duration
        else:
            duration = max(raw_duration, 15)
        # soft window = the actual scheduled slot; hard window = soft +/- buffer (respecting
        # the schema's >=15min margin requirement on each side).
        hard_buffer = 45
        min_duration = max(duration - 15, 15) if duration > 15 else duration

        others = [p for p in member_prids if p != prid]

        # Blend the client's stated service_priority with what actually happened historically
        # for this exact slot: a slot that's Never Cancelled is a firm commitment (push priority
        # up, tighten violation tolerance); one that's Always Cancelled rarely happens for real
        # (pull priority down -- not worth over-committing scheduler resources to).
        base_priority = PRIORITY_BY_SERVICE_PRIORITY.get(info['service_priority'], 0.5)
        violation_level = 0.5
        cancel_rec = find_cancellation_record(client, TARGET_WEEKDAY, start_min)
        cancel_note = ''
        if cancel_rec:
            cls = cancel_rec['classification']
            if cls == 'Never cancelled':
                base_priority = min(base_priority + 0.25, 1.0)
                violation_level = 0.15
                cancel_note = f"Never cancelled ({cancel_rec['cancellation_rate']:.0%} historically) -- firm commitment"
            elif cls == 'Always cancelled':
                base_priority = max(base_priority - 0.3, 0.05)
                violation_level = 0.85
                cancel_note = f"Always cancelled ({cancel_rec['cancellation_rate']:.0%} historically) -- rarely happens for real"
            elif cls == 'Occasionally cancelled':
                violation_level = 0.5
                cancel_note = f"Occasionally cancelled ({cancel_rec['cancellation_rate']:.0%})"
                if cancel_rec['fairness_flag'].startswith('Concentrated'):
                    cancel_note += f" -- {cancel_rec['fairness_flag']} (equity concern: avoid dropping this one again)"

        patient = {
            'pid': info['id'],
            'prid': prid,
            'gender': gender_enum(info['gender']),
            'location_id': info['id'],
            'location': {'latitude': info['lat'], 'longitude': info['lon'], 'postcode': info['postcode']},
            'request_window': {
                'start_time_hard': max(start_min - hard_buffer, 0),
                'end_time_hard': min(end_min + hard_buffer, MAX_SCHEMA_MINUTE),
                'start_time_soft': start_min,
                'end_time_soft': end_min,
                'duration': duration,
                'min_duration': min_duration,
                'duration_reduction_priority': 0.3,
                'request_window_priority': round(base_priority, 2),
                'soft_window_violation_level': violation_level,
                'match_request_list': others,
            },
            'extend_feasibility': None,  # filled in below
            '_cancellation_note': cancel_note,  # stripped before writing -- for the day-analysis report
        }
        patients.append(patient)

# Patient-side extend_feasibility: based on how much this CLIENT'S care has historically
# rotated across different carers on this weekday (from set_roster / slot history) -- a
# client who's always seen the same one or two carers is less open to substitution than
# one who's used to rotation.
for p in patients:
    client = next(c for c in client_info if client_info[c]['id'] == p['pid'])
    # crude: count distinct carers who've ever done ANY slot for this client on this weekday
    all_carers_this_client_wd = set()
    for cluster in client_slot_history.get((client, TARGET_WEEKDAY), []):
        all_carers_this_client_wd.update(c for c, dt in cluster)
    rotates = len(all_carers_this_client_wd) >= 3
    p['extend_feasibility'] = {
        'extend': rotates or day_is_short_staffed,
        'max_distance_km': 15.0 if rotates else 5.0,
        'max_time_minutes': 45 if rotates else 20,
        'max_distance_border_crossings_km': 10,
        'max_time_border_crossings_minutes': 60,
    }

print(f"\nBuilt {len(patients)} patient records")
if long_visit_flags:
    print(f"NOTE: {len(long_visit_flags)} visit(s) exceeded the schema's 8h max duration "
          f"(likely overnight/live-in shifts) and were capped to 8h:")
    for prid, raw_dur in long_visit_flags:
        print(f"   {prid}: actual {raw_dur/60:.1f}h -> capped to 8h")

# ---------------------------------------------------------------------------
# Build CAREGIVERS.JSON -- one Caregiver (shift) per carer in today_carers.csv, using the
# shift times YOU supplied (not inferred from any visit, since we don't have actual visits
# for a day that hasn't happened yet).
# ---------------------------------------------------------------------------
carers_today = sorted(carer_shift_input.keys())
caregivers = []
carer_to_crid = {}

for carer in carers_today:
    info = carer_info.get(carer)
    if not info or not info['lat']:
        print(f"WARNING: '{carer}' in today_carers.csv doesn't match an active carer with "
              f"a home coordinate -- skipped. Check the exact spelling against your carer data.")
        continue
    shift_start_dt, shift_end_dt = carer_shift_input[carer]
    shift_start = to_minutes(shift_start_dt)
    shift_end = to_minutes(shift_end_dt)
    shift_start = max(shift_start, 0)
    shift_end = min(shift_end, MAX_SCHEMA_MINUTE)

    crid = f"{info['id']}_{TARGET_DATE.isoformat()}"
    carer_to_crid[carer] = crid

    # extend_feasibility: NOT the stale stated field -- derived from her own behavior.
    # A carer who has never once done an off-roster (extra) visit is a "closed" carer --
    # tight, no extension. A carer who does cover extras gets her own computed geographic
    # search radius as the extension distance. Both get widened somewhat on a short-staffed
    # day, since the whole operation needs more flexibility to cope.
    is_flexible = carer_does_extra.get(carer, False)
    radius = carer_search_radius.get(carer, 10.0)
    if is_flexible:
        extend = True
        max_dist = radius
        max_time = 45
    else:
        extend = day_is_short_staffed
        max_dist = radius if day_is_short_staffed else 2.0
        max_time = 30 if day_is_short_staffed else 10

    travel_mode_map = {'Car': 'driving', 'Walk': 'walking'}
    caregiver = {
        'cid': info['id'],
        'crid': crid,
        'gender': gender_enum(info['gender']),
        'travel_mode': travel_mode_map.get(info['travel_method'], 'driving'),
        'location_id': info['id'],
        'location': {'latitude': info['lat'], 'longitude': info['lon'], 'postcode': info['postcode']},
        'current_location_id': info['id'],
        'start_location_id': info['id'],
        'end_location_id': info['id'],
        'shift': {'start_time': shift_start, 'end_time': shift_end},
        'extend_feasibility': {
            'extend': extend, 'max_distance_km': round(max_dist, 1), 'max_time_minutes': max_time,
            'max_distance_border_crossings_km': 10, 'max_time_border_crossings_minutes': 60,
        },
        'caregiver_usage_priority': 0.75 if is_flexible else 0.5,
    }
    caregivers.append(caregiver)

print(f"Built {len(caregivers)} caregiver records")
print(f"  Flexible (does extras): {sum(1 for c in carers_today if carer_does_extra.get(c))}")
print(f"  Closed (roster-only): {sum(1 for c in carers_today if not carer_does_extra.get(c))}")

# ---------------------------------------------------------------------------
# Build CRID_PRID_FEASIBILITY.JSON
#
# Weight formula ported from a prior, much more rigorous version of this pipeline (found
# via conversation search and verified against its actual code -- NOT reconstructed from
# memory):
#
#   consistency   = her share of this slot's total occurrences (any carer)
#   freq_factor   = 1 + log1p(client's overall call rate) -- clients needing more frequent
#                   care get more differentiated weights between their carers
#   recency_decay = exp(-days_since_her_last_visit / 21) -- steep decay, ~3-week time constant
#   status        = "Current Primary" (>=40% share, seen within 50 days) -> factor 1.0
#                   "Support / Relief" (everything else, still recent)   -> factor 0.5
#                   "Former / Relief" (>50 days since last visit)        -> factor 0.2
#   raw_weight    = consistency * freq_factor * recency_decay * status_factor * concentration_factor
#   final weight  = raw_weight normalized per SLOT, so her top carer for that slot = 1.0
#
# Two things added on top of the literal ported formula, both established and validated
# earlier in THIS pipeline and deliberately kept rather than dropped when porting the
# other formula in:
#
# 1. SLOT granularity, not just (carer, client) overall. The ported formula's own version
#    computes one weight per (carer, client) pair with no sense of time at all -- but this
#    pipeline established that time-of-day matters enormously here (Kevin Conneely's 3
#    separate daily slots each with a different regular carer; Augustine Eghoborr's specific
#    Wednesday 10:00-13:00 double-up slot). Collapsing to one number per (carer, client)
#    would flatten a carer's morning-slot affinity and evening-slot affinity for the same
#    client into the same score, and silently break double-up detection (both legs would
#    get identical candidate weights instead of reflecting who actually does which leg).
#    So consistency/status/recency below are computed against client_slot_history's pooled
#    time-of-day clusters (same clustering used everywhere else in this pipeline), not the
#    client's raw visit total.
#
# 2. concentration_factor (this pipeline's own addition, verified against real carers --
#    Vera: 305 distinct clients vs a median of 32, Nuala Sheridan: 1-2 total). It measures
#    something the ported formula doesn't: how focused a carer's OVERALL caseload is across
#    ALL her clients, not just her share of this one. A carer spread across hundreds of
#    clients showing up with a given slot's consistency is a thinner signal than a carer
#    with a tiny, focused caseload showing the same consistency -- orthogonal to (and
#    multiplied on top of) the ported formula's own factors.
#
# freq_factor's window_days=112 (16 weeks) is kept as-is from the original, used purely as
# a fixed reference scale for the demand-frequency signal. "days_since_last_visit" is
# measured relative to TARGET_DATE (the day being scheduled), not the dataset's own last
# date, since this pipeline is forward-looking.
# ---------------------------------------------------------------------------
WINDOW_DAYS = 112

def identify_carer_status(overall_pct, days_since_last_visit):
    if days_since_last_visit > 50:
        return "Former / Relief"
    if overall_pct >= 40:
        return "Current Primary"
    return "Support / Relief"

STATUS_FACTORS = {'Current Primary': 1.0, 'Support / Relief': 0.5, 'Former / Relief': 0.2}

customer_totals = Counter()  # client -> total visits, any carer, any slot -- feeds freq_factor only
for carer, wd_map in roster.items():
    for wd, visits in wd_map.items():
        for v in visits:
            if v['start_dt']:
                customer_totals[v['client']] += 1

# slot_weight[(client, weekday, cluster_idx)] = {carer: weight}; slot_lookup helps find the
# right cluster for a given request's time-of-day, same approach as find_slot_history_carers
# used elsewhere in this pipeline.
slot_weight = {}
slot_clusters_by_client_wd = defaultdict(list)  # (client, weekday) -> [(median_minute, key)]

for (client, wd), clusters in client_slot_history.items():
    total_cust_visits = customer_totals.get(client, 0)
    if total_cust_visits <= 0:
        continue
    calls_per_day = total_cust_visits / float(WINDOW_DAYS)
    freq_factor = 1 + math.log1p(calls_per_day)

    for idx, cluster in enumerate(clusters):
        slot_total = len(cluster)
        by_carer = defaultdict(list)
        for carer, dt in cluster:
            by_carer[carer].append(dt.date())
        minutes = [dt.hour * 60 + dt.minute for _, dt in cluster]
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

        slot_max = max(raw_by_carer.values(), default=0.0)
        slot_weight[key] = {
            carer: round(raw / slot_max, 4) if slot_max > 0 else 0.0
            for carer, raw in raw_by_carer.items()
        }

def find_weighted_slot(client, wd, start_minute):
    """The pooled slot (client, weekday, cluster) matching this request's time-of-day, with
    its per-carer weights, if any exist."""
    best_key, best_diff = None, None
    for median_minute, key in slot_clusters_by_client_wd.get((client, wd), []):
        diff = abs(median_minute - start_minute)
        if diff <= TIME_GAP_MINUTES and (best_diff is None or diff < best_diff):
            best_key, best_diff = key, diff
    return slot_weight.get(best_key, {})

print(f"\nComputed carer affinity weights for {len(slot_weight)} historical (client, weekday, slot) "
      f"combinations across {len(customer_totals)} clients")

feasibility_pairs = []
seen_pairs = set()

def add_pair(prid, crid, weight):
    key = (prid, crid)
    if key in seen_pairs:
        return
    seen_pairs.add(key)
    feasibility_pairs.append({'prid': prid, 'crid': crid, 'weight': round(min(max(weight, 0.0), 2.0), 2)})

for req in requests:
    client = req['client']
    if client not in client_info:
        continue
    for v in req['members']:
        prid = visit_to_prid[id(v)]
        start_minute = to_minutes(v['start_dt'])
        patient_rec = next(p for p in patients if p['prid'] == prid)

        # 1. every carer with slot-specific history for this exact client/weekday/time,
        #    at her computed affinity weight, if she's working today
        weights_here = find_weighted_slot(client, TARGET_WEEKDAY, start_minute)
        carers_with_history = set()
        for carer, w in weights_here.items():
            crid = carer_to_crid.get(carer)
            carers_with_history.add(carer)
            if crid:
                add_pair(prid, crid, w)

        # 2. extend_feasibility: bring in nearby working carers, using the radius already
        #    computed for this patient (client-side rotation history) and each candidate's
        #    own search radius. Always runs if nothing was found yet, so no request is ever
        #    left with zero feasible carers.
        has_any = any(k[0] == prid for k in seen_pairs)
        extend_ok = day_is_short_staffed or patient_rec['extend_feasibility']['extend'] or not has_any
        if extend_ok:
            client_radius = patient_rec['extend_feasibility']['max_distance_km']
            for cand_carer in carers_today:
                if cand_carer in carers_with_history:
                    continue
                cand_crid = carer_to_crid.get(cand_carer)
                if not cand_crid or (prid, cand_crid) in seen_pairs:
                    continue
                d = travel_km(cand_carer, client)
                own_radius = carer_search_radius.get(cand_carer, 10.0)
                if d is not None and d <= max(client_radius, own_radius if carer_does_extra.get(cand_carer) else 0):
                    add_pair(prid, cand_crid, 0.25)

print(f"\nBuilt {len(feasibility_pairs)} feasibility pairs across {len(patients)} patient requests")
avg_options = len(feasibility_pairs) / len(patients) if patients else 0
print(f"Average feasible caregivers per request: {avg_options:.1f}")

weight_counts = Counter(round(p['weight'], 2) for p in feasibility_pairs)
print("Weight distribution:", dict(sorted(weight_counts.items())))

# ---------------------------------------------------------------------------
# Cancellation-history summary (before stripping the internal note field)
# ---------------------------------------------------------------------------
cancel_note_counts = Counter()
for p in patients:
    note = p.get('_cancellation_note', '')
    if note.startswith('Never'):
        cancel_note_counts['Never cancelled -> priority raised'] += 1
    elif note.startswith('Always'):
        cancel_note_counts['Always cancelled -> priority lowered'] += 1
    elif note.startswith('Occasionally'):
        cancel_note_counts['Occasionally cancelled'] += 1
    else:
        cancel_note_counts['No cancellation history available'] += 1
print(f"\nCancellation-history influence on priority: {dict(cancel_note_counts)}")

# strip the internal note before schema validation/writing -- not part of the hhs schema
patient_notes = {p['prid']: p.pop('_cancellation_note') for p in patients}

# ---------------------------------------------------------------------------
# Validate everything against the real hhs pydantic schema before writing out
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, '.')
from hhs import Patient as PatientModel, Caregiver as CaregiverModel, FeasibilityPair as FeasibilityPairModel

validation_errors = []
for i, p in enumerate(patients):
    try:
        PatientModel(**p)
    except Exception as e:
        validation_errors.append(('patient', p.get('prid'), str(e)[:200]))
for i, c in enumerate(caregivers):
    try:
        CaregiverModel(**c)
    except Exception as e:
        validation_errors.append(('caregiver', c.get('crid'), str(e)[:200]))
for i, fp in enumerate(feasibility_pairs):
    try:
        FeasibilityPairModel(**fp)
    except Exception as e:
        validation_errors.append(('feasibility', (fp.get('prid'), fp.get('crid')), str(e)[:200]))

print(f"\nSchema validation: {len(validation_errors)} errors out of "
      f"{len(patients) + len(caregivers) + len(feasibility_pairs)} records")
for e in validation_errors[:10]:
    print(' ', e)

# ---------------------------------------------------------------------------
# Write the 3 output files
# ---------------------------------------------------------------------------
out_dir = './output'
date_str = TARGET_DATE.isoformat()

with open(f'{out_dir}/patient_{date_str}.json', 'w') as f:
    json.dump(patients, f, indent=2)
with open(f'{out_dir}/caregivers_{date_str}.json', 'w') as f:
    json.dump(caregivers, f, indent=2)
with open(f'{out_dir}/crid_prid_feasibility_{date_str}.json', 'w') as f:
    json.dump(feasibility_pairs, f, indent=2)

print(f"\nSaved patient_{date_str}.json, caregivers_{date_str}.json, crid_prid_feasibility_{date_str}.json")




