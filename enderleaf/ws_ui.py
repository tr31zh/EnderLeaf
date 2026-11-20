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
from dataclasses import dataclass
import json
import socket
import paramiko
import platform
import traceback

import numpy as np
from PIL import Image
import cv2

import matplotlib.pyplot as plt

import flet as ft
import flet_datatable2 as fdt
import flet_spinkit as spins

ROOT_FOLDER = Path(__file__).parent.parent
sys.path.append(str(ROOT_FOLDER))
os.chdir(str(ROOT_FOLDER))

from enderleaf.const import (
    DEFAULT_DATETIME_FORMAT,
    PT_IMAGES_RECEIVED,
    PT_IMAGES_VALIDATED,
)
from enderleaf.enums import (
    MsgType,
    LogLevel,
    ControllerCommands,
    NodeViewOption,
    LaunchOptons,
    FileTransferStatus,
)
from enderleaf.socket_message import SocketMessage
from enderleaf.tools import (
    build_color_steps,
    ensure_folder,
    list_remote_files,
    list_remote_folders,
    atomic_move,
    validate_file_content,
)

# MARK: Node configuration
# Load the node addresses from the shared configuration file. When the file is
# missing or unreadable, fall back to an empty mapping so the local fallback
# branch below can take over.
# The json also contains the address of the main node which will receive images from all the other nodes
try:
    with open(
        str(Path(".").joinpath("addons").joinpath("conf").joinpath("addresses.json")),
        "r",
    ) as file:
        addresses = json.load(file)
except:
    addresses = {}


LOCAL_CLIENTS = False
if LOCAL_CLIENTS is True or not addresses:
    # Local/development mode: spawn a fixed number of nodes on sequential
    # localhost ports and treat this client as the main (coordinating) node.
    NUM_NODES = 4
    PORTS = [i + 8760 for i in range(NUM_NODES)]
    NODES = [f"ws://localhost:{p}" for p in PORTS]
else:
    # Distributed mode: resolve the host's own IP address and compare it with the
    # configured main node IP to decide whether this client is the main node.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("1.2.3.4", 1))
    NODES = [f"ws://{ip}:8765" for ip in addresses["nodes"]]

MAIN_NODE = platform.system().lower() == "darwin"

# MARK: UI configuration constants
# Per-node identity colors, identify timing, and shared visual styling values.
ID_COLORS = build_color_steps(step_count=len(NODES))
ID_TIME = 4
BORDER_RADIUS = 6
BTN_HEIGHT = 40
PATH_LOG = Path(__file__).resolve().parent.parent.joinpath("logs")
COLOUR_ACCENT = ft.Colors.GREEN_100
FULLSCREEN_COLOUR_ACCENT = ft.Colors.BLUE_100
COLOUR_BACKGROUND = ft.Colors.SURFACE_CONTAINER_HIGH
COLOUR_DETAIL = ft.Colors.GREY_600

# One completion event per node, used to synchronise task execution across the
# distributed nodes. Cleared after every run_task call.
completion_events = {uri: asyncio.Event() for uri in NODES}


# MARK: Log
class MemoryFilter(logging.Filter):
    """Logging filter that annotates each record with the process memory usage.

    A class-level attribute tracks the previous memory percentage so that each
    record can carry a trend indicator relative to the last logged record.

    Attributes:
        last_process_mem (float): Memory percentage captured for the previous
            record. Used to compute the up/down/flat trend arrow.
    """

    last_process_mem = 0

    def filter(self, record):
        process: psutil.Process = psutil.Process(os.getpid())
        pmp = process.memory_percent()
        # Determine the memory trend relative to the previously recorded sample.
        sign = (
            "⬆"
            if pmp > self.last_process_mem
            else "⬇" if pmp < self.last_process_mem else "="
        )
        record.mem_data = f"µ{sign} {pmp:02.2f}%"
        self.last_process_mem = pmp
        return True


log_view = ft.ListView(
    spacing=10,
    padding=20,
    auto_scroll=True,
    expand=1,
    visible=False,
    auto_scroll_animation=0,
)


def on_change_log_visibility(e: ft.Event[ft.Checkbox]):
    """Toggle the visibility of the log view based on the checkbox state.

    Args:
        e (ft.Event[ft.Checkbox]): Event emitted when the "Show log" checkbox
            value changes.
    """
    log_view.visible = e.control.value is True
    log_view.update()


chk_show_log = ft.Checkbox(label="Show log", value=False, expand=True)


def level_to_color(level: LogLevel | int):
    """Map a log level to a Flet color used for alert emphasis.

    Args:
        level (LogLevel | int): The log level to map, either a LogLevel enum
            member or a raw integer that is first converted to a LogLevel.

    Returns:
        ft.Color | None: A Flet color for warning-level-and-above entries, or
        None when no emphasis color is required.
    """
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


def log(level, message: str, node_address: str | None = None):
    """Route a message to both the Python logger and the on-screen log view.

    The message is logged through the configured logger at the given level and,
    is additionally appended to the live
    log view, for warning-level-and-above entries.
    If such an entry appears while the log view is hidden, the view
    and its toggle checkbox are revealed automatically.

    Args:
        level (LogLevel): The severity level of the message.
        message (str): The message text to log and display.
        node_address (str | None, optional): Optional node address that, when
            provided, is prefixed to the message. Defaults to None.
    """
    msg = f"{dt.now().strftime(DEFAULT_DATETIME_FORMAT)} | "
    if node_address is not None:
        msg = f"{msg} [{node_address}] "
    show_log = False
    # First dispatch: emit through the Python logger at the matching level.
    match level:
        case LogLevel.DEBUG:
            logger.debug(f"[{node_address}] {message}")
        case LogLevel.INFO:
            logger.info(f"[{node_address}] {message}")
        case LogLevel.WARNING:
            logger.warning(f"[{node_address}] {message}")
        case LogLevel.EXCEPTION:
            logger.exception(f"[{node_address}] {message}")
            show_log = True
        case LogLevel.ERROR:
            logger.error(f"[{node_address}] {message}")
            show_log = True
        case LogLevel.CRITICAL:
            logger.critical(f"[{node_address}] {message}")
            show_log = True
        case _:
            logger.critical(f"[{node_address}] {message}")
            show_log = True
    # Second dispatch: pick the on-screen icon that mirrors the log level.
    match level:
        case LogLevel.DEBUG:
            msg += "🐞 "
        case LogLevel.INFO:
            msg += "ℹ️ "
        case LogLevel.WARNING:
            msg += "⚠️ "
        case LogLevel.EXCEPTION:
            msg += "🤬 "
        case LogLevel.ERROR:
            msg += "⛔️ "
        case LogLevel.CRITICAL:
            msg += "☢️ "
        case _:
            msg += "⁉️ "
    log_view.controls.append(
        ft.Text(f"{msg}{message}", color=level_to_color(level=level), expand=True)
    )
    if show_log is True and chk_show_log.value is False:
        chk_show_log.value = True
        log_view.visible = True
        chk_show_log.update()
    log_view.update()


ensure_folder(ROOT_FOLDER.joinpath("logs"))
log_file_handler = logging.FileHandler(
    ROOT_FOLDER.joinpath(
        "logs", f"enderleaf_{dt.now().strftime('%Y_%m_%d_%H%M%S')}.log"
    ),
    mode="a",
    delay=True,
)
# Annotate every log line with the process memory trend.
log_file_handler.addFilter(MemoryFilter())

# Configure the root logger to write to the file handler with the memory-augmented
# format string.
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s | %(mem_data)s | %(name)s | %(levelname)s] - %(message)s",
    handlers=[log_file_handler],
)

# Module-level logger used throughout this module.
logger = logger = logging.getLogger(__name__)
logger.info("")
logger.info("==== Starting session ====")
logger.info("")


def generate_frame(width=1280, height=720, color: tuple | list | None = None) -> bytes:
    """Generate a frame (image) with a solid color in byte format

    Args:
        width (int, optional): Frame width. Defaults to 1280.
        height (int, optional): Frame height. Defaults to 720.
        color (tuple | list | None, optional): Color in RGB format. Defaults to None.

    Returns:
        bytes: Image as bytes
    """
    color = (
        color
        if color is not None
        else (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    )
    frame = np.full(shape=(height, width, 3), dtype=np.uint8, fill_value=color)
    _, im_enc = cv2.imencode(".png", frame)
    im_b64 = base64.b64encode(im_enc)
    return im_b64.decode("utf-8")


def get_hist_plot():
    """Build a sample histogram figure used as placeholder plot content.

    Returns:
        matplotlib.figure.Figure: A figure containing a histogram of normally
        distributed sample data.
    """
    x = 4 + np.random.normal(0, 1.5, 200)
    fig, ax = plt.subplots()
    ax.hist(x, bins=8, linewidth=0.5, edgecolor="white")
    ax.set(
        xlim=(0, 8), xticks=np.arange(1, 8), ylim=(0, 56), yticks=np.linspace(0, 56, 9)
    )
    return fig


def get_hex_plot():
    """Build a sample hexbin figure used as placeholder plot content.

    Returns:
        matplotlib.figure.Figure: A figure containing a hexbin plot of sample
        correlated data.
    """
    np.random.seed(1)
    x = np.random.randn(5000)
    y = 1.2 * x + np.random.randn(5000) / 3
    fig, ax = plt.subplots()
    ax.hexbin(x, y, gridsize=20)
    ax.set(xlim=(-2, 2), ylim=(-3, 3))
    return fig


def plot_to_image(fig, dpi=300):
    """Render a matplotlib figure into a raw pixel array.

    Args:
        fig (matplotlib.figure.Figure): The figure to render.
        dpi (int, optional): Resolution in dots per inch used when rendering.
            Defaults to 300.

    Returns:
        numpy.ndarray: The figure rendered as a raw uint8 image array.
    """
    io_buf = io.BytesIO()
    fig.savefig(io_buf, format="raw")
    io_buf.seek(0)
    img_arr = np.reshape(
        np.frombuffer(io_buf.getvalue(), dtype=np.uint8),
        shape=(int(fig.bbox.bounds[3]), int(fig.bbox.bounds[2]), -1),
    )
    io_buf.close()
    return img_arr


@dataclass
class ConfUnit:
    """A single configuration key/value pair for the config data table."""

    name: str
    value: str


class NodeUi:
    """UI container for a single EnderLeaf node.

    Holds all Flet controls that make up a node's panel (image, plots, config
    table, progress bars, alerts) and the methods that update them in response to
    messages received from the corresponding node.

    Attributes:
        node_address (str): WebSocket address of the node.
        ip (str): IP portion of the node address, extracted for display.
        id_color: Color that uniquely identifies this node.
    """

    def __init__(self, node_address: str, id_color):
        self.node_address = node_address
        self.ip = node_address.replace("ws://", "").split(":")[0]
        self.id_color = id_color
        # Title checkbox also acts as the enable/disable toggle for this node.
        self.title = ft.Checkbox(
            label=self.ip,
            value=True,
            label_style=ft.TextStyle(size=20, bgcolor=COLOUR_BACKGROUND),
            on_change=self.on_change_enabled,
        )
        self.working_spin = spins.Ring(
            color=COLOUR_ACCENT, expand=True, visible=False, height=20, line_width=4
        )
        self.expand_toggle = ft.IconButton(
            icon=ft.Icons.FULLSCREEN,
            icon_color=ft.Colors.PRIMARY,
            icon_size=36,
            padding=ft.Padding.all(0),
        )

        self.image = ft.Image(
            src=generate_frame(color=self.id_color),
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
        # Two-column table used to display the node's configuration.
        self.config = fdt.DataTable2(
            expand=True,
            columns=[
                fdt.DataColumn2(
                    label=ft.Text("Param"),
                    size=fdt.DataColumnSize.L,
                    heading_row_alignment=ft.MainAxisAlignment.START,
                ),
                fdt.DataColumn2(
                    label=ft.Text("Value"),
                    numeric=True,
                    heading_row_alignment=ft.MainAxisAlignment.END,
                ),
            ],
            rows=[],
        )

        # Set when the transfer is allowed to run; used to wake a paused waiter.
        self._resume_event = asyncio.Event()
        self._ft_status = FileTransferStatus.PAUSED
        self.ft_status_text = ft.Text(self._ft_status.value)
        self.ft_progress_txt = ft.Text("Transfer progress", width=200)
        self.ft_progress = ft.ProgressBar(
            value=0, bar_height=10, expand=True, height=12
        )

        # Column shown in the main node's "File transfers" panel for this node.
        self.ft_feedback = ft.Column(
            controls=[
                ft.Text(self.ip, weight=ft.FontWeight.BOLD, expand=True),
                self.ft_status_text,
                self.ft_progress_txt,
                self.ft_progress,
                ft.Divider(),
            ],
            expand=True,
        )

        self.tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label=NodeViewOption.IMAGE.value),
                ft.Tab(label=NodeViewOption.PLOT_POSITION.value),
                ft.Tab(label=NodeViewOption.PLOT_FOCUS.value),
                # ft.Tab(label=NodeViewOption.CONFIG.value),
            ],
            # visible=False,
            scrollable=False,
        )
        self.tab_bar_view = ft.TabBarView(
            expand=True,
            controls=[
                self.image,
                self.position_plot,
                self.focus_plot,
                # self.config,
            ],
        )
        self.main_view = ft.Tabs(
            selected_index=0,
            length=len(self.tab_bar.tabs),
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
        self.expand_toggle.node_address = self.node_address
        self.progress = ft.ProgressBar(value=0, bar_height=10, expand=False, height=12)
        self.alert = ft.Text(value="Ready", expand=False)
        self._fullscreen = False

        self._ui = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self.title,
                            ft.Row(controls=[self.working_spin, self.expand_toggle]),
                        ],
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
            border_radius=BORDER_RADIUS,
            padding=4,
            expand=1,
        )

        self.max_level = 0

    async def switch_main_view(self, new_view: NodeViewOption):
        """Switch the node's tabbed main view to the requested view.

        Args:
            new_view (NodeViewOption): The view to activate.
        """
        match new_view:
            case NodeViewOption.IMAGE:
                await self.main_view.move_to(0)
            case NodeViewOption.PLOT_POSITION:
                await self.main_view.move_to(1)
            case NodeViewOption.PLOT_FOCUS:
                await self.main_view.move_to(2)
            # case NodeViewOption.CONFIG:
            #     await self.main_view.move_to(3)
            case _:
                raise NotImplementedError(f"Unknown node view mode: {new_view.value}")
        self.main_view.update()

    def reset(self, message: str | None = None):
        """Reset the node's alert to the ready state.

        Args:
            message (str | None, optional): Custom alert message. When None, the
                default "Ready" message is used. Defaults to None.
        """
        self.max_level = 0
        self.update_alert(
            log_level=LogLevel.INFO, message="Ready" if message is None else message
        )

    def on_change_enabled(self, e: ft.Event[ft.Checkbox]):
        """Show or hide the node's controls when its enable checkbox toggles.

        Args:
            e (ft.Event[ft.Checkbox]): Event from the title/enable checkbox.
        """
        for control in [self.main_view, self.progress, self.alert, self.expand_toggle]:
            control.visible = e.control.value is True
            control.update()
        self.ui.bgcolor = (
            COLOUR_BACKGROUND if e.control.value is True else ft.Colors.SURFACE_DIM
        )
        self.ui.update()

    def update_alert(self, log_level: LogLevel, message: str):
        """Update the node's alert text and border color for a given log level.

        The border color reflects the highest severity seen since the last reset,
        so that warnings and errors remain visually emphasized.

        Args:
            log_level (LogLevel): Severity level associated with the alert.
            message (str): Alert message text to display.
        """
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
        """Update the node's progress bar based on a step counter.

        Args:
            step (int): Current step number.
            total (int): Total number of steps.
        """
        self.progress.value = step / total
        self.progress.update()

    def update_image(self, image: str):
        """Update the node's main image with a new encoded image source.

        Args:
            image (str): Base64-encoded image source.
        """
        self.image.src = image
        self.image.update()

    def update_position_plot(self, image: str):
        """Update the node's position plot with a new encoded image source.

        Args:
            image (str): Base64-encoded image source.
        """
        self.position_plot.src = image
        self.position_plot.update()

    def update_focus_plot(self, image: str):
        """Update the node's focus plot with a new encoded image source.

        Args:
            image (str): Base64-encoded image source.
        """
        self.focus_plot.src = image
        self.focus_plot.update()

    def update_config_data(self, key, value: str):
        """Append a configuration row to the node's config table.

        Passing a None key clears the entire table.

        Args:
            key: Configuration parameter name, or None to clear the table.
            value (str): Configuration value to display for the given key.
        """
        if key is None:
            self.config.rows = []
        else:
            self.config.rows.append(
                fdt.DataRow2(
                    specific_row_height=50,
                    cells=[
                        ft.DataCell(content=ft.Text(key)),
                        ft.DataCell(content=ft.Text(str(value))),
                    ],
                )
            )

    def handle_file(
        self, sftp: paramiko.sftp_client.SFTPClient, src_file: Path
    ) -> bool:
        """Download and validate a single remote file, staging it locally.

        The remote file is mirrored into two local destinations: a "received"
        copy (the raw download target) and a "validated" copy (the trusted
        result). A download is skipped when a matching validated file already
        exists. After downloading, the file's size is checked against the remote
        and its content is validated; on any failure the received copy is
        removed and the method returns False. A successfully validated file is
        atomically moved into the validated folder.

        Args:
            sftp (paramiko.sftp_client.SFTPClient): An open SFTP session used to
                fetch the file and query its remote size.
            src_file (Path): Absolute path of the remote source file, whose
                tail after the "output" segment names the local mirror path.

        Returns:
            bool: True when the file is present and validated (already or newly),
                False when the download or validation failed.
        """
        # Derive the local mirror paths from the tail of the remote path that
        # follows the shared "output" segment, for the received and validated
        # copies respectively.
        received_file = PT_IMAGES_RECEIVED.joinpath(
            *src_file.parts[src_file.parts.index("output") + 1 :]
        )
        validated_file = PT_IMAGES_VALIDATED.joinpath(
            *src_file.parts[src_file.parts.index("output") + 1 :]
        )
        # Only download when no validated copy exists and the received copy is
        # either missing or out of date (different size from the remote).
        if not validated_file.is_file() and (
            not received_file.is_file()
            or received_file.stat().st_size != sftp.stat(str(src_file)).st_size
        ):
            # Download the file and verify its size matches the source.
            sftp.get(str(src_file), str(received_file))
            # A size mismatch means a truncated or corrupted download; discard it.
            if received_file.stat().st_size != sftp.stat(str(src_file)).st_size:
                received_file.unlink()
                return False
            # Validate the downloaded content by attempting to read it.
            try:
                validate_file_content(received_file)
            except Exception as e:
                log(
                    level=LogLevel.WARNING,
                    message=f"Received corrupted file '{str(received_file)}' wieth error '{str(e)}'",
                    node_address=self.ip,
                )
                received_file.unlink()
                return False
            else:
                # Content is valid: promote the received copy to the validated folder.
                atomic_move(received_file, validated_file)
        # A fully validated file can be removed from the remote.
        if (
            validated_file.is_file()
            and validated_file.stat().st_size == sftp.stat(str(src_file)).st_size
        ):
            # pass
            sftp.remove(str(src_file))
        return True

    def create_local_folders(self, sftp: paramiko.sftp_client.SFTPClient):
        """Mirror the remote output folder tree into the local received/validated dirs.

        Every subfolder listed under the remote output directory is recreated
        locally under both the received and validated roots so that downloaded
        files have their target directories in place before transfer.

        Args:
            sftp (paramiko.sftp_client.SFTPClient): An open SFTP session used to
                list the remote output subfolders.
        """
        # For each remote subfolder, create the matching received and validated
        # local folders.
        for src_fld in list_remote_folders(sftp, "/home/ender/code/EnderLeaf/output"):
            ensure_folder(
                PT_IMAGES_RECEIVED.joinpath(
                    *src_fld.parts[src_fld.parts.index("output") + 1 :]
                )
            )
            ensure_folder(
                PT_IMAGES_VALIDATED.joinpath(
                    *src_fld.parts[src_fld.parts.index("output") + 1 :]
                )
            )

    async def transfer_loop(self) -> bool:
        """Run one SSH/SFTP transfer round for this node's remote output.

        Opens an SSH/SFTP connection, ensures the local folder tree mirrors the
        remote output directory, and downloads every new or changed remote file
        through :meth:`handle_file`, updating the progress UI as it goes. The
        loop respects the transfer status: it stops when the transfer is
        stopped or finished, and yields when paused. On completion the status is
        moved back to STANDBY so the parent loop can idle before the next round.

        Returns:
            bool: True when the round completed cleanly (including the "no
                remote folder" case, which simply idles to STANDBY), False when
                an SSH or SFTP error occurred.
        """
        ssh = paramiko.SSHClient()
        remaining_files = 0
        try:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.ip,
                username="ender",
                pkey=paramiko.Ed25519Key.from_private_key_file(
                    Path.home().joinpath(".ssh/id_ed25519")
                ),
            )
            sftp = ssh.open_sftp()
            try:
                sftp.stat("/home/ender/code/EnderLeaf/output")
            except FileNotFoundError:
                # No remote output folder yet; go idle and check again later.
                log(
                    level=LogLevel.INFO,
                    message="No image folder on remote.",
                    node_address=self.ip,
                )
                if self.ft_status == FileTransferStatus.ACTIVE:
                    self.ft_status = FileTransferStatus.PAUSED
                return True
            try:
                self.create_local_folders(sftp=sftp)
                src_files = list_remote_files(sftp, "/home/ender/code/EnderLeaf/output")
                # Download each new or changed remote file, validate it, and
                # queue fully-validated files for remote deletion.
                for i, src_file in enumerate(src_files):
                    await asyncio.sleep(0)
                    # Stop early if the transfer was stopped or finished.
                    if self.ft_status in [
                        FileTransferStatus.STOP_REQUESTED,
                        FileTransferStatus.STOPPED,
                    ]:
                        break
                    # Pause until the node is free, then abort if a non-active
                    # status was requested while waiting.
                    if self.ft_status == FileTransferStatus.PAUSED:
                        await self._resume_event.wait()
                        if self.ft_status != FileTransferStatus.ACTIVE:
                            break
                    self.handle_file(sftp=sftp, src_file=src_file)
                    self.ft_progress.value = (i + 1) / len(src_files)
                    self.ft_progress.update()
                    self.ft_progress_txt.value = (
                        f"Transfer progress ({i+1}/{len(src_files)})"
                    )
                    self.ft_progress_txt.update()
            except Exception as e:
                log(level=LogLevel.ERROR, message=f"SFTP exception: {str(e)}")
                return False
            finally:
                remaining_files = len(
                    list_remote_files(sftp, "/home/ender/code/EnderLeaf/output")
                )
                sftp.close()
        except Exception as e:
            log(level=LogLevel.ERROR, message=f"SSH exception: {str(e)}")
            return False
        finally:
            # An active round that finishes with no errors idles to STANDBY
            # so the parent loop waits before retrying.
            if self.ft_status == FileTransferStatus.ACTIVE and remaining_files == 0:
                self.ft_status = FileTransferStatus.PAUSED
            ssh.close()

    async def transfer_files(self):
        """Drive the node's file-transfer task loop for its whole lifetime.

        Repeatedly dispatches transfer rounds while honoring the transfer status
        machine: ACTIVE runs a round, STANDBY idles then resumes, PAUSED waits,
        and STOP_REQUESTED/FINISHED terminate the loop. This task is launched once per
        node on the main node and runs until the transfer is finished.
        """
        while True:
            # Wait on the current transfer status before each transfer round.
            match self.ft_status:
                case FileTransferStatus.ACTIVE:
                    pass
                case FileTransferStatus.PAUSED:
                    # Node is busy: wait out the delay and retry without running a round.
                    await self._resume_event.wait()
                    continue
                case FileTransferStatus.STOP_REQUESTED:
                    # A stop was requested: move to FINISHED and end the loop.
                    self.ft_status = FileTransferStatus.STOPPED
                    await asyncio.sleep(0)
                    break
                case FileTransferStatus.STOPPED:
                    # Already finished: yield and end the loop.
                    await asyncio.sleep(0)
                    break
                case _:
                    raise NotADirectoryError(
                        f"Unknwon file transfer status: '{self.ft_status}"
                    )
            # Run the transfer round for the ACTIVE/STANDBY-resumed cases.
            await self.transfer_loop()
        log(
            level=LogLevel.INFO,
            message="Ended file transfer task",
            node_address=self.ip,
        )

    @property
    def working(self):
        """Whether the node's working spinner is currently visible."""
        return self.working_spin.visible is True

    @property
    def ui(self):
        """The root Flet container for this node's panel."""
        return self._ui

    @property
    def fullscreen(self):
        """Whether this node's panel is shown in fullscreen mode."""
        return self._fullscreen

    @fullscreen.setter
    def fullscreen(self, value):
        """Toggle fullscreen mode, hiding the title and expand toggle.

        Args:
            value (bool): Requested fullscreen state. The change is only applied
                when it differs from the current state.
        """
        if value != self._fullscreen:
            self._fullscreen = not self._fullscreen
            self.title.visible = not self._fullscreen
            self.expand_toggle.visible = not self._fullscreen
            self.title.update()
            self.expand_toggle.update()

    @property
    def ft_status(self):
        """The current file-transfer status for this node."""
        return self._ft_status

    @ft_status.setter
    def ft_status(self, value):
        """Set the file-transfer status, enforcing allowed transitions.

        Transitions out of the STOP_REQUESTED or FINISHED states are rejected so that a
        finished or stopped transfer cannot be resumed. A human-readable message
        is logged for every accepted transition.

        Args:
            value (FileTransferStatus): The new file-transfer status.
        """
        # if (
        #     self._ft_status == FileTransferStatus.PAUSED
        #     and value == FileTransferStatus.ACTIVE
        # ):
        #     print(traceback.print_stack())
        # print(f"{self._ft_status} -> {value}")
        if self._ft_status == FileTransferStatus.STOP_REQUESTED and value not in [
            FileTransferStatus.STOP_REQUESTED,
            FileTransferStatus.STOPPED,
        ]:
            log(
                level=LogLevel.WARNING,
                message=f"Stopping file transfer, switching to {value} not allowed",
                node_address=self.ip,
            )
            return
        if (
            self._ft_status == FileTransferStatus.STOPPED
            and value != FileTransferStatus.STOPPED
        ):
            log(
                level=LogLevel.WARNING,
                message=f"File transfer finished, switching to {value} not allowed",
                node_address=self.ip,
            )
            return

        if value == FileTransferStatus.PAUSED:
            # ensure a future pause actually blocks
            self._resume_event.clear()
        else:
            # tell any paused waiter it can proceed
            self._resume_event.set()

        self._ft_status = value
        self.ft_status_text.value = self._ft_status.value
        match self._ft_status:
            case FileTransferStatus.ACTIVE:
                message = "Starting file transfer"
            case FileTransferStatus.PAUSED:
                message = "Node busy, deffering file transfer"
            case FileTransferStatus.STOP_REQUESTED:
                message = "Stopping transfer files task"
            case FileTransferStatus.STOPPED:
                message = "Application closing, transfer tasks stopped"
            case _:
                raise NotADirectoryError(
                    f"Unknwon file transfer status: '{self._ft_status}"
                )
        log(level=LogLevel.INFO, message=message, node_address=self.ip)
        self.ft_status_text.update()


# One NodeUi panel per node, keyed by its WebSocket address.
node_uis = {k: NodeUi(k, id_color=ID_COLORS[i]) for i, k in enumerate(NODES)}


def fullscreen_node() -> NodeUi | None:
    """Return the NodeUi currently shown in fullscreen mode, if any.

    Returns:
        NodeUi | None: The fullscreen node UI, or None when no node is
        fullscreen.
    """
    for ui in node_uis.values():
        if ui.fullscreen is True:
            return ui
    else:
        return None


@ft.control
class EnderButton(ft.Button):
    """A Flet button pre-configured for the side control panel."""

    expand: bool = True
    height: int = BTN_HEIGHT


async def job_done(node_address):
    """Mark a node's task as complete, update its components and signal its completion event.

    Args:
        node_address (str): WebSocket address of the node whose task finished.
    """
    node_uis[node_address].working_spin.visible = False
    node_uis[node_address].working_spin.update()
    completion_events[node_address].set()


async def listen_for_updates(websocket, node_address):
    """Consume messages from a node's websocket and update the node UI.

    Each incoming message is decoded into a SocketMessage and dispatched to the
    matching UI update. On connection loss or error the node's task is marked
    complete so that awaiting callers can unwind.

    Args:
        websocket: The connected websocket to read messages from.
        node_address (str): WebSocket address identifying the owning node UI.
    """
    try:
        async for message in websocket:
            soccket_message = SocketMessage.load(message)
            node_ui = node_uis[node_address]
            # Dispatch each message type to the corresponding UI update.
            match soccket_message.type:
                case MsgType.MESSAGE:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=LogLevel.INFO,
                        message=soccket_message.message,
                        node_address=node_ui.ip,
                    )
                case MsgType.PROGRESS:
                    node_ui.update_progress(
                        step=soccket_message.step + 1, total=soccket_message.total
                    )
                case MsgType.IMAGE:
                    node_ui.update_image(image=soccket_message.image)
                case MsgType.POSITION_PLOT:
                    node_ui.update_position_plot(image=soccket_message.image)
                case MsgType.POSITION_DATA:
                    # Position data is not visualised on the client.
                    pass
                    # print(soccket_message.x, soccket_message.y, soccket_message.z)
                case MsgType.FOCUS_PLOT:
                    node_ui.update_focus_plot(image=soccket_message.image)
                case MsgType.CONFIG_DATA:
                    node_ui.update_config_data(
                        key=soccket_message.key, value=soccket_message.value
                    )
                case MsgType.RESULT:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=soccket_message.level,
                        message=soccket_message.message,
                        node_address=node_ui.ip,
                    )
                    await job_done(node_address=node_address)
                case MsgType.PROBLEM:
                    node_ui.update_alert(
                        log_level=soccket_message.level, message=soccket_message.message
                    )
                    log(
                        level=soccket_message.level,
                        message=soccket_message.message,
                        node_address=node_ui.ip,
                    )
                    # Only treat error-level problems as a completed (failed) task.
                    if soccket_message.level in [LogLevel.ERROR, LogLevel.CRITICAL]:
                        await job_done(node_address=node_address)
                case _:
                    log(
                        level=LogLevel.CRITICAL,
                        message=f"Unknown message type '{str(soccket_message.type)}'",
                        node_address=node_ui.ip,
                    )
                    node_ui.update_alert(
                        log_level=soccket_message.level,
                        message=f"Unknown message type '{str(soccket_message.type)}'",
                    )
                    await job_done(node_address=node_address)
    except websockets.exceptions.ConnectionClosed:
        log(
            level=LogLevel.ERROR,
            message=f"[{node_address}] Connection closed unexpectedly",
        )
        await job_done(node_address=node_address)
    except Exception as e:
        log(
            level=LogLevel.ERROR,
            message=f"[{node_address}] Exception while listening for update {str(soccket_message)}: {str(e)}",
        )


async def execute_on_node(uri, func_name, **kwargs):
    """Send a command to a single node and await its completion.

    Opens a websocket to the node, starts a listener for UI updates, sends the
    command, and waits for the node's completion event (with a per-command
    timeout). Connection and timeout errors are logged and surfaced on the UI.

    Args:
        uri (str): WebSocket address of the target node.
        func_name (ControllerCommands): The command to execute on the node.
        **kwargs: Keyword arguments forwarded to the node command.
    """
    try:
        async with websockets.connect(
            uri, open_timeout=5, ping_interval=60, ping_timeout=60
        ) as websocket:
            asyncio.create_task(listen_for_updates(websocket, uri))
            message = {"func": func_name, "kwargs": kwargs}
            # print(message)
            await websocket.send(json.dumps(message))
            # Select the completion timeout based on the command being sent.
            match func_name:
                case ControllerCommands.PING | ControllerCommands.GET_CONFIG:
                    time_out = 5
                case ControllerCommands.IDENTIFY:
                    time_out = 10
                case ControllerCommands.CONNECT_PRINTER:
                    time_out = 60
                case _:
                    time_out = None

            await asyncio.wait_for(completion_events[uri].wait(), timeout=time_out)
    except asyncio.TimeoutError as e:
        log(
            level=LogLevel.ERROR,
            message=f"Timeout error for '{func_name}': {str(e)}",
            node_address=uri,
        )
        node_uis[uri].update_alert(
            log_level=LogLevel.EXCEPTION,
            message=f"Timeout error for '{func_name}': {str(e)}",
        )
    except Exception as e:
        log(
            level=LogLevel.ERROR,
            message=f"Connection/Execution failed: {e}",
            node_address=uri,
        )
        node_uis[uri].update_alert(
            log_level=LogLevel.ERROR,
            message=f"Connection/Execution failed for '{func_name}': {e}",
        )


async def run_task(task_name: ControllerCommands = ControllerCommands.START):
    """Dispatch a task to the active node(s) and await completion.

    When a node is in fullscreen mode the task runs only on that node;
    otherwise it runs in parallel across all enabled nodes. Command-specific
    arguments are assembled before dispatch.

    Args:
        task_name (ControllerCommands, optional): The command to run. Defaults
        to ControllerCommands.START.
    """
    fs_node = fullscreen_node()
    log(
        level=LogLevel.INFO,
        message=(
            f"Initiating distributed execution for task: '{task_name}'"
            if fs_node is None
            else f"Initializing task for single node {fs_node.node_address}"
        ),
    )
    await set_disable(True, is_stop_enabled=task_name == ControllerCommands.START)
    tasks = []
    kwargs = {}
    match task_name:
        case ControllerCommands.MOVE_TO:
            kwargs["position"] = int(dd_position.value)
        case ControllerCommands.START:
            kwargs["launch_options"] = [
                LaunchOptons.PRECISE_FOCUS,
                LaunchOptons.CENTER_OL,
            ]
        case ControllerCommands.CENTER_ON_QR_CODE | ControllerCommands.CHECK_CORNERS:
            kwargs["launch_options"] = [LaunchOptons.PRECISE_FOCUS]
    if fs_node is None:
        # Distributed mode: run the task on every enabled node in parallel.
        for uri in NODES:
            node_ui = node_uis[uri]
            node_ui.reset(f"{task_name} in progress...")
            if task_name == ControllerCommands.IDENTIFY:
                # Give each node a unique color and a shared display duration. Color is also displayed on the node's LED ring
                kwargs["color"] = node_ui.id_color
                kwargs["wait"] = ID_TIME
            if node_ui.title.value is False:
                continue
            tasks.append(
                asyncio.create_task(
                    execute_on_node(node_ui.node_address, task_name, **kwargs)
                )
            )
    else:
        # Fullscreen mode: run the task on the single fullscreen node only.
        fs_node.reset(f"{task_name} in progress...")
        tasks.append(
            asyncio.create_task(
                execute_on_node(fs_node.node_address, task_name, **kwargs)
            )
        )
    await asyncio.gather(*tasks)
    log(
        level=LogLevel.INFO,
        message=(
            f"Task '{task_name}', all nodes completed."
            if fs_node is None
            else f"Task '{task_name}' completed for single node {fs_node.node_address}"
        ),
    )
    # Reset the completion events so they can be reused for the next run.
    for event in completion_events.values():
        event.clear()
    await set_disable(False, False)
    if run_task == ControllerCommands.START:
        for nu in node_uis.values():
            nu.ft_status = FileTransferStatus.ACTIVE


async def set_disable(is_disable: bool, is_stop_enabled: bool):
    """Enable or disable the side-panel controls and some node's components during a running task.

    Args:
        is_disable (bool): When True, the control buttons are disabled and the
            nodes' file transfers are paused.
        is_stop_enabled (bool): Whether the stop button should be enabled (set
            when a task can be interrupted).
    """
    for ctrl in [
        bt_connect_printer,
        bt_ping,
        bt_identify,
        bt_home,
        bt_park,
        bt_idle,
        bt_launch,
        bt_move_to,
        # bt_capture_still,
        bt_close,
        bt_auto,
        bt_far,
        bt_get_config,
        bt_center_on_qr_code,
        bt_check_corners,
        bt_toggle_lights,
        bt_cycle_lights,
    ]:
        ctrl.disabled = is_disable
        ctrl.update()
    spin_working.visible = is_disable
    spin_working.update()
    is_fullscreen = fullscreen_node() is not None
    for nu in node_uis.values():
        nu.title.disabled = is_disable
        nu.title.update()
        # Pause transfers while the node is busy, resume when controls re-enable.
        nu.ft_status = (
            FileTransferStatus.PAUSED if is_disable else FileTransferStatus.ACTIVE
        )
        # In fullscreen the global spinner is hidden, so show the per-node one.
        if is_fullscreen is False:
            nu.working_spin.visible = is_disable
            nu.working_spin.update()
    bt_stop.disabled = not is_stop_enabled
    bt_stop.update()


async def on_run_task(e: ft.Event[ft.Button]):
    """Run the task associated with a clicked side-panel button.

    Args:
        e (ft.Event[ft.Button]): Click event; the button content names the task.
    """
    await run_task(e.control.content)


def on_capture_array(e):
    """Placeholder handler for capturing the image array (no-op)."""
    pass


async def on_nodes_view_changed(e: ft.Event[ft.Dropdown]):
    """Switch every node's main view when the view dropdown changes.

    Args:
        e (ft.Event[ft.Dropdown]): Selection event carrying the new view option.
    """
    for node_ui in node_uis.values():
        await node_ui.switch_main_view(e.control.value)


def get_side_bar_button(command, icon=None, disabled=False, visible: bool = True):
    """Build a side-panel button that runs the given command on click.

    Args:
        command (ControllerCommands): Command executed when the button is
            clicked.
        icon (optional): Flet icon shown on the button. Defaults to None.
        disabled (bool, optional): Initial disabled state. Defaults to False.

    Returns:
        EnderButton: The configured side-panel button.
    """
    return EnderButton(
        content=command, on_click=on_run_task, icon=icon, visible=visible
    )


# MARK: Side panel controls
# Buttons and dropdowns that make up the left-hand control panel.
bt_connect_printer = ft.FilledButton(
    content=ControllerCommands.CONNECT_PRINTER,
    on_click=on_run_task,
    icon=ft.Icons.INSERT_LINK,
    expand=True,
    height=BTN_HEIGHT,
)
bt_ping = get_side_bar_button(
    command=ControllerCommands.PING, icon=ft.Icons.NETWORK_PING
)
bt_identify = get_side_bar_button(
    command=ControllerCommands.IDENTIFY, icon=ft.Icons.QUESTION_MARK
)
bt_home = get_side_bar_button(ControllerCommands.GO_HOME, icon=ft.Icons.HOME)
bt_idle = get_side_bar_button(
    ControllerCommands.GO_IDLE,
    icon=ft.Icons.AIRLINE_SEAT_INDIVIDUAL_SUITE_OUTLINED,
)
bt_park = get_side_bar_button(ControllerCommands.GO_PARK, icon=ft.Icons.LOCAL_PARKING)
bt_launch = ft.FilledButton(
    content=ControllerCommands.START,
    expand=True,
    on_click=on_run_task,
    icon=ft.Icons.PLAY_ARROW_SHARP,
    height=BTN_HEIGHT,
)
bt_stop = get_side_bar_button(
    ControllerCommands.STOP, icon=ft.Icons.STOP, disabled=True
)

# MARK:  Extra controls
bt_move_to = get_side_bar_button(
    ControllerCommands.MOVE_TO, icon=ft.Icons.ARROW_CIRCLE_RIGHT_OUTLINED
)
bt_center_on_qr_code = get_side_bar_button(
    command=ControllerCommands.CENTER_ON_QR_CODE, icon=ft.Icons.QR_CODE_SCANNER
)
bt_check_corners = get_side_bar_button(
    command=ControllerCommands.CHECK_CORNERS, icon=ft.Icons.ALL_OUT
)
bt_toggle_lights = get_side_bar_button(
    command=ControllerCommands.TOGGLE_LIGHTS, icon=ft.Icons.LIGHT_MODE, visible=True
)
bt_cycle_lights = get_side_bar_button(
    command=ControllerCommands.CYCLE_LIGHTS, icon=ft.Icons.AUTORENEW, visible=True
)
bt_close = get_side_bar_button(
    ControllerCommands.FOCUS_CLOSE,
    icon=ft.Icons.CENTER_FOCUS_STRONG_ROUNDED,
    visible=True,
)
bt_auto = get_side_bar_button(
    ControllerCommands.FOCUS_AUTO, icon=ft.Icons.AUTO_AWESOME, visible=True
)
bt_far = get_side_bar_button(
    ControllerCommands.FOCUS_FAR,
    icon=ft.Icons.FILTER_CENTER_FOCUS_ROUNDED,
    visible=True,
)
bt_get_config = get_side_bar_button(
    ControllerCommands.GET_CONFIG, icon=ft.Icons.SETTINGS_ROUNDED, visible=False
)

# Position dropdown used by the MOVE_TO command (positions 1 through 81).
dd_position = ft.Dropdown(
    options=[ft.DropdownOption(key=i + 1, content=ft.Text(i + 1)) for i in range(81)]
)

# View dropdown that switches all nodes between image/plot/config views.
dd_view = ft.Dropdown(
    options=[
        ft.DropdownOption(key=nvo.value, text=nvo.value)
        for nvo in [
            NodeViewOption.IMAGE,
            NodeViewOption.PLOT_POSITION,
            NodeViewOption.PLOT_FOCUS,
            # NodeViewOption.CONFIG,
        ]
    ],
    expand=True,
    value=NodeViewOption.IMAGE.value,
    on_select=on_nodes_view_changed,
)

# Global "working" spinner shown in the side panel while a task runs.
spin_working = spins.Wave(color=COLOUR_ACCENT, expand=True, visible=False)

# Collapsible group of secondary controls in the side panel.
ep_extra_controls = ft.ExpansionTile(
    title=ft.Text("Extra controls"),
    controls_padding=ft.Padding.symmetric(horizontal=10, vertical=10),
    affinity=ft.TileAffinity.PLATFORM,
    maintain_state=True,
    shape=ft.RoundedRectangleBorder(side=ft.BorderSide(width=1), radius=BORDER_RADIUS),
    collapsed_shape=ft.RoundedRectangleBorder(
        side=ft.BorderSide(width=1), radius=BORDER_RADIUS
    ),
    controls=ft.Column(
        controls=[
            ft.Row(bt_center_on_qr_code),
            ft.Row(bt_check_corners),
            ft.Row(controls=[bt_move_to, dd_position]),
            ft.Row(bt_toggle_lights),
            ft.Row(bt_cycle_lights),
            ft.Row(bt_get_config),
            ft.Row(bt_auto, expand=True),
            ft.Row(bt_close, expand=True),
            ft.Row(bt_far, expand=True),
        ],
        expand=True,
        intrinsic_width=True,
    ),
)

# File-transfer feedback panel, only shown on the main (coordinating) node.
ep_file_transfer = ft.ExpansionTile(
    title=ft.Text("File transfers"),
    controls_padding=ft.Padding.symmetric(horizontal=4, vertical=4),
    # affinity=ft.TileAffinity.PLATFORM,
    # maintain_state=False,
    shape=ft.RoundedRectangleBorder(side=ft.BorderSide(width=1), radius=BORDER_RADIUS),
    collapsed_shape=ft.RoundedRectangleBorder(
        side=ft.BorderSide(width=1), radius=BORDER_RADIUS
    ),
    controls=ft.Column(
        controls=[ft.Row(node.ft_feedback, expand=True) for node in node_uis.values()],
        expand=True,
        intrinsic_width=True,
    ),
    expanded=True,
    visible=MAIN_NODE,
)

# Grid of node panels, laid out horizontally with up to two columns.
gv_nodes = ft.GridView(
    spacing=10,
    scroll=ft.ScrollMode.ALWAYS,
    expand=True,
    controls=[nu.ui for nu in node_uis.values()],
    horizontal=True,
    child_aspect_ratio=0.85,
    runs_count=min(len(NODES), 2),
)

side_buttons = ft.Column(
    intrinsic_width=True,
    controls=[
        ft.Row(spin_working),
        ft.Row(bt_connect_printer),
        ft.Row(bt_ping),
        ft.Row(bt_identify),
        ft.Row(bt_home),
        ft.Row(bt_idle),
        ft.Row(bt_park),
        ft.Row(controls=[ft.Text("View: "), dd_view]),
        ft.Row(controls=[chk_show_log]),
        ep_extra_controls,
        ft.Text("Acquisition Job", size=16),
        ft.Row([bt_launch, bt_stop]),
        ep_file_transfer,
    ],
    auto_scroll=True,
    scroll=ft.ScrollMode.ADAPTIVE,
)


def global_page() -> ft.View:
    """Build the default page showing all nodes in a grid.

    Returns:
        ft.View: The root view containing the side panel, node grid, and log.
    """
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
    """Build the fullscreen page for a single node.

    Args:
        route (str): The page route, whose remainder names the node address.

    Returns:
        ft.View: The view showing the requested node in fullscreen.
    """
    node_address = route[1:]
    node = node_uis[node_address]
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
                                        title=ft.Text(node_address),
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


def confirm_exit_dialog(dialog_text: str, actions: list):
    return ft.AlertDialog(
        modal=False,
        title=ft.Text("Please confirm"),
        content=ft.Text(dialog_text),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )


async def stop_for_transfer_tasks():
    for nu in node_uis.values():
        nu.ft_status = FileTransferStatus.STOP_REQUESTED
    while True:
        waiting_count = 0
        for nu in node_uis.values():
            if nu.ft_status != FileTransferStatus.STOPPED:
                waiting_count += 1
        await asyncio.sleep(2)
        if waiting_count == 0:
            break
        print(f"Waiting for {waiting_count} nodes to finish pending transfers")
        log(
            level=LogLevel.INFO,
            message=f"Waiting for {waiting_count} nodes to finish pending transfers",
        )


async def main(page: ft.Page):
    """Entry point that builds and configures the Flet application.

    Sets up the page window, themes, routing between the global and fullscreen
    views, and (on the main node) the file-transfer background tasks.

    Args:
        page (ft.Page): The Flet page instance to configure.
    """
    page.title = "EnderLeaf"
    page.window.width = 1200
    page.window.height = 900
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    # Disable native page transitions so view switching stays instant.
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
    desktop_client = page.web is False
    if desktop_client:
        page.window.full_screen = True
    page.update()

    async def go_fullscreen(e: ft.Event[ft.IconButton]):
        # Navigate to the fullscreen route for the node that owns the button.
        # log(level=LogLevel.DEBUG, message=str(e.control.node_address))
        await page.push_route(f"/{e.control.node_address}")

    # Wire each node's expand toggle to open its fullscreen route.
    for nu in node_uis.values():
        nu.expand_toggle.on_click = go_fullscreen

    def route_change():
        # log(level=LogLevel.DEBUG, message=f"Route changed -> {page.route}")
        page.views.clear()
        # Always start from the global view, then layer a fullscreen view on top
        # when the route names a node, adjusting the theme accent accordingly.
        page.views.append(global_page())
        if len(page.route) > 1:
            page.views.append(fullscren_page(page.route))
            page.dark_theme = ft.Theme(color_scheme_seed=FULLSCREEN_COLOUR_ACCENT)
            spin_working.color = FULLSCREEN_COLOUR_ACCENT
        else:
            for nu in node_uis.values():
                nu.fullscreen = False
            page.dark_theme = ft.Theme(color_scheme_seed=COLOUR_ACCENT)
            spin_working.color = COLOUR_ACCENT

        page.update()

    async def view_pop(e):
        # When a view is popped, remove it and re-route to the new top view.
        if e.view is not None:
            # log(level=LogLevel.DEBUG, message=f"View pop: {e.view}")
            page.views.remove(e.view)
            top_view = page.views[-1]
            await page.push_route(top_view.route)

    page.on_route_change = route_change
    page.on_view_pop = view_pop

    chk_show_log.on_change = on_change_log_visibility

    if MAIN_NODE is True:
        # Launch a background file-transfer task for every node on the main node.
        transfer_files_tasks = [
            page.run_task(node.transfer_files) for node in node_uis.values()
        ]
        if desktop_client is True:

            async def handle_confirm_close_click(e: ft.Event[ft.Button]):
                await stop_for_transfer_tasks()
                await page.window.destroy()

            def handle_no_wip_click(e: ft.Event[ft.OutlinedButton]):
                page.pop_dialog()

            def handle_no_tip_click(e: ft.Event[ft.OutlinedButton]):
                for nu in node_uis.values():
                    nu.ft_status = FileTransferStatus.ACTIVE
                page.pop_dialog()

            async def window_event(e: ft.WindowEvent):
                if e.type == ft.WindowEventType.CLOSE:
                    # Check if job in progress
                    if spin_working.visible:
                        page.show_dialog(
                            confirm_exit_dialog(
                                dialog_text="Work in progress, are you sure you want to close the program?",
                                actions=[
                                    ft.Button(
                                        content="Yes",
                                        on_click=handle_confirm_close_click,
                                    ),
                                    ft.OutlinedButton(
                                        content="No", on_click=handle_no_wip_click
                                    ),
                                ],
                            )
                        )
                    else:
                        transfer_in_progress = False
                        for nu in node_uis.values():
                            if nu.ft_status == FileTransferStatus.ACTIVE:
                                transfer_in_progress = True
                            nu.ft_status = FileTransferStatus.PAUSED
                        await asyncio.sleep(0.1)
                        if transfer_in_progress:
                            page.show_dialog(
                                confirm_exit_dialog(
                                    dialog_text="File transfer in progress, are you sure you want to close the program?",
                                    actions=[
                                        ft.Button(
                                            content="Yes",
                                            on_click=handle_confirm_close_click,
                                        ),
                                        ft.OutlinedButton(
                                            content="No", on_click=handle_no_tip_click
                                        ),
                                    ],
                                )
                            )
                        else:
                            await page.window.destroy()
                    page.update()

            page.window.prevent_close = True
            page.window.on_event = window_event

        else:

            async def on_disconnect(e):
                # On app shutdown, stop all transfers and wait for them to finish.
                print("Entering disconnect")
                await stop_for_transfer_tasks()
                print("exiting disconnect")

            page.on_disconnect = on_disconnect

    route_change()

    page.update()
    for nu in node_uis.values():
        nu.ft_status = FileTransferStatus.ACTIVE
    await asyncio.sleep(0.1)

    log(LogLevel.INFO, "Started")


ft.run(main=main)
