from enum import Enum

# MARK: Colors
C_BLACK = (0, 0, 0)
C_BLUE = (255, 0, 0)
C_BLUE_VIOLET = (226, 43, 138)
C_CABIN_BLUE = (209, 133, 67)
C_CYAN = (255, 255, 0)
C_DIM_GRAY = (105, 105, 105)
C_FUCHSIA = (255, 0, 255)
C_GREEN = (0, 128, 0)
C_LIGHT_STEEL_BLUE = (222, 196, 176)
C_LIME = (0, 255, 0)
C_MAROON = (0, 0, 128)
C_ORANGE = (80, 127, 255)
C_PURPLE = (128, 0, 128)
C_RED = (0, 0, 255)
C_SILVER = (192, 192, 192)
C_TEAL = (128, 128, 0)
C_WHITE = (255, 255, 255)
C_YELLOW = (0, 255, 255)
C_BROWN = (42, 42, 165)
C_SIENNA = (45, 82, 160)

# Colorblind safe colors
C_CBF_GREEN = (115, 158, 0)
C_CBF_RED = (0, 94, 213)
C_CBF_BLUE = (178, 114, 0)

C_RGB_BLACK = (0, 0, 0)
C_RGB_BLUE = (255, 0, 0)[::-1]
C_RGB_BLUE_VIOLET = (226, 43, 138)[::-1]
C_RGB_CABIN_BLUE = (209, 133, 67)[::-1]
C_RGB_CYAN = (255, 255, 0)[::-1]
C_RGB_DIM_GRAY = (105, 105, 105)[::-1]
C_RGB_FUCHSIA = (255, 0, 255)[::-1]
C_RGB_GREEN = (0, 128, 0)[::-1]
C_RGB_LIGHT_STEEL_BLUE = (222, 196, 176)[::-1]
C_RGB_LIME = (0, 255, 0)[::-1]
C_RGB_MAROON = (0, 0, 128)[::-1]
C_RGB_ORANGE = (80, 127, 255)[::-1]
C_RGB_PURPLE = (128, 0, 128)[::-1]
C_RGB_RED = (0, 0, 255)[::-1]
C_RGB_SILVER = (192, 192, 192)[::-1]
C_RGB_TEAL = (128, 128, 0)[::-1]
C_RGB_YELLOW = (0, 255, 255)[::-1]
C_RGB_WHITE = (255, 255, 255)
C_RGB_BROWN = (42, 42, 165)[::-1]
C_RGB_SIENNA = (45, 82, 160)[::-1]
# Colorblind safe colors
C_CBF_GREEN = (115, 158, 0)[::-1]
C_CBF_RED = (0, 94, 213)[::-1]
C_CBF_BLUE = (178, 114, 0)[::-1]

COLOR_SPACES = {
    "rgb": ["r", "g", "b"],
    "hsv": ["h", "s", "v"],
    "yiq": ["y", "i", "q"],
    "lab": ["l", "a", "b"],
    "yuv": ["y", "u", "v"],
    "ycrcb": ["y", "cr", "cb"],
}

TIME_FORMAT = "%Y%m%d%H%M%S"
PRECISE_TIME_FORMAT = "%Y%m%d%H%M%S%f"
DEFAULT_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_DATE_FORMAT = "%Y-%m-%d"
DEFAULT_TIME_FORMAT = "%H:%M:%S"


# MARK: Enums
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


# MARK: Light cycles
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


# MARK: Focus methods
FM_BREN = "BREN"
FM_FFTE = "FFTE"
FM_GLVA = "GLVA"
FM_GLVN = "GLVN"
FM_GRAE = "GRAE"
FM_GRAT = "GRAT"
FM_GRAS = "GRAS"
FM_LAPE = "LAPE"
FM_LAPM = "LAPM"
FM_LAPM = "LAPM"
FM_LAPV = "LAPV"
FM_LAPD = "LAPD"
FM_SFRQ = "SFRQ"
FM_TENG = "TENG"
FM_TENV = "TENV"
FM_VOLA = "VOLA"
FM_METHODS = [
    FM_BREN,
    # FM_FFTE,
    # FM_GLVA,
    # FM_GLVN,
    # FM_GRAE,
    FM_GRAS,
    FM_GRAT,
    FM_LAPD,
    FM_LAPE,
    FM_LAPM,
    FM_LAPV,
    FM_SFRQ,
]
