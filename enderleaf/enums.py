from enum import Enum


class FocusMode(Enum):
    MANUAL = "MANUAL"
    HUNT = "HUNT"
    AUTO = "AUTO"


class CropMode(Enum):
    CROP = "Cropped image"
    LINES = "Crop lines"
    IGNORE = "Ignore"


class CameraState(Enum):
    IDLE = "idle"
    VIDEO = "video"
    STILL = "still"
    SIMULATION = "simulation"


class ELStatus(Enum):
    IDLE = "Idle"
    JOB_IN_PROGRESS = "Job in progress"
    STOP_REQUESTED = "Stop requested"


class ImageMergeMode(Enum):
    MIN = "min"
    MAX = "max"
    AVG = "avg"
    MEDIAN = "median"


class ImageMergeMethod(Enum):
    RGB = ("rgb", [ImageMergeMode.MIN, ImageMergeMode.MIN, ImageMergeMode.MIN])
    HSV = ("hsv", [ImageMergeMode.MEDIAN, ImageMergeMode.MEDIAN, ImageMergeMode.MIN])
    LAB = ("lab", [ImageMergeMode.MIN, ImageMergeMode.MEDIAN, ImageMergeMode.MEDIAN])
    YUV = ("yuv", [ImageMergeMode.MIN, ImageMergeMode.MEDIAN, ImageMergeMode.MEDIAN])
    YCrCb = (
        "ycrcb",
        [ImageMergeMode.MIN, ImageMergeMode.MEDIAN, ImageMergeMode.MEDIAN],
    )


class CardPoint(Enum):
    NORTH = "NORTH"
    EAST = "EAST"
    SOUTH = "SOUTH"
    WEST = "WEST"


LIGHTS_CONF = [
    [],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.NORTH],
    [CardPoint.WEST],
    [CardPoint.SOUTH],
    [CardPoint.EAST],
    [CardPoint.NORTH, CardPoint.WEST],
    [CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.SOUTH, CardPoint.EAST],
    [CardPoint.EAST, CardPoint.NORTH],
    [CardPoint.NORTH, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.WEST, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.SOUTH],
    [CardPoint.EAST, CardPoint.NORTH, CardPoint.WEST],
]
LEN_LIGHTS_CONF = len(LIGHTS_CONF)


class LightsCycle(Enum):
    OFF = [LIGHTS_CONF[0]]
    FULL = [LIGHTS_CONF[1]]
    ONE_FOURTH = [LIGHTS_CONF[2], LIGHTS_CONF[3], LIGHTS_CONF[4], LIGHTS_CONF[5]]
    TWO_FOURTHS = [LIGHTS_CONF[6], LIGHTS_CONF[7], LIGHTS_CONF[8], LIGHTS_CONF[9]]
    THREE_FOURTHS = [LIGHTS_CONF[10], LIGHTS_CONF[11], LIGHTS_CONF[12], LIGHTS_CONF[13]]


class MsgType(str, Enum):
    PROGRESS = "PROGRESS".lower()
    RESULT = "RESULT".lower()
    IMAGE = "IMAGE".lower()
    MESSAGE = "MESSAGE".lower()
    PROBLEM = "PROBLEM".lower()
    FOCUS_PLOT = "FOCUS_PLOT".lower()
    POSITION_PLOT = "POSITION_PLOT".lower()
    CONFIG_DATA = "CONFIG_DATA".lower()


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    EXCEPTION = "exception"
    ERROR = "error"
    CRITICAL = "critical"

    @staticmethod
    def log_level_to_int(log_level) -> int:
        match log_level:
            case LogLevel.DEBUG:
                return 10
            case LogLevel.INFO:
                return 20
            case LogLevel.WARNING:
                return 30
            case LogLevel.EXCEPTION:
                return 40
            case LogLevel.ERROR:
                return 50
            case LogLevel.CRITICAL:
                return 60
            case _:
                raise NotImplementedError(f"Unknown log level: {log_level}")

    @staticmethod
    def int_to_log_level(value: int):
        match value:
            case 10:
                return LogLevel.DEBUG
            case 20:
                return LogLevel.INFO
            case 30:
                return LogLevel.WARNING
            case 40:
                return LogLevel.EXCEPTION
            case 50:
                return LogLevel.ERROR
            case 60:
                return LogLevel.CRITICAL
            case _:
                raise NotImplementedError(f"Unknown log level: {value}")


class LaunchOptons(str, Enum):
    PRECISE_FOCUS = "Precise focus"
    SWITCH_STATE = "Switch camera state"
    CENTER_OL = "Center on leaf"


class ControllerCommands(str, Enum):
    START = "Start"
    STOP = "Stop"
    PING = "Ping"
    CAPTURE_STILL = "Capture still"
    FOCUS_AUTO = "Autofocus"
    FOCUS_CLOSE = "Focus close"
    FOCUS_FAR = "Focus far"
    CONNECT_PRINTER = "Connect printer"
    GO_HOME = "Go Home"
    GO_IDLE = "Go Idle"
    GO_PARK = "Go park"
    CENTER_ON_QR_CODE = "Center on QR code"
    CHECK_CORNERS = "Check corners"
    TOGGLE_LIGHTS = "Toggle lights"
    CYCLE_LIGHTS = "Cycle lights"
    MOVE_TO = "Move to"
    GET_CONFIG = "Get config"

class NodeViewOption(str, Enum):
    IMAGE = "Image"
    PLOT_POSITION = "Position"
    PLOT_FOCUS = "Focus"
    CONFIG = "Config"
