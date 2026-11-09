"""Buses the CAN steps run on (F19).

`VirtualTimeBus` is a discrete-event bus in simulated time: fast and deterministic.
`LiveBus` runs the same ECU objects in a thread on a real python-can bus
(`virtual` on any OS, `socketcan` on Linux) and measures in wall-clock time.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass

from .ecu_sim import SimEcu


@dataclass(frozen=True)
class Frame:
    t: float
    can_id: int
    data: bytes
    origin: str  # ECU name, "tester" or "bus"


class VirtualTimeBus:
    def __init__(self, ecus: list[SimEcu]):
        self.ecus = ecus
        self.t = 0.0
        self._q: list = []
        self._seq = itertools.count()
        self.log: list[Frame] = []

    def _push(self, origin, frames):
        for t, cid, data in frames:
            heapq.heappush(self._q, (max(t, self.t), next(self._seq), origin, cid, bytes(data)))

    def power_on(self) -> None:
        for e in self.ecus:
            self._push(e, e.power_on(self.t))

    def send(self, can_id: int, data: bytes) -> None:
        self._emit(Frame(self.t, can_id, bytes(data), "tester"), None)

    def _emit(self, fr: Frame, origin_ecu) -> None:
        self.log.append(fr)
        for e in self.ecus:
            if e is not origin_ecu:
                self._push(e, e.on_frame(fr.t, fr.can_id, fr.data))

    def collect(self, duration: float) -> list[Frame]:
        t_end = self.t + duration
        start = len(self.log)
        while True:
            due = [(d, e) for e in self.ecus if (d := e.next_due()) is not None]
            t_per = min(due, key=lambda x: x[0])[0] if due else float("inf")
            t_q = self._q[0][0] if self._q else float("inf")
            if min(t_per, t_q) > t_end:
                break
            if t_per < t_q:
                for d, e in due:
                    if d <= t_per:
                        self._push(e, e.poll(t_per))
                continue
            t, _, origin, cid, data = heapq.heappop(self._q)
            self.t = t
            self._emit(Frame(t, cid, data, origin.m.ecu), origin)
        self.t = t_end
        return [f for f in self.log[start:] if f.origin != "tester"]

    def close(self) -> None:
        pass


class LiveBus:
    """python-can bus; the ECUs run in a background thread on their own bus handle."""

    def __init__(self, ecus: list[SimEcu], interface: str = "virtual", channel: str = "eol"):
        import can

        self.ecus = ecus
        self._can = can
        self.tester = can.Bus(interface=interface, channel=channel)
        self._ecu_bus = can.Bus(interface=interface, channel=channel)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.t0 = time.time()
        self.log: list[Frame] = []

    def power_on(self) -> None:
        self.t0 = time.time()
        self._thread = threading.Thread(target=self._run, name="ecu-sim", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        q: list = []
        seq = itertools.count()
        t0 = time.perf_counter()

        def push(origin, frames):
            for t, cid, data in frames:
                heapq.heappush(q, (t, next(seq), origin, cid, bytes(data)))

        for e in self.ecus:
            push(e, e.power_on(0.0))
        while not self._stop.is_set():
            now = time.perf_counter() - t0
            for e in self.ecus:
                d = e.next_due()
                if d is not None and d <= now:
                    push(e, e.poll(now))
            while q and q[0][0] <= now:
                _, _, origin, cid, data = heapq.heappop(q)
                self._ecu_bus.send(self._can.Message(arbitration_id=cid, data=data, is_extended_id=True))
                for e in self.ecus:
                    if e is not origin:
                        push(e, e.on_frame(now, cid, data))
            # Non-blocking read + short sleep: a blocking recv(timeout) is rounded to the
            # 15.6 ms timer tick on Windows, time.sleep() uses a high-resolution timer.
            msg = self._ecu_bus.recv(timeout=0.0)
            if msg is None:
                time.sleep(0.0005)
            elif msg.is_extended_id:
                now = time.perf_counter() - t0
                for e in self.ecus:
                    push(e, e.on_frame(now, msg.arbitration_id, bytes(msg.data)))

    def send(self, can_id: int, data: bytes) -> None:
        self.tester.send(self._can.Message(arbitration_id=can_id, data=bytes(data), is_extended_id=True))

    def collect(self, duration: float) -> list[Frame]:
        out: list[Frame] = []
        deadline = time.time() + duration
        while (left := deadline - time.time()) > 0:
            msg = self.tester.recv(timeout=min(left, 0.05))
            if msg is None or not msg.is_extended_id:
                continue
            ts = msg.timestamp if msg.timestamp else time.time()
            out.append(Frame(ts - self.t0, msg.arbitration_id, bytes(msg.data), "bus"))
        self.log.extend(out)
        return out

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.tester.shutdown()
        self._ecu_bus.shutdown()
