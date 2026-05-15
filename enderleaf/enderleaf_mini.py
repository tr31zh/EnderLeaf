from enderscope.serial import default_printer_port
from enderleaf.image import to_pil, safe_pil_resize
import enderleaf.enderleaf_ui as eui
from enderleaf.enderleaf_ctrl import CameraState, ELStatus


import panel as pn

pn.extension("ipywidgets")


eui.controller.start()
eui.controller.switch_state(CameraState.STILL)

eui.controller.update_progress = eui.on_progress_updated
eui.controller.update_still = eui.on_update_still

eui.cbg_launch_options.options = [eui.LO_CENTER_OL, eui.LO_PRECISE_FOCUS]
eui.cbg_launch_options.value = [eui.LO_CENTER_OL, eui.LO_PRECISE_FOCUS]

main = eui.still_pane

sidebar = pn.Column(
    eui.bt_connect_printer,
    pn.Row(eui.bt_home, eui.bt_idle, eui.bt_park),
    # pn.Row(eui.bt_qr_code, eui.bt_check_corners),
    # pn.Row(eui.bt_lights_toggle, eui.bt_lights_cycle),
    # eui.cbg_launch_options,
    pn.Row(
        eui.bt_launch_acquisition,
        # eui.bt_stop_acquisition,
    ),
    eui.pg_progress,
)
sidebar.width = 330
