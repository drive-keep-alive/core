# Drive_keep_alive : Keep Your Drives Alive, Forever

### A dedicated drive preservation appliance. Open source, set-and-forget, and built for people who care about their data.

---

## The Problem

Hard drives and SSDs degrade silently.

- HDDs left spinning without periodic reads lose magnetic charge on sectors over years
- SSDs left **unpowered** lose charge in NAND cells; faster in warm environments
- Spin-up and spin-down cycles cause more mechanical wear than continuous operation
- Most people only find out when it is too late

You could set this up yourself with a Raspberry Pi, some scripts, and a weekend of reading. Or you could just plug in drive_keep_alive.

---

## What Drive_keep_alive Is

Drive_keep_alive is a small standalone appliance that keeps your archival drives healthy over the long term. It monitors, scrubs, and alerts. It runs 24/7 on wall power,with an integrated UPS so an outage never causes a sudden shutdown.

**It is not a NAS.** It is not designed for streaming media, fast file transfers, or replacing Synology. It does one thing well; it keeps your drives alive and tells you when something is wrong.

---

## Features

**Drive Preservation**
- Periodic sector reads on HDDs to prevent magnetic charge loss
- Scheduled read and write cycles on SSDs to refresh NAND cells
- Configurable scrub intervals per drive
- Keeps drives spinning continuously to avoid spin-up wear

**Health Monitoring**
- Full S.M.A.R.T data read on every connected drive
- Plain English health summaries; no raw numbers you have to decode
- Estimated remaining drive life per disk
- Bathtub curve failure model; accounts for infant failure and wear-out phases
- Push and email alerts when a drive approaches failure

**Data Integrity**
- Checksum verification to detect silent data corruption (bit rot)
- Full scrub history log per drive
- Flags changed or corrupted sectors immediately

**Modular Hardware**
- pay for what you need structure
- we're designing so that extra features such as:
  - granular temperature monitoring
  - active cooling
  - extra drive docks
  and others like them, are easy to integrate and easy to remove.

**Power Safety**
- Integrated battery backup (UPS); survives short outages without shutdown
- Configurable safe shutdown threshold (eg shut down at 20% battery)
- Power loss event log
- Battery health displayed in dashboard

**Web Interface**
- Clean browser-based dashboard; access from any device on your network
- No app install needed
- Drive health overview on the home screen
- Per-drive scrub schedule configuration
- Alert settings and notification(email) setup
- Full API for programmatic use

**Basic File Access**
- Read-only network sharing via Samba so you can pull files when needed
- Not designed for high-speed transfers; this is archival access only

**100% Free and Open Source**
- Every line of software is open source and auditable
  - Apache 2.0 license
- Hardware design files will be published after the campaign
- No subscriptions, no cloud dependency, no vendor lock-in
- Community contributions welcome

---

## Hardware

Drive_keep_alive is built on a low-cost single board computer.
Power management and battery control are handled by a separate microcontroller.

Enclosures for the first prototype batch will be 3D printed by outsourced professional printing services, not consumer desktop printers. This allows us faster development.

**Estimated retail price: ~$65 USD** base depending on final bill of materials and drive bay configuration,But prices can vary due to its opensource and highly modular nature.

---

## Funding Goal: $5,500

This campaign covers (in order of importance to us):

- First prototype PCB designs and fabrication
- Initial software development and testing
- Documentation
- 3D printed enclosure batch

This is a minimum viable first run. Stretch goals will cover injection molded enclosures,and a .

---

## What Drive_keep_alive Is NOT

- A NAS; do not buy it expecting Plex, Docker, or gigabit transfer speeds
- A backup solution on its own; it preserves drives you already manage
- Cloud connected; everything runs locally
- "Vibecoded"

---

## Roadmap

| Stage | Target |
|---|---|
| Prototype PCB and software | Month 1 to 2 |
| Closed beta with backers | Month 3 |
| Software polish and enclosure | Month 4 |
| First backer units ship | Month 5 to 6 |

---
