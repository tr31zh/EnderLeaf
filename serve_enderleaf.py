import panel as pn

from enderleaf.enderleaf_ui import ui_main, ui_sidebar

pn.extension("ace", "jsoneditor", "terminal", console_output="disable")

template = pn.template.BootstrapTemplate(title="EnderLeaf")
template.sidebar.append(ui_sidebar())
# template.sidebar_width = p._sidebar_width
template.main.append(ui_main())
template.servable()
