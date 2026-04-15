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

from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize, crop_image, Rectangle

pn.extension("ace", "jsoneditor", "terminal", console_output="disable")


class StillFolders(Enum):
    RAW = Path(".").joinpath("output", "raw")
    CROPPED = Path(".").joinpath("output", "cropped")


class Cards(Enum):
    THUMBNAIL = "thumbnail"
    PREVIEW = "preview"
    CROP_DATA = "crop_data"
    FOCUS = "focus"


class CameraStatus(Enum):
    STOPPED = "stopped"
    STILL = "still"
    VIDEO = "video"


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
            image = cv2.cvtColor(
                cv2.imdecode(
                    np.frombuffer(preview.output.frame, np.uint8), cv2.IMREAD_COLOR
                ),
                cv2.COLOR_BGR2RGB,
            )
            if (
                preview.crop_top != 0
                or preview.crop_bottom != 0
                or preview.crop_left != 0
                or preview.crop_right != 0
            ):
                cam_conf = preview.camera.camera_config
                main_width, main_height = (
                    cam_conf["main"]["size"][0],
                    cam_conf["main"]["size"][1],
                )
                raw_width, raw_height = (
                    cam_conf["raw"]["size"][0],
                    cam_conf["raw"]["size"][1],
                )
                image = crop_image(
                    image=image,
                    crop_data=Rectangle(
                        top=round(preview.crop_top / raw_height * main_height) & ~1,
                        bottom=-(
                            round(preview.crop_bottom / raw_height * main_height) & ~1
                        ),
                        left=round(preview.crop_left / raw_width * main_width) & ~1,
                        right=-(
                            round(preview.crop_right / raw_width * main_width) & ~1
                        ),
                    ),
                )
            preview.update_preview(to_pil(image))

        if preview.stop_event.is_set() is True or preview.output.closed is True:
            break


class PreviewPane(param.Parameterized):
    sensor_modes = param.Selector(default=2)
    act_capture_still = param.Action(
        default=lambda x: x.param.trigger("act_capture_still"), label="Capture still"
    )
    act_capture_cropped_still = param.Action(
        default=lambda x: x.param.trigger("act_capture_cropped_still"),
        label="Capture cropped still",
    )

    act_preview_start_video = param.Action(
        default=lambda x: x.param.trigger("act_preview_start_video"),
        label="Start video",
    )
    act_preview_start_still = param.Action(
        default=lambda x: x.param.trigger("act_preview_start_still"),
        label="Start still",
    )
    act_preview_stop = param.Action(
        default=lambda x: x.param.trigger("act_preview_stop"), label="Stop"
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

    def __init__(self, **params):
        super().__init__(**params)
        self._working = False
        self._sidebar_width = 300
        self.status = CameraStatus.STOPPED
        self.stop_event = Event()
        self.output = None
        self.camera = Picamera2()
        self.preview_pane = pn.pane.Placeholder(
            "Preview",
            sizing_mode="stretch_width",
        )
        self.thumbnail_pane = pn.pane.Placeholder(
            "Thumbnai",
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
        self.param.sensor_modes.objects = {
            f'{x["format"].format} {x["size"]}': i
            for i, x in enumerate(self.camera.sensor_modes)
        }
        self.param.focus_distance.bounds = self.camera.camera_controls["LensPosition"][
            :2
        ]
        self.crd_thumbnail = pn.layout.Card(
            objects=[], title="Thumbnail", collapsed=True
        )
        self.crd_preview = pn.layout.Card(objects=[], title="Preview")
        self.crd_crop_data = pn.layout.Card(objects=[], title="Crop data")
        self.crd_focus = pn.layout.Card(objects=[], title="Focus options")

    def update_preview(self, new_object):
        self.preview_pane.object = new_object
        if self.crd_thumbnail.collapsed is False:
            self.thumbnail_pane.object = safe_pil_resize(
                image=new_object,
                new_height=self._sidebar_width,
                new_width=self._sidebar_width,
            )

    def set_crop(self, left, right, top, bottom):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        # if self.status == CameraStatus.STILL:
        #     self.do_capture_array(apply_crop=True)

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
        # if self.status == CameraStatus.STILL:
        #     self.do_capture_array(apply_crop=True)

    @working
    @param.depends("act_focus", watch=True)
    def on_request_focus(self):
        self.camera.autofocus_cycle()
        # if self.status == CameraStatus.STILL:
        #     self.do_capture_array(apply_crop=True)

    @working
    @param.depends("act_focus_close", watch=True)
    def on_request_focus_close(self):
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )
        # if self.status == CameraStatus.STILL:
        #     self.do_capture_array(apply_crop=True)

    @working
    @param.depends("act_focus_far", watch=True)
    def on_request_focus_far(self):
        self.camera.set_controls({"LensPosition": 0})
        # if self.status == CameraStatus.STILL:
        #     self.do_capture_array(apply_crop=True)

    @param.depends("act_preview_start_video", watch=True)
    def on_preview_start_video(self):
        self.start_video()

    @param.depends("act_preview_start_still", watch=True)
    def on_preview_start_still(self):
        self.start_still()

    @param.depends("act_preview_stop", watch=True)
    def on_preview_stop(self):
        self.stop()

    def do_capture_array(
        self, apply_crop: bool = False, crop_data=Rectangle(0, 0, 0, 0)
    ):
        match self.status:
            case CameraStatus.STOPPED:
                print("Camera stopped, please start", flush=True)
                return None
            case CameraStatus.STILL:
                image = self.camera.switch_mode_and_capture_array(
                    self.camera.create_still_configuration(), "main"
                )
            case CameraStatus.VIDEO:
                self.stop()
                image = self.camera.switch_mode_and_capture_array(
                    self.camera.create_still_configuration(), "main"
                )
                self.camera.stop()
                self.start_video()
        if apply_crop is True:
            ret = crop_image(
                image=image,
                crop_data=Rectangle(
                    left=self.crop_left,
                    top=self.crop_top,
                    right=-self.crop_right,
                    bottom=-self.crop_bottom,
                ),
            )
        else:
            ret = crop_image(image=image, crop_data=crop_data)
        if self.status == CameraStatus.STILL:
            self.update_preview(
                safe_pil_resize(to_pil(ret), new_height=600, new_width=600)
            )
        return ret

    def do_capture_still(self, crop_data=Rectangle(0, 0, 0, 0)):
        return to_pil(self.do_capture_array(crop_data=crop_data))

    @param.depends("act_capture_still", watch=True)
    def on_capture_still(self):
        ensure_folder(StillFolders.RAW.value)
        image = self.do_capture_still()
        image.save(
            StillFolders.RAW.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    @param.depends("act_capture_cropped_still", watch=True)
    def on_capture_cropped_still(self):
        ensure_folder(StillFolders.CROPPED.value)
        image = self.do_capture_still(
            crop_data=Rectangle(
                left=self.crop_left,
                top=self.crop_top,
                right=-self.crop_right,
                bottom=-self.crop_bottom,
            )
        )
        image.save(
            StillFolders.CROPPED.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    def get_card(
        self,
        kind: Literal[Cards.THUMBNAIL, Cards.PREVIEW, Cards.CROP_DATA, Cards.FOCUS],
    ):
        match kind:
            case Cards.THUMBNAIL:
                self.crd_thumbnail.objects = [self.thumbnail_pane]
                return self.crd_thumbnail
            case Cards.PREVIEW:
                self.crd_preview.objects = [
                    pn.widgets.Select.from_param(
                        self.param.sensor_modes,
                        name="Sensor mode",
                        sizing_mode="stretch_width",
                    ),
                    pn.widgets.Button.from_param(
                        self.param.act_capture_still,
                        name="Capture raw still",
                        sizing_mode="stretch_width",
                    ),
                    pn.widgets.Button.from_param(
                        self.param.act_capture_cropped_still,
                        name="Capture cropped still",
                        sizing_mode="stretch_width",
                    ),
                    pn.Row(
                        pn.widgets.Button.from_param(
                            self.param.act_preview_start_video,
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.Button.from_param(
                            self.param.act_preview_start_still,
                            sizing_mode="stretch_width",
                        ),
                    ),
                    pn.widgets.Button.from_param(
                        self.param.act_preview_stop, sizing_mode="stretch_width"
                    ),
                ]
                return self.crd_preview
            case Cards.CROP_DATA:
                self.crd_crop_data.objects = [
                    pn.widgets.IntInput.from_param(
                        self.param.crop_top,
                        name="Top",
                        align="center",
                        sizing_mode="stretch_width",
                        step=2,
                    ),
                    pn.Row(  # do_capture_still()
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
                ]
                return self.crd_crop_data
            case Cards.FOCUS:
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
                            self.param.act_focus_close, sizing_mode="stretch_width"
                        ),
                        pn.widgets.Button.from_param(
                            self.param.act_focus_far, sizing_mode="stretch_width"
                        ),
                    ),
                    pn.widgets.FloatSlider.from_param(
                        self.param.focus_distance,
                        name="Focus Distance",
                        sizing_mode="stretch_width",
                    ),
                ]
                return self.crd_focus

    def sidebar(self):
        return pn.Column(
            *[
                self.get_card(c)
                for c in [Cards.THUMBNAIL, Cards.PREVIEW, Cards.CROP_DATA, Cards.FOCUS]
            ],
            width=self._sidebar_width,
        )

    def main(self):
        return pn.layout.Tabs(
            ("Preview", self.preview_pane),
            (
                "Camera info",
                pn.layout.Accordion(
                    ("Camera configuration", self.camera_config),
                    ("Camera controls", self.camera_controls),
                    active=[0],
                ),
            ),
            active=0,
        )

    def show(self):
        sidebar = self.sidebar()
        sidebar.width = self._sidebar_width
        return pn.Row(sidebar, self.main())

    @working
    def start_still(self):
        if self.status in [CameraStatus.STILL, CameraStatus.VIDEO]:
            self.stop()
        self.camera.start(show_preview=False)
        self.status = CameraStatus.STILL
        self.do_capture_array(apply_crop=True)

    @working
    def start_video(self):
        if self.status in [CameraStatus.STILL, CameraStatus.VIDEO]:
            self.stop()
        self.status = CameraStatus.VIDEO
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

    @working
    def stop(self):
        match self.status:
            case CameraStatus.STOPPED:
                pass
            case CameraStatus.STILL:
                self.camera.stop()
            case CameraStatus.VIDEO:
                self.stop_event.set()
                self.thread.join()
                self.camera.stop_recording()
                self.output.close()
                self.camera.stop()
        self.status = CameraStatus.STOPPED


_preview = None


def post_callback(request):
    global _preview
    if _preview._working is True:
        return
    _preview._working = True
    try:
        metadata = request.get_metadata()
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
    return _preview
