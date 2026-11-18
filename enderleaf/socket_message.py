from dataclasses import dataclass
import json


from enderleaf.enums import MsgType, LogLevel


@dataclass
class SocketMessage:
    """Standard message format for communication via sockets between system components"""
    type: MsgType = MsgType.MESSAGE
    message: str = ""
    image: str = ""
    step: int = 0
    total: int = 0
    level: LogLevel = LogLevel.INFO
    key: str | None = None
    value: str = ""
    x: float | None = None
    y: float | None = None
    z: float | None = None

    def to_json(self):
        """Converts the dataclass instance to a dictionary"""
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def __str__(self) -> str:
        return self.dump()

    @classmethod
    def from_json(cls, data: dict):
        """Creates a SocketMessage instance from a dictionary"""
        return cls(**data)

    def dump(self):
        """Serializes the message to a JSON string"""
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str):
        """Deserializes a JSON string into a SocketMessage instance"""
        return cls(**json.loads(data))


@dataclass
class SocketData:
    """Wrapper for arbitrary dictionary data transmitted via sockets"""
    data: dict

    def to_json(self):
        """Returns the inner data dictionary"""
        return self.data

    def __str__(self) -> str:
        return self.dump()

    @classmethod
    def from_json(cls, data: dict):
        """Creates a SocketData instance from a dictionary"""
        return cls(data)

    def dump(self):
        """Serializes the data dictionary to a JSON string"""
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str):
        """Deserializes a JSON string into a SocketData instance"""
        return cls(json.loads(data))


def result_message(result, ok_message, nok_message: str) -> SocketMessage:
    """Creates a SocketMessage reflecting the success or failure of an operation

    Args:
        result (bool): Success status of the operation
        ok_message (str): Message to use if result is True
        nok_message (str): Message to use if result is False

    Returns:
        SocketMessage: Message with appropriate text and log level
    """
    return SocketMessage(
        type=MsgType.RESULT,
        message=(nok_message if result is False else ok_message),
        level=LogLevel.ERROR if result is False else LogLevel.INFO,
    )
