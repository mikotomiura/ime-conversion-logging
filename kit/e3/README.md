# Execution cost of the standalone observer (E3)

This directory measures what the standalone observer (`ime-observation-logger`) costs the machine while it
runs: CPU and resident memory of that one process, under two conditions.

| Condition | What happens during the run |
|---|---|
| `idle` | The observer runs and nothing operates the machine. |
| `working` | The observer runs while the scenario emitter types a synthetic plan (`../run/run_cell.py`). |

The cost is reported for both conditions. It is never reduced to a single figure such as "the observer uses
N % CPU": an observer that is cheap when idle and expensive while typing, or the other way round, would hide
behind one number.

One condition is one night: eight hours at a 30-second interval. Two conditions on two systems make four
nights.

## Files

| File | Purpose |
|---|---|
| `run_e3.sh` | Samples the observer for one condition and writes `samples.tsv` and `conditions.txt`. |
| `sample-macos.sh`, `sample-windows.sh` | The sampler of each system, called by `run_e3.sh`. |
| `summarize_e3.py` | Summarises `samples.tsv` per condition (`--selftest` runs its negative controls). |
| `calibrate-cpu.sh` | A manual positive and negative control of the macOS CPU instrument. |

## What is measured

The samplers record one row every interval: the process is found by its name (`E3_PROC`, default
`ime-observation-logger`, without `.exe` on Windows) and no other process is listed and no window title is
read. Two other values are read from the machine: the battery level (a column that is not summarised) and,
on macOS with an interval of 10 seconds or more, the energy impact of the file manager as a reference.
`conditions.txt` records the number of logical processors, the version of the operating system and the
SHA-256 of the measured executable (`unknown` if it cannot be read; the path is not recorded).

| Column | macOS | Windows |
|---|---|---|
| CPU (%) | increase of the cumulative CPU time (`ps -o cputime`) divided by the increase of the wall-clock time: the mean over the whole interval | the performance counter `\Process(<name>)\% Processor Time`: a sample of about one second taken at the reading instant |
| Resident memory (KB) | the `mem` column of `top -l 3` | working set |
| Threads | the `th` column of `top -l 3` | thread count |

- CPU is on the basis one core = 100 %, on both systems. A machine with ten cores has a maximum of 1000 %.
- The two CPU figures are not the same kind of quantity. On macOS every row is the mean over the whole
  interval, so a burst shorter than the interval is not missed. On Windows every row is a sample of about
  one second, so a burst that falls between two samples is not seen. The 95th percentile and the maximum
  therefore must not be compared between the two systems; the mean is the comparable figure, and even that
  only as a description of each system.
- The resident memory is not the same quantity either: on macOS it is the memory column of `top`, on
  Windows it is the working set. Compare each system with itself (the slope over time), not one with the
  other.
- `top -l 2` is not used for CPU: its second frame includes the start-up time of `top` and reads about 0.48
  times the true load. `calibrate-cpu.sh` shows this for yourself: it saturates one core and prints the
  ground truth next to what each instrument reports, then prints what `top` reports for an absent process
  (empty or 0 is correct). Run it once on a new machine, before the measurement. It uses one core at full
  load for about 30 seconds and ends by itself within 60 seconds; its output is not read by the summary.
- Resident memory and threads are read at the sampling instant, so a peak shorter than the interval is not
  seen in them.
- The `power` and reference columns of the macOS sampler (the energy impact) are recorded but not
  summarised: the energy impact is a dimensionless index, not watts, and Windows has no per-process
  counterpart.
- The first row of a run has an empty CPU value (there is no previous counter). The summary leaves it out;
  it is not read as 0.
- Rows taken while the process is absent carry zero in every column. The summary leaves them out and reports
  how many there were. A file in which every row is zero is reported as `not measured`, never as a low cost.
- On Windows a counter that cannot be read also gives a zero. `run_e3.sh` reads the counter once before the
  run and stops if it fails, and the summary flags a run whose CPU is exactly zero on every usable row
  (`cpu_all_zero: true`) without changing its status: an untouched machine can produce a true zero, so the
  flag asks for a check and is not a verdict.

## The process name on macOS

On macOS `pgrep -x` compares the full executable name, so the 22-character name `ime-observation-logger`
matches (checked on macOS 27.0); only the COMMAND column of `top` shows the first 16 characters. `run_e3.sh` uses the full name when
`E3_PROC` is not set. Before the first run, start the observer and look at the name the system reports:

```
ps -c -o comm= -p <pid>
```

If it differs from what `E3_PROC` will be, set `E3_PROC` to the printed name. The name must identify the
observer alone; `run_e3.sh` counts the processes of that name before the run (see below).

## Battery is not measured

The machine runs on mains power during the measurement, and on mains power the battery level does not fall
(it may even rise), so a discharge cannot be read and a control without the observer would add nothing: every
column of the observed process is zero when the process is not there. The battery column of the samplers
is kept only so that the layout is the same on both systems; the summary does not read it.

## Procedure

Run every step on each system (macOS and Windows), on mains power, with sleep, the screen lock and the
screen saver turned off. One condition is 28800 seconds (eight hours) at a 30-second interval.

A pass takes longer than the interval: on macOS about 9 seconds more (three frames of `top` and the process
listing), on Windows a few seconds more (PowerShell is started for every reading and the counter waits about
one second). In an earlier eight-hour run at a 30-second interval the period was about 39 seconds and the
file had about 730 rows. Expect about 700 rows per eight hours; `run_e3.sh` estimates the count and warns if
it is below 480 rows or the window is shorter than four hours. Both are floors of the summary.

1. Build the observer (see `../logger/README.md`) and run `calibrate-cpu.sh` once (macOS). Find the process
   name as described above.
2. `idle`: start the observer (only one), then

   ```
   bash run_e3.sh idle 28800 30 runs/e3-idle
   ```

   and leave the keyboard and the pointer alone. `run_e3.sh` stops if the number of processes with the
   sampled name is not exactly one. The observation log that the observer writes in this condition is not
   deposited: the observer is started before this command is typed, so the log holds those key presses. Only
   `samples.tsv` and `conditions.txt` are deposited.
3. `working`: stop the observer of the `idle` condition first, because the cell runner starts its own and
   `run_e3.sh` stops if a process of that name is already running. Start the sampler first,

   ```
   bash run_e3.sh working 28800 30 runs/e3-working
   ```

   then, in another terminal, start the cell runner that starts the observer and types the plan (see
   `../run/README.md`; `run_e3.sh` prints the command and never types itself). The cell file must name the
   observer, not a wrapper script, so that the sampled process is the observer. Run it as one long plan that
   covers the whole window, so that the observer stays one process: do not repeat a short plan by starting
   the runner again, because every start replaces the observer and its resident memory starts over. If the
   runner stops the observer before the sampler ends, the rows after that are counted as absent.
4. Summarise both conditions together:

   ```
   python3 summarize_e3.py idle=runs/e3-idle/samples.tsv working=runs/e3-working/samples.tsv
   ```

The output directory must not exist: an earlier measurement is never overwritten. A duplicate process of the
same name that appears during a run is not detected, so keep no second copy of the observer running on the
machine while measuring.

## The summary

For each condition `summarize_e3.py` prints the mean, median, 95th percentile and maximum of the CPU, the
final resident memory, the least-squares slope of the resident memory in MB per hour, the measured interval
(median and maximum) and the floors that were applied. The status is one of `measured`, `insufficient` and
`not measured`; only `measured` may be quoted. Exit status: 0 when every file is measured, 1 otherwise, 2 for
a usage error or a file that cannot be read.

- The floors are 480 usable rows (`--min-samples`, at least 2), 480 rows with a CPU value, and a window of
  at least four hours between the first and the last usable row. Below any of them the status is
  `insufficient`: a short window cannot stand for a rate such as the growth of the resident memory.
- If the observer was replaced during the run (the process present, absent, present again), the transitions
  are counted and printed, and the slope of the resident memory is printed per continuous stretch together
  with the number of stretches; the single overall slope is `n/a`. A single failed reading also shows as a
  transition, so a nonzero count asks for a look at the file.
- `cpu_all_zero: true` marks a run whose CPU is exactly zero on every usable row (see above).
- Rows with a negative or non-finite number are skipped and counted; they are reported by line number.

`python3 summarize_e3.py --selftest` checks the arithmetic on known series (a CPU time that rises by 0.02
seconds every second gives 2.0 %; the values 1 to 100 give a mean of 50.5 and a 95th percentile of 95.05; a
skewed series of 1 to 99 and 1000 keeps the mean at 59.5 and the median at 50.5; a growth of 10 MB per hour
gives a slope of 10) and the negative controls: a file with no rows, a file in which every row is zero, a file
without a CPU value, a short run and a run that spans less than four hours are each reported as not measured
or insufficient; a restart splits the slope; an all-zero CPU is flagged.

If the observer has a longer process name than the system compares, the sampler finds nothing and the summary
says `not measured`; check the name as described above before a long run. `run_e3.sh` counts the processes
of the sampled name at its start and stops when the count does not fit the condition.
