# https://tabler.io/icons


from pathlib import Path
from functools import wraps
from datetime import datetime as dt

try:
    from libcamera import controls
except:
    pass

import panel as pn

from enderscope.serial import list_ports, default_printer_port
from enderscope.scan_patterns import plot_path_status
from enderscope.bed import bed
from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize
from enderleaf.enderleaf_ctrl import EnderLeafController, CropMode

pn.extension("ace", "jsoneditor", "ipywidgets")

SIDE_BAR_WIDTH = 300

_working = False


def working(method):
    @wraps(method)
    def _impl(*method_args, **method_kwargs):
        global _working
        if _working is True:
            return
        _working = True
        try:
            return method(*method_args, **method_kwargs)
        finally:
            _working = False

    return _impl


# MARK: Controller Callbacks
video_pane = pn.pane.Image(sizing_mode="stretch_width")
still_pane = pn.pane.Image(sizing_mode="stretch_width")
plt_z = pn.indicators.LinearGauge(
    name="Z position",
    value=0,
    bounds=(bed.z_min, bed.z_max),
    width=60,
    sizing_mode="stretch_height",
    format="",
    align="start",
)
plt_position = pn.pane.Matplotlib(
    object=plot_path_status(),
    sizing_mode="stretch_width",
    height=300,
    align="start",
)
plt_focus = pn.pane.Matplotlib(sizing_mode="stretch_width", height=300, align="start")
sel_position = pn.widgets.Select(
    name="Position", sizing_mode="stretch_width", options=[]
)

json_camera_config = pn.pane.JSON(object=None, name="Camera configuration", depth=-1)


def on_update_preview(image):
    video_pane.object = to_pil(image)


def on_update_still(image):
    still_pane.object = safe_pil_resize(to_pil(image), new_width=1024, new_height=768)


def on_z_moved(z):
    plt_z.value = z


def on_update_position_plot(new_plot):
    plt_position.object = new_plot


def on_update_focus_plot(new_plot):
    plt_focus.object = new_plot


def on_update_positions(positions):
    sel_position.options = [i + 1 for i in list(range(len(positions)))]


def post_callback(request):
    global _working
    if _working is True:
        return
    _working = True
    try:
        metadata = request.get_metadata()
        metadata = dict(sorted(metadata.items()))
        json_camera_config.object = metadata
        # fs_focus_distance.value = metadata["LensPosition"]
        # sel_focus_mode.value = metadata["AfState"]
    finally:
        _working = False


# MARK: Controller
controller = EnderLeafController()
controller.update_preview = on_update_preview
controller.update_still = on_update_still
controller.update_z_pos = on_z_moved
controller.update_focus_plot = on_update_focus_plot
controller.update_position_plot = on_update_position_plot
controller.update_positions = on_update_positions
controller.camera.post_callback = post_callback

# MARK: Widgets
json_camera_controls = pn.widgets.JSONEditor(
    value=dict(sorted(controller.camera.camera_controls.items())),
    name="Camera controls",
    mode="view",
    sizing_mode="stretch_width",
)
sel_sensor_modes = pn.widgets.Select(
    name="Sensor modes",
    options={
        f'{x["format"].format} {x["size"]}': i
        for i, x in enumerate(controller.camera.sensor_modes)
    },
    value=2,
    sizing_mode="stretch_width",
)
bt_capture_still = pn.widgets.Button(name="Capture still", sizing_mode="stretch_width")
bt_preview_start = pn.widgets.Button(
    name="Start preview",
    icon="player-play",
    icon_size="2em",
    sizing_mode="stretch_width",
)
bt_preview_stop = pn.widgets.Button(
    name="Stop preview",
    icon="player-stop",
    icon_size="2em",
    sizing_mode="stretch_width",
)

# sel_focus_mode = pn.widgets.Select(
#     name="Focus mode",
#     sizing_mode="stretch_width",
#     value=controls.AfModeEnum.Manual.value,
#     options={
#         afm.name: afm.value
#         for afm in [
#             controls.AfModeEnum.Manual,
#             controls.AfModeEnum.Auto,
#             controls.AfModeEnum.Continuous,
#         ]
#     },
# )
bt_focus = pn.widgets.Button(name="Focus", sizing_mode="stretch_width")
bt_focus_close = pn.widgets.Button(name="Focus close", sizing_mode="stretch_width")
bt_focus_far = pn.widgets.Button(name="Focus far", sizing_mode="stretch_width")

# focus_min, focus_max = controller.camera.camera_controls["LensPosition"][:2]
# fs_focus_distance = pn.widgets.FloatSlider(
#     name="Lens Position",
#     sizing_mode="stretch_width",
#     start=focus_min,
#     end=focus_max,
# )

sel_printer = pn.widgets.Select(
    name="Select serial connection",
    sizing_mode="stretch_width",
    options=[str(p) for p in list_ports()],
    value=str(default_printer_port()),
)
bt_connect_printer = pn.widgets.Button(
    name="Connect to printer",
    sizing_mode="stretch_width",
    icon="plug-connected",
    icon_size="2em",
)
bt_home = pn.widgets.Button(
    name="Home",
    sizing_mode="stretch_width",
    icon="home",
    icon_size="2em",
)
bt_idle = pn.widgets.Button(
    name="Wait",
    sizing_mode="stretch_width",
    icon="clock-pause",
    icon_size="2em",
)
bt_park = pn.widgets.Button(
    name="Park",
    sizing_mode="stretch_width",
    icon="parking-circle",
    icon_size="2em",
)
bt_move_to = pn.widgets.Button(
    name="Move to...",
    icon="arrow-move-right",
    icon_size="2em",
)
bt_qr_code = pn.widgets.Button(
    name="Go to QR code",
    icon="qrcode",
    icon_size="2em",
    sizing_mode="stretch_width",
)
bt_check_corners = pn.widgets.Button(
    name="Check corners",
    icon="border-corners",
    icon_size="2em",
    sizing_mode="stretch_width",
)
bt_lights_on = pn.widgets.Button(
    name="Lights on",
    icon="bulb",
    icon_size="2em",
    sizing_mode="stretch_width",
    button_type="default",
)
bt_lights_off = pn.widgets.Button(
    name="Lights Off",
    icon="bulb-off",
    icon_size="2em",
    sizing_mode="stretch_width",
    button_type="success",
)
ii_crop_top = pn.widgets.IntInput(
    name="Top",
    align="center",
    sizing_mode="stretch_width",
    value=controller.crop_top,
)
ii_crop_left = pn.widgets.IntInput(
    name="Left",
    sizing_mode="stretch_width",
    value=controller.crop_left,
)
ii_crop_right = pn.widgets.IntInput(
    name="Right",
    sizing_mode="stretch_width",
    value=controller.crop_right,
)
ii_crop_bottom = pn.widgets.IntInput(
    name="Bottom",
    align="center",
    sizing_mode="stretch_width",
    value=controller.crop_bottom,
)
sel_crop_mode = pn.widgets.Select(
    name="Crop mode",
    options=[c.value for c in [CropMode.CROP, CropMode.LINES, CropMode.IGNORE]],
    value=controller.crop_mode,
    sizing_mode="stretch_width",
)
ii_plate_x = pn.widgets.IntInput(
    name="Plate X size",
    align="center",
    sizing_mode="stretch_width",
    value=controller.plate_x,
)
ii_plate_y = pn.widgets.IntInput(
    name="Plate Y size",
    align="center",
    sizing_mode="stretch_width",
    value=controller.plate_y,
)
ii_plate_row_count = pn.widgets.IntInput(
    name="Plate ROW count",
    align="center",
    sizing_mode="stretch_width",
    value=controller.plate_row_count,
)
ii_plate_col_count = pn.widgets.IntInput(
    name="Plate COL count",
    align="center",
    sizing_mode="stretch_width",
    value=controller.plate_col_count,
)
ii_focus_start_z = pn.widgets.IntInput(
    name="Focus start Z", sizing_mode="stretch_width", value=controller.focus_start_z
)
ii_focus_delta_z = pn.widgets.IntInput(
    name="Focus ΔZ", sizing_mode="stretch_width", value=controller.focus_delta_z
)
bt_launch_acquisition = pn.widgets.Button(
    name="Launch image capture",
    icon="player-play",
    icon_size="2em",
    sizing_mode="stretch_width",
)

# MARK: Cards
crd_preview = pn.layout.Card(
    objects=[
        sel_sensor_modes,
        bt_capture_still,
        pn.layout.WidgetBox(
            "#### Focus (Ignored while in experiments)",
            # sel_focus_mode,
            bt_focus,
            pn.Row(bt_focus_close, bt_focus_far),
            # fs_focus_distance,
        ),
    ],
    title="Preview",
    collapsed=True,
)
crd_crop_data = pn.layout.Card(
    objects=[
        ii_crop_top,
        pn.Row(ii_crop_left, ii_crop_right),
        ii_crop_bottom,
        sel_crop_mode,
    ],
    title="Crop",
    collapsed=True,
)
crd_focus = pn.layout.Card(
    objects=[pn.Row(ii_focus_start_z, ii_focus_delta_z)],
    title="Focus",
    collapsed=True,
)
crd_plate = pn.layout.Card(
    objects=[
        pn.Row(ii_plate_x, ii_plate_y),
        pn.Row(ii_plate_row_count, ii_plate_col_count),
    ],
    title="Plate",
    collapsed=True,
)
crd_init = pn.layout.Card(
    objects=[
        pn.Row(bt_preview_start, bt_preview_stop),
        sel_printer,
        bt_connect_printer,
    ],
    title="Initialize",
    collapsed=False,
)
crd_move = pn.layout.Card(
    objects=[
        pn.Row(bt_home, bt_idle, bt_park),
        pn.Row(bt_qr_code, bt_check_corners),
        pn.Row(bt_move_to, sel_position),
        pn.Row(bt_lights_on, bt_lights_off),
        bt_launch_acquisition,
    ],
    title="Control",
    collapsed=False,
)


# MARK: Events
def on_capture_still(event):
    ensure_folder(Path(".").joinpath("output", "raw"))
    image, _ = controller.capture_array()
    to_pil(image).save(
        Path(".")
        .joinpath("output", "raw")
        .joinpath(dt.now().strftime("%Y%m%d%H%M%S"))
        .with_suffix(".jpg")
    )


def on_preview_start(event):
    controller.start()


def on_preview_stop(event):
    controller.stop()


@working
def on_request_focus(event):
    controller.autofocus_cycle()


@working
def on_request_focus_close(event):
    controller.set_focus_close()


@working
def on_request_focus_far(event):
    controller.set_focus_far()


def on_connect_printer(event):
    controller.connect_printer(sel_printer.value)


def on_home(event):
    controller.go_home()


def on_idle(event):
    controller.go_rest()


def on_park(event):
    controller.go_park()


def on_center_on_qr_code(event):
    controller.center_on_qr_code()


def on_check_corners(event):
    controller.check_corners()


def on_launch_acquisition(event):
    controller.launch_acquisition(
        # switch_state=True,
        # precise_focusing=False,
    )


def on_lights_on(event):
    controller.shutter(True)
    bt_lights_on.button_type = "success"
    bt_lights_off.button_type = "default"


def on_lights_off(event):
    controller.shutter(False)
    bt_lights_on.button_type = "default"
    bt_lights_off.button_type = "success"


def on_move_to(event):
    controller.move_to(sel_position.value)


# MARK: Binds
bt_capture_still.on_click(on_capture_still)
bt_preview_start.on_click(on_preview_start)
bt_preview_stop.on_click(on_preview_stop)
bt_focus.on_click(on_request_focus)
bt_focus_close.on_click(on_request_focus_close)
bt_focus_far.on_click(on_request_focus_far)
bt_connect_printer.on_click(on_connect_printer)
bt_home.on_click(on_home)
bt_idle.on_click(on_idle)
bt_park.on_click(on_park)
bt_qr_code.on_click(on_center_on_qr_code)
bt_check_corners.on_click(on_check_corners)
bt_move_to.on_click(on_move_to)
bt_lights_on.on_click(on_lights_on)
bt_lights_off.on_click(on_lights_off)
bt_launch_acquisition.on_click(on_launch_acquisition)


# MARK: Dependables
@pn.depends(sel_sensor_modes.param.value, watch=True)
def on_sensor_mode_changed(sensor_mode):
    controller.set_sensor_mode(sensor_mode)


# @working
# @pn.depends(sel_focus_mode.param.value, watch=True)
# def on_focus_mode_changed(focus_mode):
#     controller.camera.set_controls({"AfMode": focus_mode})


# @working
# @pn.depends(fs_focus_distance.param.value, watch=True)
# def on_focus_distance_changed(focus_distance):
#     controller.set_focus_distance(focus_distance)


@pn.depends(
    *[
        p.param.value
        for p in [
            ii_crop_left,
            ii_crop_right,
            ii_crop_top,
            ii_crop_bottom,
            sel_crop_mode,
        ]
    ],
    watch=True,
)
def on_crop_changed(cl, cr, ct, cb, cm):
    controller.set_crop(
        left=cl,
        right=cr,
        top=ct,
        bottom=cb,
        crop_mode=cm,
    )


@pn.depends(
    *[
        p.param.value
        for p in [
            ii_plate_x,
            ii_plate_y,
            ii_plate_row_count,
            ii_plate_col_count,
            ii_focus_start_z,
            ii_focus_delta_z,
        ]
    ],
    watch=True,
)
def on_plate_properties_changed(x, y, rc, cc, fs, fd):
    controller.set_plate(x, y, rc, cc, fs, fd)


# MARK: UI
def ui_sidebar():
    return pn.Column(
        crd_preview, crd_init, crd_plate, crd_crop_data, crd_focus, crd_move
    )


def ui_main():
    return pn.layout.Tabs(
        ("Preview", video_pane),
        (
            "Experiment",
            pn.Column(pn.Row(plt_position, plt_z, plt_focus), still_pane),
        ),
        (
            "Camera info",
            pn.layout.Accordion(
                ("Camera configuration", json_camera_config),
                ("Camera controls", json_camera_controls),
                active=[0],
            ),
        ),
        active=0,
    )


def ui_show():
    controller.start()
    sidebar = ui_sidebar()
    sidebar.width = SIDE_BAR_WIDTH
    return pn.Row(sidebar, ui_main())
