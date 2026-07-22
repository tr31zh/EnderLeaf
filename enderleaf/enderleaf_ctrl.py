import logging
from itertools import product
from pathlib import Path
from datetime import datetime as dt
from threading import Thread, Event
import time
from functools import wraps
from timeit import default_timer as timer
import io
import asyncio

from tqdm import tqdm

import numpy as np
import cv2
import pandas as pd
import albumentations as A

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    from libcamera import controls
except:
    import random
    from enderleaf.dummy_camera import Picamera2, JpegEncoder, FileOutput

    simulate_camera = True
else:
    simulate_camera = False

from enderscope.scan_patterns import (
    snake,
    get_extremes,
    plot_path_status,
    plot_discs_status,
)
from enderscope.bed import bed
from enderscope.async_serial import list_ports, Stage
from enderscope.enderlights_pi import Enderlights, default_leds

from enderleaf.const import FM_BREN, PRECISE_TIME_FORMAT
from enderleaf.enums import (
    CameraState,
    CropMode,
    CardPoint,
    LightsCycle,
    MsgType,
    LogLevel,
    ELStatus,
    LIGHTS_CONF,
    LEN_LIGHTS_CONF,
)
from enderleaf.socket_message import SocketMessage
from enderleaf.streaming import StreamingOutput
from enderleaf.focus_metrics import compute_focus_metric
from enderleaf.tools import ensure_folder, format_datetime, write_dataframe, format_time
from enderleaf.image import (
    crop_image,
    Rectangle,
    get_circles,
    merge_images,
    ImageMergeMode,
    encode_image,
)
from enderleaf.qr_reader import get_qr_data, check_qr_code
from enderleaf.draw import draw_circles, plot_focus_plt

logger = logger = logging.getLogger(__name__)

DST_FLD = Path(".").joinpath("output")


def extract_metadata(metadata):
    data = {
        k: v
        for k, v in metadata.items()
        if k
        in [
            "AeState",
            "AfState",
            "AnalogueGain",
            "ColourTemperature",
            "DigitalGain",
            "ExposureTime",
            "FocusFoM",
            "LensPosition",
            "Lux",
        ]
    } | {"GainRed": metadata["ColourGains"][0], "GainBlue": metadata["ColourGains"][1]}
    return dict(sorted({k: [v] for k, v in data.items()}.items()))


def plot_to_image(fig):
    io_buf = io.BytesIO()
    fig.savefig(io_buf, format="raw")
    io_buf.seek(0)
    img_arr = np.reshape(
        np.frombuffer(io_buf.getvalue(), dtype=np.uint8),
        shape=(int(fig.bbox.bounds[3]), int(fig.bbox.bounds[2]), -1),
    )
    io_buf.close()
    return img_arr


class EnderLeafController(object):
    # MARK: Init
    def __init__(self):
        # Camera
        self.output = None
        self.camera = Picamera2()
        self._video_conf = self.camera.create_video_configuration(
            raw=self.camera.sensor_modes[2], main={"preserve_ar": False}
        )
        self._still_conf = self.camera.create_still_configuration()
        self._camera_state = (
            CameraState.SIMULATION if simulate_camera is True else CameraState.IDLE
        )

        self.crop_left = 1200
        self.crop_right = 1200
        self.crop_top = 400
        self.crop_bottom = 350
        self.crop_mode = CropMode.LINES.value

        self.plate_x = 200
        self.plate_y = 200
        self.plate_row_count = 9
        self.plate_col_count = 9
        self.focus_start_z = 36
        self.focus_delta_z = 10
        self.focus_methods = [FM_BREN]
        self.best_focus_method = FM_BREN

        self.top_lights = Enderlights(default_leds["groov_led"], brightness=1)
        self.light_cycles = [LightsCycle.ONE_FOURTH]

        self._positions = []
        self._old_crop_values = -1, -1, -1, -1
        self._homed = False
        self._stage = None
        self._good_discs = []
        self._bad_discs = []
        self._ligths_cycle_index = 0
        self._px_to_mm = -1
        self._errors = 0
        self.status = ELStatus.IDLE

        # Socket communication
        self.socket = None
        # Callbacks
        self.update_focus_plot = None
        self.update_positions = None
        self.update_progress = None

    async def send_message(self, message):
        if self.socket is not None:
            await self.socket.send(
                SocketMessage(type=MsgType.MESSAGE, message=message).dump()
            )

    async def send_image(self, image):
        if self.socket is not None:
            result = encode_image(
                A.LongestMaxSize(max_size=1024, p=1)(image=image)["image"]
            )
            await self.socket.send(
                SocketMessage(type=MsgType.IMAGE, image=result).dump()
            )

    async def send_problem(self, level: LogLevel, message: str):
        if self.socket is not None:
            await self.socket.send(
                SocketMessage(type=MsgType.PROBLEM, message=message, level=level).dump()
            )

    async def send_progress(self, step, total):
        if self.socket is not None:
            await self.socket.send(
                SocketMessage(type=MsgType.PROGRESS, step=step, total=total).dump()
            )

    async def send_position_plot(self, index: int | list | None = None):
        if self.socket is None:
            return
        elif len(self._positions) == 0:
            fig = plot_path_status(z=await self.get_z())
        elif len(self._good_discs) > 0 or len(self._bad_discs) > 0:
            fig = plot_discs_status(
                path=self._positions,
                good_discs=self._good_discs,
                bad_discs=self._bad_discs,
                highlighted_indexes=index,
                title="" if index is not None else "Discs status",
                z=await self.get_z(),
            )
        else:
            fig = plot_path_status(
                path=self._positions,
                circle_diam=17,
                highlighted_indexes=index,
                title="",
                z=await self.get_z(),
            )

        await self.socket.send(
            SocketMessage(
                type=MsgType.POSITION_PLOT, image=encode_image(plot_to_image(fig))
            ).dump()
        )

    async def send_focus_plot(self, df_scores: pd.DataFrame):
        if self.socket is None:
            return
        print("sent focus plot")
        await self.socket.send(
            SocketMessage(
                type=MsgType.FOCUS_PLOT,
                image=encode_image(plot_to_image(plot_focus_plt(df=df_scores))),
            ).dump()
        )

    async def send_data(self, data):
        if self.socket is None:
            return
        await self.socket.send(
            SocketMessage(type=MsgType.CONFIG_DATA, image=data).dump()
        )

    async def send_ping_feedback(self):
        try:
            print("ping requested")
            _ = await self.capture_array()
            print("image sent")
            # await self.send_data(self.to_json())
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION, message=f"Ping failed: {str(e)}"
            )
            return False
        else:
            return True

    async def send_config(self):
        if self.socket is None:
            return
        try:
            await self.socket.send(
                SocketMessage(type=MsgType.CONFIG_DATA, key=None).dump()
            )
            for k, v in self.to_json().items():
                await self.socket.send(
                    SocketMessage(type=MsgType.CONFIG_DATA, key=k, value=v).dump()
                )
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message=f"Failed to send config data: {str(e)}",
            )
            return False
        else:
            return True

    async def send_result(self, level: LogLevel, message: str):
        if self.socket is None:
            return
        await self.socket.send(
            SocketMessage(type=MsgType.RESULT, message=message, level=level).dump()
        )

    def reset(self):
        self.crop_left = 1200
        self.crop_right = 1200
        self.crop_top = 400
        self.crop_bottom = 350
        self.crop_mode = CropMode.LINES.value
        self.plate_x = 200
        self.plate_y = 200
        self.plate_row_count = 9
        self.plate_col_count = 9
        self.focus_start_z = 36
        self.focus_delta_z = 10
        self.top_lights.brightness = 1.0

    def log(self, kind, message: str):
        indent = ""
        match kind:
            case LogLevel.INFO:
                logger.info(indent + message)
            case LogLevel.WARNING:
                logger.warning(indent + message)
            case LogLevel.EXCEPTION:
                logger.exception(indent + message)
            case LogLevel.ERROR:
                logger.error(indent + message)
            case LogLevel.CRITICAL:
                logger.critical(indent + message)
            case _:
                raise NotImplementedError(f"Unknown log kind '{kind}'")

    def to_json(self) -> dict:
        return {
            k: getattr(self, k)
            for k in [
                "crop_left",
                "crop_right",
                "crop_top",
                "crop_bottom",
                "crop_mode",
                "plate_x",
                "plate_y",
                "plate_row_count",
                "plate_col_count",
                "focus_start_z",
                "focus_delta_z",
            ]
        }

    def from_json(self, data: dict) -> None:
        for k, v in data.items():
            if k != "lights_conf":
                try:
                    setattr(self, k, v)
                except:
                    pass

    def backup_crop_values(self):
        self._old_crop_values = (
            self.crop_left,
            self.crop_right,
            self.crop_top,
            self.crop_bottom,
        )

    def restore_crop_values(self):
        self.crop_left, self.crop_right, self.crop_top, self.crop_bottom = (
            self._old_crop_values
        )

    def apply_image_crop(self, image, crop_data: Rectangle | None = None):
        crop_data = (
            Rectangle(
                top=self.crop_top,
                bottom=image.shape[0] - self.crop_bottom,
                left=self.crop_left,
                right=image.shape[1] - self.crop_right,
            )
            if crop_data is None
            else crop_data
        )
        if (
            crop_data.top != 0
            or crop_data.bottom != 0
            or crop_data.left != 0
            or crop_data.right != 0
        ):
            match self.crop_mode:
                case CropMode.CROP.value:
                    image = crop_image(image=image, crop_data=crop_data)
                case CropMode.LINES.value:
                    foreground = crop_image(image=image, crop_data=crop_data)
                    image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                    image = cv2.merge([image, image, image])
                    image[
                        crop_data.top : crop_data.bottom,
                        crop_data.left : crop_data.right,
                    ] = foreground
                    image = crop_data.to_cv(
                        image=image, color=(255, 0, 255), thickness=4
                    )
                case CropMode.IGNORE.value:
                    pass
                case _:
                    raise NotImplementedError(f"Unknown case {self.crop_mode}")
        return image

    async def set_crop(
        self, left, right, top, bottom, crop_mode: CropMode | None = None
    ):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        if crop_mode is not None:
            self.crop_mode = crop_mode
        await self.capture_array()

    def set_plate(
        self,
        plate_x,
        plate_y,
        plate_row_count,
        plate_col_count,
        focus_start_z,
        focus_delta_z,
    ):
        self.plate_x = plate_x
        self.plate_y = plate_y
        self.plate_row_count = plate_row_count
        self.plate_col_count = plate_col_count
        self.focus_start_z = focus_start_z
        self.focus_delta_z = focus_delta_z

    def set_controls(self, control_data: dict):
        self.camera.set_controls(control_data)

    async def set_exposure(self, wait=1):
        avg_lights = self.top_lights.mean
        if avg_lights == 255:
            cam_controls = {
                "AeEnable": False,
                "ExposureTime": 5000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.4, 0.83),
                "NoiseReductionMode": (
                    -1
                    if simulate_camera is True
                    else controls.draft.NoiseReductionModeEnum.HighQuality
                ),
            }
        elif avg_lights == 191.25:
            cam_controls = {
                "AeEnable": False,
                "ExposureTime": 6000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.4, 0.83),
                "NoiseReductionMode": (
                    -1
                    if simulate_camera is True
                    else controls.draft.NoiseReductionModeEnum.HighQuality
                ),
            }
        elif avg_lights == 127.5:
            cam_controls = {
                "AeEnable": False,
                "ExposureTime": 12000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.4, 0.83),
                "NoiseReductionMode": (
                    -1
                    if simulate_camera is True
                    else controls.draft.NoiseReductionModeEnum.HighQuality
                ),
            }
        elif avg_lights == 63.75:
            cam_controls = {
                "AeEnable": False,
                "ExposureTime": 24000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.4, 0.83),
                "NoiseReductionMode": (
                    -1
                    if simulate_camera is True
                    else controls.draft.NoiseReductionModeEnum.HighQuality
                ),
            }
        else:
            cam_controls = {"AeEnable": True, "AwbEnable": True}
        self.log(LogLevel.INFO, f"New controls: {cam_controls}")
        self.set_controls(cam_controls)
        await asyncio.sleep(wait)
        await self.capture_array()

    async def set_top_lights(self, state: bool, card_points: list | None = None):
        if state is False:
            self.top_lights.shutter(False)
        elif card_points is None:
            self.top_lights.shutter(state)
        else:
            self.top_lights.shutter(False)
            self.top_lights.set_cardinals(card_points=card_points)

    async def set_lights(self, lights: list, wait=1):
        self.top_lights.set_cardinals(lights)
        await self.set_exposure(wait=wait)

    async def shutter(self, state: bool, wait=1):
        self.top_lights.shutter(state=state)
        await self.set_exposure(wait=wait)

    async def cycle_lights(self, wait=1):
        if self._ligths_cycle_index >= LEN_LIGHTS_CONF - 1:
            self._ligths_cycle_index = 0
        else:
            self._ligths_cycle_index += 1
        self.set_lights(LIGHTS_CONF[self._ligths_cycle_index], wait=wait)

    async def set_top_lights_brightness(self, brightness, wait=1):
        self.top_lights.brightness = brightness
        await asyncio.sleep(wait)
        await self.capture_array()

    async def set_sensor_mode(self, sensor_mode, wait=0.5):
        self.camera.configure(self.camera.create_video_configuration(raw=sensor_mode))
        await asyncio.sleep(wait)
        await self.capture_array()

    async def set_focus_mode(self, focus_mode, wait=0.5):
        self.camera.set_controls({"AfMode": focus_mode})
        await asyncio.sleep(wait)
        await self.capture_array()

    async def set_focus_distance(self, focus_distance, wait=0.5):
        self.camera.set_controls({"LensPosition": focus_distance})
        await asyncio.sleep(wait)
        await self.capture_array()

    async def autofocus_cycle(self, wait=1):
        self.camera.autofocus_cycle()
        if simulate_camera is True:
            pass
        else:
            self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
        await asyncio.sleep(wait)
        await self.capture_array()

    async def set_focus_close(self):
        fc_pos = self.camera.camera_controls["LensPosition"][1]
        if self.camera.capture_metadata()["LensPosition"] != fc_pos:
            self.camera.set_controls({"LensPosition": fc_pos})
        await self.capture_array()

    async def set_focus_far(self):
        ff_pos = self.camera.camera_controls["LensPosition"][0]
        if self.camera.capture_metadata()["LensPosition"] != ff_pos:
            self.camera.set_controls({"LensPosition": ff_pos})
        await self.capture_array()

    async def capture_array(self):
        match self._camera_state:
            case CameraState.STILL:
                image = self.camera.capture_array("main")
                metadata = self.camera.capture_metadata()
            case CameraState.IDLE:
                return
            case CameraState.SIMULATION:
                image = self.camera.capture_array("raw")
                metadata = self.camera.capture_metadata()
            case _:
                raise NotImplementedError(f"Unknown case {self.camera_state}")

        await self.send_image(self.apply_image_crop(image=image))

        return (
            crop_image(
                image=image,
                crop_data=Rectangle(
                    left=self.crop_left,
                    top=self.crop_top,
                    right=-self.crop_right,
                    bottom=-self.crop_bottom,
                ),
            ),
            metadata,
        )

    async def capture_image(self):
        image, _ = await self.capture_array()
        return image

    async def start(self):
        if self._camera_state != CameraState.STILL:
            self.camera.configure(self._still_conf)
            self.camera.start()
            if simulate_camera is True:
                self.camera.color_step_size = 5
                self.camera.color_index = random.randint(0, 255)
            else:
                self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
            await self.set_focus_close()
            await self.shutter(True)
            await asyncio.sleep(0.5)
            if self._camera_state != CameraState.SIMULATION:
                self._camera_state = CameraState.STILL

    async def stop(self):
        self.sync_stop()

    def sync_stop(self):
        if self._camera_state not in [CameraState.IDLE, CameraState.SIMULATION]:
            self.camera.stop()
            self._camera_state = CameraState.IDLE

    async def switch_state(self, new_mode: CameraState):
        if (
            new_mode == self._camera_state
            or self._camera_state == CameraState.SIMULATION
        ):
            return
        match new_mode:
            case CameraState.IDLE:
                await self.stop()
            case CameraState.STILL:
                await self.stop()
                self.camera.switch_mode(self._still_conf)
            case CameraState.SIMULATION:
                pass
            case _:
                raise NotImplementedError(f"Unknown case {new_mode}")
        self._camera_state = new_mode

    def check_stage(self):
        return self._stage is not None

    def check_homed(self):
        return self._homed

    async def printer_ready(self):
        result = self.check_stage() and self.check_homed()
        if result is False:
            await self.send_problem(
                level=LogLevel.EXCEPTION, message="Printer not ready"
            )
        return result

    async def get_position(self):
        if await self.printer_ready() is False:
            return False
        return await self._stage.get_position()

    async def get_z(self):
        if await self.printer_ready() is False:
            return False
        *_, z = await self._stage.get_position()
        return z

    async def finish_moves(self):
        if await self.printer_ready() is False:
            return False
        await self._stage.finish_moves()
        await self.capture_array()

    async def move_position(
        self,
        position,
        index: int | None = None,
        update_position_plot: bool = True,
        update_preview: bool = False,
    ):
        if await self.printer_ready() is False:
            return
        await self._stage.move_position(
            position, call_back=self.capture_array if update_preview is True else None
        )
        await self.send_position_plot(index=index)
        await self.finish_moves()

    async def move_absolute(self, x, y, z, update_preview: bool = False):
        if await self.printer_ready() is False:
            return
        await self._stage.move_absolute(
            x, y, z, call_back=self.capture_array if update_preview is True else None
        )
        await self.finish_moves()

    async def move_relative(
        self, x, y, z: int | None = None, update_preview: bool = False
    ):
        if await self.printer_ready() is False:
            return
        await self._stage.move_relative(
            x, y, z, call_back=self.capture_array if update_preview is True else None
        )
        await self.finish_moves()

    async def move_to(self, position: int):
        if len(self._positions) == 0:
            return
        position -= 1
        x, y = self._positions[position]
        await self.move_position((x, y), index=[position], update_preview=True)

    async def go_home(self):
        await self.capture_array()
        if self.check_stage() is False:
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message="Unable to home, printer not connected",
            )
        try:
            await self.send_message("Started homing")
            if await self._stage.safe_home(call_back=self.capture_array) is False:
                await self.capture_array()
                self.send_problem(
                    level=LogLevel.WARNING, message="Failed to home, retrying"
                )
                if await self._stage.safe_home(call_back=self.capture_array) is False:
                    raise ConnectionError("Unable to home")
            self._homed = True
            await self.finish_moves()
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION, message=f"Unable to home: {str(e)}"
            )
            return False
        else:
            return True

    async def connect_printer(self, port_name=None):
        try:
            for port in list_ports():
                if str(port) == port_name:
                    self._stage = Stage(port, 115200)
                    if await self.go_home() is True:
                        await self.send_message(message="Homing successful")
                    else:
                        return False
                    break
            else:
                self.send_problem(
                    level=LogLevel.EXCEPTION,
                    message=f"Failed to to connect to requested port: '{str(port)}'",
                )
            x, y, _ = await self.get_position()
            await self.move_absolute(x, y, self.focus_start_z, update_preview=True)
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message=f"Unable to connect to printer: {str(e)}",
            )
            return False
        else:
            return True

    async def go_rest(self):
        if await self.printer_ready() is False:
            return False
        try:
            await self.move_position((bed.x_min, bed.y_max, bed.rest_height))
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION, message=f"Unable to go idle: {str(e)}"
            )
            return False
        else:
            return True

    async def go_park(self):
        if await self.printer_ready() is False:
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message="Unable to park, printer not ready",
            )
            return False
        try:
            await self.move_position((bed.x_min, bed.y_max // 2, bed.rest_height))
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION, message=f"Unable to go park: {str(e)}"
            )
            return False
        else:
            return True

    async def read_qr_data(self, image: np.ndarray | None = None):
        if image is None:
            image = await self.capture_image()
        qr_data = get_qr_data(image)
        if check_qr_code(qr_data) is False:
            old_brightness = self.top_lights.brightness
            try:
                self.top_lights.brightness = 0.5
                await asyncio.sleep(2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                self.top_lights.brightness = old_brightness
                await asyncio.sleep(2)
        if check_qr_code(qr_data) is False:
            try:
                await self.shutter(False, 2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                await self.shutter(True, 2)
        if check_qr_code(qr_data) is False:
            try:
                self.set_top_lights(True, [CardPoint.NORTH])
                await asyncio.sleep(2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                await self.shutter(True, 2)
        if check_qr_code(qr_data) is False:
            try:
                self.set_top_lights(True, [CardPoint.EAST])
                await asyncio.sleep(2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                await self.shutter(True, 2)
        if check_qr_code(qr_data) is False:
            try:
                self.set_top_lights(True, [CardPoint.SOUTH])
                await asyncio.sleep(2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                await self.shutter(True, 2)
        if check_qr_code(qr_data) is False:
            try:
                self.set_top_lights(True, [CardPoint.WEST])
                await asyncio.sleep(2)
                qr_data = get_qr_data(await self.capture_image())
            finally:
                await self.shutter(True, 2)

        if check_qr_code(qr_data) is False:
            self.log(LogLevel.ERROR, "Failed to read QR code")
        return qr_data

    async def get_qr_pos(self, image):
        qr_data = await self.read_qr_data(image)
        if check_qr_code(qr_data) is False:
            raise ValueError("Unable to detect QR code")
        min_x, min_y, max_x, max_y = qr_data["points"][0]
        return (min_x + max_x) // 2, (min_y + max_y) // 2, min_x, min_y, max_x, max_y

    async def build_snake(self, x, y) -> np.ndarray:
        self._positions = snake(
            cols=self.plate_col_count, rows=self.plate_row_count
        ) * [
            # steps
            self.plate_x / self.plate_row_count,
            self.plate_y / self.plate_col_count,
        ] + [
            # origin
            x,
            y,
        ]
        if self.update_positions is not None:
            self.update_positions(self._positions)
        await self.send_position_plot(index=[0])

    async def get_focused_z(self, delta_z=1):
        if await self.printer_ready() is False:
            return
        await self.send_message(message="Acquiring precise focus")
        await self.set_focus_close()
        z_range = np.array(range(-self.focus_delta_z, self.focus_delta_z, delta_z))
        pos = await self.get_position()
        mxScore = -1
        bestZ = 0
        variances = {"z": []} | {fm: [] for fm in self.focus_methods}

        for i, z in enumerate(z_range):
            await self.move_position(
                [pos[0], pos[1], z + self.focus_start_z], update_position_plot=False
            )
            await asyncio.sleep(0.2)
            img = await self.capture_image()
            gray_image = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            variances["z"].append(z + self.focus_start_z)
            for method in self.focus_methods:
                _fm = compute_focus_metric(image=gray_image, method=method)
                variances[method].append(_fm)
                if method == self.best_focus_method:
                    score = _fm
            if score > mxScore:
                mxScore = score
                bestZ = z + self.focus_start_z
            await self.send_progress(step=i, total=len(z_range))
        df_scores = pd.DataFrame(data=variances)
        for c in self.focus_methods:
            df_scores[c] = (df_scores[c] - df_scores[c].min()) / (
                df_scores[c].max() - df_scores[c].min()
            )
        df_scores["combined"] = df_scores[self.focus_methods].sum(axis=1) / len(
            self.focus_methods
        )

        await self.move_position([pos[0], pos[1], bestZ], update_position_plot=False)
        await self.capture_array()
        await self.send_focus_plot(df_scores=df_scores)

        return (
            df_scores[
                df_scores[self.best_focus_method]
                == df_scores[self.best_focus_method].max()
            ]
            .reset_index()
            .iloc[0]
            .z
        ), df_scores

    # MARK: Center On QR
    async def center_on_target(
        self, target_x: float, target_y: float, current_x: float, current_y: float
    ):
        await self.move_relative(
            -(target_x - current_x) * self._px_to_mm,
            (target_y - current_y) * self._px_to_mm,
            0,
        )

    async def center_on_qr_code(self, step_val=10, precise_focusing: bool = True):
        if await self.printer_ready() is False:
            return
        await self.set_focus_close()
        await self.shutter(True)
        self.backup_crop_values()
        self._good_discs = []
        self._bad_discs = []
        try:
            await self.set_crop(0, 0, 0, 0)
            await self.move_absolute(
                bed.qr_start_x, bed.qr_start_y, self.focus_start_z, update_preview=True
            )
            image = await self.capture_image()
            cy, cx = image.shape[0] // 2, image.shape[1] // 2
            try:
                qr_cx, qr_cy, *_ = await self.get_qr_pos(image)
            except:
                await self.get_focused_z()
                qr_cx, qr_cy, *_ = await self.get_qr_pos(await self.capture_image())
            step_x, step_y = -step_val if cx > qr_cx else step_val, (
                step_val if cy > qr_cy else -step_val
            )
            await self.move_relative(step_x, step_y)
            try:
                new_qr_cx, new_qr_cy, *_ = await self.get_qr_pos(
                    await self.capture_image()
                )
            except:
                await self.get_focused_z()
                new_qr_cx, new_qr_cy, *_ = await self.get_qr_pos(
                    await self.capture_image()
                )

            self._px_to_mm = 1 / (
                (abs(qr_cx - new_qr_cx) + abs(qr_cy - new_qr_cy)) / 2 / 10
            )

            await self.center_on_target(
                target_x=cx, target_y=cy, current_x=new_qr_cx, current_y=new_qr_cy
            )
            x, y, z = await self.get_position()
            if precise_focusing is True:
                await self.set_crop(0, 0, 0, 0)
                image = await self.capture_image()
                cx, cy, min_x, min_y, max_x, max_y = await self.get_qr_pos(image)
                await self.set_crop(
                    top=min_y,
                    bottom=image.shape[0] - max_y,
                    left=min_x,
                    right=image.shape[1] - max_x,
                )
                z, _ = await self.get_focused_z()
            await self.build_snake(x, y)
            return x, y, z
        except Exception as e:
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message=f"Failed to center on QR code: '{str(e)}'",
            )
        finally:
            self.restore_crop_values()

    async def check_corners(self):
        if await self.printer_ready() is False:
            return
        await self.set_focus_close()
        x, y, z = self.center_on_qr_code()
        self.build_snake(x, y)
        for position in get_extremes(self._positions):
            await self.move_position((position.x, position.y, z), index=[position.name])
            await asyncio.sleep(1)
        await self.move_position((x, y, z), index=[0])

    # MARK: Init Job
    async def init_job(self, precise_focusing: bool = True):
        await self.send_message("Initializing job")
        self._errors = 0
        if await self.printer_ready() is False:
            return
        await self.set_focus_close()
        await self.shutter(True)
        x, y, z = await self.center_on_qr_code(precise_focusing=precise_focusing)
        qr_data = await self.read_qr_data(await self.capture_image())
        exp_name = qr_data["info"][0].replace("_", "#")
        try:
            exp, inoc, plate = exp_name.split("#")
        except:
            exp_name = "ExpXXDMXX#IX#PXX"
            exp, inoc, plate = exp_name.split("#")
            await self.send_problem(
                level=LogLevel.EXCEPTION,
                message=f"Failed to detect QR code switching to default plate {plate} in experiemnt {exp}, inoc {inoc}",
            )
        else:
            await self.send_message(
                message=f"Detected plate {plate} in experiemnt {exp}, inoc {inoc}",
            )
        return exp, inoc, plate, z

    def parse_positions(self):
        return [
            (idx, p, c, r)
            for idx, (p, (c, r)) in enumerate(
                zip(
                    self._positions,
                    [
                        (
                            # Since we use a snake pattern row index must be updated  when
                            # column is an even number
                            chr(
                                65 + (r if c % 2 == 1 else self.plate_row_count - 1 - r)
                            ),
                            c,  # Column does not need update
                        )
                        for c, r in list(
                            # Use product to genarate the col row combinations
                            product(
                                [i + 1 for i in range(self.plate_col_count)],
                                [i for i in range(self.plate_row_count)],
                            )
                        )
                    ],
                )
            )
        ]

    # @time_method
    async def acquire_leaf_disc(
        self,
        light_cycle: list,
        center_on_leaf: bool = False,
        exp_name: str | None = None,
        inoc: str | None = None,
        plate: str | None = None,
        r: str | None = None,
        c: int | None = None,
        start_ts: str | None = None,
        dst_folder: Path | None = None,
        z: float | None = None,
    ):
        cycle_id = int(format_datetime())
        file_paths = []
        images = []
        metadatas = []
        df = pd.DataFrame()
        if center_on_leaf is True:
            image = await self.capture_image()
            cy, cx = image.shape[0] // 2, image.shape[1] // 2
            circles = get_circles(image, channel="s", resize_factor=8)
            if len(circles["accepted"]) == 1:
                _, ccx, ccy, _r = circles["accepted"][0]
                await self.center_on_target(cx, cy, ccx, ccy)
        for light_conf in light_cycle.value:
            if len(light_cycle.value) > 1:
                await self.set_lights(light_conf, wait=0.2)
            else:
                await asyncio.sleep(0.2)
            image, metadata = await self.capture_array()
            images.append(image)
            if exp_name is None:
                metadatas.append(metadata)
                continue
            now = dt.now()
            now_str = format_datetime(t=now, time_format=PRECISE_TIME_FORMAT)
            card_str = ""
            if CardPoint.NORTH in light_conf:
                card_str += "n"
            if CardPoint.EAST in light_conf:
                card_str += "e"
            if CardPoint.SOUTH in light_conf:
                card_str += "s"
            if CardPoint.WEST in light_conf:
                card_str += "w"
            file_path = dst_folder.joinpath(
                f"{exp_name}#{inoc}#{plate}#{r}#{c}#{card_str}#{now_str}"
            ).with_suffix(".png")
            file_paths.append(file_path)
            metadata = {
                "exp": [exp_name],
                "inoc": [inoc],
                "plate": [plate],
                "row": [r],
                "col": [c],
                "date_time": [now_str],
                "file_name": [file_path.name],
                "date": [now.date()],
                "year": [now.year],
                "month": [now.month],
                "day": [now.day],
                "time": [now.time()],
                "hour": [now.hour],
                "minute": [now.minute],
                "second": [now.second + (now.microsecond // 1000) / 1000],
                "job_ts": [start_ts],
                "cycle_id": [cycle_id],
                "north": [CardPoint.NORTH in light_conf],
                "east": [CardPoint.EAST in light_conf],
                "south": [CardPoint.SOUTH in light_conf],
                "west": [CardPoint.WEST in light_conf],
                "light_cycle": [light_cycle.name],
                "center_on_leaf": [center_on_leaf],
                "height": [z],
                "crop_top": [self.crop_top],
                "crop_bottom": [self.crop_bottom],
                "crop_left": [self.crop_left],
                "crop_right": [self.crop_right],
                "brightness": [self.top_lights.brightness],
            } | extract_metadata(metadata=metadata)
            metadatas.append(metadata)
            df = pd.concat([df, pd.DataFrame(metadata)])
        return images, metadatas, file_paths, df

    # MARK: Launch
    async def launch_acquisition(
        self, precise_focusing: bool = True, center_on_leaf: bool = False
    ):
        if await self.printer_ready() is False:
            return False
        await self.set_focus_close()
        self.status = ELStatus.JOB_IN_PROGRESS
        try:
            exp, inoc, plate, z = await self.init_job(precise_focusing=precise_focusing)
        except Exception as e:
            await self.send_result(
                level=LogLevel.ERROR, message=f"Failed to initialize job: '{str(e)}'"
            )
            self.status = ELStatus.IDLE
            return False
        df = pd.DataFrame()

        fld_images = DST_FLD.joinpath("images", exp, inoc)
        fld_data = DST_FLD.joinpath("job_data", exp, inoc)
        ensure_folder(fld_images)
        ensure_folder(fld_data)
        start_ts = format_datetime(dt.now())
        data_file_name = fld_data.joinpath(
            exp + "#I" + str(inoc) + "#P" + str(plate) + "#" + start_ts
        ).with_suffix(".csv")
        job_list = self.parse_positions()
        len_job_list = len(job_list)
        await self.send_message("Acquiring leaf discs images")
        for idx, p, c, r in job_list:
            try:
                await self.move_position(np.append(p, z), index=idx)
                for cycle_id, light_cycle in enumerate(self.light_cycles):
                    if len(self.light_cycles) > 1:
                        self.set_lights(light_cycle.value[0], wait=1)
                    images, _, file_paths, df_cycle = await self.acquire_leaf_disc(
                        exp_name=exp,
                        light_cycle=light_cycle,
                        inoc=inoc,
                        plate=plate,
                        r=r,
                        c=c,
                        start_ts=start_ts,
                        dst_folder=fld_images,
                        z=z,
                        center_on_leaf=center_on_leaf is True and cycle_id == 0,
                    )
                    df = pd.concat([df, df_cycle])
                    for file_path, image in zip(file_paths, images):
                        try:
                            write_ok = cv2.imwrite(
                                str(file_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                            )
                        except Exception as e:
                            await self.send_problem(
                                level=LogLevel.EXCEPTION,
                                message=f"Exception '{str(e)}' wheil saving image '{str(file_path)}'",
                            )
                        else:
                            if write_ok is True:
                                pass
                            else:
                                await self.send_problem(
                                    level=LogLevel.EXCEPTION,
                                    message=f"FAILED to write '{file_path.name}'",
                                )
                    await self.send_progress(step=idx, total=len_job_list)
            except Exception as e:
                await self.send_problem(
                    level=LogLevel.EXCEPTION,
                    message=f"Exception while acquiring C{c}, R{r}: {str(e)}",
                )
            if self.status == ELStatus.STOP_REQUESTED:
                await self.send_problem(
                    level=LogLevel.WARNING, message="Stopping process"
                )
                break

        await self.go_rest()
        self.status = ELStatus.IDLE

        if self.status == ELStatus.STOP_REQUESTED:
            await self.send_result(
                level=LogLevel.WAARNING, message="User stopped process"
            )
        else:
            write_dataframe(df, data_file_name)
            if self._errors == 0:
                await self.send_result(
                    level=LogLevel.INFO, message="All frames processed successfully"
                )
            else:
                await self.send_result(
                    level=LogLevel.ERROR,
                    message=f"All frames processed {self._errors} error(s) occured, see log for details",
                )
        await self.send_position_plot()

        return True

    # MARK: Tools
    async def check_discs_positions(self, precise_focusing: bool = False):
        if await self.printer_ready() is False:
            return
        await self.set_focus_close()
        *_, z = self.init_job(precise_focusing=precise_focusing)

        old_crop_mode = self.crop_mode
        self.crop_mode = CropMode.CROP.value

        for idx, p, *_ in self.parse_positions():
            await self.move_position(
                np.append(p, z), index=idx, update_position_plot=False
            )
            image, _ = self.capture_array()
            circles = get_circles(
                image=image,
                color_space="hsv",
                channel="s",
                min_threshold=100,
                max_threshold=255,
            )
            if len(circles["accepted"]) == 1:
                self._good_discs.append(idx)
            else:
                self._bad_discs.append(idx)
            await self.send_position_plot(index=idx + 1)
            await asyncio.sleep(1)

        self.crop_mode = old_crop_mode

    async def visualize_noise(
        self, image_count: int = 10, kernel_size: int = 7, focus_method: str = FM_BREN
    ) -> np.ndarray:
        images = [await self.capture_image() for _ in tqdm(range(image_count))]
        if kernel_size > 1:
            images = [cv2.medianBlur(image, ksize=kernel_size) for image in images]
        focus_data = np.array(
            [
                compute_focus_metric(
                    cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), focus_method
                )
                for image in tqdm(images)
            ]
        )
        img_min, img_max = (images[focus_data.argmin()], images[focus_data.argmax()])
        return [img_min, img_max, np.abs(img_max - img_min)]

    async def test_cycle(
        self,
        cycles=[LightsCycle.ONE_FOURTH],
        merge_mode=ImageMergeMode.MIN,
        center_on_leaf: bool = True,
    ):
        result = []
        for i, cycle in enumerate(cycles):
            await self.set_lights(cycle.value[0], wait=1)
            images, *_ = await self.acquire_leaf_disc(
                light_cycle=cycle, center_on_leaf=center_on_leaf and i == 0
            )
            if i == 0:
                height, width, _ = images[0].shape
                circles = get_circles(
                    images[0], color_space="hsv", channel="s", resize_factor=8
                )
                if len(circles["accepted"]) == 1:
                    _, cx, cy, r = circles["accepted"][0]
                    crop_data = Rectangle.from_circle((cx, cy, r + 16))
                else:
                    crop_data = Rectangle(left=0, top=0, right=width, bottom=height)
            if len(cycle.value) > 1:
                result.append(
                    merge_images(
                        image_list=[crop_image(image, crop_data) for image in images],
                        merge_mode=merge_mode,
                    )
                )
            else:
                result.append(crop_image(images[0], crop_data))
        return result
