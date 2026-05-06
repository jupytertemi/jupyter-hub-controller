#!/bin/bash
# 2026-05-07: pipes NVMe SMART metrics to node-exporter textfile collector
# → vmagent → VictoriaMetrics on Argus brain. Argus dashboard panel TBD.
# Runs hourly via nvme-smart-exporter.timer.

set -u
DEV="/dev/nvme0n1"
OUT="/var/lib/node_exporter/textfile_collector/nvme_smart.prom"
TMP="${OUT}.tmp"

# Take first whitespace-token from "<key>: <val> [...units...]" form, strip
# commas and %. Anchored exact match avoids "Available Spare" matching
# "Available Spare Threshold".
extract() {
    smartctl -a "$DEV" 2>/dev/null | awk -F: -v key="$1" '
        $1 ~ key {
            val = $2
            sub(/^[ \t]+/, "", val)
            split(val, parts, /[ \t]/)
            v = parts[1]
            gsub(/[,%]/, "", v)
            print v
            exit
        }'
}

CRITICAL_WARNING_HEX=$(extract "^Critical Warning$")
CRITICAL_WARNING=$(printf "%d" "${CRITICAL_WARNING_HEX:-0}" 2>/dev/null || echo 0)
TEMP_C=$(extract "^Temperature$")
AVAIL_SPARE=$(extract "^Available Spare$")
PERCENT_USED=$(extract "^Percentage Used$")
DATA_WRITTEN=$(extract "^Data Units Written$")
DATA_READ=$(extract "^Data Units Read$")
UNSAFE_SHUTDOWNS=$(extract "^Unsafe Shutdowns$")
MEDIA_ERRORS=$(extract "^Media and Data Integrity Errors$")

cat > "$TMP" <<EOF
# HELP nvme_smart_temperature_celsius NVMe drive temperature
# TYPE nvme_smart_temperature_celsius gauge
nvme_smart_temperature_celsius{device="nvme0n1"} ${TEMP_C:-0}
# HELP nvme_smart_available_spare_ratio Available spare blocks ratio (0-1, alert <0.10)
# TYPE nvme_smart_available_spare_ratio gauge
nvme_smart_available_spare_ratio{device="nvme0n1"} $(awk -v v=${AVAIL_SPARE:-0} 'BEGIN{print v/100}')
# HELP nvme_smart_percentage_used_ratio Drive wear level 0-1 (alert >0.80)
# TYPE nvme_smart_percentage_used_ratio gauge
nvme_smart_percentage_used_ratio{device="nvme0n1"} $(awk -v v=${PERCENT_USED:-0} 'BEGIN{print v/100}')
# HELP nvme_smart_data_units_written_total Cumulative data units written (1 unit = 512KB)
# TYPE nvme_smart_data_units_written_total counter
nvme_smart_data_units_written_total{device="nvme0n1"} ${DATA_WRITTEN:-0}
# HELP nvme_smart_data_units_read_total Cumulative data units read
# TYPE nvme_smart_data_units_read_total counter
nvme_smart_data_units_read_total{device="nvme0n1"} ${DATA_READ:-0}
# HELP nvme_smart_unsafe_shutdowns_total Cumulative unsafe shutdowns
# TYPE nvme_smart_unsafe_shutdowns_total counter
nvme_smart_unsafe_shutdowns_total{device="nvme0n1"} ${UNSAFE_SHUTDOWNS:-0}
# HELP nvme_smart_critical_warning_bits Critical warning flags (0=ok, !=0=alert)
# TYPE nvme_smart_critical_warning_bits gauge
nvme_smart_critical_warning_bits{device="nvme0n1"} ${CRITICAL_WARNING:-0}
# HELP nvme_smart_media_errors_total Media + data-integrity error count
# TYPE nvme_smart_media_errors_total counter
nvme_smart_media_errors_total{device="nvme0n1"} ${MEDIA_ERRORS:-0}
# HELP nvme_smart_last_export_unixtime When the exporter last successfully ran
# TYPE nvme_smart_last_export_unixtime gauge
nvme_smart_last_export_unixtime{device="nvme0n1"} $(date +%s)
EOF
mv "$TMP" "$OUT"
