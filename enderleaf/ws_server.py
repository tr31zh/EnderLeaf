import io
from pathlib import Path
import os
from datetime import datetime as dt
import psutil
import asyncio
import websockets
import json
import base64
import logging
import random
import sys

import numpy as np
from PIL import Image
import cv2

import matplotlib.pyplot as plt

import flet as ft

ROOT_FOLDER = Path(__file__).parent.parent
sys.path.append(str(ROOT_FOLDER))

from enderleaf.const import DEFAULT_DATETIME_FORMAT
from enderleaf.enums import MsgType, LogLevel, ControllerCommands, NodeViewOption
from enderleaf.socket_message import SocketMessage
from enderleaf.image import encode_image

NUM_NODES = 1
PORTS = [i + 8760 for i in range(NUM_NODES)]
# NODES = [f"ws://localhost:{p}" for p in PORTS]
NODES = [f"ws://147.100.144.150:{p}" for p in PORTS]
BTN_HEIGHT = 40
PATH_LOG = Path(__file__).resolve().parent.parent.joinpath("logs")
NODE_UI_DEFAULT_WIDTH = 600

COLOUR_ACCENT = ft.Colors.GREEN_100
FULLSCREEN_COLOUR_ACCENT = ft.Colors.BLUE_100
COLOUR_BACKGROUND = ft.Colors.SURFACE_CONTAINER_HIGH
COLOUR_DETAIL = ft.Colors.GREY_600


completion_events = {uri: asyncio.Event() for uri in NODES}


# MARK: Log
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


log_view = ft.ListView(
    spacing=10, padding=20, auto_scroll=True, expand=1, visible=False
)


def on_change_log_visibility(e: ft.Event[ft.Checkbox]):
    log_view.visible = e.control.value is True
    log_view.update()


chk_show_log = ft.Checkbox(label="Show log", value=False, expand=True)


def level_to_color(level: LogLevel | int):
    if isinstance(level, int):
        level = LogLevel.int_to_log_level(level)
    match level:
        case LogLevel.INFO:
            return None
        case LogLevel.WARNING:
            return ft.Colors.YELLOW
        case LogLevel.EXCEPTION:
            return ft.Colors.ORANGE_400
        case LogLevel.ERROR:
            return ft.Colors.ORANGE_800
        case LogLevel.CRITICAL:
            return ft.Colors.RED
        case _:
            return None


def log(level, message: str, node_ip: str | None = None):
    msg = f"{dt.now().strftime(DEFAULT_DATETIME_FORMAT)} | "
    if node_ip is not None:
        msg = f"{msg} [{node_ip}] "
    show_log = False
    match level:
        case LogLevel.INFO:
            msg += "ℹ️ "
            logger.info(msg)
        case LogLevel.WARNING:
            msg += "⚠️ "
            logger.warning(msg)
        case LogLevel.EXCEPTION:
            msg += "🤬 "
            logger.exception(msg)
            show_log = True
        case LogLevel.ERROR:
            msg += "⛔️ "
            logger.error(msg)
            show_log = True
        case LogLevel.CRITICAL:
            msg += "☢️ "
            logger.critical(msg)
            show_log = True
        case _:
            msg += "⁉️ "
            logger.critical(msg)
            show_log = True
    msg = f"{msg}{message}"
    log_view.controls.append(
        ft.Text(msg, color=level_to_color(level=level), expand=True)
    )
    if show_log is True and chk_show_log.value is False:
        chk_show_log.value = True
        log_view.visible = True
        chk_show_log.update()
    log_view.update()


log_file_handler = logging.FileHandler(
    ROOT_FOLDER.joinpath(
        "logs", f"enderleaf_{dt.now().strftime('%Y_%m_%d_%H%M%S')}.log"
    ),
    mode="a",
    delay=True,
)
log_file_handler.addFilter(MemoryFilter())

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s | %(mem_data)s | %(name)s | %(levelname)s] - %(message)s",
    handlers=[log_file_handler],
)

logger = logger = logging.getLogger(__name__)
logger.info("")
logger.info("==== Starting session ====")
logger.info("")


def generate_frame(width=1280, height=720) -> bytes:
    """Generates a random RGB image and returns it as JPEG bytes."""
    img_bytes = io.BytesIO()
    frame = np.full(
        shape=(height, width, 3),
        dtype=np.uint8,
        fill_value=(
            random.randint(0, 255),
            random.randint(0, 255),
            random.randint(0, 255),
        ),
    )
    _, im_enc = cv2.imencode(".png", frame)
    im_b64 = base64.b64encode(im_enc)
    return im_b64.decode("utf-8")


def get_hist_plot():
    x = 4 + np.random.normal(0, 1.5, 200)
    fig, ax = plt.subplots()
    ax.hist(x, bins=8, linewidth=0.5, edgecolor="white")
    ax.set(
        xlim=(0, 8), xticks=np.arange(1, 8), ylim=(0, 56), yticks=np.linspace(0, 56, 9)
    )
    return fig


def get_hex_plot():
    np.random.seed(1)
    x = np.random.randn(5000)
    y = 1.2 * x + np.random.randn(5000) / 3
    fig, ax = plt.subplots()
    ax.hexbin(x, y, gridsize=20)
    ax.set(xlim=(-2, 2), ylim=(-3, 3))
    return fig


def plot_to_image(fig, dpi=300):
    io_buf = io.BytesIO()
    fig.savefig(io_buf, format="raw")
    io_buf.seek(0)
    img_arr = np.reshape(
        np.frombuffer(io_buf.getvalue(), dtype=np.uint8),
        shape=(int(fig.bbox.bounds[3]), int(fig.bbox.bounds[2]), -1),
    )
    io_buf.close()
    return img_arr


class NodeUi:
    def __init__(self, node_ip, max_width: int = NODE_UI_DEFAULT_WIDTH):
        self.node_ip = node_ip
        self.title = ft.Checkbox(
            label=node_ip,
            value=True,
            label_style=ft.TextStyle(size=24, bgcolor=COLOUR_BACKGROUND),
            on_change=self.on_change_enabled,
        )
        self.image = ft.Image(
            src=generate_frame(),
            fit=ft.BoxFit.CONTAIN,
            gapless_playback=True,
            expand=True,
        )
        self.focus_plot = ft.Image(
            src=generate_frame(),
            fit=ft.BoxFit.CONTAIN,
            gapless_playback=True,
            expand=True,
        )

        self.position_plot = ft.Image(
            src=generate_frame(),
            fit=ft.BoxFit.CONTAIN,
            gapless_playback=True,
            expand=True,
        )
        self.tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label=NodeViewOption.IMAGE.value),
                ft.Tab(label=NodeViewOption.PLOT_POSITION.value),
                ft.Tab(label=NodeViewOption.PLOT_FOCUS.value),
            ],
            visible=False,
            scrollable=False,
        )
        self.tab_bar_view = ft.TabBarView(
            expand=True,
            controls=[self.image, self.position_plot, self.focus_plot],
        )
        self.main_view = ft.Tabs(
            selected_index=0,
            length=3,
            expand=True,
            content=ft.Column(expand=True, controls=[self.tab_bar, self.tab_bar_view]),
        )
        self.main_view.animation_duration = ft.Duration(10)
        self.expand_toggle = ft.IconButton(
            icon=ft.Icons.FULLSCREEN,
            icon_color=ft.Colors.PRIMARY,
            icon_size=36,
            padding=ft.Padding.all(0),
        )
        self.expand_toggle.node_ip = self.node_ip
        self.progress = ft.ProgressBar(value=0, bar_height=10, expand=False, height=12)
        self.alert = ft.Text(value="Ready", expand=False)
        self.max_width = max_width
        self._fullscreen = False

        self._ui = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[self.title, self.expand_toggle],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self.main_view,
                    self.progress,
                    self.alert,
                ],
                alignment=ft.Alignment.TOP_CENTER,
            ),
            bgcolor=COLOUR_BACKGROUND,
            border=ft.Border.all(2, COLOUR_DETAIL),
            border_radius=10,
            padding=4,
            expand=1,
        )

        self.max_level = 0

    @property
    def ui(self):
        return self._ui

    @property
    def fullscreen(self):
        return self._fullscreen

    @fullscreen.setter
    def fullscreen(self, value):
        if value != self._fullscreen:
            self._fullscreen = not self._fullscreen
            self.title.visible = not self._fullscreen
            self.expand_toggle.visible = not self._fullscreen
            self.title.update()
            self.expand_toggle.update()

    async def switch_main_view(self, new_view: NodeViewOption):
        match new_view:
            case NodeViewOption.IMAGE:
                await self.main_view.move_to(0)
            case NodeViewOption.PLOT_POSITION:
                await self.main_view.move_to(1)
            case NodeViewOption.PLOT_FOCUS:
                await self.main_view.move_to(2)
            case _:
                raise NotImplementedError(f"Unknown node view mode: {e.control.value}")
        self.main_view.update()

    def reset(self):
        self.max_level = 0
        self.update_alert(log_level=LogLevel.INFO, message="Ready")

    def on_change_enabled(self, e: ft.Event[ft.Checkbox]):
        for control in [self.main_view, self.progress, self.alert, self.expand_toggle]:
            control.visible = e.control.value is True
            control.update()
        self.ui.bgcolor = (
            COLOUR_BACKGROUND if e.control.value is True else ft.Colors.SURFACE_DIM
        )
        self.ui.update()

    def update_size(self, new_size):
        self.max_width = new_size
        self._ui.width = new_size
        for control in [self.title, self.image, self.progress, self.alert]:
            control.update()

    def update_alert(self, log_level: LogLevel, message: str):
        self.max_level = max(self.max_level, LogLevel.log_level_to_int(log_level))
        self.alert.value = message
        border_color = level_to_color(
            max(self.max_level, LogLevel.log_level_to_int(log_level))
        )
        self.ui.border = ft.Border.all(
            2, border_color if border_color is not None else COLOUR_DETAIL
        )
        self.ui.update()
        self.alert.color = level_to_color(log_level)
        self.alert.update()

    def update_progress(self, step: int, total: int):
        self.progress.value = step / total
        self.progress.update()

    def update_image(self, image: str):
        self.image.src = image
        self.image.update()

    def update_position_plot(self, image: str):
        self.position_plot.src = image
        self.position_plot.update()

    def update_focus_plot(self, image: str):
        self.focus_plot.src = image
        self.focus_plot.update()


node_uis = {k: NodeUi(k, max_width=NODE_UI_DEFAULT_WIDTH) for k in NODES}


def fullscreen_node() -> NodeUi | None:
    for ui in node_uis.values():
        if ui.fullscreen is True:
            return ui
    else:
        return None


@ft.control
class EnderButton(ft.Button):
    expand: bool = True
    height: int = BTN_HEIGHT


async def listen_for_updates(websocket, node_ip):
    try:
        async for message in websocket:
            soccket_message = SocketMessage.load(message)
            node_ui = node_uis[node_ip]
            match soccket_message.type:
                case MsgType.MESSAGE:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=LogLevel.INFO,
                        message=soccket_message.message,
                        node_ip=node_ip,
                    )
                case MsgType.PROGRESS:
                    node_ui.update_progress(
                        step=soccket_message.step, total=soccket_message.total
                    )
                case MsgType.IMAGE:
                    node_ui.update_image(image=soccket_message.image)
                case MsgType.POSITION_PLOT:
                    node_ui.update_position_plot(image=soccket_message.image)
                case MsgType.FOCUS_PLOT:
                    node_ui.update_focus_plot(image=soccket_message.image)
                case MsgType.RESULT:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=soccket_message.level,
                        message=soccket_message.message,
                        node_ip=node_ip,
                    )
                    completion_events[node_ip].set()
                case MsgType.PROBLEM:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=soccket_message.level,
                        message=soccket_message.message,
                        node_ip=node_ip,
                    )
                    if soccket_message.level in [LogLevel.ERROR, LogLevel.CRITICAL]:
                        completion_events[node_ip].set()
                case _:
                    log(
                        level=LogLevel.CRITICAL,
                        message=f"Unknown message type '{str(soccket_message.type)}'",
                        node_ip=node_ip,
                    )
                    node_ui.update_alert(
                        log_level=soccket_message.level,
                        message=f"Unknown message type '{str(soccket_message.type)}'",
                    )
                    completion_events[node_ip].set()
    except websockets.exceptions.ConnectionClosed:
        log(
            level=LogLevel.ERROR,
            message=f"[{node_ip}] Connection closed unexpectedly",
        )
        completion_events[node_ip].set()
    except Exception as e:
        log(
            level=LogLevel.ERROR,
            message=f"[{node_ip}] Exception while listening for update {str(soccket_message)}: {str(e)}",
        )


async def execute_on_node(uri, func_name, **kwargs):
    try:
        async with websockets.connect(uri) as websocket:
            asyncio.create_task(listen_for_updates(websocket, uri))
            message = {"func": func_name, "kwargs": kwargs}
            await websocket.send(json.dumps(message))
            await asyncio.wait_for(completion_events[uri].wait(), timeout=None)
    except asyncio.TimeoutError as e:
        log(level=LogLevel.ERROR, message="Timeout waiting for completion", node_ip=uri)
        node_uis[uri].update_alert(
            log_level=LogLevel.EXCEPTION,
            message=f"Time out error for '{func_name}': {e}",
        )
    except Exception as e:
        log(
            level=LogLevel.ERROR,
            message=f"Connection/Execution failed: {e}",
            node_ip=uri,
        )
        node_uis[uri].update_alert(
            log_level=LogLevel.ERROR,
            message=f"Connection/Execution failed for '{func_name}': {e}",
        )


async def run_task(task_name: ControllerCommands = ControllerCommands.START):
    fs_node = fullscreen_node()
    log(
        level=LogLevel.INFO,
        message=(
            f"Initiating distributed execution for task: '{task_name}'"
            if fs_node is None
            else f"Initializing task for single node {fs_node.node_ip}"
        ),
    )
    await set_disable(True, is_stop_enabled=task_name == ControllerCommands.START)
    tasks = []
    kwargs = {}
    if task_name == ControllerCommands.MOVE_TO:
        kwargs["position"] = int(dd_position.value)
    if fs_node is None:
        for uri in NODES:
            node_ui = node_uis[uri]
            if task_name == "start":
                node_ui.reset()
            if node_ui.title.value is False:
                continue
            tasks.append(
                asyncio.create_task(
                    execute_on_node(node_ui.node_ip, task_name, **kwargs)
                )
            )
    else:
        if task_name == ControllerCommands.START:
            fs_node.reset()
        tasks.append(
            asyncio.create_task(execute_on_node(fs_node.node_ip, task_name, **kwargs))
        )
    await asyncio.gather(*tasks)
    log(
        level=LogLevel.INFO,
        message=(
            f"Task '{task_name}', all nodes completed."
            if fs_node is None
            else f"Task '{task_name}' completed for single node {fs_node.node_ip}"
        ),
    )
    for event in completion_events.values():
        event.clear()
    await set_disable(False, False)


async def set_disable(is_disable: bool, is_stop_enabled: bool):
    for ctrl in [
        bt_connect_printer,
        bt_ping,
        bt_home,
        bt_park,
        bt_idle,
        bt_launch,
        bt_move_to,
        bt_capture_still,
        bt_close,
        bt_auto,
        bt_far,
    ]:
        ctrl.disabled = is_disable
        ctrl.update()
    for uri in NODES:
        node_uis[uri].title.disabled = is_disable
        node_uis[uri].title.update()
    bt_stop.disabled = not is_stop_enabled
    bt_stop.update()


async def on_run_task(e: ft.Event[ft.Button]):
    await run_task(e.control.content.lower().replace(" ", "_"))


def on_capture_array(e):
    pass


async def on_nodes_view_changed(e: ft.Event[ft.Dropdown]):
    for node_ui in node_uis.values():
        await node_ui.switch_main_view(e.control.value)


bt_connect_printer = ft.FilledButton(
    content="Connect Printer",
    on_click=on_run_task,
    icon=ft.Icons.INSERT_LINK,
    expand=True,
    height=BTN_HEIGHT,
)
bt_ping = EnderButton(content="Ping", on_click=on_run_task, icon=ft.Icons.NETWORK_PING)
bt_capture_still = EnderButton(
    content="Capture still", on_click=on_run_task, icon=ft.Icons.ADD_A_PHOTO_SHARP
)
bt_home = EnderButton(content="Go Home", on_click=on_run_task, icon=ft.Icons.HOME)
bt_idle = EnderButton(
    content="Go Idle",
    on_click=on_run_task,
    icon=ft.Icons.AIRLINE_SEAT_INDIVIDUAL_SUITE_OUTLINED,
)
bt_park = EnderButton(
    content="Go Park", on_click=on_run_task, icon=ft.Icons.LOCAL_PARKING
)
bt_launch = ft.FilledButton(
    content="Start",
    expand=True,
    on_click=on_run_task,
    icon=ft.Icons.PLAY_ARROW_SHARP,
    height=BTN_HEIGHT,
)
bt_stop = EnderButton(
    content="Stop", on_click=on_run_task, icon=ft.Icons.STOP, disabled=True
)
bt_move_to = EnderButton(
    content="Move to",
    on_click=on_run_task,
    icon=ft.Icons.ARROW_CIRCLE_RIGHT_OUTLINED,
)
dd_position = ft.Dropdown(
    options=[ft.DropdownOption(key=i, content=ft.Text(i)) for i in range(78)]
)

dd_view = ft.Dropdown(
    options=[
        ft.DropdownOption(key=nvo.value, text=nvo.value)
        for nvo in [
            NodeViewOption.IMAGE,
            NodeViewOption.PLOT_POSITION,
            NodeViewOption.PLOT_FOCUS,
        ]
    ],
    expand=True,
    value=NodeViewOption.IMAGE.value,
    on_select=on_nodes_view_changed,
)


bt_close = EnderButton(content="Close", on_click=on_run_task)
bt_auto = EnderButton(content="Auto", on_click=on_run_task)
bt_far = EnderButton(content="Far", on_click=on_run_task)

gv_nodes = ft.GridView(
    spacing=10,
    max_extent=NODE_UI_DEFAULT_WIDTH,
    scroll=ft.ScrollMode.ALWAYS,
    expand=True,
    controls=[nu.ui for nu in node_uis.values()],
    horizontal=True,
    child_aspect_ratio=0.9,
)

side_buttons = ft.Column(
    intrinsic_width=True,
    controls=[
        ft.Row(bt_connect_printer),
        ft.Row(bt_ping),
        ft.Row(bt_home),
        ft.Row(bt_idle),
        ft.Row(bt_park),
        ft.Row(bt_capture_still),
        ft.Row(controls=[bt_move_to, dd_position]),
        ft.Row(controls=[ft.Text("View"), dd_view]),
        ft.Row(controls=[chk_show_log]),
        ft.Divider(),
        ft.Text("Request Focus", size=16),
        ft.Row([bt_close, bt_auto, bt_far]),
        ft.Divider(),
        ft.Text("Acquisition Job", size=16),
        ft.Row([bt_launch, bt_stop]),
    ],
    auto_scroll=True,
)


def global_page() -> ft.View:
    return ft.View(
        route="/",
        controls=[
            ft.Column(
                [
                    ft.Row(
                        controls=[side_buttons, ft.VerticalDivider(), gv_nodes],
                        expand=3,
                    ),
                    ft.Divider(),
                    log_view,
                ],
                expand=True,
                spacing=4,
            )
        ],
    )


def fullscren_page(route) -> ft.View:
    node_ip = route[1:]
    node = node_uis[node_ip]
    node.fullscreen = True
    return ft.View(
        route=route,
        controls=[
            ft.Column(
                [
                    ft.Row(
                        controls=[
                            side_buttons,
                            ft.VerticalDivider(),
                            ft.Column(
                                controls=[
                                    ft.AppBar(
                                        title=ft.Text(node_ip),
                                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                                    ),
                                    node.ui,
                                ],
                                expand=True,
                            ),
                        ],
                        expand=3,
                    ),
                    ft.Divider(),
                    log_view,
                ],
                expand=True,
                spacing=4,
            )
        ],
    )


async def main(page: ft.Page):
    page.title = "EnderLeaf"
    page.window.width = 1200
    page.window.height = 1000
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.theme = ft.Theme(
        page_transitions=ft.PageTransitionsTheme(
            android=ft.PageTransitionTheme.NONE,
            ios=ft.PageTransitionTheme.NONE,
            linux=ft.PageTransitionTheme.NONE,
            macos=ft.PageTransitionTheme.NONE,
            windows=ft.PageTransitionTheme.NONE,
        ),
        color_scheme_seed=COLOUR_ACCENT,
    )
    page.dark_theme = ft.Theme(
        page_transitions=ft.PageTransitionsTheme(
            android=ft.PageTransitionTheme.NONE,
            ios=ft.PageTransitionTheme.NONE,
            linux=ft.PageTransitionTheme.NONE,
            macos=ft.PageTransitionTheme.NONE,
            windows=ft.PageTransitionTheme.NONE,
        ),
        color_scheme_seed=COLOUR_ACCENT,
    )
    # page.theme_mode = ft.ThemeMode.LIGHT
    page.update()
    if page.web is False:
        await page.window.center()
    page.update()

    async def go_fullscreen(e: ft.Event[ft.IconButton]):
        log(level=LogLevel.INFO, message=str(e.control.node_ip))
        node_ui = node_uis[e.control.node_ip]
        # if node_ui.fullscreen is True:
        #     await page.push_route("/")
        await page.push_route(f"/{e.control.node_ip}")

    for nu in node_uis.values():
        nu.expand_toggle.on_click = go_fullscreen

    def route_change():
        try:
            log(level=LogLevel.INFO, message=f"Route changed -> {page.route}")
        except:
            pass
        page.views.clear()
        page.views.append(global_page())
        if len(page.route) > 1:
            page.views.append(fullscren_page(page.route))
            page.dark_theme = ft.Theme(color_scheme_seed=FULLSCREEN_COLOUR_ACCENT)
        else:
            for nu in node_uis.values():
                nu.fullscreen = False
            page.dark_theme = ft.Theme(color_scheme_seed=COLOUR_ACCENT)

        page.update()

    async def view_pop(e):
        if e.view is not None:
            log(level=LogLevel.INFO, message=f"View pop: {e.view}")
            page.views.remove(e.view)
            top_view = page.views[-1]
            await page.push_route(top_view.route)

    page.on_route_change = route_change
    page.on_view_pop = view_pop

    chk_show_log.on_change = on_change_log_visibility

    route_change()

    log(LogLevel.INFO, "Started")

    # log(LogLevel.INFO, "INFO")
    # log(LogLevel.WARNING, "WARNING")
    # log(LogLevel.EXCEPTION, "EXCEPTION")
    # log(LogLevel.ERROR, "ERROR")
    # log(LogLevel.CRITICAL, "CRITICAL")


ft.run(main=main)
