# https://tabler.io/icons

import os
import logging
import psutil
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

from enderleaf.const import CropMode, ELStatus, LightsCycle
from enderleaf.draw import plot_focus_plt
from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize
from enderleaf.enderleaf_ctrl import EnderLeafController

pn.extension("jsoneditor", "ipywidgets")

mkd_log = pn.pane.Markdown(object="", sizing_mode="scale_width")
crd_log = pn.layout.Card(mkd_log, title="Log", collapsed=True)


class PanelLogHandler(logging.Handler):
    def __init__(self, target, card):
        super().__init__()
        self.target = target
        self._logs = []

    def emit(self, record):
        msg = self.format(record)
        self._logs.insert(0, msg)
        if len(self._logs) > 10:
            self._logs = self._logs[:10]
        crd_log.title = msg
        self.target.object = "- " + ("\n\n - ").join(self._logs)


class MemoryFilter(logging.Filter):

    last_process_mem = 0

    def filter(self, record):
        process: psutil.Process = psutil.Process(os.getpid())
        pmp = process.memory_percent()
        sign = (
            "⬆"
            if pmp > self.last_process_mem
            else "⬇" if pmp < self.last_process_mem else "="
        )
        record.mem_data = f"µ{sign} {pmp:02.2f}%"
        self.last_process_mem = pmp
        return True


PATH_LOG = Path(__file__).resolve().parent.parent.joinpath("logs")

ensure_folder(PATH_LOG)

log_file_handler = logging.FileHandler(
    PATH_LOG.joinpath(f"enderleaf_{dt.now().strftime('%Y_%m_%d')}.log"),
    mode="a",
    delay=True,
)
log_file_handler.addFilter(MemoryFilter())

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(mem_data)s - %(name)s - %(levelname)s] - %(message)s",
    handlers=[
        log_file_handler,
        PanelLogHandler(target=mkd_log, card=crd_log),
    ],
)

logger = logger = logging.getLogger(__name__)
logger.info("")
logger.info("==== Starting session ====")
logger.info(pn.state.session_info)
logger.info("")

SIDE_BAR_WIDTH = 300
LO_PRECISE_FOCUS = "Precise focus"
LO_SWITCH_STATE = "Switch camera state"
LO_CENTER_OL = "Center on leaf"

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
video_pane = pn.pane.Image(sizing_mode="scale_width")
still_pane = pn.pane.Image(sizing_mode="scale_width")
plt_position = pn.pane.Matplotlib(
    object=plot_path_status(title=""),
    sizing_mode="scale_width",
    height=330,
    align="center",
)
plt_focus = pn.pane.Matplotlib(sizing_mode="stretch_width", height=330, align="center")
sel_position = pn.widgets.Select(name="Position", options=[], width=100)

json_camera_config = pn.pane.JSON(
    object=None, name="Camera configuration", depth=-1, sizing_mode="stretch_width"
)

pg_progress = pn.indicators.Progress(value=0, sizing_mode="stretch_width")


def on_update_preview(image):
    video_pane.object = to_pil(image)


def on_update_still(image):
    still_pane.object = safe_pil_resize(to_pil(image), new_width=1024, new_height=768)


def on_update_position_plot(new_plot):
    plt_position.object = new_plot


def on_update_focus_plot(df_scores):
    plt_focus.object = plot_focus_plt(df=df_scores)


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
    finally:
        _working = False


def on_progress_updated(current, total):
    if pg_progress.max != total:
        pg_progress.max = total
    pg_progress.value = current + 1


# MARK: Controller
controller = EnderLeafController()
pg_progress.max = controller.plate_row_count * controller.plate_col_count

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
bt_capture_still = pn.widgets.Button(
    name="Capture still", icon="capture", icon_size="2em", sizing_mode="stretch_width"
)
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

bt_focus = pn.widgets.Button(name="Focus", sizing_mode="stretch_width")
bt_focus_close = pn.widgets.Button(name="Focus close", sizing_mode="stretch_width")
bt_focus_far = pn.widgets.Button(name="Focus far", sizing_mode="stretch_width")

sel_printer = pn.widgets.Select(
    name="Select serial connection",
    options=[str(p) for p in list_ports()],
    value=str(default_printer_port()),
)
bt_connect_printer = pn.widgets.Button(
    name="Connect to printer", icon="plug-connected", icon_size="2em"
)
bt_home = pn.widgets.Button(
    name="Home",
    sizing_mode="stretch_width",
    icon="home",
    icon_size="2em",
)
bt_idle = pn.widgets.Button(name="Wait", icon="clock-pause", icon_size="2em")
bt_park = pn.widgets.Button(name="Park", icon="parking-circle", icon_size="2em")
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
bt_lights_toggle = pn.widgets.Button(
    name="TOP lights",
    icon="bulb",
    icon_size="2em",
    sizing_mode="stretch_width",
    button_type="success",
)
bt_lights_cycle = pn.widgets.Button(
    name="Cycle lights",
    icon="recycle",
    icon_size="2em",
    sizing_mode="stretch_width",
    button_type="default",
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
    name="Launch job", icon="player-play", icon_size="2em"
)
bt_stop_acquisition = pn.widgets.Button(
    name="Stop", icon="circle-dashed-x", icon_size="2em", button_type="danger"
)
cbg_launch_options = pn.widgets.CheckBoxGroup(
    name="Launch options",
    options=[LO_PRECISE_FOCUS, LO_SWITCH_STATE, LO_CENTER_OL],
    value=[LO_PRECISE_FOCUS, LO_SWITCH_STATE, LO_CENTER_OL],
    sizing_mode="stretch_width",
)
bt_check_discs = pn.widgets.Button(
    name="Check disc positions",
    icon="zoom-check",
    icon_size="2em",
    sizing_mode="stretch_width",
    button_type="warning",
)
eis_lights_top_brightness = pn.widgets.EditableFloatSlider(
    name="Brightness",
    start=0,
    end=1.0,
    step=0.05,
    value=controller.top_lights.brightness,
    sizing_mode="scale_width",
)
sel_lights_cycle = pn.widgets.MultiChoice(
    name="Acquisition lights cycle",
    # label="Acquisition lights cycle",
    options={
        lc.name: lc
        for lc in [
            LightsCycle.OFF,
            LightsCycle.FULL,
            LightsCycle.ONE_FOURTH,
            LightsCycle.TWO_FOURTHS,
            LightsCycle.THREE_FOURTHS,
        ]
    },
    value=controller.light_cycles,
    sizing_mode="scale_width",
)


# MARK: Cards
crd_preview_options = pn.Column(
    objects=[
        sel_sensor_modes,
        pn.Row(bt_preview_start, bt_preview_stop),
        bt_capture_still,
        pn.layout.WidgetBox(
            "#### Focus (Ignored while in experiments)",
            bt_focus,
            pn.Row(bt_focus_close, bt_focus_far),
        ),
    ],
)
crd_configure = pn.Column(
    objects=[
        pn.layout.WidgetBox("### Launh options", cbg_launch_options, sel_lights_cycle),
        pn.layout.WidgetBox(
            "### Crop",
            ii_crop_top,
            pn.Row(ii_crop_left, ii_crop_right),
            ii_crop_bottom,
            sel_crop_mode,
        ),
        pn.layout.WidgetBox("### Focus", pn.Row(ii_focus_start_z, ii_focus_delta_z)),
        pn.layout.WidgetBox(
            "### Plate",
            pn.Row(ii_plate_x, ii_plate_y),
            pn.Row(ii_plate_row_count, ii_plate_col_count),
        ),
        pn.layout.WidgetBox(
            "### Lights",
            pn.Row(pn.pane.Str("TOP", width=20), eis_lights_top_brightness),
        ),
    ],
)
crd_move = pn.Column(
    objects=[
        bt_home,
        pn.Row(bt_qr_code, bt_check_corners),
        bt_check_discs,
        pn.Row(bt_lights_toggle, bt_lights_cycle),
        pg_progress,
    ],
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
    controller.center_on_qr_code(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
    )


def on_check_corners(event):
    controller.check_corners()


def on_launch_acquisition(event):
    controller.launch_acquisition(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
        center_on_leaf=LO_CENTER_OL in cbg_launch_options.value,
    )


def on_cancel_request(event):
    if controller.status == ELStatus.JOB_IN_PROGRESS:
        controller.status = ELStatus.STOP_REQUESTED


def on_check_disc_positions(event):
    controller.check_discs_positions(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
    )


def on_toggle_lights(event):
    if controller.top_lights.mean == 0:
        bt_lights_toggle.icon = "bulb"
        bt_lights_toggle.button_type = "success"
        controller.shutter(True)
    else:
        bt_lights_toggle.icon = "bulb-off"
        bt_lights_toggle.button_type = "default"
        controller.shutter(False)


def on_cycle_lights(event):
    controller.cycle_lights()


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
bt_lights_toggle.on_click(on_toggle_lights)
bt_lights_cycle.on_click(on_cycle_lights)
bt_launch_acquisition.on_click(on_launch_acquisition)
bt_check_discs.on_click(on_check_disc_positions)


# MARK: Dependables
@pn.depends(sel_sensor_modes.param.value, watch=True)
def on_sensor_mode_changed(sensor_mode):
    controller.set_sensor_mode(sensor_mode)


@pn.depends(eis_lights_top_brightness.param.value, watch=True)
def on_top_brightness_changed(brightness):
    controller.top_lights.brightness = brightness


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


@pn.depends(sel_lights_cycle.param.value, watch=True)
def on_light_cycle_changed(light_cycle):
    controller.light_cycles = light_cycle


# MARK: UI
def ui_sidebar():
    return pn.layout.Accordion(
        ("Camera position", pn.Column(plt_position, pg_progress)),
        ("Focus", plt_focus),
        ("Preview options", crd_preview_options),
        ("Configure", crd_configure),
        ("Control printer", crd_move),
        ("Camera configuration", json_camera_config),
        ("Camera control", json_camera_controls),
        active=[0],
    )


def ui_main():
    return pn.Column(
        pn.layout.FlexBox(
            pn.Row(sel_printer, bt_connect_printer),
            pn.Row(bt_idle, bt_park),
            pn.Row(bt_move_to, sel_position),
            pn.Row(bt_launch_acquisition, bt_stop_acquisition),
        ),
        pn.layout.Accordion(
            ("Live preview", video_pane),
            ("Still preview", still_pane),
            active=[0],
            sizing_mode="stretch_height",
        ),
        crd_log,
    )


def ui_show():
    controller.update_preview = on_update_preview
    controller.update_still = on_update_still
    controller.update_focus_plot = on_update_focus_plot
    controller.update_position_plot = on_update_position_plot
    controller.update_positions = on_update_positions
    controller.update_progress = on_progress_updated
    controller.camera.post_callback = post_callback
    controller.start()
    sidebar = ui_sidebar()
    sidebar.width = SIDE_BAR_WIDTH
    return pn.Row(sidebar, ui_main())
