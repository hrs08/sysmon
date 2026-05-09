#!/usr/bin/env python3
"""
  ███████╗██╗   ██╗███████╗███╗   ███╗ ██████╗ ███╗   ██╗
  ██╔════╝╚██╗ ██╔╝██╔════╝████╗ ████║██╔═══██╗████╗  ██║
  ███████╗ ╚████╔╝ ███████╗██╔████╔██║██║   ██║██╔██╗ ██║
  ╚════██║  ╚██╔╝  ╚════██║██║╚██╔╝██║██║   ██║██║╚██╗██║
  ███████║   ██║   ███████║██║ ╚═╝ ██║╚██████╔╝██║ ╚████║
  ╚══════╝   ╚═╝   ╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝

  A beautiful, low-overhead terminal system monitor.
  Usage:  python sysmon.py
  Needs:  pip install textual psutil
"""

from __future__ import annotations
import time, platform
from datetime import datetime
from collections import deque
from typing import Optional

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

# ── Constants ─────────────────────────────────────────────────────────────────
HIST_LEN = 60
INTERVAL = 1.0
BRAILLE  = " ⣀⣄⣤⣦⣶⣷⣿"

# ── Colour helpers ─────────────────────────────────────────────────────────────
def pct_col(p):
    if p >= 85: return "#ff6b6b"
    if p >= 60: return "#ffb347"
    return "#50fa7b"

def temp_col(t):
    if t >= 85: return "#ff6b6b"
    if t >= 70: return "#ffb347"
    return "#50fa7b"

def badge_pct(p):
    c = pct_col(p)
    return f"[bold {c}]{p:5.1f}%[/bold {c}]"

def bar(pct, w=22):
    pct  = max(0.0, min(100.0, pct))
    c    = pct_col(pct)
    fill = round(pct / 100 * w)
    return f"[{c}]{'█' * fill}[/{c}][dim]{'░' * (w - fill)}[/dim]"

def braille_spark(hist, w=36):
    vals  = list(hist)[-w:]
    vals  = [0.0] * (w - len(vals)) + vals
    return "".join(BRAILLE[min(int(v / 100 * 7), 7)] for v in vals)

def block_columns(hist, w=28, h=5):
    """LYRE-style vertical block columns for CPU history."""
    vals   = list(hist)[-w:]
    vals   = [0.0] * (w - len(vals)) + vals
    filled = [round(v / 100 * h) for v in vals]
    lines  = []
    for row in range(h, 0, -1):
        line = ""
        for f in filled:
            if f >= row:
                bright = int(120 + (row / h) * 135)
                line += f"[rgb({bright},80,255)]█[/rgb({bright},80,255)]"
            else:
                line += "[dim]░[/dim]"
        lines.append("  " + line)
    return lines

def fbytes(n):
    for u in ("B","K","M","G","T"):
        if abs(n) < 1024: return f"{n:6.1f}{u}"
        n /= 1024
    return f"{n:6.1f}P"

def fspeed(n): return f"{fbytes(n)}/s"

# ── Collector ──────────────────────────────────────────────────────────────────
class Collector:
    def __init__(self):
        self.boot_time   = psutil.boot_time()
        self.cpu_hist    = deque(maxlen=HIST_LEN)
        self.rx_hist     = deque(maxlen=HIST_LEN)
        self.tx_hist     = deque(maxlen=HIST_LEN)
        self._prev_net   = psutil.net_io_counters()
        self._prev_net_t = time.monotonic()
        self._peak_net   = 1.0
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(percpu=True, interval=None)

    def snapshot(self):
        d = {}
        self._cpu(d); self._mem(d); self._disk(d)
        self._net(d); self._procs(d); self._meta(d)
        return d

    def _cpu(self, d):
        d["cpu"]      = psutil.cpu_percent(interval=None)
        d["cpus"]     = psutil.cpu_percent(percpu=True, interval=None)
        d["cpu_n"]    = psutil.cpu_count(logical=True)
        d["cpu_phys"] = psutil.cpu_count(logical=False) or 1
        self.cpu_hist.append(d["cpu"])
        d["cpu_hist"] = list(self.cpu_hist)
        freq = psutil.cpu_freq()
        d["cpu_ghz"]  = (freq.current/1000 if freq.current>100 else freq.current) if freq else None
        d["cpu_temp"] = self._temp()

    def _mem(self, d):
        m = psutil.virtual_memory(); s = psutil.swap_memory()
        d.update(mem_pct=m.percent, mem_used=m.used, mem_total=m.total,
                 mem_avail=m.available, swap_pct=s.percent,
                 swap_used=s.used, swap_total=s.total)

    def _disk(self, d):
        skip  = {"squashfs","tmpfs","devtmpfs",""}
        disks = []
        for p in psutil.disk_partitions(all=False):
            if p.fstype in skip: continue
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append(dict(mount=p.mountpoint[:18], pct=u.percent,
                                  used=u.used, total=u.total))
            except (PermissionError, OSError): pass
        d["disks"] = disks[:4]

    def _net(self, d):
        now = time.monotonic(); net = psutil.net_io_counters()
        dt  = max(now - self._prev_net_t, 0.001)
        rx  = max((net.bytes_recv - self._prev_net.bytes_recv)/dt, 0)
        tx  = max((net.bytes_sent - self._prev_net.bytes_sent)/dt, 0)
        self._prev_net, self._prev_net_t = net, now
        self._peak_net = max(self._peak_net, rx, tx, 1)
        self.rx_hist.append(rx/self._peak_net*100)
        self.tx_hist.append(tx/self._peak_net*100)
        d.update(net_rx=rx, net_tx=tx, rx_hist=list(self.rx_hist),
                 tx_hist=list(self.tx_hist),
                 net_total_rx=net.bytes_recv, net_total_tx=net.bytes_sent)

    def _procs(self, d):
        procs = []
        for p in psutil.process_iter(["pid","name","cpu_percent","memory_info","status","username"]):
            try:
                i = p.info
                procs.append(dict(pid=i["pid"], name=(i["name"] or "?")[:22],
                    cpu=i["cpu_percent"] or 0.0,
                    mem=i["memory_info"].rss if i["memory_info"] else 0,
                    status=i["status"] or "?",
                    user=(i["username"] or "?")[:12]))
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        d["procs"] = procs[:80]; d["proc_n"] = len(procs)

    def _meta(self, d):
        up = int(time.time()-self.boot_time)
        h, rem = divmod(up,3600); m, s = divmod(rem,60)
        d["uptime"] = f"{h}h {m:02d}m {s:02d}s"
        d["host"]   = platform.node()
        d["os"]     = f"{platform.system()} {platform.release()}"
        d["arch"]   = platform.machine()
        d["time"]   = datetime.now().strftime("%H:%M:%S")

    def _temp(self):
        try:
            s = psutil.sensors_temperatures()
            for k in ("coretemp","k10temp","cpu_thermal","acpitz","cpu-thermal"):
                t = s.get(k)
                if t: return t[0].current
        except Exception: pass
        return None

# ── Widgets ────────────────────────────────────────────────────────────────────
class HeaderBar(Static):
    def refresh_data(self, d):
        temp = ""
        if d.get("cpu_temp"):
            tc   = temp_col(d["cpu_temp"])
            temp = f"  [dim]·[/dim]  [bold {tc}]{d['cpu_temp']:.0f}°C[/bold {tc}]"
        cc = pct_col(d["cpu"])
        self.update(
            f"[bold #a78bfa]◆ SYSMON[/bold #a78bfa]"
            f"  [dim]│[/dim]  [bold white]{d['host']}[/bold white]"
            f"  [dim]{d['os']}  {d['arch']}[/dim]"
            f"  [dim]│[/dim]  [dim]up[/dim] [#50fa7b]{d['uptime']}[/#50fa7b]"
            f"  [dim]│[/dim]  [dim]cpu[/dim] [bold {cc}]{d['cpu']:.1f}%[/bold {cc}]{temp}"
            f"  [dim]│[/dim]  [bold #a78bfa]{d['time']}[/bold #a78bfa]"
        )

class CpuPanel(Static):
    def refresh_data(self, d):
        lines = [
            f"  [bold #a78bfa]CPU[/bold #a78bfa]  [dim]{d['cpu_n']} logical  ·  {d['cpu_phys']} physical[/dim]  {badge_pct(d['cpu'])}",
            "",
        ]
        for i, pct in enumerate(d["cpus"]):
            lines.append(f"  [dim]c{i:<2}[/dim]  {bar(pct, 18)}  {badge_pct(pct)}")
        lines.append("")
        lines.append(f"  [dim]history ─────────────────────── {HIST_LEN}s[/dim]")
        lines += block_columns(d["cpu_hist"], w=28, h=5)
        lines.append("")
        meta = []
        if d.get("cpu_ghz"): meta.append(f"[bold white]{d['cpu_ghz']:.2f} GHz[/bold white]")
        if d.get("cpu_temp"):
            tc = temp_col(d["cpu_temp"])
            meta.append(f"[bold {tc}]{d['cpu_temp']:.0f}°C[/bold {tc}]")
        if meta: lines.append("  " + "  [dim]·[/dim]  ".join(meta))
        self.update("\n".join(lines))

class MemPanel(Static):
    def refresh_data(self, d):
        lines = [f"  [bold #a78bfa]MEMORY[/bold #a78bfa]", ""]
        lines += [
            f"  [dim]RAM [/dim]  {bar(d['mem_pct'], 24)}  {badge_pct(d['mem_pct'])}",
            f"  [dim]        {fbytes(d['mem_used'])} / {fbytes(d['mem_total'])}  ·  avail {fbytes(d['mem_avail'])}[/dim]",
            "",
        ]
        if d["swap_total"]:
            lines += [
                f"  [dim]SWAP[/dim]  {bar(d['swap_pct'], 24)}  {badge_pct(d['swap_pct'])}",
                f"  [dim]        {fbytes(d['swap_used'])} / {fbytes(d['swap_total'])}[/dim]",
            ]
        else:
            lines.append("  [dim]SWAP  ─  not configured[/dim]")
        self.update("\n".join(lines))

class NetPanel(Static):
    def refresh_data(self, d):
        lines = [f"  [bold #a78bfa]NETWORK[/bold #a78bfa]", ""]
        lines += [
            f"  [#50fa7b]▼ rx[/#50fa7b]  [bold white]{fspeed(d['net_rx'])}[/bold white]  [dim]total {fbytes(d['net_total_rx'])}[/dim]",
            f"  [#8be9fd]▲ tx[/#8be9fd]  [bold white]{fspeed(d['net_tx'])}[/bold white]  [dim]total {fbytes(d['net_total_tx'])}[/dim]",
            "",
            f"  [#50fa7b]{braille_spark(d['rx_hist'], 30)}[/#50fa7b]  [dim]rx[/dim]",
            f"  [#8be9fd]{braille_spark(d['tx_hist'], 30)}[/#8be9fd]  [dim]tx[/dim]",
            f"  [dim]{'─'*30} {HIST_LEN}s[/dim]",
        ]
        self.update("\n".join(lines))

class DiskPanel(Static):
    def refresh_data(self, d):
        lines = [f"  [bold #a78bfa]DISK[/bold #a78bfa]", ""]
        if not d["disks"]:
            lines.append("  [dim]no disks detected[/dim]")
        else:
            for disk in d["disks"]:
                lines.append(
                    f"  [dim]{disk['mount']:<18}[/dim]  {bar(disk['pct'], 16)}"
                    f"  {badge_pct(disk['pct'])}  [dim]{fbytes(disk['used'])} / {fbytes(disk['total'])}[/dim]"
                )
        self.update("\n".join(lines))

class ProcPanel(Static):
    _rows = 20

    def refresh_data(self, d, sort_key="cpu"):
        procs = list(d["procs"])
        if sort_key == "mem": procs.sort(key=lambda x: x["mem"], reverse=True)

        lines = [
            f"  [bold #a78bfa]PROCESSES[/bold #a78bfa]  [dim]{d['proc_n']} running  ·  sort[/dim] "
            f"[bold white]{sort_key}[/bold white]  [dim][bold]c[/bold] cpu  [bold]m[/bold] mem[/dim]",
            "",
            f"  [dim]  {'PID':>6}  {'NAME':<24}  {'CPU%':>6}  {'MEM':>8}  STATUS        USER[/dim]",
            f"  [dim]{'─'*80}[/dim]",
        ]

        for p in procs[:self._rows]:
            c  = pct_col(p["cpu"])
            if p["status"] == "running":
                st = "[bold #50fa7b]● running [/bold #50fa7b]"
            elif p["status"] in ("sleeping","sleep"):
                st = "[dim]○ sleeping[/dim]"
            elif p["status"] == "idle":
                st = "[dim]◌ idle    [/dim]"
            else:
                st = f"[dim]{p['status']:<10}[/dim]"

            nb  = "[bold white]" if p["cpu"] > 5 else "[white]"
            nbe = "[/bold white]" if p["cpu"] > 5 else "[/white]"
            lines.append(
                f"  [dim]{p['pid']:>6}[/dim]  "
                f"{nb}{p['name']:<24}{nbe}  "
                f"[bold {c}]{p['cpu']:>6.1f}[/bold {c}]  "
                f"[dim]{fbytes(p['mem']):>8}[/dim]  "
                f"{st}  [dim]{p['user']}[/dim]"
            )
        self.update("\n".join(lines))

# ── App ────────────────────────────────────────────────────────────────────────
class Sysmon(App):
    TITLE = "sysmon"

    CSS = """
    Screen { background: #0e0e1a; }
    HeaderBar {
        height: 1; background: #13132b; padding: 0 2;
        border-bottom: solid #2a2a4a; content-align: left middle; color: #c9d1d9;
    }
    #main { height: 1fr; }
    #left { width: 52; border-right: solid #2a2a4a; }
    CpuPanel { height: 1fr; padding: 1 0; color: #c9d1d9; }
    #right { width: 1fr; }
    #top-right { height: auto; border-bottom: solid #2a2a4a; }
    MemPanel {
        width: 1fr; height: auto; padding: 1 0;
        border-right: solid #2a2a4a; color: #c9d1d9;
    }
    NetPanel { width: 46; height: auto; padding: 1 0; color: #c9d1d9; }
    DiskPanel {
        height: auto; padding: 1 0;
        border-bottom: solid #2a2a4a; color: #c9d1d9;
    }
    ProcPanel { height: 1fr; padding: 1 0; color: #c9d1d9; overflow-y: auto; }
    Footer { height: 1; background: #13132b; border-top: solid #2a2a4a; color: #6e7691; }
    Footer > .footer--key { background: #2a2a4a; color: #a78bfa; }
    """

    BINDINGS = [
        Binding("q", "quit",          "Quit",     priority=True),
        Binding("c", "sort_cpu",      "CPU sort"),
        Binding("m", "sort_mem",      "MEM sort"),
        Binding("r", "force_refresh", "Refresh"),
        Binding("p", "toggle_pause",  "Pause"),
    ]

    def __init__(self):
        super().__init__()
        self._col    = Collector()
        self._data   = {}
        self._sort   = "cpu"
        self._paused = False

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
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

    def on_mount(self):
        self.set_interval(INTERVAL, self._tick)
        self._tick()

    def _tick(self):
        if self._paused: return
        self._data = self._col.snapshot()
        self._render()

    def _render(self):
        d = self._data
        if not d: return
        self.query_one("#header", HeaderBar).refresh_data(d)
        self.query_one("#cpu",    CpuPanel).refresh_data(d)
        self.query_one("#mem",    MemPanel).refresh_data(d)
        self.query_one("#net",    NetPanel).refresh_data(d)
        self.query_one("#disk",   DiskPanel).refresh_data(d)
        self.query_one("#proc",   ProcPanel).refresh_data(d, self._sort)

    def action_sort_cpu(self):
        self._sort = "cpu"
        self.query_one("#proc", ProcPanel).refresh_data(self._data, "cpu")

    def action_sort_mem(self):
        self._sort = "mem"
        self.query_one("#proc", ProcPanel).refresh_data(self._data, "mem")

    def action_force_refresh(self):
        self._paused = False; self._tick()

    def action_toggle_pause(self):
        self._paused = not self._paused
        if not self._paused: self._tick()

if __name__ == "__main__":
    import sys
    try:
        import textual, psutil  # noqa
    except ImportError as e:
        print(f"\n  Missing: {e}\n  Run: pip install textual psutil\n")
        sys.exit(1)
    Sysmon().run()
