import panel as pn

from enderleaf.enderleaf_mini import main, sidebar

pn.extension()

template = pn.template.BootstrapTemplate(title="EnderLeaf")
template.sidebar.append(sidebar)
template.main.append(main)
template.servable()
