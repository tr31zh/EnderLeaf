from pathlib import Path
import io
from threading import Thread, Condition
from functools import wraps
from datetime import datetime as dt
from enum import Enum
from typing import Literal
import time

import numpy as np
import cv2

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

from libcamera import Transform, controls

import param
import panel as pn

from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, crop_image, Rectangle


class StillFolders(Enum):
    RAW = Path(".").joinpath("output", "raw")
    CROPPED = Path(".").joinpath("output", "cropped")


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
            if preview._working is True:
                continue
            preview._working = True
            try:
                if (
                    preview.crop_top == 0
                    and preview.crop_bottom == 0
                    and preview.crop_left == 0
                    and preview.crop_right == 0
                ):
                    preview.preview_pane.object = preview.output.frame
                else:
                    image = cv2.imdecode(
                        np.frombuffer(preview.output.frame, np.uint8), cv2.IMREAD_COLOR
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
                    preview.preview_pane.object = to_pil(
                        crop_image(
                            image=image,
                            crop_data=Rectangle(
                                top=round(preview.crop_top / raw_height * main_height)
                                & ~1,
                                bottom=-(
                                    round(
                                        preview.crop_bottom / raw_height * main_height
                                    )
                                    & ~1
                                ),
                                left=round(preview.crop_left / raw_width * main_width)
                                & ~1,
                                right=-(
                                    round(preview.crop_right / raw_width * main_width)
                                    & ~1
                                ),
                            ),
                        )
                    )
            except Exception as e:
                pass
            finally:
                preview._working = False

        if preview.output.closed:
            break


class PreviewPane(param.Parameterized):
    sensor_modes = param.Selector()
    capture_still = param.Action(
        default=lambda x: x.param.trigger("capture_still"), label="Capture still"
    )
    capture_cropped_still = param.Action(
        default=lambda x: x.param.trigger("capture_cropped_still"),
        label="Capture cropped still",
    )

    crop_left = param.Integer(0)
    crop_right = param.Integer(0)
    crop_top = param.Integer(0)
    crop_bottom = param.Integer(0)

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
    do_focus = param.Action(
        default=lambda x: x.param.trigger("do_focus"), label="Focus"
    )
    focus_distance = param.Number()

    def __init__(self, **params):
        super().__init__(**params)
        self._working = False
        self.started = False
        self.output = StreamingOutput()
        self.camera = Picamera2()
        self.preview_pane = pn.pane.Placeholder(
            "Preview",
            sizing_mode="stretch_width",
        )
        self.camera_config = pn.pane.JSON(
            object=None, name="Camera configuration", depth=-1
        )
        self.camera_controls = pn.widgets.JSONEditor(
            value=self.camera.camera_controls,
            name="Camera controls",
            mode="view",
            sizing_mode="stretch_width",
        )
        self.debug = pn.pane.JSON(object={}, name="Debug", depth=-1)
        self.param.sensor_modes.objects = {
            f'{x["format"].format} {x["size"]}': i
            for i, x in enumerate(self.camera.sensor_modes)
        }
        self.param.focus_distance.bounds = self.camera.camera_controls["LensPosition"][
            :2
        ]

    @working
    def set_crop(self, left, right, top, bottom):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

    @param.depends("sensor_modes", watch=True)
    @working
    def on_sensor_mode_changed(self):
        self.camera.stop_recording()
        new_mode = self.camera.sensor_modes[self.sensor_modes]
        self.camera.configure(self.camera.create_video_configuration(raw=new_mode))
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))

    @param.depends("focus_mode", watch=True)
    @working
    def on_focus_mode_changed(self):
        self.camera.set_controls({"AfMode": self.focus_mode})

    @param.depends("focus_distance", watch=True)
    @working
    def on_focus_distance_changed(self):
        self.camera.set_controls({"LensPosition": self.focus_distance})

    @param.depends("do_focus", watch=True)
    @working
    def on_request_focus(self):
        self.camera.autofocus_cycle()

    def do_capture_array(
        self,
        crop_data=Rectangle(0, 0, 0, 0),
        focus_cycle: bool = False,
        sleep_time: float = 0,
    ):
        if self.started is True:
            if focus_cycle is True:
                self.camera.autofocus_cycle()
            image = self.camera.switch_mode_and_capture_array(
                self.camera.create_still_configuration(), "main"
            )
        else:
            self.camera.start(show_preview=False)
            if sleep_time > 0:
                time.sleep(sleep_time)
            if focus_cycle is True:
                self.camera.autofocus_cycle()
            image = self.camera.switch_mode_and_capture_array(
                self.camera.create_still_configuration(), "main"
            )
            self.camera.stop()

        return crop_image(image=image, crop_data=crop_data)

    def do_capture_still(
        self,
        crop_data=Rectangle(0, 0, 0, 0),
        focus_cycle: bool = False,
        sleep_time: float = 0,
    ):
        return to_pil(
            self.do_capture_array(
                crop_data=crop_data, focus_cycle=focus_cycle, sleep_time=sleep_time
            )
        )

    @param.depends("capture_still", watch=True)
    @working
    def on_capture_still(self):
        ensure_folder(StillFolders.RAW.value)
        self.do_capture_still().save(
            StillFolders.RAW.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    @param.depends("capture_cropped_still", watch=True)
    @working
    def on_capture_cropped_still(self):
        ensure_folder(StillFolders.CROPPED.value)
        self.do_capture_still(
            crop_data=Rectangle(
                left=self.crop_left,
                top=self.crop_top,
                right=-self.crop_right,
                bottom=-self.crop_bottom,
            )
        ).save(
            StillFolders.CROPPED.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    def sidebar(self):
        return pn.Column(
            pn.WidgetBox(
                "### Main",
                pn.widgets.Select.from_param(
                    self.param.sensor_modes,
                    name="Sensor mode",
                    sizing_mode="stretch_width",
                ),
                pn.widgets.Button.from_param(
                    self.param.capture_still,
                    name="Capture raw still",
                    sizing_mode="stretch_width",
                ),
                pn.widgets.Button.from_param(
                    self.param.capture_cropped_still,
                    name="Capture cropped still",
                    sizing_mode="stretch_width",
                ),
            ),
            pn.WidgetBox(
                "### Crop data",
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
                align="center",
                sizing_mode="stretch_width",
            ),
            pn.WidgetBox(
                "### Focus options",
                pn.widgets.Select.from_param(
                    self.param.focus_mode,
                    name="Focus mode",
                    sizing_mode="stretch_width",
                ),
                pn.widgets.Button.from_param(
                    self.param.do_focus, name="Focus", sizing_mode="stretch_width"
                ),
                pn.widgets.FloatSlider.from_param(
                    self.param.focus_distance,
                    name="Focus Distance",
                    sizing_mode="stretch_width",
                ),
                align="center",
                sizing_mode="stretch_width",
            ),
            width=200,
        )

    def main(self):
        return pn.layout.Accordion(
            ("Camera configuration", self.camera_config),
            ("Camera controls", self.camera_controls),
            ("Preview", self.preview_pane),
            ("Debug", self.debug),
            active=[2],
        )

    @working
    def start(self):
        self.started = True
        mode = self.camera.sensor_modes[0]
        self.camera.configure(
            self.camera.create_video_configuration(
                raw=mode, main={"preserve_ar": False}
            )
        )
        self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))
        self.thread = Thread(target=update_panel, args=(self,))
        self.thread.start()

    def stop(self):
        self.camera.stop_recording()
        self.started = False

    def show(self):
        return pn.Row(self.sidebar(), self.main())

    def close(self):
        self.stop()
        self.output.close()
        self.thread.join()


_preview = None


def post_callback(request):
    global _preview
    if _preview._working is True:
        return
    _preview._working = True
    try:
        metadata = request.get_metadata()
        _preview.camera_config.object = metadata
    finally:
        _preview._working = False


def preview():
    global _preview
    if _preview is None:
        _preview = PreviewPane()
        _preview.camera.post_callback = post_callback
    return _preview


def get_last_still(kind: Literal[StillFolders.RAW, StillFolders.CROPPED]):
    return cv2.imread(
        str(
            kind.value.joinpath(
                str(
                    sorted(
                        [int(n.with_suffix("").name) for n in kind.value.glob("*.jpg")]
                    )[-1]
                )
            ).with_suffix(".jpg")
        )
    )
