
**A beautiful, low-overhead terminal system monitor.**  
Prettier than `top`. Snappier than `htop`.

```
◆ sysmon  │  myhost  Linux 6.x x86_64  │  up 3h 22m  │  cpu 14.2%  │  14:37:08
```

---

## Features

- **CPU panel** — per-core bars with live history sparkline, clock speed, temperature
- **Memory panel** — RAM + swap bars with used/avail/cached stats
- **Network panel** — rx/tx speeds with dual 60-second sparklines
- **Disk panel** — per-mount usage bars for up to 4 volumes
- **Process table** — top 22 processes sorted by CPU or MEM, refreshed every second
- **Low overhead** — single-threaded, async render loop, no blocking I/O

## Install Required library

```bash
pip install textual psutil
```

## Run

```bash
python sysmon.py
```

## Keybindings

| Key | Action            |
|-----|-------------------|
| `q` | Quit              |
| `c` | Sort by CPU%      |
| `m` | Sort by Memory    |
| `r` | Force refresh     |
| `p` | Pause / Resume    |

## Compatibility

| Platform | Status |
|----------|--------|
| Linux    | ✅ Full (CPU temp, all metrics) |
| macOS    | ✅ Full (temp via IOKit if available) |
| Windows  | ✅ Partial (no temp sensors) |

## Customise

At the top of `sysmon.py`:

```python
INTERVAL = 1.0    # seconds between data snapshots — lower = more responsive
HIST_LEN = 60     # sparkline history window (seconds)
```

Colour thresholds (green / yellow / red) are in `pct_color()` and `temp_color()`.

## Stack

| Library | Role |
|---------|------|
| [Textual](https://github.com/Textualize/textual) | TUI framework — layout, rendering, keybindings |
| [psutil](https://github.com/giampaolo/psutil) | Cross-platform system metrics |

---

Built with Python 3.10+ 
