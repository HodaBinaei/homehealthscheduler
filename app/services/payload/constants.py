MIN_CAREGIVER_SEGMENT_MINUTES = 30
MIN_ONLY_SET_LONG_CALL_MINUTES = 480
MIN_PATIENT_WINDOW_SLACK_MINUTES = 15
MIN_MATCHED_PATIENT_WINDOW_SLACK_MINUTES = 5
PATIENT_SEQUENCE_GAP_MINUTES = 30
MIN_DURATION_FLOOR_RATIO = 0.85
ROSTER_MUST_VISIT_WEIGHT = 1.0
ROSTER_ONLY_VISIT_WEIGHT = 2.0
ROSTER_HARD_EXCLUDE_WEIGHT = 0.0
REQUEST_WINDOW_PRIORITY_COORDINATOR = 1.0
REQUEST_WINDOW_PRIORITY_HISTORICAL = 0.01
DEFAULT_DURATION_REDUCTION_PRIORITY = 0.3
DEFAULT_SOFT_WINDOW_VIOLATION_LEVEL = 0.5
DEFAULT_CAREGIVER_USAGE_PRIORITY = 0.5
MUST_SOURCE_COORDINATOR = "coordinator"
MUST_SOURCE_HISTORICAL = "historical"
# Engine accepts this placeholder when the double-up partner is outside the payload.
UNLOCAL_MATCH_REQUEST = "unlocal_match_request"
CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD = 1439  # 24*60 - 1
MINUTES_IN_DAY = 1440
DURATION_MINIMUM = 15

# Must match hhs.DistanceItem bounds (worker validates with these).
# DB/OSRM often stores 1440 (= 24h) as an unreachable/outlier travel time.
ENGINE_MAX_TRAVEL_MINUTES = 600  # 10 hours
ENGINE_MAX_DISTANCE_KM = 1000.0

DAY_NAMES = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

GENDER_MAP = {
    "Male": "MALE",
    "Female": "FEMALE",
    "Other": "OTHER",
    "Prefer not to say": "PREFER_NOT_TO_SAY",
}

TRAVEL_METHOD_MAP = {
    "Car": "DRIVING",
    "Bike": "CYCLING",
    "Walk": "WALKING",
    "PublicTransport": "DRIVING",
}

MATRIX_METHOD_TO_PAYLOAD = {
    "walk": "walking_data",
    "bike": "cycling_data",
    "car": "driving_data",
}
