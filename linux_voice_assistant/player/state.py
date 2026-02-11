from enum import Enum, auto


class PlayerState(Enum):
    IDLE = auto()
    LOADING = auto()
    PLAYING = auto()
    PAUSED = auto()
    STOPPING = auto()
    ERROR = auto()
