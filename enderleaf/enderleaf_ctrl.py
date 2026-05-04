from itertools import product
from pathlib import Path
from datetime import datetime as dt
from threading import Thread, Condition, Event
from enum import Enum
import time
import io

import numpy as np
import cv2
import pandas as pd

from matplotlib.figure import Figure
from matplotlib.patches import Circle

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
from libcamera import controls

from enderscope.scan_patterns import snake, get_extremes, plot_path_status
from enderscope.bed import bed
from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.enderlights_pi import Enderlights
from enderleaf.tools import ensure_folder, format_datetime, write_dataframe
from enderleaf.image import crop_image, Rectangle, lap_var
from enderleaf.qr_reader import get_qr_data, get_points_extremes

DST_FLD = Path(".").joinpath("output")


class CropMode(Enum):
    CROP = "Cropped image"
    LINES = "Crop lines"
    IGNORE = "Ignore"


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


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class EnderLeafController(object):
    def __init__(self, parent):
        self.parent = parent
        self._last_job_data = []
        # Camera
        self._started = False
        self.stop_event = Event()
        self.output = None
        self.camera = Picamera2()

        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        self.crop_mode = CropMode.LINES
        self._old_crop_values = -1, -1, -1, -1

        self.plate_x = 0
        self.plate_y = 0
        self.plate_row_count = 0
        self.plate_col_count = 0
        self.focus_start_z = 0
        self.focus_delta_z = 0

        self._homed = False
        self._stage = None
        self.lights = Enderlights()
        self.lights.shutter(False)
        self._is_lights_on = False

        self._positions = []

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

    def update_parent_preview(self):
        while True:
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
                self.parent.update_preview(
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

            if self.stop_event.is_set() is True or self.output.closed is True:
                break

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
                case CropMode.IGNORE:
                    pass
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

    def shutter(self, state: bool, value: list | tuple = (255, 255, 255), wait=2):
        self._is_lights_on = state
        self.lights.shutter(state=state, value=value)
        controls = (
            {
                "AeEnable": False,
                "ExposureTime": 7000,
                "AnalogueGain": 1,
                "AwbEnable": False,
                "ColourGains": (2.4, 1.0),
            }
            if state is True
            else {"AeEnable": True, "AwbEnable": True}
        )
        self.camera.set_controls(controls)

    def toggle_lights(self):
        self.shutter(not self._is_lights_on)

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

    def set_focus_close(self):
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )

    def set_focus_far(self):
        self.camera.set_controls({"LensPosition": 0})

    def capture_array(self):
        self.stop()
        image = self.camera.switch_mode_and_capture_array(
            self.camera.create_still_configuration(), "main"
        )
        metadata = self.camera.capture_metadata()
        self.camera.stop()
        self.start()
        self.parent.on_still_captured(self.apply_image_crop(image=image))

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

    def start(self):
        if self._started is False:
            self.stop_event.clear()
            mode = self.camera.sensor_modes[2]
            self.camera.configure(
                self.camera.create_video_configuration(
                    raw=mode, main={"preserve_ar": False}
                )
            )
            self.output = StreamingOutput()
            self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
            self.camera.start_recording(JpegEncoder(), FileOutput(self.output))
            self.thread = Thread(target=self.update_parent_preview)
            self.thread.start()
            time.sleep(0.5)
            self._started = True

    def stop(self):
        if self._started is True:
            self.stop_event.set()
            self.thread.join()
            self.camera.stop_recording()
            self.output.close()
            self.camera.stop()
            self._started = False

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
        self.parent.on_z_moved(self.get_position()[2])

    def update_positions_plot(self, index: int | None = None):
        return plot_path_status(
            path=self._positions, circle_diam=17, highlighted_indexes=index
        )

    def move_position(self, position, index: int | None = None):
        if self.printer_ready() is False:
            return
        self._stage.move_position(position)
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

    def get_focused_z(self, delta_z=1) -> float:
        if self.printer_ready() is False:
            return
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
        self.parent.update_focus_plot(fig)

        return bestZ + self.focus_start_z

    def get_qr_data(self, image: np.ndarray | None = None):
        if image is None:
            image = self.capture_array()[0]
        qr_data = get_qr_data(image)
        if qr_data["retval"] is False:
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

    def move_to(self, position: int | None = None):
        if not self._positions:
            return
        position -= 1
        x, y = self._positions[position]
        self.move_position((x, y), index=[position])

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

    def center_on_qr_code(self, step_val=10):
        if self.printer_ready() is False:
            return
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )
        self.backup_crop_values()
        try:
            self.set_crop(0, 0, 0, 0)
            self.move_absolute(bed.qr_start_x, bed.qr_start_y, bed.individual_height)
            self.get_focused_z()
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

    def check_corners(self):
        if self.printer_ready() is False:
            return
        x, y, z = self.center_on_qr_code()
        self.build_snake(x, y)
        for position in get_extremes(self._positions):
            self.move_position((position.x, position.y, z), index=[position.name])
            self.capture_array()
            time.sleep(1)
        self.move_position((x, y, z), index=[0])

    def launch_acquisition(self):
        if self.printer_ready() is False:
            return
        x, y, z = self.center_on_qr_code()
        self.backup_crop_values()
        try:
            self.set_crop(0,0,0,0)
            image, _ = self.capture_array()
            cx, cy, min_x, min_y, max_x, max_y = self.get_qr_pos(image)
            self.set_crop(
                top=min_y,
                bottom=image.shape[0] - max_y,
                left=min_x,
                right=image.shape[1] - max_x,
            )
            z = self.get_focused_z()
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

        files = []
        self._last_job_data = []
        df = pd.DataFrame()

        fld_images = DST_FLD.joinpath("images", exp, inoc)
        fld_data = DST_FLD.joinpath("job_data", exp, inoc)
        ensure_folder(fld_images)
        ensure_folder(fld_data)
        start_ts = format_datetime(dt.now())
        data_file_name = fld_data.joinpath(
            exp + "#I" + str(inoc) + "#P" + str(plate) + "#" + start_ts
        ).with_suffix(".csv")

        for idx, (p, (c, r)) in enumerate(
            zip(
                self._positions,
                [
                    (
                        # Since we use a snake pattern row index must be updated  when
                        # column is a even number
                        chr(65 + (r if c % 2 == 1 else self.plate_row_count - 1 - r)),
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
        ):
            self.move_position(np.append(p, z), index=idx)
            file_path = fld_images.joinpath(
                f"{exp_name}#{r}#{c}#{format_datetime()}"
            ).with_suffix(".png")
            files.append(file_path)
            image, metadata = self.capture_array()
            metadata = (
                expand_file_path(file_path)
                | {"job_ts": start_ts}
                | extract_metadata(metadata=metadata)
                | {
                    "height": [z],
                    "lights": [self._is_lights_on],
                    "crop_top": [self.crop_top],
                    "crop_bottom": [self.crop_bottom],
                    "crop_left": [self.crop_left],
                    "crop_right": [self.crop_right],
                }
            )
            self._last_job_data.append((image, metadata))
            cv2.imwrite(str(file_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            df = pd.concat([df, pd.DataFrame(metadata)])
        write_dataframe(df, data_file_name)
        self.update_positions_plot()
        self.go_rest()

    @property
    def last_job_data(self):
        return self._last_job_data