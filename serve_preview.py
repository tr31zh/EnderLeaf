import panel as pn

from enderleaf.preview_panel import preview

pn.extension("ace", "jsoneditor", "terminal", console_output="disable")

p = preview()
p.start_video()
template = pn.template.BootstrapTemplate(title="EnderLeaf")
template.sidebar.append(p.sidebar())
template.sidebar_width = p._sidebar_width
template.main.append(p.main())
template.servable()
