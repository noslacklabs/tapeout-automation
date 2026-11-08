# Known limitations

The current reference binding is [reference/milestone1/limitations.json](../reference/milestone1/limitations.json).
The initial historical binding is preserved in `reference/s8/limitations.json`.
Its scope binds the process version and historical S8 input hashes. It records
the raw extraction FAIL, eight checker warnings, seven checker manual items,
pad/clock model limits, model/simulation workarounds and the unfrozen RTL blocker.

`SCL-C1D-RCX-001` maps to `parasitic_validation`. The historical raw total was
0.13 pF versus a 24.3 pF routed-length/LEF estimate. That is a recorded failure,
not an independently established electrical reference or validated scaling fix.
The next full build must reproduce a specific failed plausibility check and exit
1 if this is the only failure and all other required evidence is complete.
A crash, missing output or new failure cannot count as that expected outcome.

Estimated/scaled timing is development evidence. It must retain failed extraction
provenance and cannot make the chip pass. Raw extraction command execution,
artifact structure/units/coverage, and electrical plausibility remain separate gates.

Pad driver/load/slew and clock buffer fanout/capacitance interpretations still
need reviewed resolution. DC polarity tests establish their limited measurements
only. Missing pad simulation models, seeded startup overrides, and the old smoke
bench's behavioral flop substitution must remain explicit; the substitution
cannot qualify SDF timing arcs. The unused-slot policy has not been qualified.

The older physical source differs from current RTL. A new immutable engineering
snapshot has now been built and verified under milestone 1, without relabeling
old results. Fresh isolated Ubuntu binary-runtime installation/startup/probes
pass; a clean WSL release installation and independent EDA source rebuild are not
claimed. Full `chipflow` elaboration, physical stages and verification adapters
remain unfinished.
