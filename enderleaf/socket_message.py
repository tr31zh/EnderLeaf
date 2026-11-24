from dataclasses import dataclass
import json


from enderleaf.enums import MsgType, LogLevel


@dataclass
class SocketMessage:
    """Standard message format for communication via sockets between system components

    Attributes:
        type (MsgType): Message type. Defaults to MsgType.MESSAGE.
        message (str): Message text. Defaults to "".
        image (str): Image payload (e.g. base64). Defaults to "".
        step (int): Current step for progress. Defaults to 0.
        total (int): Total steps for progress. Defaults to 0.
        level (LogLevel): Log level. Defaults to LogLevel.INFO.
        key (str | None): Optional key. Defaults to None.
        value (str): Value associated with key. Defaults to "".
        x (float | None): X coordinate. Defaults to None.
        y (float | None): Y coordinate. Defaults to None.
        z (float | None): Z coordinate. Defaults to None.
    """
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

    def to_json(self) -> dict:
        """Convert the message to a dictionary

        Returns:
            dict: Dictionary of the message fields.
        """
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def __str__(self) -> str:
        """Return the message serialized to a JSON string

        Returns:
            str: JSON string of the message.
        """
        return self.dump()

    @classmethod
    def from_json(cls, data: dict) -> "SocketMessage":
        """Create a SocketMessage instance from a dictionary

        Args:
            data (dict): Dictionary of the message fields.

        Returns:
            SocketMessage: Instance created from the dictionary.
        """
        return cls(**data)

    def dump(self) -> str:
        """Serialize the message to a JSON string

        Returns:
            str: JSON string of the message.
        """
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str) -> "SocketMessage":
        """Deserialize a JSON string into a SocketMessage instance

        Args:
            data (str): JSON string to deserialize.

        Returns:
            SocketMessage: Instance created from the JSON string.
        """
        return cls(**json.loads(data))


@dataclass
class SocketData:
    """Wrapper for arbitrary dictionary data transmitted via sockets

    Attributes:
        data (dict): The inner data dictionary to transmit.
    """
    data: dict

    def to_json(self) -> dict:
        """Return the inner data dictionary

        Returns:
            dict: The inner data dictionary.
        """
        return self.data

    def __str__(self) -> str:
        """Return the data serialized to a JSON string

        Returns:
            str: JSON string of the data.
        """
        return self.dump()

    @classmethod
    def from_json(cls, data: dict) -> "SocketData":
        """Create a SocketData instance from a dictionary

        Args:
            data (dict): Dictionary to wrap.

        Returns:
            SocketData: Instance wrapping the dictionary.
        """
        return cls(data)

    def dump(self) -> str:
        """Serialize the data dictionary to a JSON string

        Returns:
            str: JSON string of the data.
        """
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str) -> "SocketData":
        """Deserialize a JSON string into a SocketData instance

        Args:
            data (str): JSON string to deserialize.

        Returns:
            SocketData: Instance created from the JSON string.
        """
        return cls(json.loads(data))


def result_message(result: bool, ok_message: str, nok_message: str) -> SocketMessage:
    """Create a SocketMessage reflecting the success or failure of an operation

    Args:
        result (bool): Success status of the operation.
        ok_message (str): Message to use if result is True.
        nok_message (str): Message to use if result is False.

    Returns:
        SocketMessage: Message with appropriate text and log level.
    """
    return SocketMessage(
        type=MsgType.RESULT,
        message=(nok_message if result is False else ok_message),
        level=LogLevel.ERROR if result is False else LogLevel.INFO,
    )
