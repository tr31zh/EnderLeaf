from dataclasses import dataclass
import json


from enderleaf.enums import MsgType, LogLevel


@dataclass
class SocketMessage:
    type: MsgType = MsgType.MESSAGE
    message: str = ""
    image: str = ""
    step: int = 0
    total: int = 0
    level: LogLevel = LogLevel.INFO
    key: str | None = None
    value: str = ""

    def to_json(self):
        return {
            k: getattr(self, k)
            for k in [
                "type",
                "message",
                "image",
                "step",
                "total",
                "level",
                "key",
                "value",
            ]
        }

    def __str__(self) -> str:
        return self.dump()

    @classmethod
    def from_json(cls, data: dict):
        return cls(**data)

    def dump(self):
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str):
        return cls(**json.loads(data))
    

@dataclass
class SocketData:
    data: dict

    def to_json(self):
        return self.data

    def __str__(self) -> str:
        return self.dump()

    @classmethod
    def from_json(cls, data: dict):
        return cls(data)

    def dump(self):
        return json.dumps(self.to_json())

    @classmethod
    def load(cls, data: str):
        return cls(json.loads(data))


def result_message(result, ok_message, nok_message: str) -> SocketMessage:
    return SocketMessage(
        type=MsgType.RESULT,
        message=(nok_message if result is False else ok_message),
        level=LogLevel.ERROR if result is False else LogLevel.INFO,
    )
