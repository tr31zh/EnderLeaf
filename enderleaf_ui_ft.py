import os
from datetime import datetime as dt
from pathlib import Path
import io
from dataclasses import field
import base64
import logging
import psutil

import cv2

import flet as ft

from enderleaf_ctrl import (
    EnderLeafController,
    ELStatus,
    list_ports,
    default_printer_port,
)

BTN_HEIGHT = 50
PATH_LOG = Path(__file__).resolve().parent.parent.joinpath("logs")


class UiLogHandler(logging.Handler):
    def __init__(self, target):
        super().__init__()
        self.target = target
        self._logs = []

    def emit(self, record):
        match record.levelno:
            case 10:
                color = ft.Colors.GREY
            case 20:
                color = None
            case 30:
                color = ft.Colors.YELLOW
            case 40:
                color = ft.Colors.ORANGE
            case 50:
                color = ft.Colors.RED
        msg = self.format(record)
        self.target.controls.append(ft.Text(msg, color=color, expand=True))
        self.target.update()


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


log_view = ft.ListView(spacing=10, padding=20, auto_scroll=True, expand=True)


@ft.control
class EnderButton(ft.Button):
    expand: bool = True
    height: int = BTN_HEIGHT


async def on_update_progress(value, total):
    progress_view.value = (value + 1) / total
    progress_view.update()


def capture_to_bytes(image):
    _, im_enc = cv2.imencode(".png", image)
    im_b64 = base64.b64encode(im_enc)
    return im_b64.decode("utf-8")


controller = EnderLeafController()
controller.camera.color_step_size = 50
controller.start()

controller.update_progress = on_update_progress


image_view = ft.Image(
    src=capture_to_bytes(controller.capture_array()[0]),
    fit=ft.BoxFit.COVER,
    gapless_playback=True,
    expand=True,
)


def add_to_log(message):
    log_view.controls.append(ft.Text(message, expand=True))
    log_view.update()


def on_connect_printer(e):
    controller.connect_printer("port")


def on_home(e):
    controller.go_home()


def on_idle(e):
    controller.go_rest()


def on_park(e):
    controller.go_park()


def set_disable(is_disable: bool):
    for ctrl in [bt_connect_printer, bt_home, bt_park, bt_idle, bt_capture_image]:
        ctrl.disabled = is_disable
        ctrl.update()


async def on_launch_acquisition(e):
    if controller.status == ELStatus.JOB_IN_PROGRESS:
        return
    set_disable(True)
    controller.update_still = on_update_still
    controller.camera.color_step_size = 5
    try:
        await controller.launch_acquisition()
    finally:
        set_disable(False)
        controller.update_still = None
        controller.camera.color_step_size = 50


def on_stop_acquisition(e):
    if controller.status == ELStatus.JOB_IN_PROGRESS:
        controller.status = ELStatus.STOP_REQUESTED


def on_capture_array(e):
    image_view.src = capture_to_bytes(controller.capture_array()[0])
    image_view.update()


bt_connect_printer = EnderButton(
    content="Connect Printer", on_click=on_connect_printer, icon=ft.Icons.INSERT_LINK
)
dd_ports = ft.Dropdown(
    expand=True,
    value=str(default_printer_port()),
    options=[ft.DropdownOption(key=str(p), text=str(p)) for p in list_ports()],
    width=200,
)
bt_home = EnderButton(content="Home", on_click=on_home, icon=ft.Icons.HOME)
bt_idle = EnderButton(
    content="Rest",
    on_click=on_idle,
    icon=ft.Icons.AIRLINE_SEAT_INDIVIDUAL_SUITE_OUTLINED,
)
bt_park = EnderButton(content="Park", on_click=on_park, icon=ft.Icons.LOCAL_PARKING)
bt_launch = ft.FilledButton(
    content="Start",
    expand=True,
    on_click=on_launch_acquisition,
    icon=ft.Icons.PLAY_ARROW_SHARP,
    height=BTN_HEIGHT,
)
bt_stop = EnderButton(content="Stop", on_click=on_stop_acquisition, icon=ft.Icons.STOP)
bt_capture_image = EnderButton(
    content="Capture image", on_click=on_capture_array, icon=ft.Icons.IMAGE
)

progress_view = ft.ProgressBar(value=0, bar_height=10, expand=False, height=12)


async def on_update_still(image):
    image_view.src = capture_to_bytes(image)
    image_view.update()


async def main(page: ft.Page):
    page.title = "EnderLeaf"
    page.window.width = 1100
    page.window.height = 900
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.GREEN_100)
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.GREEN_100)
    # page.theme_mode = ft.ThemeMode.LIGHT
    page.update()
    if page.web is False:
        await page.window.center()
    page.update()

    page.add(
        ft.Column(
            [
                ft.Row(
                    controls=[
                        ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            intrinsic_width=True,
                            controls=[
                                bt_connect_printer,
                                dd_ports,
                                ft.Divider(),
                                ft.Row([bt_home, bt_idle, bt_park]),
                                ft.Divider(),
                                bt_capture_image,
                                ft.Divider(),
                                ft.Text("Acquisition Job"),
                                ft.Row([bt_launch, bt_stop]),
                            ],
                        ),
                        image_view,
                    ],
                    spacing=4,
                ),
                progress_view,
                ft.Tabs(
                    length=4,
                    expand=True,
                    content=ft.Column(
                        expand=True,
                        controls=[
                            ft.TabBar(
                                tabs=[
                                    ft.Tab(label="Log", icon=ft.Icons.MESSAGE),
                                    ft.Tab(label="Settings", icon=ft.Icons.SETTINGS),
                                    ft.Tab(label="Feedback", icon=ft.Icons.SCATTER_PLOT),
                                    ft.Tab(
                                        label="Manual control", icon=ft.Icons.FRONT_HAND
                                    ),
                                ],
                                scrollable=False,
                                height=20,
                            ),
                            ft.TabBarView(
                                expand=True,
                                controls=[
                                    log_view,
                                    ft.Container(
                                        alignment=ft.Alignment.CENTER,
                                        content=ft.Text("Settings content"),
                                    ),
                                    ft.Container(
                                        alignment=ft.Alignment.CENTER,
                                        content=ft.Text("Feedback content"),
                                    ),
                                    ft.Container(
                                        alignment=ft.Alignment.CENTER,
                                        content=ft.Text("Manual control"),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ),
            ],
            expand=True,
            spacing=4,
        )
    )

    log_file_handler = logging.FileHandler(
        Path(".").joinpath("log", f"enderleaf_{dt.now().strftime('%Y_%m_%d')}.log"),
        mode="a",
        delay=True,
    )
    log_file_handler.addFilter(MemoryFilter())

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s | %(mem_data)s | %(name)s | %(levelname)s] - %(message)s",
        handlers=[log_file_handler, UiLogHandler(target=log_view)],
    )

    logger = logger = logging.getLogger(__name__)
    logger.info("")
    logger.info("==== Starting session ====")
    logger.info("")
    logger.debug("debug")
    logger.info("info")
    logger.warning("warning")
    logger.error("error")
    logger.critical("critical")


ft.run(main=main)
