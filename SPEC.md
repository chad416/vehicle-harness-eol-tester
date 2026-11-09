# SPEC: vehicle-harness-eol-tester

Rev B, 9 Nov 2026. (Rev A: first draft. Rev B: interlock, miswire alternatives, live-bus allowance and fault syntax as built.)

## 1. Purpose

This is an end-of-line (EOL) electrical test station for a vehicle cab, built in simulation. It runs in two stages:
- It tests the bare harness against its 2D schematic: continuity, miswires, shorts, ground bonds, insulation and dielectric withstand.
- With the control modules fitted, it checks CAN termination, power-up current and the J1939-style CAN network: address claim, cyclic messages, DM1 faults, signal plausibility, software versions and command/response.

For every cab it writes a report and a machine-readable record. A batch mode measures detection, localisation and false rejects over many simulated cabs.

Everything here is simulated: the harness, the instruments and the control units. Section 8 says what that means.

## 2. Unit under test

A generic cab electrical system, defined by data and not by code:

| File | Content |
|---|---|
| `data/variants/<v>/connectors.csv` | Connectors, pin counts, and which module plugs into each |
| `data/variants/<v>/netlist.csv` | One row per wire: wire id, net, from, to (`X1:12`, splice `SP3`, ground stud `GS1`), length, cross-section, colour |
| `data/variants/<v>/recipe.yaml` | Test limits, insulation groups, expected CAN nodes (address, NAME function, software version, termination), current bands, signal ranges |
| `data/protocol/cab.dbc` | Cyclic CAN messages (29-bit ids, proprietary-B PGNs) with `GenMsgCycleTime` |

There are two variants:
- `cmd`: a command cab with a remote-control gateway module (RCG).
- `base`: no RCG, and the CAN bus terminates in the HVAC module.

`tools/gen_harness.py` generates both netlists from a seed, so they can be rebuilt. The CSV files are checked in, as if they had been exported from a wiring CAD tool.

## 3. Requirements

| ID | Requirement |
|---|---|
| F01 | Load the variant's connectors, netlist, recipe and DBC. Reject inconsistent data (unknown pin, a pin used twice, a recipe node missing from the DBC). |
| F02 | Continuity: every pin of a net is connected to the rest of that net. Report each **open** with the pins cut off and the wire(s) that can cause it. |
| F03 | Miswire: detect two pins swapped at a connector, and report them as `Xn:a <-> Xn:b`, not as two opens plus a short. When a swap at the other end of the same two wires looks identical, report that pair as an equal alternative. |
| F04 | Isolation between nets: report a **hard short** (nets merged) or a **resistive leak** (10 Ω to 100 kΩ), naming both nets. |
| F05 | Wire resistance: pin-to-pin resistance must not exceed the value computed from the netlist by more than `max_extra_ohm`. Report **high resistance** and name the wire most likely at fault. |
| F06 | Ground bond: every chassis-bonded pin measures ≤ `bond_max_ohm` to the chassis. |
| F07 | Insulation resistance: each insulation group measured against everything else at `ir_test_v` DC must reach `ir_min_ohm` or more. |
| F08 | Dielectric withstand: each group at `hipot_v` for `hipot_dwell_s`, no breakdown and leakage ≤ `hipot_max_a`. Groups that failed F07 are not hi-potted. |
| F09 | Interlock: no high voltage (F07, F08) after a short or miswire in S1. No power-up unless S1–S3 pass. No CAN network test if power-up found an overcurrent. A blocked step is reported as `BLOCKED`, not `PASS`. The termination check (F10) is passive and always runs. |
| F10 | CAN termination, power off, modules fitted: CAN_H–CAN_L at the diagnostic connector within `term_ohm ± term_tol`. |
| F11 | Power-up: the current of each distribution circuit at idle lies within its recipe band. Report `OVERCURRENT` or `UNDERCURRENT`. |
| F12 | Address claim: every expected node claims its address with the expected NAME function. Report a missing node, a wrong address, an unexpected node and an address conflict (Cannot Claim). |
| F13 | Cyclic messages: for every DBC message of a present node, the mean period is within `cycle_tol` and the longest gap ≤ `max_gap_factor` × period. A missing message is a failure. On live buses, the gap limit gets a fixed scheduling allowance (150 ms), because the simulated units run as a Python thread on a non-real-time OS. The mean-period limit is not relaxed. |
| F14 | DM1: decode active DTCs (SPN, FMI, OC) of every node. Any active DTC fails, and its SPN and FMI are reported. |
| F15 | Signal plausibility: the decoded signals lie inside their recipe ranges (for example: gateway in LOCAL mode, e-stop chain OK, no breaker trip), and the gateway alive counter keeps changing. |
| F16 | Command/response: each node answers a destination-specific GET_VERSION request within `resp_timeout_s` with the recipe software version. The answer also carries the node's termination setting, which localises a termination fault (F10) to one module. |
| F17 | One record per serial: a JSON result (verdict, steps, measurements with limits, findings with locations) and a PDF report. |
| F18 | Batch: simulate N cabs with random faults (seeded), run test → repair what was diagnosed → retest (at most 3 loops). Report per fault type: detection, correct localisation, escapes, false rejects on good cabs, first-pass yield, a Pareto of findings, detection against fault size, and modelled test time. |
| F19 | The CAN steps run unchanged on three buses: a virtual-time bus (fast, deterministic), python-can `virtual` (real threads, wall time, any OS) and SocketCAN (`vcan0` on Linux). |
| F20 | Same seed, same results (virtual-time bus). |
| P01 | One cab on the virtual-time bus takes < 2 s wall time. |
| P02 | A batch of 100 cabs takes < 60 s wall time in CI. |

## 4. Measurement model (simulated instruments)

- **Harness:** a resistor network. Each wire has R = ρ·L/A (ρ = 0.0178 Ω·mm²/m) plus 0.5 mΩ per crimp. Splices are internal nodes that can't be probed. Ground stud `GS1` is bonded to `CHASSIS` (1 mΩ).
- **Continuity meter:** 4-wire, range 10 MΩ (above that it reads open). Noise is 0.5 % + 1 mΩ. Pin-to-pin resistance is the effective resistance of the network (Laplacian pseudo-inverse per connected component).
- **Insulation meter:** each group is measured against all other nodes (chassis and other nets together). The nominal insulation leak is proportional to wire length (2·10¹¹ Ω·m). The reading is capped at 10 GΩ.
- **Hi-pot:** leakage = V / IR. A wire with damaged insulation breaks down when the test voltage exceeds its breakdown voltage, even if its IR at 500 V is fine.
- **Power supply:** 27.0 V. The current per circuit is the module's idle current (recipe nominal ± 3 % noise) or 0 A for a module that isn't fitted.
- **CAN nodes:**
  - ECU objects send their DBC messages at `GenMsgCycleTime` (random phase, 0.2 ms jitter on the virtual-time bus) and DM1 at 1 s.
  - They claim their address at power-up and on request, and resolve address conflicts by NAME (the lower NAME wins, the loser sends Cannot Claim at address 254 and goes silent).
  - They answer GET_VERSION over proprietary-A after 5–15 ms.
  - The tester uses source address 0xF9.

## 5. Fault catalogue (injectable; used by tests and batch)

The syntax is `kind:arg/arg`; pins keep their `X1:5` form.

| Fault | Effect | Expected finding |
|---|---|---|
| `open:W017` | wire removed | `OPEN` (F02) |
| `highres:W017/0.5` | +R on a wire | `HIGH_RES` (F05), if above the limit |
| `short:X1:5/X3:13`, `short:X2:5/CHASSIS` | 0.05 Ω between two pins (or a pin and the body) | `SHORT` (F04); high voltage then blocked |
| `leak:W040/5e6` | resistance from a wire end to chassis | `LEAK` (F04) if < 100 kΩ, `IR_LOW` (F07) if < `ir_min_ohm` |
| `miswire:X1:5/X1:6` | terminations of two pins swapped | `MISWIRE` (F03) |
| `damaged:W041/700` | insulation breaks down above V | `BREAKDOWN` (F08) |
| `stud:0.3` | loose ground stud | `BOND` (F06), `HIGH_RES` on GND |
| `noterm:RCG` / `extraterm:LIGHT` | termination missing / extra | `TERMINATION` (F10), `TERMINATION_CONFIG` (F16) |
| `idle:HVAC/3.0` / `unplugged:LIGHT` | wrong module current / module not fitted | `OVERCURRENT` / `UNDERCURRENT` (F11), `NODE_MISSING` (F12) |
| `addr:HVAC/0x41` | node configured to a wrong address | `WRONG_ADDRESS` (F12) |
| `conflict:HVAC/DASH` | node claims another node's address | `ADDRESS_CONFLICT` (F12) |
| `cycle:PDU/1.5` | node's cyclic period × factor | `CYCLE_TIME` (F13) |
| `dtc:LIGHT/520300/7` | node reports an active DTC | `ACTIVE_DTC` (F14) |
| `remote` | gateway reports REMOTE mode | `SIGNAL_RANGE` (F15) |
| `alive` | gateway alive counter stuck | `ALIVE_COUNTER` (F15) |
| `sw:PDU/3.2.2` | wrong software version | `SW_VERSION` (F16) |
| `noresp:DASH` | node ignores GET_VERSION | `NO_RESPONSE` (F16) |

## 6. Tests

| ID | What |
|---|---|
| UT-* | Unit tests in `tests/unit`: J1939 id, NAME and DM1 encoding; data loading; network resistance; each diagnosis rule; each CAN check; report output |
| IT01 | Good `cmd` and `base` cabs pass every step (virtual-time bus) |
| IT02 | Every fault in §5 on its own fails the cab, with the expected finding and location |
| IT03 | Interlock: harness faults block power-up and CAN, and the report says BLOCKED |
| IT04 | Repair by diagnosis: after repairing what was reported, the retest passes |
| IT05 | python-can `virtual` bus: a good cab passes and one with CAN faults fails, in wall time |
| IT06 | SocketCAN `vcan0` (Linux only; skipped when it is absent): same as IT05 |
| IT07 | Batch of 100 cabs, seed 1: no false rejects, no escapes above the detection limits, results stable across runs |
| IT08 | P01 and P02 timing |

`docs/traceability.md` is generated from the `@pytest.mark.req(...)` markers by `tools/traceability.py`, and CI checks that it is up to date.

## 7. Outputs

- `out/<serial>.json`: the record (F17)
- `out/<serial>.pdf`: a 2-page report (summary and findings; measurements and CAN timing)
- `results/batch_seed<N>/summary.json` and the PNG charts (F18)

## 8. Limits (honest)

- **The physics is simplified.** The harness is a pure resistor network: no capacitance, no inductance, no temperature coefficient. Insulation is a linear resistance with a single breakdown threshold.
- **The physical CAN layer is not modelled.** A broken CAN wire shows up in the harness tests and through the termination value, not as bus errors. The interlock blocks the CAN tests whenever the harness has failed, so this gap doesn't let a faulty cab pass.
- **DM1 is limited to one DTC per node** (single frame). A multi-DTC DM1 needs the transport protocol (BAM), which is not implemented.
- **The limits are placeholders** (for example, IR ≥ 100 MΩ at 500 V, hi-pot 1000 V DC). They are not taken from a real vehicle standard. A real recipe takes them from the customer's specification.
- **The J1939 parts are a subset.** Only the id layout, the PGN address claim, request, DM1 and proprietary A/B are used. The cyclic messages and SPNs are made up (proprietary range), not standard SPNs.
- **The test times are modelled** from recipe parameters, not measured on hardware.
