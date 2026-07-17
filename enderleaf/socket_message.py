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

    def to_json(self):
        return {
            "type": self.type,
            "message": self.message,
            "image": self.image,
            "step": self.step,
            "total": self.total,
            "level": self.level,
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


def result_message(result, ok_message, nok_message: str) -> SocketMessage:
    return SocketMessage(
        type=MsgType.RESULT,
        message=(ok_message if result is True else nok_message),
        level=LogLevel.INFO if result is True else LogLevel.ERROR,
    )
