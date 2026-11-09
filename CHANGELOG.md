# Changelog

## 0.1.0 (9 Nov 2026)

First release.
- **Harness stage:**
  - Continuity, with open localisation down to the wire.
  - Miswire detection, reporting the equal alternative at the other end when there is one.
  - Hard shorts and resistive leaks.
  - Wire resistance against the netlist, with high-resistance localisation.
  - Ground bond.
  - Insulation resistance and dielectric withstand.
- **Fitted stage:**
  - CAN termination.
  - Power-up current per circuit.
  - J1939-style network check: address claim and conflicts, cyclic timing, DM1, signal plausibility, alive counter, software version, and the termination setting per node.
- **Interlocks:**
  - No high voltage after a short or miswire.
  - No power-up after a harness or insulation fault.
  - No CAN check after an overcurrent.
- **Buses:** the CAN step runs on a virtual-time bus, on the python-can `virtual` bus and on SocketCAN `vcan0`.
- **Outputs and evaluation:**
  - A JSON record and a 2-page PDF report per serial.
  - Batch evaluation with test → repair → retest, and a detection-against-size sweep.
- **Tests:** 82 tests and SPEC traceability. CI covers lint, generator and traceability checks, tests, demo runs and the batch.
