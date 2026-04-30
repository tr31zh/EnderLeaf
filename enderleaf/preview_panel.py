from pathlib import Path
import io
from threading import Thread, Condition, Event
from functools import wraps
from datetime import datetime as dt
from enum import Enum
from typing import Literal
import time
from itertools import product

import numpy as np
import cv2
import pandas as pd

from matplotlib.figure import Figure
from matplotlib.patches import Circle

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

from libcamera import controls

import param
import panel as pn

from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.enderlights_pi import Enderlights
from enderscope.scan_patterns import snake, plot_path, get_extremes, plot_path_status
from enderscope.bed import bed
from enderleaf.tools import (
    ensure_folder,
    format_datetime,
    write_dataframe,
    read_dataframe,
)
from enderleaf.image import to_pil, safe_pil_resize, crop_image, Rectangle, lap_var
from enderleaf.qr_reader import get_qr_data, get_points_extremes

pn.extension("ace", "jsoneditor", "ipywidgets")

DST_FLD = Path(".").joinpath("output")


class StillFolders(Enum):
    RAW = Path(".").joinpath("output", "raw")
    CROPPED = Path(".").joinpath("output", "cropped")


class SideBarCards(Enum):
    PREVIEW = "Preview"
    CROP_DATA = "Crop"
    FOCUS = "Focus options"
    INIT = "Initialize"
    PLATE = "Plate"
    MOVE = "Control"


class CropMode(Enum):
    CROP = "Cropped image"
    LINES = "Crop lines"
    IGNORE = "Ignore"


def working(method):
    @wraps(method)
    def _impl(self, *method_args, **method_kwargs):
        if self._working is True:
            return
        self._working = True
        try:
            return method(self, *method_args, **method_kwargs)
        finally:
            self._working = False

    return _impl


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


def update_panel(preview):
    while True:
        with preview.output.condition:
            preview.output.condition.wait()
            image = cv2.cvtColor(
                cv2.imdecode(
                    np.frombuffer(preview.output.frame, np.uint8), cv2.IMREAD_COLOR
                ),
                cv2.COLOR_BGR2RGB,
            )
            cam_conf = preview.camera.camera_config
            main_width, main_height = (
                cam_conf["main"]["size"][0],
                cam_conf["main"]["size"][1],
            )
            raw_width, raw_height = (
                cam_conf["raw"]["size"][0],
                cam_conf["raw"]["size"][1],
            )
            preview.update_preview(
                image=image,
                crop_data=Rectangle(
                    top=round(preview.cam_crop_top / raw_height * main_height) & ~1,
                    bottom=main_height
                    - (round(preview.cam_crop_bottom / raw_height * main_height) & ~1),
                    left=round(preview.cam_crop_left / raw_width * main_width) & ~1,
                    right=main_width
                    - (round(preview.cam_crop_right / raw_width * main_width) & ~1),
                ),
            )

        if preview.stop_event.is_set() is True or preview.output.closed is True:
            break


class PreviewPane(param.Parameterized):
    # MARK: PARAMS
    # Camera
    sensor_modes = param.Selector(default=2)
    act_capture_still = param.Action(
        default=lambda x: x.param.trigger("act_capture_still"), label="Capture still"
    )
    act_preview_start = param.Action(
        default=lambda x: x.param.trigger("act_preview_start"), label="Start preview"
    )
    act_preview_stop = param.Action(
        default=lambda x: x.param.trigger("act_preview_stop"), label="Stop preview"
    )

    cam_crop_left = param.Integer(0)
    cam_crop_right = param.Integer(0)
    cam_crop_top = param.Integer(0)
    cam_crop_bottom = param.Integer(0)
    crop_mode = param.Selector(
        objects=[c.value for c in [CropMode.CROP, CropMode.LINES, CropMode.IGNORE]],
        default=CropMode.LINES.value,
        label="Crop mode",
    )

    focus_mode = param.Selector(
        default=controls.AfModeEnum.Manual.value,
        objects={
            afm.name: afm.value
            for afm in [
                controls.AfModeEnum.Manual,
                controls.AfModeEnum.Auto,
                controls.AfModeEnum.Continuous,
            ]
        },
    )
    act_focus = param.Action(
        default=lambda x: x.param.trigger("act_focus"), label="Focus"
    )
    act_focus_close = param.Action(
        default=lambda x: x.param.trigger("act_focus_close"), label="Focus close"
    )
    act_focus_far = param.Action(
        default=lambda x: x.param.trigger("act_focus_far"), label="Focus far"
    )
    focus_distance = param.Number()

    # Printer
    act_home = param.Action(default=lambda x: x.param.trigger("act_home"), label="Home")
    act_idle = param.Action(default=lambda x: x.param.trigger("act_idle"), label="Idle")
    act_park = param.Action(default=lambda x: x.param.trigger("act_park"), label="Park")
    act_center_on_qr_code = param.Action(
        default=lambda x: x.param.trigger("act_center_on_qr_code"),
        label="Go to QR code",
    )
    act_check_corners = param.Action(
        default=lambda x: x.param.trigger("act_check_corners"), label="Check Corners"
    )
    act_connect_printer = param.Action(
        default=lambda x: x.param.trigger("act_connect_printer"),
        label="Connect printer",
    )
    act_launch_acquisition = param.Action(
        default=lambda x: x.param.trigger("act_launch_acquisition"),
        label="Capture images",
    )
    act_lights_on = param.Action(
        default=lambda x: x.param.trigger("act_lights_on"),
        label="Lights on",
    )
    act_lights_off = param.Action(
        default=lambda x: x.param.trigger("act_lights_off"),
        label="Lights off",
    )
    act_move_to = param.Action(
        default=lambda x: x.param.trigger("act_move_to"),
        label="Move to...",
    )
    sel_position = param.Selector(
        objects=[],
        default=None,
        label="Postion",
    )
    sel_printer = param.Selector(
        objects=[str(p) for p in list_ports()],
        default=str(default_printer_port()),
        label="Select serial connection",
    )
    exp_crop_left = param.Integer(1200)
    exp_crop_right = param.Integer(1200)
    exp_crop_top = param.Integer(400)
    exp_crop_bottom = param.Integer(350)

    exp_plate_x = param.Integer(200)
    exp_plate_y = param.Integer(200)
    exp_plate_row_count = param.Integer(9)
    exp_plate_col_count = param.Integer(9)

    exp_focus_start_z = param.Integer(36)
    exp_focus_delta_z = param.Integer(10)

    def __init__(self, **params):
        # MARK: INIT
        super().__init__(**params)
        self._working = False
        self._sidebar_width = 300
        # Camera
        self._started = False
        self.stop_event = Event()
        self.output = None
        self.camera = Picamera2()
        self.video_pane = pn.pane.Image(sizing_mode="stretch_width")
        self.still_pane = pn.pane.Image(sizing_mode="stretch_width")
        self.camera_config = pn.pane.JSON(
            object=None, name="Camera configuration", depth=-1
        )
        self.camera_controls = pn.widgets.JSONEditor(
            value=dict(sorted(self.camera.camera_controls.items())),
            name="Camera controls",
            mode="view",
            sizing_mode="stretch_width",
        )
        self.param.sensor_modes.objects = {
            f'{x["format"].format} {x["size"]}': i
            for i, x in enumerate(self.camera.sensor_modes)
        }
        self.param.focus_distance.bounds = self.camera.camera_controls["LensPosition"][
            :2
        ]
        self.crd_preview = pn.layout.Card(
            objects=[], title=SideBarCards.PREVIEW.value, collapsed=True
        )
        self.crd_crop_data = pn.layout.Card(
            objects=[], title=SideBarCards.CROP_DATA.value, collapsed=True
        )
        self.crd_focus = pn.layout.Card(
            objects=[], title=SideBarCards.FOCUS.value, collapsed=True
        )
        self.crd_plate = pn.layout.Card(
            objects=[], title=SideBarCards.PLATE.value, collapsed=True
        )

        # Printer
        self._homed = False
        self._stage = None
        self.lights = Enderlights()
        self.lights.shutter(False)
        self._bt_connect_printer = pn.widgets.Button.from_param(
            self.param.act_connect_printer,
            sizing_mode="stretch_width",
            icon="plug-connected",
            icon_size="2em",
        )
        self._bt_home = pn.widgets.Button.from_param(
            self.param.act_home,
            sizing_mode="stretch_width",
            icon="home",
            icon_size="2em",
        )
        self._bt_idle = pn.widgets.Button.from_param(
            self.param.act_idle,
            sizing_mode="stretch_width",
            icon="clock-pause",
            icon_size="2em",
        )
        self._bt_park = pn.widgets.Button.from_param(
            self.param.act_park,
            sizing_mode="stretch_width",
            icon="parking-circle",
            icon_size="2em",
        )
        self._bt_qr_code = pn.widgets.Button.from_param(
            self.param.act_center_on_qr_code,
            icon="qrcode",
            icon_size="2em",
            sizing_mode="stretch_width",
        )
        self.bt_check_corners = pn.widgets.Button.from_param(
            self.param.act_check_corners,
            icon="border-corners",
            icon_size="2em",
            sizing_mode="stretch_width",
        )
        self.bt_launch_acquisition = pn.widgets.Button.from_param(
            self.param.act_launch_acquisition,
            icon="player-play",
            icon_size="2em",
            sizing_mode="stretch_width",
        )
        self.bt_lights_on = pn.widgets.Button.from_param(
            self.param.act_lights_on,
            icon="bulb",
            icon_size="2em",
            sizing_mode="stretch_width",
        )
        self.bt_lights_off = pn.widgets.Button.from_param(
            self.param.act_lights_off,
            icon="bulb-off",
            icon_size="2em",
            sizing_mode="stretch_width",
        )
        self._is_lights_on = False
        self.crd_init = pn.layout.Card(
            objects=[], title=SideBarCards.INIT.value, collapsed=False
        )
        self.crd_move = pn.layout.Card(
            objects=[], title=SideBarCards.MOVE.value, collapsed=False
        )
        self.plot_focus = pn.pane.Matplotlib(
            sizing_mode="stretch_width", height=300, align="start"
        )
        self.plot_position = pn.pane.Matplotlib(
            object=plot_path_status(),
            sizing_mode="stretch_width",
            height=300,
            align="start",
        )
        self.plot_z = pn.indicators.LinearGauge(
            name="Z position",
            value=0,
            bounds=(bed.z_min, bed.z_max),
            width=60,
            sizing_mode="stretch_height",
            format="",
            align="start",
        )
        self._positions = []

        # Misc
        self._counter = 0
        self._debugger = pn.pane.Placeholder("Debugger", sizing_mode="stretch_width")
        self._debug_counter = pn.pane.Placeholder(
            "Debug counter", sizing_mode="stretch_width"
        )

    # MARK: Preview
    def apply_image_crop(self, image, crop_data: Rectangle | None = None):
        try:
            crop_data = (
                Rectangle(
                    top=self.cam_crop_top,
                    bottom=image.shape[0] - self.cam_crop_bottom,
                    left=self.cam_crop_left,
                    right=image.shape[1] - self.cam_crop_right,
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
        except Exception as e:
            self.update_debugger({"error": str(e), "crop_data": str(crop_data)})
            return None
        else:
            return image

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

    def update_preview(self, image, crop_data: Rectangle | None = None):
        self.video_pane.object = to_pil(
            self.apply_image_crop(image=image, crop_data=crop_data)
        )

    def update_debugger(self, data):
        self._counter += 1
        self._debugger.object = data
        self._debug_counter.object = self._counter

    def set_crop(self, left, right, top, bottom):
        self.cam_crop_left = left
        self.cam_crop_right = right
        self.cam_crop_top = top
        self.cam_crop_bottom = bottom

    @param.depends(
        "cam_crop_left",
        "cam_crop_right",
        "cam_crop_top",
        "cam_crop_bottom",
        "crop_mode",
        watch=True,
    )
    def on_crop_changed(self):
        pass

    @param.depends("sensor_modes", watch=True)
    def on_sensor_mode_changed(self):
        self.camera.stop_recording()
        new_mode = self.camera.sensor_modes[self.sensor_modes]
        self.camera.configure(self.camera.create_video_configuration(raw=new_mode))
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))

    @working
    @param.depends("focus_mode", watch=True)
    def on_focus_mode_changed(self):
        self.camera.set_controls({"AfMode": self.focus_mode})

    @working
    @param.depends("focus_distance", watch=True)
    def on_focus_distance_changed(self):
        self.camera.set_controls({"LensPosition": self.focus_distance})

    @working
    @param.depends("act_focus", watch=True)
    def on_request_focus(self):
        self.camera.autofocus_cycle()

    @working
    @param.depends("act_focus_close", watch=True)
    def on_request_focus_close(self):
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )

    @working
    @param.depends("act_focus_far", watch=True)
    def on_request_focus_far(self):
        self.camera.set_controls({"LensPosition": 0})

    def capture_array(self):
        self.stop()
        image = self.camera.switch_mode_and_capture_array(
            self.camera.create_still_configuration(), "main"
        )
        metadata = self.camera.capture_metadata()
        self.camera.stop()
        self.start()
        self.still_pane.object = safe_pil_resize(
            image=to_pil(self.apply_image_crop(image=image)),
            new_width=1024,
            new_height=768,
        )

        return (
            crop_image(
                image=image,
                crop_data=Rectangle(
                    left=self.cam_crop_left,
                    top=self.cam_crop_top,
                    right=-self.cam_crop_right,
                    bottom=-self.cam_crop_bottom,
                ),
            ),
            metadata,
        )

    @param.depends("act_preview_start", watch=True)
    def on_cpreview_start(self):
        self.start()

    @param.depends("act_preview_stop", watch=True)
    def on_cpreview_stop(self):
        self.stop()

    @param.depends("act_capture_still", watch=True)
    def on_capture_still(self):
        ensure_folder(StillFolders.RAW.value)
        image, _ = self.capture_array()
        to_pil(image).save(
            StillFolders.RAW.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    def start(self):
        if self._started is False:
            self.stop_event.clear()
            mode = self.camera.sensor_modes[self.sensor_modes]
            self.camera.configure(
                self.camera.create_video_configuration(
                    raw=mode, main={"preserve_ar": False}
                )
            )
            self.output = StreamingOutput()
            self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
            self.camera.start_recording(JpegEncoder(), FileOutput(self.output))
            self.thread = Thread(target=update_panel, args=(self,))
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

    # MARK: Printer
    def check_stage(self):
        return self._stage is not None

    def check_homed(self):
        return self._homed

    def get_position(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        pos = self._stage.get_position()
        self.plot_z.value = pos[2]
        return pos

    def finish_moves(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self._stage.finish_moves()
        self.get_position()

    def update_positions_plot(self, index: int | None = None):
        self.plot_position.object = plot_path_status(
            path=self._positions, circle_diam=17, highlighted_indexes=index
        )

    def move_position(self, position, index: int | None = None):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self._stage.move_position(position)
        if index is not None:
            self.update_positions_plot(index=index)
        self.finish_moves()

    def move_absolute(self, x, y, z):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self._stage.move_absolute(x, y, z)
        self.finish_moves()

    def move_relative(self, x, y, z: int | None = None):
        if self.check_stage() is False or self.check_homed() is False:
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

    def go_rest(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self.move_position((bed.x_min, bed.y_max, bed.rest_height))

    def go_park(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self.move_position((bed.x_min, bed.y_max // 2, bed.rest_height))

    def get_focused_z(self, delta_z=1) -> float:
        if self.check_stage() is False or self.check_homed() is False:
            return
        zrange = np.array(
            range(-self.exp_focus_delta_z, self.exp_focus_delta_z, delta_z)
        )
        pos = self.get_position()
        self.exp_focus_start_z
        mxScore = -1
        bestZ = 0
        variances = {}

        for z in zrange:
            self.move_position([pos[0], pos[1], z + self.exp_focus_start_z])
            img, _ = self.capture_array()
            grayImage = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            score = lap_var(grayImage)
            variances[z + self.exp_focus_start_z] = score
            if score > mxScore:
                mxScore = score
                bestZ = z

        self.move_position([pos[0], pos[1], self.exp_focus_start_z])

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
        self.plot_focus.object = fig

        return bestZ + self.exp_focus_start_z
    
    def get_qr_data(self, image:np.ndarray|None=None):
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

    def get_qr_pos(self,image):
        qr_data = self.get_qr_data(image)
        if qr_data["retval"] is False:
            raise ValueError("Unable to detect QR code")
        min_x, min_y, max_x, max_y = get_points_extremes(points=qr_data["points"][0])
        return (min_x + max_x) // 2, (min_y + max_y) // 2, min_x, min_y, max_x, max_y

    def move_to(self, position: int | None = None):
        if not self.param.sel_position:
            return
        position_index = int(self.sel_position) if position is None else position
        position_index -= 1
        x, y = self._positions[position_index]
        self.move_position((x, y), index=[position_index])

    def build_snake(self, x, y) -> np.ndarray:
        self._positions = snake(
            cols=self.exp_plate_col_count, rows=self.exp_plate_row_count
        ) * [
            # steps
            self.exp_plate_x / self.exp_plate_row_count,
            self.exp_plate_y / self.exp_plate_col_count,
        ] + [
            # origin
            x,
            y,
        ]
        self.param.sel_position.objects = [
            i + 1 for i in list(range(len(self._positions)))
        ]
        self.update_positions_plot()

    def center_on_qr_code(self, step_val=10):
        if self.check_stage() is False or self.check_homed() is False:
            return
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )
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

    def check_corners(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        x, y, z = self.center_on_qr_code()
        self.build_snake(x, y)
        self.set_crop(
            top=self.exp_crop_top,
            bottom=self.exp_crop_bottom,
            left=self.exp_crop_left,
            right=self.exp_crop_right,
        )
        for position in get_extremes(self._positions):
            self.move_position((position.x, position.y, z), index=[position.name])
            self.capture_array()
            time.sleep(1)
        self.set_crop(top=0, bottom=0, left=0, right=0)
        self.move_position((x, y, z), index=[0])

    def launch_acquisition(self):
        if self.check_stage() is False or self.check_homed() is False:
            return
        x, y, z = self.center_on_qr_code()
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
        self.set_crop(
            top=self.exp_crop_top,
            bottom=self.exp_crop_bottom,
            left=self.exp_crop_left,
            right=self.exp_crop_right,
        )
        exp_name = self.get_qr_data(self.capture_array()[0])["info"][0].replace("_", "#")
        try:
            exp, inoc, plate = exp_name.split("#")
        except:
            exp_name = "Exp00DM00#I0#P00"
            exp, inoc, plate = exp_name.split("#")

        column_names = [i + 1 for i in range(self.exp_plate_col_count)]
        row_names = [chr(65 + i) for i in range(self.exp_plate_row_count)]
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

        for idx, (p, (c, r)) in enumerate(
            zip(self._positions, list(product(column_names, row_names)))
        ):
            self.move_position(np.append(p, z), index=idx)
            file_path = fld_images.joinpath(
                f"{exp_name}#{r}#{c}#{format_datetime()}"
            ).with_suffix(".png")
            files.append(file_path)
            image, metadata = self.capture_array()
            cv2.imwrite(str(file_path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            df = pd.concat(
                [
                    df,
                    pd.DataFrame(
                        expand_file_path(file_path)
                        | {"job_ts": start_ts}
                        | extract_metadata(metadata=metadata)
                        | {
                            "lights": [self._is_lights_on],
                            "crop_top": [self.exp_crop_top],
                            "crop_bottom": [self.exp_crop_bottom],
                            "crop_left": [self.exp_crop_left],
                            "crop_right": [self.exp_crop_right],
                        }
                    ),
                ]
            )
        write_dataframe(df, data_file_name)
        self.update_positions_plot()
        self.set_crop(top=0, bottom=0, left=0, right=0)
        self.go_rest()

    @param.depends("act_connect_printer", watch=True)
    def on_connect_printer(self):
        for port in list_ports():
            if str(port) == self.sel_printer:
                self._stage = Stage(port, 115200)
                self.go_home()
                break

    @param.depends("act_home", watch=True)
    def on_home(self):
        self.go_home()

    @param.depends("act_idle", watch=True)
    def on_rest(self):
        self.go_rest()

    @param.depends("act_park", watch=True)
    def on_park(self):
        self.go_park()

    @param.depends("act_center_on_qr_code", watch=True)
    def on_center_on_qr_code(self):
        self.center_on_qr_code()

    @param.depends("act_check_corners", watch=True)
    def on_check_corners(self):
        self.check_corners()

    @param.depends("act_launch_acquisition", watch=True)
    def on_launch_acquisition(self):
        self.launch_acquisition()

    @param.depends("act_lights_on", watch=True)
    def on_lights_on(self):
        self.shutter(True)

    @param.depends("act_lights_off", watch=True)
    def on_lights_off(self):
        self.shutter(False)

    @param.depends("act_move_to", watch=True)
    def on_move_to(self):
        self.move_to()

    # MARK: UI
    def get_card(
        self,
        kind: Literal[
            SideBarCards.PREVIEW,
            SideBarCards.CROP_DATA,
            SideBarCards.FOCUS,
            SideBarCards.INIT,
            SideBarCards.MOVE,
        ],
    ):
        margin = (10, 2, 10, 2)
        match kind:
            case SideBarCards.PREVIEW:
                self.crd_preview.objects = [
                    pn.widgets.Select.from_param(
                        self.param.sensor_modes,
                        name="Sensor mode",
                        sizing_mode="stretch_width",
                    ),
                    pn.widgets.Button.from_param(
                        self.param.act_capture_still, sizing_mode="stretch_width"
                    ),
                    pn.Row(
                        pn.widgets.Button.from_param(
                            self.param.act_preview_start, sizing_mode="stretch_width"
                        ),
                        pn.widgets.Button.from_param(
                            self.param.act_preview_stop, sizing_mode="stretch_width"
                        ),
                    ),
                    pn.layout.WidgetBox(
                        "#### Crop Feedback (Ignored while in experiments)",
                        pn.Row(
                            pn.widgets.IntInput.from_param(
                                self.param.cam_crop_top,
                                name="Top",
                                align="center",
                                sizing_mode="stretch_width",
                                step=2,
                                margin=margin,
                            ),
                            pn.widgets.IntInput.from_param(
                                self.param.cam_crop_left,
                                name="Left",
                                sizing_mode="stretch_width",
                                step=2,
                                margin=margin,
                            ),
                            pn.widgets.IntInput.from_param(
                                self.param.cam_crop_right,
                                name="Right",
                                sizing_mode="stretch_width",
                                step=2,
                                margin=margin,
                            ),
                            pn.widgets.IntInput.from_param(
                                self.param.cam_crop_bottom,
                                name="Bottom",
                                align="center",
                                sizing_mode="stretch_width",
                                step=2,
                                margin=margin,
                            ),
                            margin=(2, 8, 2, 8),
                        ),
                        pn.widgets.Select.from_param(
                            self.param.crop_mode, sizing_mode="stretch_width"
                        ),
                    ),
                    pn.layout.WidgetBox(
                        "#### Focus (Ignored while in experiments)",
                        pn.widgets.Select.from_param(
                            self.param.focus_mode,
                            name="Focus mode",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.Button.from_param(
                            self.param.act_focus, sizing_mode="stretch_width"
                        ),
                        pn.Row(
                            pn.widgets.Button.from_param(
                                self.param.act_focus_far, sizing_mode="stretch_width"
                            ),
                            pn.widgets.Button.from_param(
                                self.param.act_focus_close, sizing_mode="stretch_width"
                            ),
                        ),
                        pn.widgets.FloatSlider.from_param(
                            self.param.focus_distance,
                            name="Lens Position",
                            sizing_mode="stretch_width",
                        ),
                    ),
                ]
                return self.crd_preview
            case SideBarCards.INIT:
                self.crd_init.objects = [
                    pn.widgets.Select.from_param(
                        self.param.sel_printer, sizing_mode="stretch_width"
                    ),
                    self._bt_connect_printer,
                ]
                return self.crd_init
            case SideBarCards.PLATE:
                self.crd_plate.objects = [
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.exp_plate_x,
                            name="Plate X size",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.exp_plate_y,
                            name="Plate Y size",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                    ),
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.exp_plate_row_count,
                            name="Plate ROW count",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.exp_plate_col_count,
                            name="Plate COL count",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                    ),
                ]
                return self.crd_plate
            case SideBarCards.CROP_DATA:
                self.crd_crop_data.objects = [
                    pn.widgets.IntInput.from_param(
                        self.param.exp_crop_top,
                        name="Top",
                        align="center",
                        sizing_mode="stretch_width",
                    ),
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.exp_crop_left,
                            name="Left",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.exp_crop_right,
                            name="Right",
                            sizing_mode="stretch_width",
                        ),
                    ),
                    pn.widgets.IntInput.from_param(
                        self.param.exp_crop_bottom,
                        name="Bottom",
                        align="center",
                        sizing_mode="stretch_width",
                    ),
                ]
                return self.crd_crop_data
            case SideBarCards.FOCUS:
                self.crd_focus.objects = [
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.exp_focus_start_z,
                            name="Focus start Z",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.exp_focus_delta_z,
                            name="Focus ΔZ",
                            sizing_mode="stretch_width",
                        ),
                    )
                ]
                return self.crd_focus
            case SideBarCards.MOVE:
                self.crd_move.objects = [
                    pn.Row(self._bt_home, self._bt_idle, self._bt_park),
                    pn.Row(self._bt_qr_code, self.bt_check_corners),
                    pn.Row(
                        pn.widgets.Button.from_param(
                            self.param.act_move_to,
                            icon="arrow-move-right",
                            icon_size="2em",
                        ),
                        pn.widgets.Select.from_param(
                            self.param.sel_position, sizing_mode="stretch_width"
                        ),
                    ),
                    pn.Row(self.bt_lights_on, self.bt_lights_off),
                    self.bt_launch_acquisition,
                ]
                return self.crd_move

    def sidebar(self):
        return pn.Column(
            *[
                self.get_card(c)
                for c in [
                    SideBarCards.PREVIEW,
                    SideBarCards.INIT,
                    SideBarCards.PLATE,
                    SideBarCards.CROP_DATA,
                    SideBarCards.FOCUS,
                    SideBarCards.MOVE,
                ]
            ],
            width=self._sidebar_width,
        )

    def main(self):
        return pn.layout.Tabs(
            ("Preview", self.video_pane),
            (
                "Experiment",
                pn.Column(
                    pn.Row(self.plot_position, self.plot_z, self.plot_focus),
                    self.still_pane,
                ),
            ),
            (
                "Camera info",
                pn.layout.Accordion(
                    ("Camera configuration", self.camera_config),
                    ("Camera controls", self.camera_controls),
                    active=[0],
                ),
            ),
            ("Debugger", pn.Column(self._debug_counter, self._debugger)),
            active=0,
        )

    def show(self):
        sidebar = self.sidebar()
        sidebar.width = self._sidebar_width
        return pn.Row(sidebar, self.main())


_preview = None


def post_callback(request):
    global _preview
    if _preview._working is True:
        return
    _preview._working = True
    try:
        metadata = request.get_metadata()
        metadata = dict(sorted(metadata.items()))
        _preview.camera_config.object = metadata
        _preview.focus_distance = metadata["LensPosition"]
        _preview.focus_mode = metadata["AfState"]
    finally:
        _preview._working = False


def preview():
    global _preview
    if _preview is None:
        _preview = PreviewPane()
        _preview.camera.post_callback = post_callback
        _preview.start()
    return _preview
