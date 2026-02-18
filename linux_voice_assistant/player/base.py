from abc import ABC, abstractmethod
from .state import PlayerState


class AudioPlayer(ABC):

    @abstractmethod
    def play(self, url: str) -> None:
        pass

    @abstractmethod
    def pause(self) -> None:
        pass

    @abstractmethod
    def resume(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def state(self) -> PlayerState:
        pass
