from itertools import product
from pathlib import Path
from datetime import datetime as dt
from threading import Thread, Event
import time
from typing import Literal

from tqdm import tqdm

import numpy as np
import cv2
import pandas as pd

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    from libcamera import controls
except:
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
from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.enderlights_pi import Enderlights, default_leds

from enderleaf.const import (
    CropMode,
    CameraState,
    ELStatus,
    CardPoint,
    LIGHTS_CYCLE,
    LEN_LIGHTS_CYCLE,
    FM_METHODS,
    FM_LAPV,
    FM_BREN,
)
from enderleaf.streaming import StreamingOutput
from enderleaf.focus_metrics import compute_focus_metric
from enderleaf.tools import ensure_folder, format_datetime, write_dataframe, time_method
from enderleaf.image import (
    crop_image,
    Rectangle,
    get_circles,
    get_channel,
    merge_images,
    ImageMergeMode,
)
from enderleaf.qr_reader import get_qr_data, get_points_extremes
from enderleaf.draw import draw_circles

DST_FLD = Path(".").joinpath("output")


def expand_file_path(file_path: Path, use_file_ts: bool = False):
    file_name = file_path.with_suffix("").name.replace("_", "#")
    file_parts = file_name.split("#")
    current_file_ts = pd.to_datetime(
        dt.fromtimestamp(round(file_path.stat().st_ctime))
        if use_file_ts is True
        else file_parts[-1]
    )
    return {
        "exp": [file_parts[0]],
        "inoc": [file_parts[1].replace("I", "")],
        "plate": [file_parts[2].replace("P", "")],
        "row": [file_parts[3]],
        "col": [file_parts[4]],
        "date_time": [current_file_ts],
        "file_name": [file_path.name],
        "date": [current_file_ts.date()],
        "year": [current_file_ts.year],
        "month": [current_file_ts.month],
        "day": [current_file_ts.day],
        "time": [current_file_ts.time()],
        "hour": [current_file_ts.hour],
        "minute": [current_file_ts.minute],
        "second": [current_file_ts.second],
    }


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


class EnderLeafController(object):
    # MARK: Init
    def __init__(self):
        # Camera
        self.stop_event = Event()
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
        self.acq_light_configurations = [LIGHTS_CYCLE[1]]

        self._positions = []
        self._old_crop_values = -1, -1, -1, -1
        self._homed = False
        self._stage = None
        self._good_discs = []
        self._bad_discs = []
        self._ligths_cycle_index = 0
        self._px_to_mm = -1
        self.status = ELStatus.IDLE

        # Callbacks
        self.update_preview = None
        self.update_still = None
        self.update_position_plot = None
        self.update_focus_plot = None
        self.update_positions = None
        self.update_progress = None

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
        } | {
            "lights_conf": {
                "top": self.top_lights.to_json(),
                # "side": self.side_lights.to_json(),
            }
        }

    def from_json(self, data: dict) -> None:
        for k, v in data.items():
            if k != "lights_conf":
                try:
                    setattr(self, k, v)
                except:
                    pass
        self.top_lights.from_json(data["lights_conf"]["top"])
        # self.side_lights.from_json(data["lights_conf"]["side"])

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

    def call_update_preview(self):
        while True:
            if self.stop_event.is_set() is True or self.output.closed is True:
                break
            if self.update_preview is None:
                continue
            with self.output.condition:
                self.output.condition.wait()
                # print("exited", flush=True)
                image = cv2.cvtColor(
                    cv2.imdecode(
                        np.frombuffer(self.output.frame, np.uint8), cv2.IMREAD_COLOR
                    ),
                    cv2.COLOR_BGR2RGB,
                )
                cam_conf = self.camera.camera_config
                main_width, main_height = (
                    cam_conf["main"]["size"][0],
                    cam_conf["main"]["size"][1],
                )
                raw_width, raw_height = (
                    cam_conf["raw"]["size"][0],
                    cam_conf["raw"]["size"][1],
                )
                self.update_preview(
                    image=self.apply_image_crop(
                        image=image,
                        crop_data=Rectangle(
                            top=round(self.crop_top / raw_height * main_height) & ~1,
                            bottom=main_height
                            - (round(self.crop_bottom / raw_height * main_height) & ~1),
                            left=round(self.crop_left / raw_width * main_width) & ~1,
                            right=main_width
                            - (round(self.crop_right / raw_width * main_width) & ~1),
                        ),
                    )
                )

    def dummy_call_update_preview(self):
        while True:
            if self.stop_event.is_set() is True or self.output.closed is True:
                break
            if self.update_preview is None:
                continue
            data = self.camera.read()
            if not data:
                break  # EOF reached
            # Decode JPEG bytes back to image
            image = cv2.cvtColor(
                cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR),
                cv2.COLOR_BGR2RGB,
            )
            main_width, main_height = self.camera.get_image_size("main")
            raw_width, raw_height = self.camera.get_image_size("raw")
            self.update_preview(
                image=self.apply_image_crop(
                    image=image,
                    crop_data=Rectangle(
                        top=round(self.crop_top / raw_height * main_height) & ~1,
                        bottom=main_height
                        - (round(self.crop_bottom / raw_height * main_height) & ~1),
                        left=round(self.crop_left / raw_width * main_width) & ~1,
                        right=main_width
                        - (round(self.crop_right / raw_width * main_width) & ~1),
                    ),
                )
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

    def set_crop(self, left, right, top, bottom, crop_mode: CropMode | None = None):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        if crop_mode is not None:
            self.crop_mode = crop_mode

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

    def set_exposure(self):
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
        self.set_controls(cam_controls)

    def set_top_lights(self, state: bool, card_points: list | None = None):
        if state is False:
            self.top_lights.shutter(False)
        elif card_points is None:
            self.top_lights.shutter(state)
        else:
            self.top_lights.shutter(False)
            self.top_lights.set_cardinals(card_points=card_points)

    def set_lights(self, lights: list, wait=1):
        self.top_lights.set_cardinals(lights)
        self.set_exposure()
        time.sleep(wait)

    def shutter(self, state: bool, wait=1):
        self.top_lights.shutter(state=state)
        self.set_exposure()
        time.sleep(wait)

    def cycle_lights(self, wait=1):
        if self._ligths_cycle_index >= LEN_LIGHTS_CYCLE - 1:
            self._ligths_cycle_index = 0
        else:
            self._ligths_cycle_index += 1
        self.set_lights(LIGHTS_CYCLE[self._ligths_cycle_index], wait=wait)

    def set_top_lights_brightness(self, brightness):
        self.top_lights.brightness = brightness

    def set_sensor_mode(self, sensor_mode):
        self.camera.stop_recording()
        self.camera.configure(self.camera.create_video_configuration(raw=sensor_mode))
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))

    def set_focus_mode(self, focus_mode):
        self.camera.set_controls({"AfMode": focus_mode})

    def set_focus_distance(self, focus_distance):
        self.camera.set_controls({"LensPosition": focus_distance})

    def autofocus_cycle(self):
        self.camera.autofocus_cycle()
        if simulate_camera is True:
            pass
        else:
            self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})

    def set_focus_close(self):
        fc_pos = self.camera.camera_controls["LensPosition"][1]
        if self.camera.capture_metadata()["LensPosition"] != fc_pos:
            self.camera.set_controls({"LensPosition": fc_pos})

    def set_focus_far(self):
        ff_pos = self.camera.camera_controls["LensPosition"][0]
        if self.camera.capture_metadata()["LensPosition"] != ff_pos:
            self.camera.set_controls({"LensPosition": ff_pos})

    def capture_array(self):
        match self._camera_state:
            case CameraState.VIDEO:
                self.switch_state(CameraState.STILL)
                image = self.camera.capture_array("main")
                metadata = self.camera.capture_metadata()
                self.switch_state(CameraState.VIDEO)
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

        if self.update_still is not None:
            self.update_still(self.apply_image_crop(image=image))

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

    def switch_state(
        self, new_mode: Literal[CameraState.IDLE, CameraState.VIDEO, CameraState.STILL]
    ):
        if (
            new_mode == self._camera_state
            or self._camera_state == CameraState.SIMULATION
        ):
            return
        match new_mode:
            case CameraState.IDLE:
                self.stop()
            case CameraState.VIDEO:
                if self._camera_state == CameraState.STILL:
                    self.camera.stop()
                self.start()
            case CameraState.STILL:
                self.stop()
                self.camera.switch_mode(self._still_conf)
            case CameraState.SIMULATION:
                pass
            case _:
                raise NotImplementedError(f"Unknown case {new_mode}")
        self._camera_state = new_mode

    def start(self):
        if self._camera_state != CameraState.VIDEO:
            self.stop_event.clear()
            self.camera.configure(self._video_conf)
            self.output = StreamingOutput()
            if simulate_camera is True:
                pass
            else:
                self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
            self.camera.start_recording(JpegEncoder(), FileOutput(self.output))
            if simulate_camera is True:
                self.thread = Thread(target=self.dummy_call_update_preview)
            else:
                self.thread = Thread(target=self.call_update_preview)
            self.thread.start()
            self.set_focus_close()
            self.shutter(True)
            time.sleep(0.5)
            if self._camera_state != CameraState.SIMULATION:
                self._camera_state = CameraState.VIDEO

    def stop(self):
        if self._camera_state not in [CameraState.IDLE, CameraState.SIMULATION]:
            self.stop_event.set()
            self.thread.join()
            self.camera.stop_recording()
            self.output.close()
            self.camera.stop()
            self._camera_state = CameraState.IDLE

    def check_stage(self):
        return self._stage is not None

    def check_homed(self):
        return self._homed

    def printer_ready(self):
        return self.check_stage() and self.check_homed()

    def get_position(self):
        if self.printer_ready() is False:
            return
        return self._stage.get_position()

    def finish_moves(self):
        if self.printer_ready() is False:
            return
        self._stage.finish_moves()

    def call_update_position_plot(self, index: int | list | None = None):
        if self.update_position_plot is None:
            pass
        elif len(self._positions) == 0:
            self.update_position_plot(plot_path_status(z=self.get_position()[2]))
        elif len(self._good_discs) > 0 or len(self._bad_discs) > 0:
            self.update_position_plot(
                plot_discs_status(
                    path=self._positions,
                    good_discs=self._good_discs,
                    bad_discs=self._bad_discs,
                    highlighted_indexes=index,
                    title="",
                    z=self.get_position()[2],
                )
            )
        else:
            self.update_position_plot(
                plot_path_status(
                    path=self._positions,
                    circle_diam=17,
                    highlighted_indexes=index,
                    title="",
                    z=self.get_position()[2],
                )
            )

    def move_position(
        self, position, index: int | None = None, update_position_plot: bool = True
    ):
        if self.printer_ready() is False:
            return
        self._stage.move_position(position)
        if update_position_plot is True:
            self.call_update_position_plot(index=index)
        self.finish_moves()

    def move_absolute(self, x, y, z):
        if self.printer_ready() is False:
            return
        self._stage.move_absolute(x, y, z)
        self.finish_moves()

    def move_relative(self, x, y, z: int | None = None):
        if self.printer_ready() is False:
            return
        self._stage.move_relative(x, y, z)
        self.finish_moves()

    def move_to(self, position: int):
        if len(self._positions) == 0:
            return
        position -= 1
        x, y = self._positions[position]
        self.move_position((x, y), index=[position])
        self.capture_array()

    def go_home(self):
        if self.check_stage() is False:
            return
        if self._stage.safe_home() is False:
            if self._stage.safe_home() is False:
                raise ConnectionError("Unable to home")
        self._homed = True
        self.finish_moves()

    def connect_printer(self, port_name):
        for port in list_ports():
            if str(port) == port_name:
                self._stage = Stage(port, 115200)
                self.go_home()
                break
        x, y, _ = self.get_position()
        self.move_absolute(x, y, self.focus_start_z)

    def go_rest(self):
        if self.printer_ready() is False:
            return
        self.move_position((bed.x_min, bed.y_max, bed.rest_height))

    def go_park(self):
        if self.printer_ready() is False:
            return
        self.move_position((bed.x_min, bed.y_max // 2, bed.rest_height))

    def get_qr_data(self, image: np.ndarray | None = None):
        if image is None:
            image = self.capture_array()[0]
        qr_data = get_qr_data(image)
        if (
            qr_data["retval"] is False
            or len(qr_data["info"]) == 0
            or len(qr_data["points"]) == 0
        ):
            old_brightness = self.top_lights.brightness
            self.top_lights.brightness = 0
            time.sleep(2)
            qr_data = get_qr_data(self.capture_array()[0])
            self.top_lights.brightness = old_brightness
            time.sleep(2)
        return qr_data

    def get_qr_pos(self, image):
        qr_data = self.get_qr_data(image)
        if qr_data["retval"] is False:
            raise ValueError("Unable to detect QR code")
        min_x, min_y, max_x, max_y = get_points_extremes(points=qr_data["points"][0])
        return (min_x + max_x) // 2, (min_y + max_y) // 2, min_x, min_y, max_x, max_y

    def build_snake(self, x, y) -> np.ndarray:
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
        self.call_update_position_plot(index=[0])

    def get_focused_z(self, delta_z=1, switch_state: bool = False) -> float:
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        z_range = np.array(range(-self.focus_delta_z, self.focus_delta_z, delta_z))
        pos = self.get_position()
        mxScore = -1
        bestZ = 0
        variances = {"z": []} | {fm: [] for fm in self.focus_methods}

        for i, z in enumerate(z_range):
            self.move_position(
                [pos[0], pos[1], z + self.focus_start_z], update_position_plot=False
            )
            time.sleep(0.2)
            img, _ = self.capture_array()
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
            if self.update_progress is not None:
                self.update_progress(i, len(z_range))
        df_scores = pd.DataFrame(data=variances)
        for c in self.focus_methods:
            df_scores[c] = (df_scores[c] - df_scores[c].min()) / (
                df_scores[c].max() - df_scores[c].min()
            )
        df_scores["combined"] = df_scores[self.focus_methods].sum(axis=1) / len(
            self.focus_methods
        )

        self.move_position([pos[0], pos[1], bestZ], update_position_plot=False)

        # if self.update_focus_plot is not None:
        #     self.update_focus_plot(df_scores)

        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

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
    def center_on_target(
        self, target_x: float, target_y: float, current_x: float, current_y: float
    ):
        self.move_relative(
            -(target_x - current_x) * self._px_to_mm,
            (target_y - current_y) * self._px_to_mm,
            0,
        )

    def center_on_qr_code(
        self, step_val=10, switch_state: bool = False, precise_focusing: bool = True
    ):
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        self.set_focus_close()
        self.shutter(True)
        self.backup_crop_values()
        self._good_discs = []
        self._bad_discs = []
        try:
            self.set_crop(0, 0, 0, 0)
            self.move_absolute(bed.qr_start_x, bed.qr_start_y, self.focus_start_z)
            image, _ = self.capture_array()
            cy, cx = image.shape[0] // 2, image.shape[1] // 2
            try:
                qr_cx, qr_cy, *_ = self.get_qr_pos(image)
            except:
                self.get_focused_z(switch_state=False)
                qr_cx, qr_cy, *_ = self.get_qr_pos(image)
            step_x, step_y = -step_val if cx > qr_cx else step_val, (
                step_val if cy > qr_cy else -step_val
            )
            self.move_relative(step_x, step_y)
            new_qr_cx, new_qr_cy, *_ = self.get_qr_pos(self.capture_array()[0])

            self._px_to_mm = 1 / (
                (abs(qr_cx - new_qr_cx) + abs(qr_cy - new_qr_cy)) / 2 / 10
            )

            self.center_on_target(
                target_x=cx, target_y=cy, current_x=new_qr_cx, current_y=new_qr_cy
            )
            x, y, z = self.get_position()
            if precise_focusing is True:
                self.set_crop(0, 0, 0, 0)
                image, _ = self.capture_array()
                cx, cy, min_x, min_y, max_x, max_y = self.get_qr_pos(image)
                self.set_crop(
                    top=min_y,
                    bottom=image.shape[0] - max_y,
                    left=min_x,
                    right=image.shape[1] - max_x,
                )
                z, _ = self.get_focused_z(switch_state=False)
            self.build_snake(x, y)
            return x, y, z
        finally:
            self.restore_crop_values()
            if switch_state is True:
                self.switch_state(CameraState.VIDEO)

    def check_corners(self, switch_state: bool = False):
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        x, y, z = self.center_on_qr_code(switch_state=False)
        self.build_snake(x, y)
        for position in get_extremes(self._positions):
            self.move_position((position.x, position.y, z), index=[position.name])
            self.capture_array()
            time.sleep(1)
        self.move_position((x, y, z), index=[0])
        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

    # MARK: Init Job
    def init_job(self, precise_focusing: bool = True, switch_state: bool = False):
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        self.shutter(True)
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        x, y, z = self.center_on_qr_code(
            switch_state=False, precise_focusing=precise_focusing
        )
        exp_name = self.get_qr_data(self.capture_array()[0])["info"][0].replace(
            "_", "#"
        )
        try:
            exp, inoc, plate = exp_name.split("#")
        except:
            exp_name = "ExpXXDMXX#IX#PXX"
            exp, inoc, plate = exp_name.split("#")
        return exp_name, exp, inoc, plate, z

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
    def acquire_leaf_disc(
        self,
        switch_state: bool = False,
        center_on_leaf: bool = False,
        exp_name: str | None = None,
        r: str | None = None,
        c: int | None = None,
        start_ts: str | None = None,
        dst_folder: Path | None = None,
        z: float | None = None,
    ):
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        cycle_id = (
            int(format_datetime()) if len(self.acq_light_configurations) > 1 else None
        )
        file_paths = []
        images = []
        metadatas = []
        df = pd.DataFrame()
        if center_on_leaf is True:
            image, _ = self.capture_array()
            cy, cx = image.shape[0] // 2, image.shape[1] // 2
            circles = get_circles(image, channel="s", resize_factor=8)
            if len(circles["accepted"]) == 1:
                _, ccx, ccy, _r = circles["accepted"][0]
                self.center_on_target(cx, cy, ccx, ccy)
        for light_conf in self.acq_light_configurations:
            if len(self.acq_light_configurations) > 1:
                self.set_lights(light_conf, wait=0.2)
            else:
                time.sleep(0.2)
            image, metadata = self.capture_array()
            images.append(image)
            if exp_name is None:
                metadatas.append(metadata)
                continue
            file_path = dst_folder.joinpath(
                f"{exp_name}#{r}#{c}#{format_datetime()}"
            ).with_suffix(".png")
            file_paths.append(file_path)
            metadata = (
                expand_file_path(file_path)
                | {
                    "job_ts": [start_ts],
                    "cycle_id": [cycle_id],
                    "north": [CardPoint.NORTH in light_conf],
                    "east": [CardPoint.EAST in light_conf],
                    "south": [CardPoint.SOUTH in light_conf],
                    "west": [CardPoint.WEST in light_conf],
                    "center_on_leaf": [center_on_leaf],
                    "height": [z],
                    "crop_top": [self.crop_top],
                    "crop_bottom": [self.crop_bottom],
                    "crop_left": [self.crop_left],
                    "crop_right": [self.crop_right],
                    "brightness": [self.top_lights.brightness],
                }
                | extract_metadata(metadata=metadata)
            )
            metadatas.append(metadata)
            df = pd.concat([df, pd.DataFrame(metadata)])
        if switch_state is True:
            self.switch_state(CameraState.VIDEO)
        return images, metadatas, file_paths, df

    # MARK: Launch
    def launch_acquisition(
        self,
        precise_focusing: bool = True,
        switch_state: bool = False,
        center_on_leaf: bool = False,
    ):
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        self.status = ELStatus.JOB_IN_PROGRESS
        exp_name, exp, inoc, plate, z = self.init_job(
            precise_focusing=precise_focusing, switch_state=switch_state
        )
        df = pd.DataFrame()

        fld_images = DST_FLD.joinpath("images", exp, inoc)
        fld_data = DST_FLD.joinpath("job_data", exp, inoc)
        ensure_folder(fld_images)
        ensure_folder(fld_data)
        start_ts = format_datetime(dt.now())
        data_file_name = fld_data.joinpath(
            exp + "#I" + str(inoc) + "#P" + str(plate) + "#" + start_ts
        ).with_suffix(".csv")
        self.set_lights(self.acq_light_configurations[0])
        job_list = self.parse_positions()
        len_job_list = len(job_list)
        for idx, p, c, r in job_list:
            self.move_position(np.append(p, z), index=idx)
            images, _, file_paths, df_cycle = self.acquire_leaf_disc(
                exp_name=exp_name,
                r=r,
                c=c,
                start_ts=start_ts,
                dst_folder=fld_images,
                z=z,
                center_on_leaf=center_on_leaf,
            )
            df = pd.concat([df, df_cycle])
            for file_path, image in zip(file_paths, images):
                cv2.imwrite(str(file_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            if self.update_progress is not None:
                self.update_progress(idx, len_job_list)
            if self.status == ELStatus.STOP_REQUESTED:
                break
        write_dataframe(df, data_file_name)
        self.call_update_position_plot()

        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

        self.go_rest()
        self.status = ELStatus.IDLE

    def check_discs_positions(
        self, precise_focusing: bool = False, switch_state: bool = True
    ):
        if self.printer_ready() is False:
            return
        self.set_focus_close()
        *_, z = self.init_job(
            precise_focusing=precise_focusing, switch_state=switch_state
        )

        old_crop_mode = self.crop_mode
        self.crop_mode = CropMode.CROP.value

        for idx, p, *_ in self.parse_positions():
            self.move_position(np.append(p, z), index=idx, update_position_plot=False)
            image, _ = self.capture_array()
            circles = get_circles(
                image=image,
                color_space="hsv",
                channel="s",
                min_threshold=100,
                max_threshold=255,
            )
            if self.update_still is not None:
                self.update_still(draw_circles(image, circles))
            if len(circles["accepted"]) == 1:
                self._good_discs.append(idx)
            else:
                self._bad_discs.append(idx)
            if self.update_position_plot is None:
                continue
            self.update_position_plot(
                plot_discs_status(
                    path=self._positions,
                    good_discs=self._good_discs,
                    bad_discs=self._bad_discs,
                )
            )
            time.sleep(1)

        self.crop_mode = old_crop_mode
        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

    # MARK: Tools
    def visualize_noise(
        self, image_count: int = 10, kernel_size: int = 7, focus_method: str = FM_BREN
    ) -> np.ndarray:
        images = [self.capture_array()[0] for _ in tqdm(range(image_count))]
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

    def test_cycles(
        self,
        cycles: list,
        merge_mode=ImageMergeMode.MIN,
        switch_state: bool = True,
        center_on_leaf: bool = True,
    ):
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        old_cycles = self.acq_light_configurations
        result = []
        for i, cycle in enumerate(cycles):
            self.acq_light_configurations = cycle
            self.set_lights(self.acq_light_configurations[0], wait=1)
            images, *_ = self.acquire_leaf_disc(
                switch_state=False, center_on_leaf=center_on_leaf and i == 0
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
            if len(cycle) > 1:
                result.append(
                    merge_images(
                        image_list=[crop_image(image, crop_data) for image in images],
                        merge_mode=merge_mode,
                    )
                )
            else:
                result.append(crop_image(images[0], crop_data))
        if switch_state is True:
            self.switch_state(CameraState.VIDEO)
        self.acq_light_configurations = old_cycles
        return result
