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


class LogKind(Enum):
    INFO = "info"
    WARNING = "warning"
    EXCEPTION = "exception"
    ERROR = "error"
    CRITICAL = "critical"


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
    PROGRESS = "progress"
    RESULT = "result"
    IMAGE = "image"
    MESSAGE = "message"
    PROBLEM = "problem"
    FOCUS_PLOT = "focus_plot"
    POSITION_PLOT = "position_plot"


class LogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    EXCEPTION = "exception"
    ERROR = "error"
    CRITICAL = "critical"

    @staticmethod
    def log_level_to_int(log_level) -> int:
        match log_level:
            case LogLevel.INFO:
                return 0
            case LogLevel.WARNING:
                return 10
            case LogLevel.EXCEPTION:
                return 20
            case LogLevel.ERROR:
                return 30
            case LogLevel.CRITICAL:
                return 40
            case _:
                return -13

    @staticmethod
    def int_to_log_level(value: int):
        match value:
            case 0:
                return LogLevel.INFO
            case 10:
                return LogLevel.WARNING
            case 20:
                return LogLevel.EXCEPTION
            case 30:
                return LogLevel.ERROR
            case 40:
                return LogLevel.CRITICAL
            case _:
                raise NotImplementedError(f"Unknown log level: {value}")


class LaunchOptons(str, Enum):
    PRECISE_FOCUS = "Precise focus"
    SWITCH_STATE = "Switch camera state"
    CENTER_OL = "Center on leaf"


class ControllerCommands(str, Enum):
    START = "node_start"
    STOP = "node_stop"
    PING = "node_ping"
    CAPTURE_STILL = "node_capture_still"
    AUTO = "node_auto"
    CLOSE = "node_close"
    FAR = "node_far"
    CONNECT_PRINTER = "node_connect_printer"
    GO_HOME = "node_go_home"
    GO_IDLE = "node_go_idle"
    GO_PARK = "node_go_park"
    CENTER_ON_QR_CODE = "node_center_on_qr_code"
    CHECK_CORNERS = "node_check_corners"
    TOGGLE_LIGHTS = "node_toggle_lights"
    CYCLE_LIGHTS = "node_cycle_lights"
    MOVE_TO = "node_move_to"


class NodeViewOption(str, Enum):
    IMAGE = "Image"
    PLOT_POSITION = "Position Plot"
    PLOT_FOCUS = "Focus Plot"
