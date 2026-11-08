import pya

layout = pya.Layout()
layout.dbu = 0.001
top = layout.create_cell('CHIPFLOW_PROBE')
top.shapes(layout.layer(8, 0)).insert(pya.Box(0, 0, 1000, 1000))
layout.write('batch_probe.gds')
