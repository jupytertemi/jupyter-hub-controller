# Argus Grafana panels — drop-in PromQL queries

Drop-in queries for the Argus team to add Grafana panels for the new
metrics flowing from every hub via vmagent → VictoriaMetrics.

All metrics are scraped per-hub and labelled with `instance` (hub
hostname) automatically by node-exporter, so every panel below is
trivially per-hub-groupable.

## NVMe SMART (drive health)

### Drive temperature
```
nvme_smart_temperature_celsius
```
Suggested visualization: time series. Alert: any hub > 70 °C for > 5 min.

### Drive wear level (most important — predicts failure)
```
nvme_smart_percentage_used_ratio
```
Range 0-1. Alert at:
- `> 0.80` — page on-call (drive entering end-of-life zone)
- `> 0.95` — critical (replace immediately)

Suggested visualization: bar chart per hub, sorted descending. Heatmap
across the fleet over time.

### Available spare blocks
```
nvme_smart_available_spare_ratio
```
Range 0-1. Alert at:
- `< 0.10` — drive near exhaustion of spare blocks
- `< 0.05` — critical

### Write throughput per hub (1-hour rolling rate)
```
rate(nvme_smart_data_units_written_total[1h]) * 512
```
Units: bytes/sec (1 NVMe data unit = 512 bytes; verify per-vendor — some are 512KB).
Use this to identify "abnormally high write" hubs (could indicate
bad cleanup logic, runaway logs).

### Unsafe shutdown rate (UPS recommendation signal)
```
rate(nvme_smart_unsafe_shutdowns_total[24h]) * 86400
```
Unsafe shutdowns per day. If consistently > 0.5/day across the fleet,
that's a hardware-side BOM signal that customers' homes have unstable
power and we should ship with capacitor-backed NVMe or in-line UPS.

### Critical warning bits (page immediately)
```
nvme_smart_critical_warning_bits != 0
```
Any non-zero value means the drive itself is reporting one of: spare
below threshold, temperature exceeded, NVM subsystem reliability
degraded, read-only mode, volatile-memory backup failed, persistent
memory unreliable. Page on-call.

### Media errors (any growth = bad blocks)
```
increase(nvme_smart_media_errors_total[7d])
```
If a hub's media error count grows in a week, that drive is failing.
Schedule replacement.

### Stale exporter detection
```
time() - nvme_smart_last_export_unixtime > 7200
```
If last export was > 2 h ago, the hub's own exporter timer broke.
Surfaces hubs where Argus has gone deaf.

## TURN credential rotation freshness

### Rotation age per hub (alert if > 7 days)
```
turn_credential_age_seconds
```
Cadence is 2 days, so anything > 7 d means rotation has been failing
for ≥ 3 cycles. Top-N panel + alert.

### Rotation timestamp scatter
```
turn_credential_last_rotation_unixtime
```
Useful as a fleet-wide histogram — concentration of rotations should
match the every-2-day cadence. Big gaps indicate fleet-side rotation
failures (Cloudflare TURN service issue, Celery beat broken on those
hubs).

### Hubs with no TURN row at all
```
turn_credential_present == 0
```
Indicates hub onboarded without TURN cred provisioning. Probably a
cloud-side onboarding bug. Fleet count should be 0.

## Suggested dashboard layout

**Page 1 — Fleet overview**
- Big stat: hubs reporting / hubs registered (uptime SLO)
- Gauge: 95th-percentile drive wear across fleet
- Gauge: 95th-percentile TURN rotation age
- Time series: total fleet write throughput

**Page 2 — Drive health drilldown (per-hub)**
- Top-N bar chart: most-worn drives (descending `percentage_used_ratio`)
- Top-N: hubs with most unsafe shutdowns over 30 d
- Per-hub temperature time series (group by `instance`)
- Per-hub critical warnings counter (should always be 0)

**Page 3 — TURN health**
- Bar chart: rotation age per hub
- Hubs in alert (rotation age > 7 d)
- Alert log: hubs currently failing rotation

## Alert rules (suggested)

```yaml
groups:
  - name: hub-storage-and-turn-2026-05-07
    rules:
      - alert: HubDriveWearCritical
        expr: nvme_smart_percentage_used_ratio > 0.95
        for: 1h
        labels: { severity: critical }
        annotations:
          summary: "Hub {{ $labels.instance }} drive at {{ $value | humanizePercentage }} wear — replace ASAP"

      - alert: HubDriveWearWarning
        expr: nvme_smart_percentage_used_ratio > 0.80
        for: 24h
        labels: { severity: warning }

      - alert: HubDriveSpareLow
        expr: nvme_smart_available_spare_ratio < 0.10
        for: 1h
        labels: { severity: critical }

      - alert: HubDriveCriticalWarning
        expr: nvme_smart_critical_warning_bits != 0
        for: 0m
        labels: { severity: critical }
        annotations:
          summary: "Hub {{ $labels.instance }} drive raised SMART critical warning ({{ $value }})"

      - alert: HubTurnRotationStale
        expr: turn_credential_age_seconds > 604800
        for: 1h
        labels: { severity: warning }
        annotations:
          summary: "Hub {{ $labels.instance }} TURN rotation {{ $value }}s ago (cadence is 2d, > 7d means failed)"

      - alert: HubTurnRotationMissing
        expr: turn_credential_present == 0
        for: 1h
        labels: { severity: warning }

      - alert: HubExporterDeaf
        expr: time() - nvme_smart_last_export_unixtime > 7200
        for: 30m
        labels: { severity: info }
```
