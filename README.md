# drive_keep_alive

a low-power appliance that keeps archival hard drives and ssds alive: periodic reads, smart monitoring, self-tests, and bad-sector scans, with a browser dashboard. to be run on a small sbc (raspberry pi zero class).

## status: software stage

this is the software foundation. the hardware enclosure, pcb, ups, and packaging will be linked in seperate repos when built. the code is functional and covered by tests, but it is not polished or packaged for end users:

- **implemented**: smart polling (ata + nvme), keep-alive reads, weekly short / monthly long smart self-tests, monthly read-only badblocks scans, spin-down prevention via hdparm, nvme power state handling, drive discovery, a read-only web dashboard, and a toml config file.
- **planned, not built**: email/push alerts, checksum/bit-rot scrubbing, estimated remaining life and failure modeling, samba read-only file sharing, ups/battery integration, per-drive config in the web ui, and a full proggramatic api.

everything below describes what exists now, not the complete campaign vision.

## what it does

all jobs run from one scheduler (apscheduler) on startup:

| task | frequency | implementation |
|---|---|---|
| keep-alive read | every 4 min | plain file read on each mounted drive |
| smart attribute poll | every 15 min | pysmart, stored as attribute snapshots |
| smart short self-test | weekly | `smartctl -t short` |
| smart long self-test | monthly | `smartctl -t long` |
| bad sector scan | monthly | `badblocks` read-only |
| spin-down prevention | at startup | `hdparm` / `nvme-cli` |

frequencies and health thresholds live in `config.toml`.

## layout

```
main.py               fastapi app, scheduler registration, dashboard routes
poll_handling.py      smart reads, selftest/badblocks runs, health labels, power settings
config_handling.py    toml loading and merging (config.toml + config.local.toml)
database_handling.py  sqlite engine and session helper
models.py             sqlmodel schemas (drive, smartattribute, selftestresult, badblockscan)
frontend/             htmx dashboard templates and static assets
tests/                pytest suite (no hardware or network required)
database/             sqlite store (created at startup)
```

## to run:

developed in nixos, all dependencies are declared in `shell.nix`:

```sh
nix-shell
sudo uvicorn main:app --host 0.0.0.0 --port 8000
```

needs sudo for nmcli and various other disk operations,in production set_cap features will be needed
on startup it creates the sqlite db, discovers connected drives, schedules all jobs, and applies power settings. the dashboard is at `/dashboard` (the `/dashboard/drives` fragment is polled by htmx).

## configuration

`config.toml` is the single config source. changes take effect on restart. sections:

- `scheduler` - job cadences (minutes/days)
- `health` - warn/critical thresholds per attribute; ata (temperature, reallocated/pending/uncorrectable sectors, spin retry) and nvme (percentage used, media errors, unsafe shutdowns, available spare)
- `dashboard` - ui refresh interval
- `database` - SMART snapshot retention window (older rows are pruned daily)

a missing or broken file falls back to defaults so the application always starts.

## health reporting

the dashboard shows a per-drive plain-english health label (`ok` / `warn` / `critical`) derived from the threshold table, current temperature, and active test/scan status. no alerts exist yet; health is visible in the dashboard only.

## tests

hardware-independent; fake pysmart devices and canned subprocess output. no root, real devices, or a real db are needed. temp files stay under the repo (never `/tmp`).

```sh
nix-shell --run "pytest -q"
```

## hardware target

designed for a small sbc with limited ram (raspberry pi zero class). to keep the runtime light: sqlite on disk, no background workers beyond the scheduler, no heavy orm features.

## license

apache 2.0
