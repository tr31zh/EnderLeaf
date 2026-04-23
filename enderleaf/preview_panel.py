from pathlib import Path
import io
from threading import Thread, Condition, Event
from functools import wraps
from datetime import datetime as dt
from enum import Enum
from typing import Literal
import time
import logging

import numpy as np
import cv2

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

from libcamera import controls

import param
import panel as pn

from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.enderlights_pi import Enderlights
from enderscope.bed import bed
from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize, crop_image, Rectangle, lap_var
from enderleaf.qr_reader import get_qr_data, get_points_extremes

pn.extension("ace", "jsoneditor")


class StillFolders(Enum):
    RAW = Path(".").joinpath("output", "raw")
    CROPPED = Path(".").joinpath("output", "cropped")


class SideBarCards(Enum):
    PREVIEW = "Preview"
    CROP_DATA = "Crop"
    FOCUS = "Focus options"
    INIT = "Initialize"
    MOVE = "Move"


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


def printer_busy(method):
    @wraps(method)
    def _impl(self, *method_args, **method_kwargs):
        self.lock_printer()
        try:
            return method(self, *method_args, **method_kwargs)
        finally:
            self.unlock_printer()

    return _impl


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
                    top=round(preview.crop_top / raw_height * main_height) & ~1,
                    bottom=main_height
                    - (round(preview.crop_bottom / raw_height * main_height) & ~1),
                    left=round(preview.crop_left / raw_width * main_width) & ~1,
                    right=main_width
                    - (round(preview.crop_right / raw_width * main_width) & ~1),
                ),
            )

        if preview.stop_event.is_set() is True or preview.output.closed is True:
            break


class PreviewPane(param.Parameterized):
    # Camera
    sensor_modes = param.Selector(default=2)
    act_capture_still = param.Action(
        default=lambda x: x.param.trigger("act_capture_still"), label="Capture still"
    )

    crop_left = param.Integer(0)
    crop_right = param.Integer(0)
    crop_top = param.Integer(0)
    crop_bottom = param.Integer(0)
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
    act_rest = param.Action(default=lambda x: x.param.trigger("act_rest"), label="Rest")
    act_center_on_qr_code = param.Action(
        default=lambda x: x.param.trigger("act_center_on_qr_code"), label="Find QR code"
    )
    act_connect_printer = param.Action(
        default=lambda x: x.param.trigger("act_connect_printer"),
        label="Connect printer",
    )
    act_connect_lights = param.Action(
        default=lambda x: x.param.trigger("act_connect_lights"), label="Connect lights"
    )
    sel_printer = param.Selector(
        objects=[str(p) for p in list_ports()],
        default=str(default_printer_port()),
        label="Select serial connection",
    )

    def __init__(self, **params):
        super().__init__(**params)
        self._working = False
        self._sidebar_width = 300
        # Camera
        self._started = False
        self.stop_event = Event()
        self.output = None
        self.camera = Picamera2()
        self.video_pane = pn.pane.Image(sizing_mode="stretch_width")
        # self.still_pane = pn.pane.Image(sizing_mode="stretch_width")
        self.camera_config = pn.pane.JSON(
            object=None, name="Camera configuration", depth=-1
        )
        self.camera_controls = pn.widgets.JSONEditor(
            value=self.camera.camera_controls,
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

        # Printer
        self._homed = False
        self._stage = None
        self._bt_connect_printer = pn.widgets.Button.from_param(
            self.param.act_connect_printer, sizing_mode="stretch_width"
        )
        self._bt_home = pn.widgets.Button.from_param(
            self.param.act_home, sizing_mode="stretch_width", disabled=True
        )
        self._bt_rest = pn.widgets.Button.from_param(
            self.param.act_rest, sizing_mode="stretch_width", disabled=True
        )
        self._bt_qr_code = pn.widgets.Button.from_param(
            self.param.act_center_on_qr_code, sizing_mode="stretch_width", disabled=True
        )
        self.crd_init = pn.layout.Card(
            objects=[], title=SideBarCards.INIT.value, collapsed=False
        )
        self.crd_move = pn.layout.Card(
            objects=[], title=SideBarCards.MOVE.value, collapsed=False
        )

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
        except Exception as e:
            self.update_debugger({"error": str(e), "crop_data": str(crop_data)})
            return None
        else:
            return image

    def update_preview(self, image, crop_data: Rectangle | None = None):
        self.video_pane.object = to_pil(
            self.apply_image_crop(image=image, crop_data=crop_data)
        )

    def update_debugger(self, data):
        self._counter += 1
        self._debugger.object = data
        self._debug_counter.object = self._counter

    def set_crop(self, left, right, top, bottom):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

    @param.depends(
        "crop_left", "crop_right", "crop_top", "crop_bottom", "crop_mode", watch=True
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
        self.camera.stop()
        self.start()
        # self.still_pane.object = safe_pil_resize(
        #     image=to_pil(self.apply_image_crop(image=image)),
        #     new_width=1024,
        #     new_height=768,
        # )

        return crop_image(
            image=image,
            crop_data=Rectangle(
                left=self.crop_left,
                top=self.crop_top,
                right=-self.crop_right,
                bottom=-self.crop_bottom,
            ),
        )

    @param.depends("act_capture_still", watch=True)
    def on_capture_still(self):
        ensure_folder(StillFolders.RAW.value)
        to_pil(self.capture_array()).save(
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
    def lock_printer(self):
        self._bt_home.disabled = True
        self._bt_rest.disabled = True
        self._bt_qr_code.disabled = True

    def unlock_printer(self):
        self._bt_home.disabled = False
        self._bt_rest.disabled = False
        self._bt_qr_code.disabled = False

    @printer_busy
    def home(self):
        if self._stage.safe_home() is False:
            if self._stage.safe_home() is False:
                raise ConnectionError("Unable to home")

    @printer_busy
    def rest(self):
        self._stage.move_position((bed.x_min, bed.x_max, bed.rest_height))
        self._stage.finish_moves()

    @printer_busy
    def get_focused_z(
        self, start_height=bed.individual_height, min_rel_z=-10, max_rel_z=10, delta_z=1
    ):
        zrange = np.array(range(min_rel_z, max_rel_z, delta_z))
        pos = self._stage.get_position()
        start_height
        mxScore = -1
        bestZ = 0

        for z in zrange:
            self._stage.move_position([pos[0], pos[1], z + start_height])
            self._stage.finish_moves()
            img = preview().capture_array()
            grayImage = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            score = lap_var(grayImage)
            if score > mxScore:
                mxScore = score
                bestZ = z

        self._stage.move_position([pos[0], pos[1], start_height])
        self._stage.finish_moves()

        return bestZ + start_height

    @staticmethod
    def get_qr_pos(image):
        qr_data = get_qr_data(image)
        if qr_data["retval"] is False:
            raise ValueError("Unable to detect QR code")
        min_x, min_y, max_x, max_y = get_points_extremes(points=qr_data["points"][0])
        return (min_x + max_x) // 2, (min_y + max_y) // 2

    @printer_busy
    def center_on_qr_code(self, step_val=10):
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )
        self.set_crop(0, 0, 0, 0)
        self._stage.move_absolute(bed.qr_start_x, bed.qr_start_y, bed.individual_height)
        self.get_focused_z()
        image = self.capture_array()
        cy, cx = image.shape[0] // 2, image.shape[1] // 2
        qr_cx, qr_cy = self.get_qr_pos(image)
        step_x, step_y = -step_val if cx > qr_cx else step_val, (
            step_val if cy > qr_cy else -step_val
        )
        self._stage.move_relative(step_x, step_y)
        self._stage.finish_moves()
        new_qr_cx, new_qr_cy = self.get_qr_pos(self.capture_array())

        self._stage.move_relative(-step_x, -step_y)
        self._stage.move_relative(
            abs(cx - qr_cx) * step_x / abs(new_qr_cx - qr_cx),
            abs(cy - qr_cy) * step_y / abs(new_qr_cy - qr_cy),
        )
        return self._stage.get_position()

    @param.depends("act_connect_printer", watch=True)
    def on_connect_printer(self):
        for port in list_ports():
            if str(port) == self.sel_printer:
                self._stage = Stage(port, 115200)
                self.home()
                break

    @param.depends("act_home", watch=True)
    def on_home(self):
        self.home()

    @param.depends("act_rest", watch=True)
    def on_rest(self):
        self.rest()

    @param.depends("act_center_on_qr_code", watch=True)
    def on_center_on_qr_code(self):
        self.center_on_qr_code()

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
                ]
                return self.crd_preview
            case SideBarCards.CROP_DATA:
                self.crd_crop_data.objects = [
                    pn.widgets.IntInput.from_param(
                        self.param.crop_top,
                        name="Top",
                        align="center",
                        sizing_mode="stretch_width",
                        step=2,
                    ),
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.crop_left,
                            name="Left",
                            sizing_mode="stretch_width",
                            step=2,
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.crop_right,
                            name="Right",
                            sizing_mode="stretch_width",
                            step=2,
                        ),
                    ),
                    pn.widgets.IntInput.from_param(
                        self.param.crop_bottom,
                        name="Bottom",
                        align="center",
                        sizing_mode="stretch_width",
                        step=2,
                    ),
                    pn.widgets.Select.from_param(
                        self.param.crop_mode, sizing_mode="stretch_width"
                    ),
                ]
                return self.crd_crop_data
            case SideBarCards.FOCUS:
                self.crd_focus.objects = [
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
                ]
                return self.crd_focus
            case SideBarCards.INIT:
                self.crd_init.objects = [
                    pn.widgets.Select.from_param(
                        self.param.sel_printer, sizing_mode="stretch_width"
                    ),
                    self._bt_connect_printer,
                ]
                return self.crd_init
            case SideBarCards.MOVE:
                self.crd_move.objects = [
                    pn.Row(self._bt_home, self._bt_rest),
                    self._bt_qr_code,
                ]
                return self.crd_move

    def sidebar(self):
        return pn.Column(
            *[
                self.get_card(c)
                for c in [
                    SideBarCards.PREVIEW,
                    SideBarCards.CROP_DATA,
                    SideBarCards.FOCUS,
                    SideBarCards.INIT,
                    SideBarCards.MOVE,
                ]
            ],
            width=self._sidebar_width,
        )

    def main(self):
        return pn.layout.Tabs(
            ("Video", self.video_pane),
            # ("Still", self.still_pane),
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
