from pathlib import Path
import asyncio
from websockets.asyncio.server import serve
import json
import socket
import os
import sys
import signal

sys.path.append(str(Path(__file__).parent.parent))
os.chdir(str(Path(__file__).parent.parent))

from enderleaf.enums import ControllerCommands, MsgType, LogLevel, LaunchOptons
from enderleaf.socket_message import SocketMessage, result_message
from enderleaf.enderleaf_ctrl import EnderLeafController, ELStatus
from enderscope.serial import default_printer_port

controller = EnderLeafController()


def sigint_handler(sig, frame):
    print("")
    print("Stopping node")
    controller.close()
    print("Controller closed")
    sys.exit(0)


signal.signal(signal.SIGINT, sigint_handler)


async def node_connect_printer(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Connecting to printer. No other operation is allowed",
        ).dump()
    )
    await websocket.send(
        result_message(
            result=await controller.connect_printer(
                kwargs.get("port", str(default_printer_port()))
            ),
            ok_message="Connected to printer",
            nok_message="Failed to connect to printer",
        ).dump()
    )


async def node_ping(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        result_message(
            result=await controller.ping(),
            ok_message="Ping OK",
            nok_message="Ping failed",
        ).dump()
    )


async def node_get_config(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.RESULT,
            message="Retrieving node config. No other operation is allowed",
        ).dump()
    )
    await websocket.send(
        result_message(
            result=await controller.send_config(),
            ok_message="Config sent",
            nok_message="Failed to send config",
        ).dump()
    )


async def node_go_home(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Homming. No other operation is allowed",
        ).dump()
    )
    await websocket.send(
        result_message(
            result=await controller.go_home(),
            ok_message="Home OK",
            nok_message="Home failed",
        ).dump()
    )


async def node_go_idle(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Idling. No other operation is allowed",
        ).dump()
    )
    await websocket.send(
        result_message(
            result=await controller.go_rest(),
            ok_message="Idle OK",
            nok_message="Idle failed",
        ).dump()
    )


async def node_start(websocket, **kwargs):
    controller.socket = websocket
    launch_options = kwargs.get("launch_options", [])
    await controller.launch_acquisition(
        precise_focusing=LaunchOptons.PRECISE_FOCUS in launch_options,
        center_on_leaf=LaunchOptons.CENTER_OL in launch_options,
    )


async def node_stop(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(type=MsgType.MESSAGE, message="Stop requested").dump()
    )
    controller.status = ELStatus.STOP_REQUESTED


async def node_capture_still(websocket, **kwargs):
    controller.socket = websocket
    await controller.capture_array()
    await websocket.send(
        SocketMessage(type=MsgType.RESULT, message="Image acquired").dump()
    )


async def node_request_focus(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        result_message(
            result=await controller.autofocus_cycle(),
            ok_message="Focus acquired",
            nok_message="Focus failed",
        ).dump()
    )


async def node_request_focus_close(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        result_message(
            result=await controller.set_focus_close(),
            ok_message="Focused close",
            nok_message="Focus close failed",
        ).dump()
    )


async def node_request_focus_far(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        result_message(
            result=await controller.set_focus_far(),
            ok_message="Focused far",
            nok_message="Focus far failed",
        ).dump()
    )


async def node_go_park(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Parking. No other operation is allowed",
        ).dump()
    )
    await controller.go_park()
    await websocket.send(SocketMessage(type=MsgType.RESULT, message="Park").dump())


async def node_center_on_qr_code(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Centering o,n QR code. No other operation is allowed",
        ).dump()
    )
    launch_options = kwargs.get("launch_options", [])
    await controller.center_on_qr_code(
        precise_focusing=LaunchOptons.PRECISE_FOCUS in launch_options
    )
    await websocket.send(
        SocketMessage(type=MsgType.RESULT, message="Centered on QR code").dump()
    )


async def node_check_corners(websocket, **kwargs):
    controller.socket = websocket
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message="Checking corners. No other operation is allowed",
        ).dump()
    )
    launch_options = kwargs.get("launch_options", [])
    await controller.check_corners(
        precise_focusing=LaunchOptons.PRECISE_FOCUS in launch_options
    )
    await websocket.send(
        SocketMessage(type=MsgType.RESULT, message="Corners checked").dump()
    )


async def node_toggle_lights(websocket, **kwargs):
    controller.socket = websocket
    await controller.shutter(controller.top_lights.mean == 0)
    await websocket.send(
        SocketMessage(type=MsgType.RESULT, message="Lights togled").dump()
    )


async def node_cycle_lights(websocket, **kwargs):
    controller.socket = websocket
    await controller.cycle_lights()
    await websocket.send(
        SocketMessage(type=MsgType.RESULT, message="Lights cycled").dump()
    )


async def node_move_to(websocket, **kwargs):
    controller.socket = websocket
    position = kwargs.get("position", 1)
    await websocket.send(
        SocketMessage(
            type=MsgType.MESSAGE,
            message=f"Moving to position {position}. No other operation is allowed",
        ).dump()
    )
    await controller.move_to(position)
    await websocket.send(SocketMessage(type=MsgType.RESULT, message="Moved").dump())


FUNCTION_REGISTRY = {
    ControllerCommands.START: node_start,
    ControllerCommands.STOP: node_stop,
    ControllerCommands.PING: node_ping,
    ControllerCommands.CAPTURE_STILL: node_capture_still,
    ControllerCommands.FOCUS_AUTO: node_request_focus,
    ControllerCommands.FOCUS_CLOSE: node_request_focus_close,
    ControllerCommands.FOCUS_FAR: node_request_focus_far,
    ControllerCommands.CONNECT_PRINTER: node_connect_printer,
    ControllerCommands.GO_HOME: node_go_home,
    ControllerCommands.GO_IDLE: node_go_idle,
    ControllerCommands.GO_PARK: node_go_park,
    ControllerCommands.CENTER_ON_QR_CODE: node_center_on_qr_code,
    ControllerCommands.CHECK_CORNERS: node_check_corners,
    ControllerCommands.TOGGLE_LIGHTS: node_toggle_lights,
    ControllerCommands.CYCLE_LIGHTS: node_cycle_lights,
    ControllerCommands.MOVE_TO: node_move_to,
    ControllerCommands.GET_CONFIG: node_get_config,
}


async def handler(websocket):
    async for message in websocket:
        try:
            data = json.loads(message)
            func_name = data.get("func")
            kwargs = data.get("kwargs", {})

            if func_name not in FUNCTION_REGISTRY:
                await websocket.send(
                    SocketMessage(
                        type=MsgType.PROBLEM,
                        message=f"Unknown function: {func_name}",
                        level=LogLevel.ERROR,
                    ).dump()
                )
                continue

            await FUNCTION_REGISTRY[func_name](websocket, **kwargs)
        except json.JSONDecodeError:
            await websocket.send(
                SocketMessage(
                    type=MsgType.PROBLEM,
                    message="Invalid JSON",
                    level=LogLevel.EXCEPTION,
                ).dump()
            )
        except Exception as e:
            await websocket.send(
                SocketMessage(
                    type=MsgType.PROBLEM, message=str(e), level=LogLevel.EXCEPTION
                ).dump()
            )


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    host = "0.0.0.0"
    await controller.start()
    async with serve(handler, host, port, max_size=None):
        print(
            f"Node server running on {host}:{port} (Hostname: {socket.gethostname()})",
            flush=True,
        )
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
