#!/usr/bin/env bash
# Windows (git-bash): sample the observed process every INTERVAL seconds.
# Nothing but the process name is recorded.
#
# The process to sample is named by the environment variable E3_PROC (default: ime-observation-logger),
# without the .exe suffix. PowerShell reads it as $env:E3_PROC.
set -euo pipefail
DURATION_S="$1"; INTERVAL_S="$2"
E3_PROC="${E3_PROC:-ime-observation-logger}"; export E3_PROC
# Do not use a single quote inside the PowerShell text (it would collide with the bash '...'); use " throughout.
read -r -d '' PS <<'PWSH' || true
$p = Get-Process -Name $env:E3_PROC -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $p) { "0`t0`t0`t0" } else {
  # Get-Counter returns the same basis as `top` on macOS: one core = 100 % (settled on a real machine).
  # It is not divided by the number of cores.
  $c = (Get-Counter -Counter ("\Process(" + $p.ProcessName + ")\% Processor Time") -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue
  if ($null -eq $c) { $c = 0 }
  # Column 6 (rss_alt_kb) is PrivateMemorySize64, the counterpart of the cross-check on macOS.
  ("{0}`t{1}`t{2}`t{3}" -f [math]::Round($c,2), [int]($p.WorkingSet64/1024), $p.Threads.Count, [int]($p.PrivateMemorySize64/1024))
}
PWSH
read -r -d '' PSB <<'PWSH' || true
$b = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $b) { "" } else { $b.EstimatedChargeRemaining }
PWSH
end=$(( $(date +%s) + DURATION_S ))
while [ "$(date +%s)" -lt "$end" ]; do
  now=$(date +%s)
  row=$(powershell.exe -NoProfile -NonInteractive -Command "$PS" 2>/dev/null | tr -d '\r' | tail -1 || true)
  batt=$(powershell.exe -NoProfile -NonInteractive -Command "$PSB" 2>/dev/null | tr -d '\r' | tail -1 || true)
  [ -n "$row" ] || row=$'0\t0\t0\t0'
  # PowerShell returns "cpu<TAB>ws<TAB>th<TAB>priv"; the battery value is inserted as the fifth column.
  cpu=$(printf '%s' "$row" | cut -f1); ws=$(printf '%s' "$row" | cut -f2)
  th=$(printf '%s' "$row" | cut -f3); priv=$(printf '%s' "$row" | cut -f4)
  # The output has 8 columns, the same layout as the macOS sampler. A header with 8 columns and rows with
  # 6 would make the aggregation report insufficient data for the whole run.
  #
  # Columns 7 and 8 are empty (not 0). Windows has no per-process public counterpart of the macOS "energy
  # impact" (`top -stats power`). Empty means "not measurable on this system" and 0 means "measured and it
  # was 0"; mixing the two would make the aggregation report a broken instrument.
  printf '%s\t%s\t%s\t%s\t%s\t%s\t\t\n' "$now" "$cpu" "$ws" "$th" "${batt:-}" "$priv"
  sleep "$INTERVAL_S"
done
