#!/usr/bin/env python3
"""
  ▗▄▄▖ ▗▖  ▗▖ ▗▄▄▖▗▖  ▗▖ ▗▄▖ ▗▖  ▗▖
 ▐▌   ▝▚▗▞▘▐▌   ▐▛▚▞▜▌▐▌ ▐▌▐▛▚▖▐▌
  ▝▀▚▖  ▐▌ ▐▌   ▐▌  ▐▌▐▌ ▐▌▐▌ ▝▜▌
 ▗▄▄▞▘  ▐▌  ▝▚▄▄▖▐▌  ▐▌▝▚▄▞▘▐▌  ▐▌

 A beautiful, low-overhead terminal system monitor.
 Prettier than top. Faster than htop.

 Usage:   python sysmon.py
 Needs:   pip install textual psutil
"""

from __future__ import annotations

import time
import platform
from datetime import datetime
from collections import deque
from typing import Optional

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

# ─── Constants ────────────────────────────────────────────────────────────────

SPARK       = "▁▂▃▄▅▆▇█"
HIST_LEN    = 60
INTERVAL    = 1.0          # seconds between updates
BAR_FULL    = "█"
BAR_EMPTY   = "░"

# ─── Colour helpers ──────────────────────────────────────────────────────────

def pct_color(p: float) -> str:
    return "red" if p >= 85 else "yellow" if p >= 60 else "green"

def temp_color(t: float) -> str:
    return "red" if t >= 85 else "yellow" if t >= 70 else "green"

# ─── Render helpers ──────────────────────────────────────────────────────────

def bar(pct: float, w: int = 20) -> str:
    """Render a coloured block-bar."""
    filled = round(max(0, min(100, pct)) / 100 * w)
    c = pct_color(pct)
    return f"[{c}]{BAR_FULL * filled}[/{c}][dim]{BAR_EMPTY * (w - filled)}[/dim]"

def sparkline(hist: list, w: int = 40) -> str:
    """Render a unicode sparkline from a list of 0-100 floats."""
    vals = list(hist)[-w:]
    vals = [0.0] * (w - len(vals)) + vals
    return "".join(SPARK[min(int(v / 100 * 7), 7)] for v in vals)

def fbytes(n: float) -> str:
    """Format bytes to human-readable string."""
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:6.1f}{unit}"
        n /= 1024
    return f"{n:6.1f}P"

def fspeed(n: float) -> str:
    return f"{fbytes(n)}/s"

# ─── Data Collector ──────────────────────────────────────────────────────────

class Collector:
    """
    Gathers all system metrics.
    Designed to be called once per second with zero blocking I/O.
    """

    def __init__(self):
        self.boot_time     = psutil.boot_time()
        self.cpu_hist      : deque = deque(maxlen=HIST_LEN)
        self.rx_hist       : deque = deque(maxlen=HIST_LEN)
        self.tx_hist       : deque = deque(maxlen=HIST_LEN)
        self._prev_net     = psutil.net_io_counters()
        self._prev_net_t   = time.monotonic()
        self._peak_net     = 1.0

        # Prime psutil CPU (first call always returns 0.0)
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)

    # ── public ──────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a fresh dict of all metrics. Call every ~1 s."""
        d: dict = {}
        self._collect_cpu(d)
        self._collect_memory(d)
        self._collect_disk(d)
        self._collect_network(d)
        self._collect_processes(d)
        self._collect_meta(d)
        return d

    # ── private ─────────────────────────────────────────────────────────

    def _collect_cpu(self, d: dict):
        d["cpu"]      = psutil.cpu_percent(interval=None)
        d["cpus"]     = psutil.cpu_percent(percpu=True, interval=None)
        d["cpu_n"]    = psutil.cpu_count(logical=True)
        d["cpu_phys"] = psutil.cpu_count(logical=False) or 1
        self.cpu_hist.append(d["cpu"])
        d["cpu_hist"] = list(self.cpu_hist)

        freq = psutil.cpu_freq()
        if freq:
            d["cpu_ghz"] = freq.current / 1000 if freq.current > 100 else freq.current
        else:
            d["cpu_ghz"] = None

        d["cpu_temp"] = self._cpu_temp()

    def _collect_memory(self, d: dict):
        m = psutil.virtual_memory()
        s = psutil.swap_memory()
        d.update(
            mem_pct    = m.percent,
            mem_used   = m.used,
            mem_total  = m.total,
            mem_avail  = m.available,
            mem_cached = getattr(m, "cached", 0),
            swap_pct   = s.percent,
            swap_used  = s.used,
            swap_total = s.total,
        )

    def _collect_disk(self, d: dict):
        skip_fs = {"squashfs", "tmpfs", "devtmpfs", ""}
        disks = []
        for part in psutil.disk_partitions(all=False):
            if part.fstype in skip_fs:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
                disks.append(dict(
                    mount = part.mountpoint[:16],
                    pct   = u.percent,
                    used  = u.used,
                    total = u.total,
                ))
            except (PermissionError, OSError):
                pass
        d["disks"] = disks[:4]

    def _collect_network(self, d: dict):
        now = time.monotonic()
        net = psutil.net_io_counters()
        dt  = max(now - self._prev_net_t, 0.001)
        rx  = max((net.bytes_recv - self._prev_net.bytes_recv) / dt, 0)
        tx  = max((net.bytes_sent - self._prev_net.bytes_sent) / dt, 0)
        self._prev_net, self._prev_net_t = net, now
        self._peak_net = max(self._peak_net, rx, tx, 1)
        self.rx_hist.append(rx / self._peak_net * 100)
        self.tx_hist.append(tx / self._peak_net * 100)
        d.update(
            net_rx   = rx,
            net_tx   = tx,
            rx_hist  = list(self.rx_hist),
            tx_hist  = list(self.tx_hist),
            net_total_rx = net.bytes_recv,
            net_total_tx = net.bytes_sent,
        )

    def _collect_processes(self, d: dict):
        procs = []
        attrs = ["pid", "name", "cpu_percent", "memory_info", "status", "username"]
        for p in psutil.process_iter(attrs):
            try:
                i = p.info
                procs.append(dict(
                    pid    = i["pid"],
                    name   = (i["name"] or "?")[:24],
                    cpu    = i["cpu_percent"] or 0.0,
                    mem    = i["memory_info"].rss if i["memory_info"] else 0,
                    status = i["status"] or "?",
                    user   = (i["username"] or "?")[:14],
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        d["procs"]   = procs[:80]
        d["proc_n"]  = len(procs)

    def _collect_meta(self, d: dict):
        up = int(time.time() - self.boot_time)
        h, rem = divmod(up, 3600)
        m, s = divmod(rem, 60)
        d["uptime"] = f"{h}h {m:02d}m {s:02d}s"
        d["host"]   = platform.node()
        d["os"]     = f"{platform.system()} {platform.release()}"
        d["arch"]   = platform.machine()
        d["time"]   = datetime.now().strftime("%H:%M:%S")

    def _cpu_temp(self) -> Optional[float]:
        try:
            sensors = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "cpu-thermal"):
                t = sensors.get(key)
                if t:
                    return t[0].current
        except Exception:
            pass
        return None


# ─── Widgets ─────────────────────────────────────────────────────────────────

class TopBar(Static):
    """One-line header: logo · hostname · OS · uptime · avg cpu · clock."""

    def refresh_data(self, d: dict):
        c    = pct_color(d["cpu"])
        temp = ""
        if d.get("cpu_temp"):
            tc   = temp_color(d["cpu_temp"])
            temp = f"  [dim]·[/dim]  [{tc}]{d['cpu_temp']:.0f}°C[/{tc}]"

        self.update(
            f"[bold cyan]◆ sysmon[/bold cyan]"
            f"  [dim]│[/dim]  [bold white]{d['host']}[/bold white]"
            f"  [dim]{d['os']} {d['arch']}[/dim]"
            f"  [dim]│[/dim]  up [green]{d['uptime']}[/green]"
            f"  [dim]│[/dim]  cpu [{c}]{d['cpu']:.1f}%[/{c}]"
            f"{temp}"
            f"  [dim]│[/dim]  [bold]{d['time']}[/bold]"
        )


class CpuPanel(Static):
    """Left column: per-core bars, history sparkline, freq/temp."""

    def refresh_data(self, d: dict):
        cpus = d["cpus"]
        total = d["cpu"]
        lines = [
            f"[bold cyan]  CPU[/bold cyan]  "
            f"[dim]{d['cpu_n']} logical  {d['cpu_phys']} physical[/dim]",
            "",
        ]

        for i, pct in enumerate(cpus):
            col = pct_color(pct)
            lines.append(
                f"  [dim]c{i:<2}[/dim]  {bar(pct, 16)}  [{col}]{pct:5.1f}%[/{col}]"
            )

        lines.append("")
        col = pct_color(total)
        lines.append(
            f"  [dim]avg[/dim]  {bar(total, 16)}  [{col}]{total:5.1f}%[/{col}]"
        )
        lines.append("")

        # History sparkline
        sp = sparkline(d["cpu_hist"], 32)
        lines.append(f"  [dim cyan]{sp}[/dim cyan]")
        lines.append(f"  [dim]{'─'*32} {HIST_LEN}s[/dim]")
        lines.append("")

        # Meta: freq + temp
        meta = []
        if d.get("cpu_ghz"):
            meta.append(f"[white]{d['cpu_ghz']:.2f} GHz[/white]")
        if d.get("cpu_temp"):
            tc = temp_color(d["cpu_temp"])
            meta.append(f"[{tc}]{d['cpu_temp']:.0f}°C[/{tc}]")
        if meta:
            lines.append("  " + "  [dim]·[/dim]  ".join(meta))

        self.update("\n".join(lines))


class MemPanel(Static):
    """RAM + swap usage bars with stats."""

    def refresh_data(self, d: dict):
        lines = ["[bold cyan]  Memory[/bold cyan]", ""]
        c = pct_color(d["mem_pct"])
        lines += [
            f"  [dim]RAM [/dim]  {bar(d['mem_pct'], 22)}  [{c}]{d['mem_pct']:5.1f}%[/{c}]",
            f"  [dim]        {fbytes(d['mem_used'])} / {fbytes(d['mem_total'])}"
            f"  ·  avail {fbytes(d['mem_avail'])}[/dim]",
            "",
        ]
        if d["swap_total"]:
            sc = pct_color(d["swap_pct"])
            lines += [
                f"  [dim]SWAP[/dim]  {bar(d['swap_pct'], 22)}  [{sc}]{d['swap_pct']:5.1f}%[/{sc}]",
                f"  [dim]        {fbytes(d['swap_used'])} / {fbytes(d['swap_total'])}[/dim]",
            ]
        else:
            lines.append("  [dim]SWAP  ─  not configured[/dim]")
        self.update("\n".join(lines))


class NetPanel(Static):
    """Network rx/tx speeds with dual sparklines."""

    def refresh_data(self, d: dict):
        lines = ["[bold cyan]  Network[/bold cyan]", ""]
        lines += [
            f"  [green]▼ rx[/green]  [bold green]{fspeed(d['net_rx'])}[/bold green]"
            f"  [dim]total {fbytes(d['net_total_rx'])}[/dim]",
            f"  [cyan]▲ tx[/cyan]  [bold cyan]{fspeed(d['net_tx'])}[/bold cyan]"
            f"  [dim]total {fbytes(d['net_total_tx'])}[/dim]",
            "",
            f"  [dim green]{sparkline(d['rx_hist'], 30)}[/dim green]",
            f"  [dim cyan]{sparkline(d['tx_hist'], 30)}[/dim cyan]",
            f"  [dim]{'─'*30} {HIST_LEN}s[/dim]",
        ]
        self.update("\n".join(lines))


class DiskPanel(Static):
    """Disk mount usage bars."""

    def refresh_data(self, d: dict):
        lines = ["[bold cyan]  Disk[/bold cyan]", ""]
        if not d["disks"]:
            lines.append("  [dim]no disks detected[/dim]")
        else:
            for disk in d["disks"]:
                c = pct_color(disk["pct"])
                lines.append(
                    f"  [dim]{disk['mount']:<16}[/dim]"
                    f"  {bar(disk['pct'], 14)}"
                    f"  [{c}]{disk['pct']:5.1f}%[/{c}]"
                    f"  [dim]{fbytes(disk['used'])} / {fbytes(disk['total'])}[/dim]"
                )
        self.update("\n".join(lines))


class ProcPanel(Static):
    """Top-N process table sorted by CPU% or MEM."""

    # How many processes to display (auto-adjusted in refresh_data)
    _rows = 22

    def refresh_data(self, d: dict, sort_key: str = "cpu"):
        procs = list(d["procs"])
        if sort_key == "mem":
            procs.sort(key=lambda x: x["mem"], reverse=True)

        lines = [
            f"[bold cyan]  Processes[/bold cyan]"
            f"  [dim]{d['proc_n']} running[/dim]"
            f"  [dim]  sort [bold]{sort_key}[/bold]  ·  [bold]c[/bold] cpu  [bold]m[/bold] mem[/dim]",
            "",
            f"  [dim]  {'PID':>6}  {'NAME':<24}  {'CPU%':>6}  {'MEM':>8}  {'STATUS':<10}  USER[/dim]",
            f"  [dim]{'─'*76}[/dim]",
        ]

        for p in procs[:self._rows]:
            c   = pct_color(p["cpu"])
            sc  = "green" if p["status"] == "running" else "dim"
            nb  = "[bold white]" if p["cpu"] > 8 else "[white]"
            nbe = "[/bold white]" if p["cpu"] > 8 else "[/white]"

            lines.append(
                f"  [dim]{p['pid']:>6}[/dim]  "
                f"{nb}{p['name']:<24}{nbe}  "
                f"[{c}]{p['cpu']:>6.1f}[/{c}]  "
                f"{fbytes(p['mem']):>8}  "
                f"[{sc}]{p['status']:<10}[/{sc}]  "
                f"[dim]{p['user']}[/dim]"
            )

        self.update("\n".join(lines))


# ─── App ─────────────────────────────────────────────────────────────────────

class Sysmon(App):
    """sysmon — low-overhead terminal resource monitor."""

    TITLE    = "sysmon"
    CSS_PATH = None      # CSS is inline below

    CSS = """
    /* ── Global ──────────────────────────────────────────────────── */
    Screen {
        background: #0d1117;
        layers: base;
    }

    /* ── Top bar ─────────────────────────────────────────────────── */
    TopBar {
        height: 1;
        background: #161b22;
        padding: 0 2;
        border-bottom: solid #21262d;
        content-align: left middle;
        color: #c9d1d9;
    }

    /* ── Main layout ─────────────────────────────────────────────── */
    #main {
        height: 1fr;
    }

    /* ── Left column: CPU ────────────────────────────────────────── */
    #left {
        width: 46;
        border-right: solid #21262d;
    }

    CpuPanel {
        height: 1fr;
        padding: 1 0;
        color: #c9d1d9;
    }

    /* ── Right column ────────────────────────────────────────────── */
    #right {
        width: 1fr;
    }

    /* ── Top-right: Mem + Net ────────────────────────────────────── */
    #top-right {
        height: auto;
        border-bottom: solid #21262d;
    }

    MemPanel {
        width: 1fr;
        height: auto;
        padding: 1 0;
        border-right: solid #21262d;
        color: #c9d1d9;
    }

    NetPanel {
        width: 40;
        height: auto;
        padding: 1 0;
        color: #c9d1d9;
    }

    /* ── Disk ────────────────────────────────────────────────────── */
    DiskPanel {
        height: auto;
        padding: 1 0;
        border-bottom: solid #21262d;
        border-top: solid #21262d;
        color: #c9d1d9;
    }

    /* ── Process table ───────────────────────────────────────────── */
    ProcPanel {
        height: 1fr;
        padding: 1 0;
        color: #c9d1d9;
        overflow-y: auto;
    }

    /* ── Footer ──────────────────────────────────────────────────── */
    Footer {
        height: 1;
        background: #161b22;
        border-top: solid #21262d;
        color: #6e7681;
    }

    Footer > .footer--key {
        background: #21262d;
        color: #79c0ff;
    }
    """

    BINDINGS = [
        Binding("q",      "quit",         "Quit",       priority=True),
        Binding("c",      "sort_cpu",     "Sort CPU"),
        Binding("m",      "sort_mem",     "Sort MEM"),
        Binding("r",      "force_refresh","Refresh"),
        Binding("p",      "toggle_pause", "Pause"),
    ]

    def __init__(self):
        super().__init__()
        self._collector = Collector()
        self._data: dict = {}
        self._sort: str  = "cpu"
        self._paused: bool = False

    # ── Layout ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield CpuPanel(id="cpu")
            with Vertical(id="right"):
                with Horizontal(id="top-right"):
                    yield MemPanel(id="mem")
                    yield NetPanel(id="net")
                yield DiskPanel(id="disk")
                yield ProcPanel(id="proc")
        yield Footer()

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_mount(self):
        self.set_interval(INTERVAL, self._tick)
        self._tick()   # immediate first render

    # ── Tick ─────────────────────────────────────────────────────────

    def _tick(self):
        if self._paused:
            return
        self._data = self._collector.snapshot()
        self._render()

    def _render(self):
        d = self._data
        if not d:
            return
        self.query_one("#topbar", TopBar).refresh_data(d)
        self.query_one("#cpu",    CpuPanel).refresh_data(d)
        self.query_one("#mem",    MemPanel).refresh_data(d)
        self.query_one("#net",    NetPanel).refresh_data(d)
        self.query_one("#disk",   DiskPanel).refresh_data(d)
        self.query_one("#proc",   ProcPanel).refresh_data(d, self._sort)

    # ── Actions ──────────────────────────────────────────────────────

    def action_sort_cpu(self):
        self._sort = "cpu"
        self.query_one("#proc", ProcPanel).refresh_data(self._data, "cpu")

    def action_sort_mem(self):
        self._sort = "mem"
        self.query_one("#proc", ProcPanel).refresh_data(self._data, "mem")

    def action_force_refresh(self):
        self._paused = False
        self._tick()

    def action_toggle_pause(self):
        self._paused = not self._paused
        if not self._paused:
            self._tick()


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Quick dep check
    try:
        import textual  # noqa: F401
        import psutil   # noqa: F401
    except ImportError as e:
        print(f"\n  Missing dependency: {e}")
        print("  Install with:  pip install textual psutil\n")
        sys.exit(1)

    Sysmon().run()
