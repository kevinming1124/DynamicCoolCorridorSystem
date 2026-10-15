"""
Pedestrian Agent Model

Defines the data structure and states used to represent individual
pedestrians within the campus simulation.

Each pedestrian maintains information about their origin, destination,
assigned route, walking speed, current position, and movement state.

This module is responsible for representing pedestrian state, while
movement logic, route progression, and simulation updates are handled
by separate components.
"""

from dataclasses import dataclass, field
from enum import Enum

class PedestrianState(str, Enum):
    WALKING = "walking"
    WAITING = "waiting"
    ARRIVED = "arrived"

@dataclass
class Pedestrian:
    pedestrian_id: int

    origin: str
    destination: str

    route: list[str]

    walking_speed_mps: float = 1.4

    current_edge_index: int = 0
    
    # Measured in meters
    position_m: float = 0.0  

    state: PedestrianState = PedestrianState.WALKING

    total_travel_time_sec: float = 0.0
    total_wait_time_sec: float = 0.0