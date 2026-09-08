MIN_CAREGIVER_SEGMENT_MINUTES = 30
MIN_ONLY_SET_LONG_CALL_MINUTES = 480
MIN_PATIENT_WINDOW_SLACK_MINUTES = 45
PATIENT_SEQUENCE_GAP_MINUTES = 30
MIN_DURATION_FLOOR_RATIO = 0.85
ROSTER_MUST_VISIT_WEIGHT = 1.0
CLIENT_EXPORT_END_OF_DAY_EXTEND_THRESHOLD = 1439  # 24*60 - 1
MINUTES_IN_DAY = 1440

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
