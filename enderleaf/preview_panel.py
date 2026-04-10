from pathlib import Path
import io
from threading import Thread, Condition
from functools import wraps
from datetime import datetime as dt

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

from libcamera import Transform, controls

import param
import panel as pn

""" 
From https://medium.com/@abhishekjainindore24/advanced-python-12-condition-variables-producer-consumer-problem-wait-notify-patterns-6b6a8c3c4e14
"""

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


def update_panel(output, target):
    while True:
        with output.condition:
            output.condition.wait()
            target.object = output.frame
        if output.closed:
            break


class PreviewPane(param.Parameterized):
    sensor_modes = param.Selector()
    capture_still = param.Action(default=lambda x: x.param.trigger('capture_still'), label='Capture still')
    capture_cropped_still = param.Action(default=lambda x: x.param.trigger('capture_cropped_still'), label='Capture cropped still')

    crop_left = param.Integer(0)
    crop_right = param.Integer(0)
    crop_top = param.Integer(0)
    crop_bottom = param.Integer(0)

    focus_mode = param.Selector(
        default=controls.AfModeEnum.Manual.value, 
        objects={afm.name:afm.value for afm in [
                controls.AfModeEnum.Manual,
                controls.AfModeEnum.Auto,
                controls.AfModeEnum.Continuous
            ]
        },
    )
    do_focus = param.Action(default=lambda x: x.param.trigger('do_focus'), label='Focus')
    focus_distance = param.Number()

    def __init__(self, **params):
        super().__init__(**params)
        self._working = False
        self.output = StreamingOutput()
        self.camera = Picamera2()
        mode = self.camera.sensor_modes[0]
        self.camera.configure(
            self.camera.create_video_configuration(
                raw=mode
            )
        )
        self.camera.set_controls({"AfMode": controls.AfModeEnum.Manual})
        self.preview_pane = pn.pane.Placeholder(
            "Preview", 
            sizing_mode="stretch_width",
        )
        self.capture_config = self.camera.create_still_configuration()
        self.param.sensor_modes.objects = {
            f'{x["format"].format} {x["size"]}':i for i,x in enumerate(self.camera.sensor_modes)
        }
        self.param.focus_distance.bounds = self.camera.camera_controls["LensPosition"][:2]

    def update_crop_values(self):
        _, scaler_crop, _ = self.camera.camera_controls['ScalerCrop']
        self.crop_left = scaler_crop[0]
        self.crop_top = scaler_crop[1]
        self.crop_right = scaler_crop[2]
        self.crop_bottom = scaler_crop[3]

    @param.depends("sensor_modes", watch=True)
    @working
    def on_sensor_mode_changed(self):
        self.camera.stop_recording()
        new_mode = self.camera.sensor_modes[self.sensor_modes]
        self.camera.configure(
            self.camera.create_video_configuration(
                raw=new_mode
            )
        )
        self.update_crop_values()
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))

    @param.depends("crop_left","crop_right","crop_top","crop_bottom",watch=True)
    def on_crop_changed(self):
        if self._working is True:
            return
        self.camera.set_controls(
            {
                "ScalerCrop": (
                    self.crop_left, self.crop_top, self.crop_right, self.crop_bottom
                )
            }
        )

    @param.depends("focus_mode", watch=True)
    @working
    def on_focus_mode_changed(self):
        self.camera.set_controls({"AfMode": self.focus_mode})

    @param.depends("focus_distance", watch=True)
    @working
    def on_focus_distance_changed(self):
        self.camera.set_controls({"LensPosition": self.focus_distance})

    @param.depends('do_focus', watch=True)
    @working
    def on_request_focus(self):
        self.camera.autofocus_cycle()

    @param.depends('capture_still', watch=True)
    @working
    def on_capture_still(self):
        image = self.camera.switch_mode_and_capture_image(self.capture_config, "main")
        image.save(
            Path(".").joinpath("output").joinpath(dt.now().strftime("%Y%m%d-%H%M%S")).with_suffix(".jpg")
        )

    @param.depends('capture_cropped_still', watch=True)
    @working
    def on_capture_cropped_still(self):
        pass

    @working
    def start(self):
        self.camera.start_recording(JpegEncoder(), FileOutput(self.output))
        self.update_crop_values()
        self.thread = Thread(target = update_panel, args = (self.output, self.preview_pane))
        self.thread.start()

    def stop(self):
        self.camera.stop_recording()

    def show(self):
        return pn.Row(
            pn.Column(
                pn.WidgetBox(
                    "### Main",
                    pn.widgets.Select.from_param(self.param.sensor_modes, name="Sensor mode",sizing_mode="stretch_width"),
                    pn.widgets.Button.from_param(self.param.capture_still, name="Capture still",sizing_mode="stretch_width"),
                    pn.widgets.Button.from_param(self.param.capture_cropped_still, name="Capture cropped still",sizing_mode="stretch_width"),
                ),
                pn.WidgetBox(
                    "### Crop data",
                    pn.widgets.IntInput.from_param(self.param.crop_top, name="Top", align="center",sizing_mode="stretch_width"),
                    pn.Row(
                        pn.widgets.IntInput.from_param(self.param.crop_left, name="Left",sizing_mode="stretch_width"),
                        pn.widgets.IntInput.from_param(self.param.crop_right, name="Right",sizing_mode="stretch_width"),
                    ),
                    pn.widgets.IntInput.from_param(self.param.crop_bottom, name="Bottom", align="center",sizing_mode="stretch_width"),
                    align="center",
                    sizing_mode="stretch_width"
                ),
                pn.WidgetBox(
                    "### Focus options",
                    pn.widgets.Select.from_param(self.param.focus_mode, name="Focus mode",sizing_mode="stretch_width"),
                    pn.widgets.Button.from_param(self.param.do_focus, name="Focus",sizing_mode="stretch_width"),
                    pn.widgets.FloatSlider.from_param(self.param.focus_distance, name="Focus Distance",sizing_mode="stretch_width"),
                    align="center",
                    sizing_mode="stretch_width"
                ),
                width=200
            ), 
            self.preview_pane
        )

    def close(self):
        self.stop()
        self.output.close()
        self.thread.join()

def post_callback(request):
    # Extract metadata from the camera request
    metadata = request.get_metadata()
    # Sort metadata for better readability
    sorted_metadata = sorted(metadata.items(), key=lambda x: x[0] if "Awb" not in x[0] else f"Z{x[0]}")
    pretty_metadata = []
    for k, v in sorted_metadata:
        row = ""
        try:
            iter(v)  # Check if value is iterable
            if k == "ColourCorrectionMatrix":
                matrix = np.around(np.reshape(v, (-1, 3)), decimals=2)
                row = f"{k}:\n{matrix}"
            else:
                row_data = [f'{x:.2f}' if type(x) is float else f'{x}' for x in v]
                row = f"{k}: ({', '.join(row_data)})"
        except TypeError:
            row = f"{k}: {v:.2f}" if isinstance(v, float) else f"{k}: {v}"
        pretty_metadata.append(row)
    # Update the info tab with formatted metadata
    info_tab.setText('\n'.join(pretty_metadata))

    # Update AEC and AWB controls if they are disabled
    if not aec_tab.exposure_time.isEnabled():
        aec_tab.exposure_time.setValue(metadata["ExposureTime"])
        aec_tab.analogue_gain.setValue(metadata["AnalogueGain"])
    if not aec_tab.colour_gain_r.isEnabled():
        aec_tab.colour_gain_r.setValue(metadata.get("ColourGains", [1.0, 1.0])[0])
        aec_tab.colour_gain_b.setValue(metadata.get("ColourGains", [1.0, 1.0])[1])
    vid_tab.frametime = metadata["FrameDuration"]