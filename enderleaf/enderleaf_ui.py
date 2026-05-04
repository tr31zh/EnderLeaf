# https://tabler.io/icons


from pathlib import Path
from functools import wraps
from datetime import datetime as dt
from enum import Enum
from typing import Literal

import numpy as np


from libcamera import controls

import param
import panel as pn

from enderscope.serial import list_ports, default_printer_port, Stage
from enderscope.scan_patterns import plot_path_status
from enderscope.bed import bed
from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize, Rectangle
from enderleaf.enderleaf_ctrl import EnderLeafController, CropMode

pn.extension("ace", "jsoneditor", "ipywidgets")


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


class EnderLeafUi(param.Parameterized):
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
    crop_left = param.Integer(1200)
    crop_right = param.Integer(1200)
    crop_top = param.Integer(400)
    crop_bottom = param.Integer(350)
    crop_mode = param.Selector(
        objects=[c.value for c in [CropMode.CROP, CropMode.LINES, CropMode.IGNORE]],
        default=CropMode.LINES.value,
        label="Crop mode",
    )

    plate_x = param.Integer(200)
    plate_y = param.Integer(200)
    plate_row_count = param.Integer(9)
    plate_col_count = param.Integer(9)

    focus_start_z = param.Integer(36)
    focus_delta_z = param.Integer(10)

    def __init__(self, **params):
        # MARK: INIT
        super().__init__(**params)
        self._working = False
        self._sidebar_width = 300
        self.controller = EnderLeafController(parent=self)
        self.video_pane = pn.pane.Image(
            sizing_mode="stretch_width",
            # enable_streaming=True,
        )
        self.still_pane = pn.pane.Image(sizing_mode="stretch_width")
        self.camera_config = pn.pane.JSON(
            object=None, name="Camera configuration", depth=-1
        )
        self.camera_controls = pn.widgets.JSONEditor(
            value=dict(sorted(self.controller.camera.camera_controls.items())),
            name="Camera controls",
            mode="view",
            sizing_mode="stretch_width",
        )
        self.param.sensor_modes.objects = {
            f'{x["format"].format} {x["size"]}': i
            for i, x in enumerate(self.controller.camera.sensor_modes)
        }
        self.param.focus_distance.bounds = self.controller.camera.camera_controls[
            "LensPosition"
        ][:2]
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

        self.on_crop_changed()
        self.on_plate_properties_changed()

    # MARK: Preview
    def update_preview(self, image):
        self.video_pane.object = to_pil(image)

    def on_still_captured(self, image):
        self.still_pane.object = safe_pil_resize(
            image=to_pil(image), new_width=1024, new_height=768
        )

    @param.depends(
        "crop_left",
        "crop_right",
        "crop_top",
        "crop_bottom",
        "crop_mode",
        watch=True,
    )
    def on_crop_changed(self):
        self.controller.set_crop(
            left=self.crop_left,
            right=self.crop_right,
            top=self.crop_top,
            bottom=self.crop_bottom,
            crop_mode=self.crop_mode,
        )

    @param.depends(
        "plate_x",
        "plate_y",
        "plate_row_count",
        "plate_col_count",
        "focus_start_z",
        "focus_delta_z",
        watch=True,
    )
    def on_plate_properties_changed(self):
        self.controller.set_plate(
            self.plate_x,
            self.plate_y,
            self.plate_row_count,
            self.plate_col_count,
            self.focus_start_z,
            self.focus_delta_z,
        )

    @param.depends("sensor_modes", watch=True)
    def on_sensor_mode_changed(self):
        self.controller.set_sensor_mode(self.sensor_modes)

    @working
    @param.depends("focus_mode", watch=True)
    def on_focus_mode_changed(self):
        self.controller.camera.set_controls({"AfMode": self.focus_mode})

    @working
    @param.depends("focus_distance", watch=True)
    def on_focus_distance_changed(self):
        self.controller.set_focus_distance(self.focus_distance)

    @working
    @param.depends("act_focus", watch=True)
    def on_request_focus(self):
        self.controller.autofocus_cycle()

    @working
    @param.depends("act_focus_close", watch=True)
    def on_request_focus_close(self):
        self.controller.set_focus_close()

    @working
    @param.depends("act_focus_far", watch=True)
    def on_request_focus_far(self):
        self.controller.set_focus_far()

    def capture_array(self):
        return self.controller.capture_array()

    @param.depends("act_preview_start", watch=True)
    def on_cpreview_start(self):
        self.controller.start()

    @param.depends("act_preview_stop", watch=True)
    def on_cpreview_stop(self):
        self.controller.stop()

    @param.depends("act_capture_still", watch=True)
    def on_capture_still(self):
        ensure_folder(StillFolders.RAW.value)
        image, _ = self.capture_array()
        to_pil(image).save(
            StillFolders.RAW.value.joinpath(
                dt.now().strftime("%Y%m%d%H%M%S")
            ).with_suffix(".jpg")
        )

    # MARK: Printer
    def get_position(self):
        return self.controller.get_position()

    def on_z_moved(self, z):
        self.plot_z.value = z

    def update_positions_plot(self, index: int | None = None):
        self.plot_position.object = self.controller.update_positions_plot(index)

    def move_position(self, position, index: int | None = None):
        self.controller.move_position(position, index)
        if index is not None:
            self.update_positions_plot(index=index)

    def move_absolute(self, x, y, z):
        self.controller.move_absolute(x, y, z)

    def move_relative(self, x, y, z: int | None = None):
        self.controller.move_relative(x, y, z)

    def go_home(self):
        self.controller.go_home()

    def go_rest(self):
        self.controller.go_rest()

    def go_park(self):
        self.controller.go_park()

    def update_focus_plot(self, focus_plot):
        self.plot_focus.object = focus_plot

    def build_snake(self, x, y) -> np.ndarray:
        self.controller.build_snake(x, y)
        self.param.sel_position.objects = [
            i + 1 for i in list(range(len(self.controller._positions)))
        ]
        self.update_positions_plot()

    def center_on_qr_code(self, step_val=10):
        x, y, z = self.controller.center_on_qr_code(step_val=step_val)
        self.build_snake(x, y)
        return x, y, z

    def check_corners(self):
        self.controller.check_corners()

    def launch_acquisition(self):
        self.controller.launch_acquisition()

    @param.depends("act_connect_printer", watch=True)
    def on_connect_printer(self):
        self.controller.connect_printer(self.sel_printer)

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
        self.controller.shutter(True)

    @param.depends("act_lights_off", watch=True)
    def on_lights_off(self):
        self.controller.shutter(False)

    @param.depends("act_move_to", watch=True)
    def on_move_to(self):
        self.controller.move_to()

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
                    pn.Row(
                        pn.widgets.Button.from_param(
                            self.param.act_preview_start,
                            sizing_mode="stretch_width",
                            icon="player-play",
                            icon_size="2em",
                        ),
                        pn.widgets.Button.from_param(
                            self.param.act_preview_stop,
                            sizing_mode="stretch_width",
                            icon="player-stop",
                            icon_size="2em",
                        ),
                    ),
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
                            self.param.plate_x,
                            name="Plate X size",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.plate_y,
                            name="Plate Y size",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                    ),
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.plate_row_count,
                            name="Plate ROW count",
                            align="center",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.plate_col_count,
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
                        self.param.crop_top,
                        name="Top",
                        align="center",
                        sizing_mode="stretch_width",
                    ),
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.crop_left,
                            name="Left",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.crop_right,
                            name="Right",
                            sizing_mode="stretch_width",
                        ),
                    ),
                    pn.widgets.IntInput.from_param(
                        self.param.crop_bottom,
                        name="Bottom",
                        align="center",
                        sizing_mode="stretch_width",
                    ),
                    pn.widgets.Select.from_param(
                        self.param.crop_mode, sizing_mode="stretch_width"
                    ),
                ]
                return self.crd_crop_data
            case SideBarCards.FOCUS:
                self.crd_focus.objects = [
                    pn.Row(
                        pn.widgets.IntInput.from_param(
                            self.param.focus_start_z,
                            name="Focus start Z",
                            sizing_mode="stretch_width",
                        ),
                        pn.widgets.IntInput.from_param(
                            self.param.focus_delta_z,
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
            active=0,
        )

    def show(self):
        sidebar = self.sidebar()
        sidebar.width = self._sidebar_width
        return pn.Row(sidebar, self.main())


_ender_leaf = None


def post_callback(request):
    global _ender_leaf
    if _ender_leaf._working is True:
        return
    _ender_leaf._working = True
    try:
        metadata = request.get_metadata()
        metadata = dict(sorted(metadata.items()))
        _ender_leaf.camera_config.object = metadata
        _ender_leaf.focus_distance = metadata["LensPosition"]
        _ender_leaf.focus_mode = metadata["AfState"]
    finally:
        _ender_leaf._working = False


def ender_leaf():
    global _ender_leaf
    if _ender_leaf is None:
        _ender_leaf = EnderLeafUi()
        _ender_leaf.controller.camera.post_callback = post_callback
        _ender_leaf.controller.start()
    return _ender_leaf
