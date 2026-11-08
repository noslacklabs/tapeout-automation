# Project contract, schema 1

`load_project(path)` resolves project-relative paths independently of the caller's
working directory and returns a frozen `ProjectConfig` with JSON-safe `to_dict()`
and an ordered `dependencies` tuple. The foundation rejects unknown keys,
unsupported versions, asynchronous reset declarations, missing inputs, unsafe
output roots, duplicate constraints and nonfinite timing values.

The following example describes the syntax. Electrical values are illustrative
inputs for a fixture, not qualified defaults for a new chip:

```toml
schema_version = 1
[project]
name = "counter"
top = "counter"
sources = ["counter.v"]
include_dirs = ["include"]
defines = ["WIDTH=4"]
parameters = { WIDTH = 4 }
extra_files = []
[technology]
id = "scl_c1d"
frame = "scl-c1d-2mm"
profile_version = "0.1.0"
top_cell = "OS0000"
mode = "development"
development_identifiers = true
[clock]
port = "tick"
period_ns = 100.0
waveform_ns = [0.0, 50.0]
transition_ns = 1.0
uncertainty_ns = 0.5
[reset]
port = "reset_high"
kind = "synchronous"
active = "high"
[io]
corners = ["min", "typ", "max"]
inputs = [{ port = "reset_high", min_ns = 1.0, max_ns = 3.0 }]
outputs = [{ port = "value[0]", min_ns = 1.0, max_ns = 5.0, load_pf = 10.0 }]
[pads]
mapping = "pads.toml"
[build]
root = "builds"
seed = 7
threads = 1
timeout_seconds = 60
[verification]
required_checks = ["counter.behavior"]
optional_checks = ["formal"]
manual_items = ["Confirm board loads"]
data = ["expected.json"]
```

All tables shown are required; optional project and verification arrays may be
omitted. Sources are ordered `.v`/`.sv` files. Include directories and files must
stay within the project after symlink resolution. Every file in declared include
directories is captured. Explicit `extra_files` and verification `data` capture
testbench/model/config dependencies. Complete discovered Verilog includes and
external test dependencies will need reconciliation by the elaboration adapters.

Clock/reset names are scalar Verilog identifiers. Data port constraints may use
explicit signed bus indices. The initial structural contract requires finite,
nonnegative input/output delays with min <= max, positive loads/transitions,
all three corners, and a min/max constraint for the synchronous reset. Clock
data delays are forbidden. Full RTL port/bus, state and clock coverage remain NOT RUN.
Parameter values are integer or single-line string; the future elaborator must
validate their supported Yosys representation before execution.

`build.root` is a dedicated project subdirectory. It cannot contain an input or
overlap an include directory; execution additionally protects the PDK and package.
The profile minimum checks are always added. Projects can add requirements but
cannot mark a minimum check optional.

At this stage `pads.mapping` is captured as an external input, with its semantics
explicitly unqualified. No pad cell, driver, polarity or unused-slot policy has
been approved by this loader. Full validation stays incomplete until every slot
is accounted for against a tested frame profile. The example is not an acceptance
counter project and does not establish supported electrical/physical behavior.
