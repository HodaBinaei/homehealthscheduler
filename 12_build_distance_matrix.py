import json, pickle, time

import os
PROJECT_ROOT = os.environ.get(
    'HHS_PROJECT_ROOT',
    os.path.dirname(os.path.abspath(__file__)),
)

def norm(s):
    if s is None:
        return ''
    return ' '.join(s.strip().split()).lower()

with open(f'{PROJECT_ROOT}/data/users-new.json') as f:
    users = json.load(f)['user']
carer_id_by_name = {}
for u in users:
    if u.get('status') == 'Active' and u.get('is_caregiver'):
        first = (u.get('name') or '').strip()
        last = (u.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        carer_id_by_name[display] = str(u.get('id'))

with open(f'{PROJECT_ROOT}/data/clients-new.json') as f:
    clients = json.load(f)['client']
client_id_by_name = {}
for c in clients:
    if c.get('status') == 'Active':
        first = (c.get('name') or '').strip()
        last = (c.get('lastname') or '').strip()
        display = f"{first} {last}".strip()
        client_id_by_name[display] = str(c.get('id'))

print(f"{len(carer_id_by_name)} active carers, {len(client_id_by_name)} active clients")

t0 = time.time()
with open(f'{PROJECT_ROOT}/data/driving_data.json') as f:
    data = json.load(f)
print(f"Loaded driving_data.json in {time.time() - t0:.1f}s")
dist = data['distance']

# Build a much smaller carer-name -> client-name -> km lookup, checking both key orders
# since the matrix stores each pair once but not consistently ordered.
carer_client_km = {}
missing = []
for carer_name, cid in carer_id_by_name.items():
    for client_name, did in client_id_by_name.items():
        key1 = f"{cid}_{did}"
        key2 = f"{did}_{cid}"
        d = dist.get(key1)
        if d is None:
            d = dist.get(key2)
        if d is not None:
            carer_client_km[(carer_name, client_name)] = float(d)
        else:
            missing.append((carer_name, client_name))

print(f"Matched pairs: {len(carer_client_km)}")
print(f"Missing pairs: {len(missing)}")
if missing:
    print("Sample missing:", missing[:5])

with open(f'{PROJECT_ROOT}/carer_client_distances.pkl', 'wb') as f:
    pickle.dump(carer_client_km, f)
print("Saved carer_client_distances.pkl")
