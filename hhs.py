
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Annotated
from pydantic import BaseModel, Field, model_validator


MINIMUM_SHIFT_DURATION = 30  # Minimum shift duration in minutes
MINIMUM_SHIFT_START = 0  # Minimum shift start time in minutes 
MAXIMUM_SHIFT_END = (23 * 60 + 59) * 2  # Maximum shift end time in minutes 

DURATION_MINIMUM = 15  # Minimum duration in minutes
DURATION_MAXIMUM = 8 * 60   # Maximum duration in minutes

PATIENT_EARLEST_REQUEST_TIME = 0  # Earliest request time in minutes 
PATIENT_LATEST_REQUEST_TIME = (23 * 60 + 59) * 2  # Latest request time in minutes (10 PM)

MAXIMUM_DISTANCE_BETWEEN_LOCATIONS_KM = 1000.0  # Maximum distance between locations in kilometers
MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES = 10 * 60  # Maximum travel time between locations in minutes (4 hours)



class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class TravelMode(str, Enum):
    DRIVING = "driving"
    WALKING = "walking"
    BICYCLING = "bicycling"
    PUBLIC_TRANSIT = "public_transit"


class Location(BaseModel):
    latitude: Annotated[float, Field(...,
                                     min_value=-90.0,
                                     max_value=90.0,
                                     description="Latitude of the location")]
    longitude: Annotated[float, Field(...,
                                        min_value=-180.0,
                                        max_value=180.0,
                                        description="Longitude of the location")]
    postcode: Optional[Annotated[str, Field(None, description="Postcode of the location")]] = None


class Shift(BaseModel):
    start_time: Annotated[int, Field(
        ...,
        min_value=MINIMUM_SHIFT_START,
        max_value=MAXIMUM_SHIFT_END,
        description="Start time of the shift in minutes since midnight",
        example=8*60  # Example: 8 AM
    )]
    end_time: Annotated[int, Field(
        ...,
        min_value=MINIMUM_SHIFT_START,
        max_value=MAXIMUM_SHIFT_END,
        description="End time of the shift in minutes since midnight",
        example=17*60  # Example: 5 PM
    )]

    @model_validator(mode='after')
    def validate_shift(cls, values):
        start_time = values.start_time
        end_time = values.end_time
        assert end_time - start_time >= MINIMUM_SHIFT_DURATION, f"Shift duration must be at least {MINIMUM_SHIFT_DURATION} minutes"
        return values


class RequestWindow(BaseModel):
    start_time_hard: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="Start time of the hard request window in minutes since midnight. this is the earliest time the patient can be visited.",
        example=9*60  # Example: 9 AM
    )]
    end_time_hard: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="End time of the hard request window in minutes since midnight. this is the latest time minus the min_duration the patient can be visited.",
        example=17*60  # Example: 5 PM
    )]

    start_time_soft: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="Start time of the flexible request window in minutes since midnight. patient can be visited at any time after this time or even before this time with some penalty that depend on the window type but not before the hard start time.",
        example=9*60  # Example: 9 AM
    )]
    end_time_soft: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="End time of the flexible request window in minutes since midnight. patient can be visited at any time before this time or even after this time with some penalty that depend on the window type but not after the hard end time.",
        example=17*60  # Example: 5 PM
    )]

    duration: Annotated[int, Field(
        ...,
        min_value=DURATION_MINIMUM,
        max_value=DURATION_MAXIMUM,
        description="Duration of the request window in minutes. This is the time that the patient needs to be visited for.",
        example=60  # Example: 60 minutes
    )]

    min_duration: Annotated[int, Field(
        ...,
        min_value=DURATION_MINIMUM,
        max_value=DURATION_MAXIMUM,
        description="Minimum duration of the request window in minutes. This is the minimum time that the patient needs to be visited for.",
        example=30  # Example: 30 minutes
    )]

    duration_reduction_priority: Annotated[float, Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Priority for reducing the duration of the request window (0.0 to 1.0). Higher priority means preferable to reduce the duration of this request window.",
        example=0.5  # Example: 0.5
    )]

    request_window_priority: Annotated[float, Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Priority for the request window (0.0 to 1.0). Higher priority means preferable to serve this request window.",
        example=0.8  # Example: 0.8
    )]

    soft_window_violation_level: Annotated[float, Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Soft window violation level of the request window (0.0 to 1.0). Higher values mean less penalty for soft window violations.",
        example=0.7  # Example: 0.7
    )]

    match_request_list: Annotated[List[str], Field(
        ...,
        description="List of request IDs to be served simultaneously with this request window",
        example=["123", "4567", "910122"]  # Example: List of request IDs
    )]


    @model_validator(mode='after')
    def validate_window(cls, values):
        """
        Validate the request window to ensure that the hard and soft windows are consistent and that the duration is within the specified limits.
        also the duration need to be within the hard window and the soft window. and the soft window need to be within the hard window.

        For a match_request_list (double-up) request, the hard/soft margin is tightened to 5
        minutes instead of the usual 15 -- these requests are already sharing a deliberately
        narrow, forced-overlap window with their linked partner(s), and the wider general
        margin isn't needed there.
        """
        start_time_hard = values.start_time_hard
        end_time_hard = values.end_time_hard
        start_time_soft = values.start_time_soft
        end_time_soft = values.end_time_soft
        margin = 5 if values.match_request_list else 15
        assert end_time_hard - end_time_soft >= margin, f"Hard window end time must be at least {margin} minutes greater than soft window end time"
        assert start_time_hard - start_time_soft <= -margin, f"Hard window start time must be at least {margin} minutes less than soft window start time"
        assert end_time_soft - start_time_soft >= values.duration, "Hard window duration must be greater than or equal to the specified duration"

        duration = values.duration
        min_duration = values.min_duration
        assert duration - min_duration >= 10, "Duration must be greater than or equal to minimum duration"

        return values


class FeasibilityExtension(BaseModel):

    extend: Annotated[bool, Field(
        ...,
        description="Whether to extend the feasible pairs",
        example=True
    )]
    max_distance_km: Annotated[float, Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Maximum distance in kilometers for extending the feasible pairs. if the distance between the patient and caregiver (calculated based on their location_id) is greater than this value, the pair will not be considered feasible.",
        example=5.0  # Example: 5 km
    )]
    max_time_minutes: Annotated[int, Field(
        ...,
        ge=0,
        le=120,
        description="Maximum time in minutes for extending the feasible pairs. if the travel time between the patient and caregiver (calculated based on their location_id and travel_mode) is greater than this value, the pair will not be considered feasible.",
        example=30  # Example: 30 minutes
    )]
    max_distance_border_crossings_km: Annotated[int, Field(
        ...,
        ge=0,
        le=50,
        description="Maximum distance in kilometers for border crossings when extending the feasible pairs. if the distance between the patient and caregiver closest feasible patient to this patient (calculated based on their location_id) is greater than this value, the pair will not be considered feasible.",
        example=10  # Example: 10 km
    )]
    max_time_border_crossings_minutes: Annotated[int, Field(
        ...,
        ge=0,
        le=120,
        description="Maximum time in minutes for border crossings when extending the feasible pairs. if the travel time between the patient and caregiver closest feasible patient to this patient (calculated based on their location_id and travel_mode) is greater than this value, the pair will not be considered feasible.",
        example=60  # Example: 60 minutes
    )]


class Patient(BaseModel):
    pid: Annotated[str, Field(
        ...,
        description="Patient ID",
        example="123456789"
    )]
    prid: Annotated[str, Field(
        ...,
        description="Patient Request ID",
        example="987654321"
    )]
    gender: Annotated[Gender, Field(
        ...,
        description="Gender of the patient",
        example=Gender.MALE
    )]
    location_id: Annotated[str, Field(
        ...,
        description="Location ID",
        example="123456789"
    )]
    location: Location
    request_window: RequestWindow
    extend_feasibility: FeasibilityExtension


class Caregiver(BaseModel):

    cid: Annotated[str, Field(
        ...,
        description="Caregiver ID",
        example="987654321"
    )]
    crid: Annotated[str, Field(
        ...,
        description="Caregiver Request shift ID",
        example="123456789"
    )]
    gender: Annotated[Gender, Field(
        ...,
        description="Gender of the caregiver",
        example=Gender.FEMALE
    )]
    travel_mode: Annotated[TravelMode, Field(
        ...,
        description="Travel mode of the caregiver",
        example=TravelMode.DRIVING
    )]
    location_id: Annotated[str, Field(
        ...,
        description="Location ID of the caregiver",
        example="123456789"
    )]
    location: Location
    current_location_id: Annotated[str, Field(
        ...,
        description="Current location ID of the caregiver",
        example="987654321"
    )]
    start_location_id: Annotated[str, Field(
        ...,
        description="Start location ID of the caregiver",
        example="123456789"
    )]
    end_location_id: Annotated[str, Field(
        ...,
        description="End location ID of the caregiver",
        example="987654321"
    )]

    shift: Shift
    extend_feasibility: FeasibilityExtension
    caregiver_usage_priority: Annotated[float, Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Priority for caregiver usage (0.0 to 1.0). Higher priority means preferable to use this caregiver for assignments.",
        example=0.9  # Example: 0.9
    )]


class FeasibilityPair(BaseModel):
    prid: Annotated[str, Field(
        ...,
        description="Patient Request ID",
        example="123456789"
    )]
    crid: Annotated[str, Field(
        ...,
        description="Caregiver Request shift ID",
        example="987654321"
    )]
    weight: Annotated[float, Field(
        ...,
        ge=0.0,
        le=2.0,
        description="Weight of the feasibility pair;(0:dislike don't assign, 1:must assign highest priority, 2:only assignment, 0-1: weighted assignment priority)",
        example=0.75
    )]

    @model_validator(mode='after')
    def validate_weight(cls, values):
        weight = values.weight
        assert  weight <=1.0 or weight == 2.0, "Weight must be between 0.0 and 1.0 or equal to 2.0"
        return values


class FeasibilityPairs(BaseModel):
    feasibility_pairs: Annotated[List[FeasibilityPair], Field(
        ...,
        description="List of feasibility pairs",
        example=[
            FeasibilityPair(prid="123456789", crid="987654321", weight=0.75),
            FeasibilityPair(prid="987654321", crid="123456789", weight=0.85)
        ]
    )]


class SingleVisit(BaseModel):
    prid: Annotated[str, Field(
        ...,
        description="Patient Request ID",
        example="123456789"
    )]
    start_time: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="Start time of the visit in minutes since midnight",
        example=9*60  # Example: 9 AM
    )]
    end_time: Annotated[int, Field(
        ...,
        min_value=PATIENT_EARLEST_REQUEST_TIME,
        max_value=PATIENT_LATEST_REQUEST_TIME,
        description="End time of the visit in minutes since midnight",
        example=10*60  # Example: 10 AM
    )]
    duration: Annotated[int, Field(
        ...,
        min_value=DURATION_MINIMUM,
        max_value=DURATION_MAXIMUM,
        description="Duration of the visit in minutes",
        example=60  # Example: 60 minutes
    )]
    crid: Annotated[str, Field(
        ...,
        description="Caregiver Request shift ID",
        example="987654321"
    )]
    travel_time: Annotated[int, Field(
        default=0,
        ge=0,
        le=MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES,
        description="Travel time in minutes to the next visit (if applicable)",
        example=15  # Example: 15 minutes
    )]
    waiting_time: Annotated[int, Field(
        default=0,
        ge=0,
        le=MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES,
        description="Waiting time in minutes before the visit (if applicable)",
        example=5  # Example: 5 minutes
    )]


class SingleCaregiverSchedule(BaseModel):
    cid: Annotated[str, Field(
        ...,
        description="Caregiver ID",
        example="987654321"
    )]
    crid: Annotated[str, Field(
        ...,
        description="Caregiver Request shift ID",
        example="987654321"
    )]
    shift: Shift
    location: Location
    visits: Annotated[List[SingleVisit], Field(
        ...,
        description="List of visits for the caregiver",
        example=[
            SingleVisit(prid="123456789", start_time=9*60, end_time=10*60, duration=60, crid="987654321", travel_time=15, waiting_time=5),
            SingleVisit(prid="987654321", start_time=10*60, end_time=11*60, duration=60, crid="987654321", travel_time=15, waiting_time=5)
        ]
    )]


class Schedule(BaseModel):
    date: Annotated[str, Field(
        ...,
        description="Date of the schedule in YYYY-MM-DD format",
        example="2024-06-01"
    )]
    assigned_crid_list: Annotated[List[str], Field(
        ...,
        description="List of assigned caregiver request shift IDs",
        example=["987654321", "123456789"]
    )]
    assigned_prid_list: Annotated[List[str], Field(
        ...,
        description="List of assigned patient request IDs",
        example=["123456789", "987654321"]
    )]
    unassigned_prid_list: Annotated[List[str], Field(
        ...,
        description="List of unassigned patient request IDs",
        example=["111111111", "222222222"]
    )]
    unassigned_crid_list: Annotated[List[str], Field(
        ...,
        description="List of unassigned caregiver request shift IDs",
        example=["333333333", "444444444"]
    )]

    removed_prid_list: Annotated[List[str], Field(
        ...,
        description="List of removed patient request IDs",
        example=["555555555", "666666666"]
    )]
    removed_crid_list: Annotated[List[str], Field(
        ...,
        description="List of removed caregiver request shift IDs",
        example=["777777777", "888888888"]
    )]


    caregiver_schedules: Annotated[dict[str, SingleCaregiverSchedule], Field(
        ...,
        description="List of caregiver schedules",
        example={
            "987654321": SingleCaregiverSchedule(
                cid="987654321",
                crid="987654321",
                shift=Shift(start_time=8*60, end_time=17*60),
                location=Location(latitude=37.7749, longitude=-122.4194, postcode="94103"),
                visits=[
                    SingleVisit(prid="123456789", start_time=9*60, end_time=10*60, duration=60, crid="987654321", travel_time=15, waiting_time=5),
                    SingleVisit(prid="987654321", start_time=10*60, end_time=11*60, duration=60, crid="987654321", travel_time=15, waiting_time=5)
                ]
            )
        }
    )]


    @model_validator(mode='after')
    def validate_schedule(cls, values):
        for crid, schedule in values.caregiver_schedules.items():
            assert crid == schedule.crid, f"Caregiver schedule ID '{crid}' does not match the schedule's crid '{schedule.crid}'"
        return values


class DistanceItem(BaseModel):
    from_location_id: Annotated[str, Field(
        ...,
        description="ID of the starting location",
        example="123456789"
    )]
    to_location_id: Annotated[str, Field(
        ...,
        description="ID of the destination location",
        example="987654321"
    )]
    distance_km: Annotated[float, Field(
        ...,
        ge=0.0,
        le=MAXIMUM_DISTANCE_BETWEEN_LOCATIONS_KM,
        description="Distance in kilometers between the two locations",
        example=5.0  # Example: 5 km
    )]
    distance_minute: Annotated[int, Field(
        ...,
        ge=0,
        le=MAXIMUM_TRAVEL_TIME_BETWEEN_LOCATIONS_MINUTES,
        description="Travel time in minutes between the two locations",
        example=15  # Example: 15 minutes
    )]


class Distances(BaseModel):
    distances: Annotated[dict[str, DistanceItem], Field(
        ...,
        description="List of distances between locations",
        example={
            "123456789_987654321": DistanceItem(from_location_id="123456789", to_location_id="987654321", distance_km=5.0, distance_minute=15),
            "987654321_123456789": DistanceItem(from_location_id="987654321", to_location_id="123456789", distance_km=5.0, distance_minute=15)
        }
    )]

    @model_validator(mode='after')
    def validate_distances(cls, values):
        distances = values.distances
        for key, distance_item in distances.items():
            estimated_key = f"{distance_item.from_location_id}_{distance_item.to_location_id}"
            assert key == estimated_key, f"Distance key '{key}' does not match the expected format '{estimated_key}'"
        return values


if __name__ == "__main__":
    # Example usage
    patient = Patient(
        pid="123456789",
        prid="987654321",
        gender=Gender.MALE,
        location_id="123456789",
        location=Location(latitude=37.7749, longitude=-122.4194, postcode="94103"),
        request_window=RequestWindow(
            start_time_hard=9*60,
            end_time_hard=17*60,
            start_time_soft=9*60,
            end_time_soft=17*60,
            duration=60,
            min_duration=30,
            duration_reduction_priority=0.5,
            window_type=WindowType.STRICT,
            request_window_priority=0.8,
            match_request_list=["123", "4567", "910122"]
        ),
        extend_feasibility=FeasibilityExtension(
            extend=True,
            max_distance_km=5.0,
            max_time_minutes=30,
            max_distance_border_crossings_km=10,
            max_time_border_crossings_minutes=60
        )
    )

    caregiver = Caregiver(
        cid="987654321",
        crid="123456789",
        gender=Gender.FEMALE,
        travel_mode=TravelMode.DRIVING,
        location_id="987654321",
        location=Location(latitude=37.7749, longitude=-122.4194, postcode="94103"),
        current_location_id="987654321",
        start_location_id="123456789",
        end_location_id="987654321",
        shift=Shift(start_time=8*60, end_time=17*60),
        extend_feasibility=FeasibilityExtension(
            extend=True,
            max_distance_km=5.0,
            max_time_minutes=30,
            max_distance_border_crossings_km=10,
            max_time_border_crossings_minutes=60
        ),
        caregiver_usage_priority=0.9
    )

    driving_distance = Distances(
        distances={
            "123456789_987654321": DistanceItem(from_location_id="123456789", to_location_id="987654321", distance_km=5.0, distance_minute=15),
            "987654321_123456789": DistanceItem(from_location_id="987654321", to_location_id="123456789", distance_km=5.0, distance_minute=15)
        }
    )

    print(patient.model_dump())
    print("=="*22)
    print(caregiver.model_dump())
    print("=="*22)
    print(driving_distance.model_dump())

 
