# https://tabler.io/icons

import os
import logging
import psutil
from pathlib import Path
from functools import wraps
from datetime import datetime as dt


import panel as pn

from enderscope.serial import list_ports, default_printer_port
from enderscope.scan_patterns import plot_path_status
from enderscope.bed import bed

from enderleaf.enums import CropMode, ELStatus, LightsCycle
from enderleaf.draw import plot_focus_plt
from enderleaf.tools import ensure_folder
from enderleaf.image import to_pil, safe_pil_resize
from enderleaf.enderleaf_ctrl import EnderLeafController

pn.extension("jsoneditor", "ipywidgets")

mkd_log = pn.pane.Markdown(object="", sizing_mode="scale_width")
crd_log = pn.layout.Card(mkd_log, title="Log", collapsed=True)


class PanelLogHandler(logging.Handler):
    """Custom logging handler that mirrors the last log records into a Panel markdown pane

    Attributes:
        target (pn.pane.Markdown): Markdown pane where the log messages are displayed.
        _logs (list): Buffer holding the most recent formatted log messages.
    """

    def __init__(self, target, card):
        """Initialize the handler with the markdown target and its card

        Args:
            target (pn.pane.Markdown): Markdown pane updated with the log messages.
            card (pn.layout.Card): Card whose title reflects the latest log message.
        """
        super().__init__()
        self.target = target
        self._logs = []

    def emit(self, record):
        """Emit a log record by refreshing the markdown pane and card title

        Args:
            record (logging.LogRecord): The log record to format and display.
        """
        msg = self.format(record)
        self._logs.insert(0, msg)
        if len(self._logs) > 10:
            self._logs = self._logs[:10]
        crd_log.title = msg
        self.target.object = "- " + ("\n\n - ").join(self._logs)


class MemoryFilter(logging.Filter):
    """Logging filter that enriches each record with the current process memory usage

    Attributes:
        last_process_mem (float): Process memory percentage recorded on the previous call.
    """

    last_process_mem = 0

    def filter(self, record):
        """Attach the process memory usage and its trend to the log record

        Args:
            record (logging.LogRecord): The log record to enrich with memory data.

        Returns:
            bool: Always True, keeping every record.
        """
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
    handlers=[log_file_handler],
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
    """Decorator that guards a method against concurrent re-entrancy

    While a guarded call is in progress, further calls return immediately without
    re-entering the method.

    Args:
        method (callable): The method to guard.

    Returns:
        callable: The guarded wrapper.
    """
    @wraps(method)
    def _impl(*method_args, **method_kwargs):
        """Guarded implementation that runs the method unless already in progress

        Returns:
            Any: The method's return value when executed, or None when a call was
                skipped because the method is already running.
        """
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
still_pane = pn.pane.Image(sizing_mode="scale_width")
plt_position = pn.pane.Matplotlib(
    object=plot_path_status(title=""),
    sizing_mode="scale_width",
    height=330,
    align="center",
)
plt_focus = pn.pane.Matplotlib(sizing_mode="stretch_width", height=330, align="center")
sel_position = pn.widgets.Select(
    name="Position", options=[i + 1 for i in range(81)], width=100
)

json_camera_config = pn.pane.JSON(
    object=None, name="Camera configuration", depth=-1, sizing_mode="stretch_width"
)

pg_progress = pn.indicators.Progress(value=0, sizing_mode="stretch_width")


async def on_update_still(image):
    """Update the still image pane with a freshly captured image

    Args:
        image (np.ndarray): Source image to display.
    """
    still_pane.object = safe_pil_resize(to_pil(image), new_width=1024, new_height=768)


async def on_message_received(message):
    """Append a received message to the log markdown pane

    Args:
        message (str): The message text to append.
    """
    mkd_log.object += "- " +  message + "\n\n"


async def on_update_position_plot(new_plot):
    """Update the position plot pane with a new figure

    Args:
        new_plot (matplotlib.figure.Figure): Figure to display.
    """
    plt_position.object = new_plot


async def on_update_focus_plot(fig):
    """Update the focus plot pane with a new figure

    Args:
        fig (matplotlib.figure.Figure): Figure to display.
    """
    plt_focus.object = fig


def post_callback(request):
    """Update the camera configuration pane with the request metadata

    Args:
        request (Any): Camera request exposing ``get_metadata()``.
    """
    global _working
    # Guard against re-entrant camera requests (mirrors the `working` decorator)
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
    """Update the progress indicator as a job advances

    Args:
        current (int): Index of the current step.
        total (int): Total number of steps.
    """
    if pg_progress.max != total:
        pg_progress.max = total
    pg_progress.value = current + 1


# MARK: Controller
controller = EnderLeafController()

# MARK: Widgets

# Camera configuration display
json_camera_controls = pn.widgets.JSONEditor(
    value=dict(sorted(controller.camera.camera_controls.items())),
    name="Camera controls",
    mode="view",
    sizing_mode="stretch_width",
)

# Focus
bt_focus = pn.widgets.Button(name="Focus", sizing_mode="stretch_width")
bt_focus_close = pn.widgets.Button(name="Focus close", sizing_mode="stretch_width")
bt_focus_far = pn.widgets.Button(name="Focus far", sizing_mode="stretch_width")

# Printer control
sel_printer = pn.widgets.Select(
    name="Select serial connection",
    options=[str(p) for p in list_ports()],
    value=str(default_printer_port()),
)
bt_connect_printer = pn.widgets.Button(
    name="Connect to printer", icon="plug-connected", icon_size="2em"
)
bt_ping = pn.widgets.Button(
    name="Ping", icon="ping-pong", icon_size="2em"
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
bt_check_corners = pn.widgets.Button(  # verify the plate corners are reachable
    name="Check corners",
    icon="border-corners",
    icon_size="2em",
    sizing_mode="stretch_width",
)
bt_lights_toggle = pn.widgets.Button(
    # Toggles the LED ring on/off; icon and color reflect the current state
    name="LED ring",
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
    # Crop margins (px) applied to each acquired image; initialized from the controller
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
    # Plate grid (mm) and disc grid layout; initialized from the controller
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
    # Launch / stop the multi-disc acquisition job
    name="Launch job", icon="player-play", icon_size="2em"
)
bt_stop_acquisition = pn.widgets.Button(
    name="Stop", icon="circle-dashed-x", icon_size="2em", button_type="danger"
)
cbg_launch_options = pn.widgets.CheckBoxGroup(
    # Job launch options, forwarded to launch/check routines
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
    # LED ring brightness, synced back to the controller on change
    name="Brightness",
    start=0,
    end=1.0,
    step=0.05,
    value=controller.led_ring.brightness,
    sizing_mode="scale_width",
)
sel_lights_cycle = pn.widgets.MultiChoice(
    # Lighting configurations applied per disc during acquisition
    name="Acquisition lights cycle",
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
@working
def on_request_focus(event):
    """Trigger the autofocus cycle when the Focus button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    controller.autofocus_cycle()


@working
def on_request_focus_close(event):
    """Move the camera focus close when the Focus close button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    controller.set_focus_close()


@working
def on_request_focus_far(event):
    """Move the camera focus far when the Focus far button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    controller.set_focus_far()


async def on_connect_printer(event):
    """Connect to the printer using the selected serial port

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.connect_printer(sel_printer.value)


async def on_home(event):
    """Home the stage when the Home button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.go_home()


async def on_idle(event):
    """Move the stage to its rest position when the Wait button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.go_rest()


async def on_park(event):
    """Move the stage to its park position when the Park button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.go_park()


async def on_center_on_qr_code(event):
    """Center the camera on the QR code using the selected launch options

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.center_on_qr_code(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
    )


async def on_check_corners(event):
    """Check the plate corners when the Check corners button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.check_corners()


async def on_launch_acquisition(event):
    """Launch the acquisition job using the selected launch options

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.launch_acquisition(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
        center_on_leaf=LO_CENTER_OL in cbg_launch_options.value,
    )


async def on_cancel_request(event):
    """Request a stop of the in-progress job when the Stop button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    # Signal the controller to stop by flipping its status; the job loop checks it
    if controller.status == ELStatus.JOB_IN_PROGRESS:
        controller.status = ELStatus.STOP_REQUESTED


async def on_check_disc_positions(event):
    """Check the disc positions using the selected launch options

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.check_discs_positions(
        precise_focusing=LO_PRECISE_FOCUS in cbg_launch_options.value,
        switch_state=LO_SWITCH_STATE in cbg_launch_options.value,
    )


async def on_toggle_lights(event):
    """Toggle the LED ring on or off, updating the button appearance

    Args:
        event (Any): The click event passed by the button.
    """
    # Only toggle the shutter; the button icon/type mirror the resulting ring state
    if controller.led_ring.mean == 0:
        bt_lights_toggle.icon = "bulb"
        bt_lights_toggle.button_type = "success"
        await controller.shutter(True)
    else:
        bt_lights_toggle.icon = "bulb-off"
        bt_lights_toggle.button_type = "default"
        await controller.shutter(False)


async def on_cycle_lights(event):
    """Cycle to the next lighting pattern when the Cycle lights button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.cycle_lights()


async def on_move_to(event):
    """Move the stage to the selected position when the Move to button is clicked

    Args:
        event (Any): The click event passed by the button.
    """
    await controller.move_to(sel_position.value)


# MARK: Binds
# Wire each button to its handler
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
@pn.depends(eis_lights_top_brightness.param.value, watch=True)
def on_top_brightness_changed(brightness):
    """Update the LED ring brightness when the brightness slider changes

    Args:
        brightness (float): The new LED ring brightness.
    """
    controller.led_ring.brightness = brightness


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
    """Update the controller crop settings when any crop widget changes

    Args:
        cl (int): Left crop margin.
        cr (int): Right crop margin.
        ct (int): Top crop margin.
        cb (int): Bottom crop margin.
        cm (CropMode): The selected crop mode.
    """
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
    """Update the controller plate settings when any plate widget changes

    Args:
        x (int): Plate X size.
        y (int): Plate Y size.
        rc (int): Plate row count.
        cc (int): Plate column count.
        fs (int): Focus start Z.
        fd (int): Focus delta Z.
    """
    controller.set_plate(x, y, rc, cc, fs, fd)


@pn.depends(sel_lights_cycle.param.value, watch=True)
def on_light_cycle_changed(light_cycle):
    """Update the acquisition light cycles when the selection changes

    Args:
        light_cycle (list): The selected list of light cycles.
    """
    controller.light_cycles = light_cycle


# MARK: UI
def ui_sidebar():
    """Build the UI sidebar accordion with the configuration and control panels

    Returns:
        pn.layout.Accordion: The assembled sidebar.
    """
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
    """Build the main UI column with controls, preview, and logs

    Returns:
        pn.Column: The assembled main area.
    """
    return pn.Column(
        pn.layout.FlexBox(
            pn.Row(sel_printer, bt_connect_printer),
            pn.Row(bt_ping),
            pn.Row(bt_idle, bt_park),
            pn.Row(bt_move_to, sel_position),
            pn.Row(bt_launch_acquisition, bt_stop_acquisition),
        ),
        still_pane,
        crd_log,
    )


async def ui_show():
    """Wire the controller callbacks, start the controller, and assemble the full UI

    Returns:
        pn.Row: The assembled sidebar and main UI.
    """
    # Wire controller callbacks to the UI panes before starting
    controller.on_send_image = on_update_still
    controller.on_send_focus_plot = on_update_focus_plot
    controller.on_send_position_plot = on_update_position_plot
    controller.on_send_progress = on_progress_updated
    controller.on_send_message = on_message_received
    controller.camera.post_callback = post_callback
    await controller.start()
    await controller.capture_image()
    sidebar = ui_sidebar()
    sidebar.width = SIDE_BAR_WIDTH
    return pn.Row(sidebar, ui_main())
