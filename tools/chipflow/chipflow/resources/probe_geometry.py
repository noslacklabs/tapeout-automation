"""Check the generated batch GDS and inventory the actual external frame."""
import json
import math
from pathlib import Path
import klayout.db as db

layout = db.Layout()
layout.read('batch_probe.gds')
top = layout.cell('CHIPFLOW_PROBE')
if layout.dbu != 0.001 or top is None or top.bbox() != db.Box(0, 0, 1000, 1000):
    raise ValueError('batch geometry round-trip did not preserve units or extent')
if top.shapes(layout.layer(8, 0)).size() != 1:
    raise ValueError('batch geometry round-trip lost the probe shape')
frame = db.Layout()
frame.read('frame.gds')
if not math.isfinite(frame.dbu) or frame.dbu <= 0:
    raise ValueError('frame DBU must be positive and finite')
candidate = frame.cell('frame2mmx2mm')
if candidate is None:
    raise ValueError('expected 2mm reference frame top is missing')
bounds = candidate.bbox()
width, height = bounds.width() * frame.dbu, bounds.height() * frame.dbu
if abs(width - 2000.0) > frame.dbu or abs(height - 2000.0) > frame.dbu:
    raise ValueError('reference frame extent differs from the 2mm profile')
Path('geometry.json').write_text(json.dumps({
    'batch_round_trip': 'PASS', 'frame_read': 'PASS', 'frame_top': candidate.name,
    'frame_dbu_um': frame.dbu, 'width_um': width, 'height_um': height,
    'frame_instances': sum(1 for _ in candidate.each_inst()),
    'pad_policy_qualification': 'NOT RUN',
}) + '\n', encoding='utf-8')
