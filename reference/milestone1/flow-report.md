# Build report

**Private: contains PDK data.** Written by `flow/report/make_report.py` on 2026-11-07, from `rtl/s8.v`.

| Check | Result | Detail |
|---|---|---|
| Synthesis | **PASS** | Yosys 0.33 netlist written |
| Router (OpenROAD detailed route) | **PASS** | 0 violations after 4 iterations: 156 35 22 0 |
| Routed wires overlapping another net or cell metal | **PASS** | 0 overlaps across 393 nets. Routing attempt used: 0, counting from 0 |
| Top-level GDS in SCL's 2x2 frame | **PASS** | 345 cells, 1386 fillers. Written, one top cell checked below |
| Row fill with power and pads | **PASS** | 65.8% (10928 of 16598 um). The plan's limit is about 80% |
| SCL DRC deck (v1.1, KLayout 0.30.7) | **PASS** | 0 markers |
| SCL LVS deck (KLayout 0.30.7) | **PASS** | circuits matched 22, not matched 0. Layout and schematic netlists compared |
| Decks fail on deliberately broken copies | **PASS** | 11 of 11 tests behaved as expected |
| DRC cross-check, deck in flat mode | **PASS** | 0 markers |
| presubmit-check | **PASS** | PASS 38 WARN 8 MANUAL 7 |
| VDD and VSS through metal alone, pads to every row (LVS can't see this) | **PASS** | 92 points checked, 0 problems |
| Metal wider than 25 um in what we drew | **PASS** | M1 0, M2 0 |
| Metal space including notches (the deck skips notches) | **PASS** | M1 0, M2 0 |
| Routed vias over poly (document rule, deck doesn't flag) | **PASS** | 0 of 1773: 0 at pin centres, 0 elsewhere |
| Routed vias over contact | **PASS** | 0 |
| One top cell, extent exactly the die | **PASS** | ['OS0000'], (0,0;2000,2000) |
| Timing at three corners, 10 MHz, flip-flop to flip-flop | **PASS** | Setup and hold met at 100 ns, flip-flop to flip-flop. Wire caps from routed length and LEF values, 30 pF assumed per output |
| Core slew under SCL's 2.5 ns limit | **PASS** | min 0 pins, worst 0.00 ns; typ 0 pins, worst 0.00 ns; max 0 pins, worst 0.00 ns |
| Max capacitance on core nets | **PASS** | min 0; typ 0; max 0 |
| Wire capacitance from OpenRCX with SCL's rules | **FAIL** | 0.14 pF, against 26.9 pF from 107544 um of routed wire and LEF values. Not used for timing: see flow-status.md |
| Pad timing to the RAM at 10 MHz | **PASS** | Smallest margin 7.3 ns. 30 pF per output assumed, no board delay |
| SCK gate structure in the routed netlist | **PASS** | One multi-input cell, OR2101, fed by the clock and one flip-flop. Truth table right. Cone: DELBUF -> DELBUF -> OR2101 -> INVR01 -> INVR04. On its clock input: nothing. On its idle input: DELBUF wire36, DELBUF fanout35 |
| SCK gate margins at the gate, three corners (hard stop at the freeze) | **PASS** | Smallest: setup 40.11 ns, hold 4.61 ns |
| Output edge at the pad, against the RAM's 20 ns limit | **PASS** | Worst 11.7 ns, with 30 pF assumed. min 5.7; typ 8.1; max 11.7 |
| Post-route simulation, routed netlist, smoke test | **PASS** | Printed values, halt and gpo checked against each program's expected list. RTL: Fibonacci ok, ALU ok. Routed: Fibonacci ok, ALU ok. No delays, random flip-flop start |
| Antenna rules | **NOT RUN** | Not in SCL's deck or documents, so nothing to check against |
| Metal necking, 12 um bus on one contact, ERC guidelines | **NOT RUN** | Manual review, due at G4 |
| Current density | **NOT RUN** | No real activity figures yet |
| Silicon number block | **NOT RUN** | Not issued yet. Top cell name is a placeholder, OS0000 |

## Timing by corner

Period 100 ns. Limits exclude the output pads' own 2.5 ns entry: see `flow-status.md`.

| Corner | Min period (ns) | Max MHz | Setup slack (ns) | Hold slack (ns) | Core pins over 2.5 ns | Cap violators | Worst core slew (ns) |
|---|---:|---:|---:|---:|---:|---:|---:|
| fast (5.5 V, -20 C) | 17.20 | 58.12 | 82.80 | 1.06 | 0 | 0 | 0.00 |
| typical (5 V, 27 C) | 21.25 | 47.06 | 78.75 | 2.17 | 0 | 0 | 0.00 |
| slow (4.5 V, 85 C) | 24.26 | 41.22 | 75.74 | 2.60 | 0 | 0 | 0.00 |

## Pad timing to the RAM at 10 MHz

Margins in ns, for SCK = NOT(CLK OR idle), with 30 pF on each output and no board delay. Arrival times run from the CLK pad, with CLK's duty cycle at 50%.
Industrial-grade RAM limits from DS20005155C Table 1-2, checked in `spec/cpu-status.md`: setup 10, hold 10, CS setup 25, CS hold 50, output valid 25, output hold 0 ns. The CS hold column conservatively counts from the last SCK fall; the datasheet counts from the preceding rise. Extended-grade limits are checked separately by the routed RAM-model runs.

| Corner | SCK arrival | MOSI arrival | CS arrival | MOSI setup | MOSI hold | CS setup | CS hold | MISO setup | MISO hold |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| fast (5.5 V, -20 C) | rise 9.1-9.1, fall 6.7-6.7 | 9.4-21.8 | 8.4-10.2 | 27.3 | 40.4 | 23.9 | 51.7 | 56.0 | 7.3 |
| typical (5 V, 27 C) | rise 12.6-12.6, fall 9.8-9.8 | 15.0-32.3 | 14.5-16.2 | 20.3 | 42.4 | 21.3 | 54.7 | 52.3 | 10.7 |
| slow (4.5 V, 85 C) | rise 15.2-15.2, fall 10.9-10.9 | 16.7-36.6 | 16.4-19.2 | 18.6 | 41.4 | 21.0 | 55.5 | 50.8 | 12.3 |

### SCK gate, measured at the gate's own inputs

Times in ns from the CLK pad's edge. Setup: `idle` must settle before CLK falls at the gate. Hold: `idle` must not change before CLK rises at the gate. If hold fails, SCK gets an extra pulse in each frame and no RAM read works.

| Corner | CLK at the gate | `idle` at the gate | Setup margin | Hold margin |
|---|---|---|---:|---:|
| fast (5.5 V, -20 C) | rise 3.15, fall 3.45 | 7.76-7.80 | 45.65 | 4.61 |
| typical (5 V, 27 C) | rise 4.31, fall 5.02 | 13.74-13.98 | 41.04 | 9.43 |
| slow (4.5 V, 85 C) | rise 4.81, fall 6.12 | 15.97-16.01 | 40.11 | 11.16 |

## Checker items needing an answer

| Status | ID | Item |
|---|---|---|
| WARN | C04.2 | 657 off-grid coordinates in unmodified SCL pad cells |
| WARN | C04.3 | 44 off-grid coordinates in unmodified SCL standard cells |
| WARN | C04.4 | 22 off-grid coordinates from SCL's reference frame |
| MANUAL | C07.2 | MD5 report not compared |
| WARN | C09.3 | SCL hasn't confirmed which pad frame set is correct |
| WARN | C10.2 | 12 signal pad changes from the reference frame |
| MANUAL | C15.1 | Silicon number block not checked |
| WARN | C16.2 | Pad metal is over 25.000 x 25.000 um; the PDK doesn't say if pads are exempt |
| WARN | C16.3 | Frame, logo, block or standard cell metal is over 25.000 x 25.000 um |
| WARN | C17.2 | SCL's own cells have via over contact or poly. Ask SCL whether they are accepted |
| MANUAL | C18.1 | Metal necking at single diffusion contacts |
| MANUAL | C18.2 | Metal 1 bus width on a single diffusion contact |
| MANUAL | C18.3 | ERC guidelines |
| MANUAL | C18.4 | All electrical checks |
| MANUAL | C20.1 | DRC and LVS must still be run separately |
