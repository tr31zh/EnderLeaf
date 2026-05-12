from itertools import product
from pathlib import Path
from datetime import datetime as dt
from threading import Thread, Condition, Event
from enum import Enum
import time
import io
from typing import Literal

import numpy as np
import cv2
import pandas as pd

from matplotlib.figure import Figure
from matplotlib.patches import Circle

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

from enderleaf.streaming import StreamingOutput
from enderscope.scan_patterns import (
    snake,
    get_extremes,
    plot_path_status,
    plot_discs_status,
)
from enderscope.bed import bed
from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.enderlights_pi import Enderlights, default_leds
from enderleaf.tools import ensure_folder, format_datetime, write_dataframe
from enderleaf.image import crop_image, Rectangle, lap_var, get_circles
from enderleaf.qr_reader import get_qr_data, get_points_extremes
from enderleaf.draw import draw_circles

DST_FLD = Path(".").joinpath("output")


class CropMode(Enum):
    CROP = "Cropped image"
    LINES = "Crop lines"
    IGNORE = "Ignore"


class CameraState(Enum):
    IDLE = "idle"
    VIDEO = "video"
    STILL = "still"
    SIMULATION = "simulation"


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

        self.top_lights = Enderlights(default_leds["groov_led"], default_intensity=255)
        self.top_lights.shutter(False)
        # self.side_lights = Enderlights(default_leds["hobby_led"], default_intensity=255)
        # self.side_lights.shutter(False)

        self._positions = []
        self._last_job_data = []
        self._old_crop_values = -1, -1, -1, -1
        self._homed = False
        self._stage = None
        self._good_discs = []
        self._bad_discs = []

        # Callbacks
        self.update_preview = None
        self.update_still = None
        self.update_z_pos = None
        self.update_position_plot = None
        self.update_focus_plot = None
        self.update_positions = None

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
        self.top_lights.default_intensity = 255
        # self.side_lights.default_intensity = 125

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

    def set_exposure(self):
        pass

    def set_top_lights(self, state: bool, card_points: list | None = None):
        if state is False:
            self.top_lights.shutter(False)
        elif card_points is None:
            self.top_lights.shutter(state)
        else:
            self.top_lights.shutter(False)
            self.top_lights.set_cardinals(card_points=card_points)

    def shutter(self, state: bool, wait=2):
        self.top_lights.shutter(state=state)
        # self.side_lights.shutter(state=state)
        self.camera.set_controls(
            {
                "AeEnable": False,
                "ExposureTime": 3000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.3, 0.9),
            }
            if state is True
            else {"AeEnable": True, "AwbEnable": True}
        )
        time.sleep(wait)

    def set_top_lights_intensity(self, intensity):
        self.top_lights.default_intensity = intensity

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
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )

    def set_focus_far(self):
        self.camera.set_controls({"LensPosition": 0})

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
        if new_mode == self._camera_state:
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
        if self.update_z_pos is not None:
            self.update_z_pos(self.get_position()[2])

    def call_update_position_plot(self, index: int | list | None = None):
        if self.update_position_plot is None:
            pass
        elif len(self._positions) == 0:
            self.update_position_plot(None)
        elif len(self._good_discs) > 0 or len(self._bad_discs) > 0:
            self.update_position_plot(
                plot_discs_status(
                    path=self._positions,
                    good_discs=self._good_discs,
                    bad_discs=self._bad_discs,
                    highlighted_indexes=index,
                )
            )
        else:
            self.update_position_plot(
                plot_path_status(
                    path=self._positions, circle_diam=17, highlighted_indexes=index
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
            self.toggle_lights()
            time.sleep(2)
            qr_data = get_qr_data(self.capture_array()[0])
            self.toggle_lights()
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
        zrange = np.array(range(-self.focus_delta_z, self.focus_delta_z, delta_z))
        pos = self.get_position()
        self.focus_start_z
        mxScore = -1
        bestZ = 0
        variances = {}

        for z in zrange:
            self.move_position([pos[0], pos[1], z + self.focus_start_z])
            img, _ = self.capture_array()
            grayImage = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            score = lap_var(grayImage)
            variances[z + self.focus_start_z] = score
            if score > mxScore:
                mxScore = score
                bestZ = z

        self.move_position([pos[0], pos[1], self.focus_start_z])

        if self.update_focus_plot is not None:
            fig = Figure(figsize=(4, 4))
            ax = fig.subplots(nrows=1, ncols=1)
            fig.suptitle("Variance")
            ax.plot(list(variances.keys()), list(variances.values()))
            ax.add_patch(
                Circle(
                    xy=(pos[2] + bestZ, variances[pos[2] + bestZ]),
                    radius=1,
                    edgecolor="lime",
                    facecolor="lime",
                    linewidth=1,
                )
            )
            self.update_focus_plot(fig)

        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

        return bestZ + self.focus_start_z

    def center_on_qr_code(self, step_val=10, switch_state: bool = False):
        if self.printer_ready() is False:
            return
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        self.backup_crop_values()
        self._good_discs = []
        self._bad_discs = []
        try:
            self.set_crop(0, 0, 0, 0)
            self.move_absolute(bed.qr_start_x, bed.qr_start_y, bed.individual_height)
            self.get_focused_z(switch_state=False)
            image, _ = self.capture_array()
            cy, cx = image.shape[0] // 2, image.shape[1] // 2
            qr_cx, qr_cy, *_ = self.get_qr_pos(image)
            step_x, step_y = -step_val if cx > qr_cx else step_val, (
                step_val if cy > qr_cy else -step_val
            )
            self.move_relative(step_x, step_y)
            new_qr_cx, new_qr_cy, *_ = self.get_qr_pos(self.capture_array()[0])
            self.move_relative(-step_x, -step_y)
            self.move_relative(
                abs(cx - qr_cx) * step_x / abs(new_qr_cx - qr_cx),
                abs(cy - qr_cy) * step_y / abs(new_qr_cy - qr_cy),
            )
            x, y, z = self.get_position()
            self.build_snake(x, y)
            return x, y, z
        finally:
            self.restore_crop_values()
            if switch_state is True:
                self.switch_state(CameraState.VIDEO)

    def check_corners(self, switch_state: bool = False):
        if self.printer_ready() is False:
            return
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

    def init_job(self, precise_focusing: bool = True, switch_state: bool = False):
        if self.printer_ready() is False:
            return
        if switch_state is True:
            self.switch_state(CameraState.STILL)
        x, y, z = self.center_on_qr_code(switch_state=False)
        if precise_focusing is True:
            self.backup_crop_values()
            try:
                self.set_crop(0, 0, 0, 0)
                image, _ = self.capture_array()
                cx, cy, min_x, min_y, max_x, max_y = self.get_qr_pos(image)
                self.set_crop(
                    top=min_y,
                    bottom=image.shape[0] - max_y,
                    left=min_x,
                    right=image.shape[1] - max_x,
                )
                z = self.get_focused_z(switch_state=False)
                self.build_snake(x, y)
            finally:
                self.restore_crop_values()
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

    def launch_acquisition(
        self, precise_focusing: bool = True, switch_state: bool = False
    ):
        self._last_job_data = []
        exp_name, exp, inoc, plate, z = self.init_job(
            precise_focusing=precise_focusing, switch_state=switch_state
        )
        files = []
        df = pd.DataFrame()

        fld_images = DST_FLD.joinpath("images", exp, inoc)
        fld_data = DST_FLD.joinpath("job_data", exp, inoc)
        ensure_folder(fld_images)
        ensure_folder(fld_data)
        start_ts = format_datetime(dt.now())
        data_file_name = fld_data.joinpath(
            exp + "#I" + str(inoc) + "#P" + str(plate) + "#" + start_ts
        ).with_suffix(".csv")

        for idx, p, c, r in self.parse_positions():
            self.move_position(np.append(p, z), index=idx)
            file_path = fld_images.joinpath(
                f"{exp_name}#{r}#{c}#{format_datetime()}"
            ).with_suffix(".png")
            files.append(file_path)
            image, metadata = self.capture_array()
            metadata = (
                expand_file_path(file_path)
                | {"job_ts": [start_ts]}
                | extract_metadata(metadata=metadata)
                | {
                    "height": [z],
                    "crop_top": [self.crop_top],
                    "crop_bottom": [self.crop_bottom],
                    "crop_left": [self.crop_left],
                    "crop_right": [self.crop_right],
                    "top_light_intensity": [self.top_lights.default_intensity],
                }
            )
            self._last_job_data.append((image, metadata))
            cv2.imwrite(str(file_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            df = pd.concat([df, pd.DataFrame(metadata)])
        write_dataframe(df, data_file_name)
        self.call_update_position_plot()

        if switch_state is True:
            self.switch_state(CameraState.VIDEO)

        self.go_rest()

    def check_discs_positions(
        self, precise_focusing: bool = False, switch_state: bool = True
    ):
        if self.printer_ready() is False:
            return
        *_, z = self.init_job(
            precise_focusing=precise_focusing, switch_state=switch_state
        )

        old_crop_mode = self.crop_mode
        self.crop_mode = CropMode.CROP.value

        for idx, p, *_ in self.parse_positions()[:10]:
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

    @property
    def last_job_data(self):
        return self._last_job_data
