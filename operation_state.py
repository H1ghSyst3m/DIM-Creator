from enum import Enum


class OperationState(Enum):
    IDLE = "idle"
    EXTRACTING = "extracting"
    PACKAGING = "packaging"
    CLOSING = "closing"
