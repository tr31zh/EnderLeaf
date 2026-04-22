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


def update_still_preview(method):
    @wraps(method)
    def _impl(self, *method_args, **method_kwargs):
        result = method(self, *method_args, **method_kwargs)
        self.still_update_preview()
        return result

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
    sensor_modes = param.Selector(default=2)
    act_capture_still = param.Action(
        default=lambda x: x.param.trigger("act_capture_still"), label="Capture still"
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
        self.crd_crop_data = pn.layout.Card(objects=[], title="Crop")
        self.crd_focus = pn.layout.Card(objects=[], title="Focus options")
        self._counter = 0
        self._debugger = pn.pane.Placeholder("Debugger", sizing_mode="stretch_width")
        self._debug_counter = pn.pane.Placeholder(
            "Debug counter", sizing_mode="stretch_width"
        )

    def update_preview(self, image, crop_data: Rectangle | None = None):
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
            ) and self.crop_mode != CropMode.IGNORE:
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

            preview_object = to_pil(image)
        except Exception as e:
            self.update_debugger({"error": str(e), "crop_data": str(crop_data)})
        else:
            match self.status:
                case CameraStatus.STOPPED:
                    pass
                case CameraStatus.STILL:
                    self.preview_pane.object = safe_pil_resize(
                        image=preview_object, new_height=768, new_width=1024
                    )
                case CameraStatus.VIDEO:
                    self._counter += 1
                    self.preview_pane.object = preview_object
            if self.crd_thumbnail.collapsed is False:
                self.thumbnail_pane.object = safe_pil_resize(
                    image=preview_object,
                    new_height=self._sidebar_width,
                    new_width=self._sidebar_width,
                )

    def update_debugger(self, data):
        self._counter += 1
        self._debugger.object = data
        self._debug_counter.object = self._counter

    def still_update_preview(self):
        if self.status == CameraStatus.STILL:
            self.do_capture_array()

    def set_crop(self, left, right, top, bottom):
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

    @update_still_preview
    @param.depends(
        "crop_left", "crop_right", "crop_top", "crop_bottom", "crop_mode", watch=True
    )
    def on_crop_changed(self):
        pass

    @update_still_preview
    @param.depends("sensor_modes", watch=True)
    def on_sensor_mode_changed(self):
        self.camera.stop_recording()
        new_mode = self.camera.sensor_modes[self.sensor_modes]
        self.camera.configure(self.camera.create_video_configuration(raw=new_mode))
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))

    @working
    @update_still_preview
    @param.depends("focus_mode", watch=True)
    def on_focus_mode_changed(self):
        self.camera.set_controls({"AfMode": self.focus_mode})

    @working
    @update_still_preview
    @param.depends("focus_distance", watch=True)
    def on_focus_distance_changed(self):
        self.camera.set_controls({"LensPosition": self.focus_distance})

    @working
    @update_still_preview
    @param.depends("act_focus", watch=True)
    def on_request_focus(self):
        self.camera.autofocus_cycle()

    @update_still_preview
    @working
    @param.depends("act_focus_close", watch=True)
    def on_request_focus_close(self):
        self.camera.set_controls(
            {"LensPosition": self.camera.camera_controls["LensPosition"][1]}
        )

    @update_still_preview
    @working
    @param.depends("act_focus_far", watch=True)
    def on_request_focus_far(self):
        self.camera.set_controls({"LensPosition": 0})

    @param.depends("act_preview_start_video", watch=True)
    def on_preview_start_video(self):
        self.start_video()

    @param.depends("act_preview_start_still", watch=True)
    def on_preview_start_still(self):
        self.start_still()

    @param.depends("act_preview_stop", watch=True)
    def on_preview_stop(self):
        self.stop()

    def do_capture_array(self):
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

        if self.status == CameraStatus.STILL:
            self.update_preview(image)
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
        to_pil(self.do_capture_array()).save(
            StillFolders.RAW.value.joinpath(
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
                    # pn.widgets.Select.from_param(
                    #     self.param.sensor_modes,
                    #     name="Sensor mode",
                    #     sizing_mode="stretch_width",
                    # ),
                    pn.widgets.Button.from_param(
                        self.param.act_capture_still, sizing_mode="stretch_width"
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
            ("Debugger", pn.Column(self._debug_counter, self._debugger)),
            active=0,
        )

    def show(self):
        sidebar = self.sidebar()
        sidebar.width = self._sidebar_width
        return pn.Row(sidebar, self.main())

    @working
    @update_still_preview
    def start_still(self):
        if self.status in [CameraStatus.STILL, CameraStatus.VIDEO]:
            self.stop()
        self.camera.start(show_preview=False)
        time.sleep(0.5)
        self.status = CameraStatus.STILL

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
