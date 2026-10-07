# Datasheet: records of the synthetic-input validation

This datasheet describes the dataset "Logging Japanese IME conversion without recording text: Records of the
synthetic-input validation" (DOI 10.5281/zenodo.23183438). It follows the sections proposed by Gebru et al. (2021):
motivation, composition, collection process, preprocessing, uses, distribution and maintenance. It is deposited with
the data, and with `FORMATS.md` it describes every file of the deposit. The registrations, the article and the code
repository give the details that are referred to here.

## Motivation

**Purpose.** The dataset holds the records of a pre-registered validation of a logger that records when a Japanese
input method converts input, as the duration of a conversion session, the number of arrow-key presses during it and
whether it was abandoned, without recording any text. The logger's rows were compared with a reference: an input field
that records the calls it receives from the input method. Scripted key presses from a generated plan drove both. The
records are released so that the registered analyses can be re-run from the deposit, and so that other rules that infer
conversions from key presses can be evaluated against the same reference without new measurement.

**Creator and funding.** The dataset was created by Mikoto Miura, an independent researcher (Hiroshima, Japan; ORCID
0009-0000-4196-0508). This research received no specific grant from any funding agency in the public, commercial or
not-for-profit sectors.

## Composition

**Instances.** The unit of the dataset is a run: one plan, played once by the runner on a measuring machine. A plan
consists of blocks, and a block consists of trials. A trial types the reading of one phrase, or a string of kana made
by the generator, by one of the scenarios S1 to S15 (for example, converting and committing, cancelling, cycling
through the candidates, or bringing another application to the front) at one of three speeds. S9 is never run, and S11
runs only on macOS with live conversion on, so the plans of the measurement have 14 or 13 scenarios (sections 2.3 and
10.5 of the registration); some pilot plans are shorter. No instance represents a person.

**Files of a run.** Each run has five files (a run that the runner aborted may lack a log). The four logs follow the
formats of `FORMATS.md`:

| File | Format | What it holds |
|---|---|---|
| `plan.jsonl` | `plan/1` | The blocks and trials of the plan: scenario, speed, phrase identifier, intended outcome and key schedule, with the SHA-256 of the generator's settings, procedures and corpus |
| `oracle.jsonl` | `oracle/1` | Every press and release that the executor sent, by key name, with wall-clock and monotonic times; block and trial boundaries; one row per press of a block-start marker |
| `gt.jsonl` | `gt/1` | The calls that the reference input field received from the input method: kinds, lengths in UTF-16 code units, positions of the selection and of the replaced range, the kinds of commands and of the input method's windows as fixed names, and times; the key codes of key-downs; changes of focus. No text |
| `obs.jsonl` | `obs/1` | The logger's header, its key rows (fourteen categories of key, without key names, with press and release) and its session rows (duration, number of arrow-key presses, abandonment). No text |
| `run.json` | run manifest (`run/1`) | Described below |

Before every block the runner, not the executor, brings the reference input field to the front and presses the key
that switches the input method to Japanese (the kana key on macOS, `VK_IME_ON` on Windows); these presses are not in
the executor's record. The run manifest holds: `cell` (the settings of the run, including the name and version of the
input method and the commands that started the programs), `plan_sha256`, `git_commit` and `git_dirty` (the commit of
the git checkout that held the runner and whether it was modified, or `null` when the runner did not run from a git
checkout), `host` (the platform string, the processor architecture, the version of the operating system and the version
of Python), `started` and `ended` (wall-clock and monotonic times), `outputs` (the file name, SHA-256 and number of
lines of each log), `children` (for each program, its exit status, whether it was stopped and the name of its file of
error output, which is not deposited), `resets` (per block, whether a reset of the input method was run or skipped,
and why), `focus` (per block, the outcome of the command that brought the reference input field to the front and of
the check that it was in front), `aborted` and `abort_reason`. Before the manifest is written, the
home directory and the root of the checkout are replaced by `~` and `<repo>` in every string. Of the deposited runs,
only the macOS runs of the calibration-pilot part and the Windows pilot run `toolcheck-tsf` record a commit (with
`git_dirty` true in `verify-s5`, `verify-off` and `toolcheck-tsf` and false in the others); in the remaining runs both
fields are `null`, because the runner ran from an export of the code. The values of every run are in
`experiment/data.md` (macOS) and `experiment/data.windows.md` (Windows) of the code repository. A commit recorded in
`git_commit` belongs to the author's working repository, which is not public; the code of the freezing tags is in the
code repository.

**Runtime samples.** Each condition of the runtime-cost study has `samples.tsv` and `conditions.txt`. `samples.tsv` is
tab-separated, with a header line and one row per reading, in the columns `epoch_s` (Unix time in seconds), `cpu_pct`
(the logger's CPU, one core = 100 %), `rss_kb` (resident memory in KB), `threads`, `battery_pct`, `rss_alt_kb` (a second
measure of memory in KB, which differs by system), `energy_impact` and `ref_energy_impact` (macOS only: an energy index
of the logger and of the file manager as a reference; empty on Windows). On macOS `cpu_pct` is the mean over the
interval, `rss_kb` the memory column of `top` and `rss_alt_kb` the resident set of `ps`; on Windows `cpu_pct` is a
sample of about one second at the moment of reading, `rss_kb` the working set and `rss_alt_kb` the private bytes, which
is not resident memory. On macOS the first row of a run has an empty `cpu_pct`. In a row read while the process was
absent, the measured columns (`cpu_pct`, `rss_kb`, `threads`, `rss_alt_kb` and, on macOS, `energy_impact`) are zero,
`ref_energy_impact` is empty, and `epoch_s` and `battery_pct` keep their values; on Windows a CPU counter that cannot be
read also gives zero. The interval was set to 30 seconds; a reading takes longer than that (on macOS the period was
about 39 seconds in a trial run), so the times of the rows are not evenly spaced at 30 seconds. The values are compared
within each system and not between systems; the battery level and the energy index are not summarised. `conditions.txt`
holds lines of the form `name=value`: the condition, the operating system and its version, the number of logical
processors, the name of the sampled process, the SHA-256 of the logger's executable (`unknown` when it could not be
found at the start, as when the logger was not running yet; a line `executable_sha256_at_end` then gives it at the
end), the number of such processes before the run, the set duration and interval in seconds, and the start and end
times in UTC.

**Parts.** The dataset has two parts, which are kept apart.

1. *Measurement runs*, made from the registered plans after the freezes of the registrations:
   - `macos-standard-live-on`: macOS Japanese input method, Romaji input, live conversion on; 3,150 trials in 126
     blocks.
   - `macos-standard-live-off`: the same with live conversion off; 2,925 trials in 117 blocks.
   - `windows-msime-tsf`: Microsoft IME (Japanese), Romaji input, with the reference field that is a client of the
     Text Services Framework; 2,925 trials in 117 blocks (attempt 2).
   - The `working` condition of the runtime-cost study on each system: the logger running while the runner plays the
     plan of `macos-standard-live-on` (macOS) or of `windows-msime-tsf` (Windows). Its logs are deposited under the
     rules of the measurement runs; the run of each system passed the checks.
   - The start of `windows-msime-tsf` that the runner refused before the first trial (administrator rights; no key
     sent). It passes the checks of the deposit and is not counted among the attempts.
   - The runtime samples of the `idle` and the `working` condition on each system.
2. *Calibration-pilot runs*, made before the freezes from other plans, with provisional settings, that meet the
   registered criteria for deposit (`experiment/pilot-macos.md` and `experiment/pilot-windows.md` of the code
   repository, section "Raw logs"). The candidates are the macOS pilot runs `pilot-1`, `verify-1`, `verify-s5`,
   `pilot2-on`, `pilot2-off`, `verify-off` and `short-1` and any other run of the runner made before the first macOS
   pilot; the Windows pilot runs with the TSF reference field `short-tsf`, `pilot-tsf`, `verify-tsf` and
   `toolcheck-tsf`, and the three Windows pilot attempts with that field that the runner aborted. This part also
   holds, under the same criteria, the check that preceded the Windows measurement (section 10.8 of the registration):
   the 39-trial plan `verify` of the Windows pilot, run on the measuring machine with the versions of the measurement
   before the first attempt, and its first start, which the runner aborted before any key was sent. At deposit every
   macOS candidate passed the check, among them `dryrun-1`, a run of the runner before the first pilot that sent no
   key and was interrupted. Of the Windows candidates, all passed but one of the three aborted attempts: it was stopped
   after four trials, when an application window used as the target of S15 had been closed, and its observation log
   holds key presses that the measured trials and the procedure do not explain. The two attempts that passed were
   stopped before any key was sent, when the system refused to bring the executor's window to the front.

Shared by both parts are the phrase corpus (`phrases.jsonl`), the settings of the plan generator
(`plan_config.macos.json`, `plan_config.windows.json`), the procedures of the scenarios (`recipes.json`,
`recipes.macos-live-off.json`), the seed (`SEED`), the specification of the formats (`FORMATS.md`) and this
datasheet.

**What is not deposited.** A completed run that the stopping rule catches is not deposited, because its logs may hold a
person's key presses. A run that the runner aborted is deposited only if it passes the same checks or left neither a
reference log nor an observation log; one aborted inside a trial does not pass. Not deposited, therefore, are: the first
Windows attempt, which was stopped from outside the runner inside a trial; the Windows pilot attempt stopped after four
trials (Part 2 above); the observation logs of the `idle` condition on both systems, which hold the key presses that
started the sampling (only their samples are deposited); and the Windows pilot runs with the other reference field,
which received the key press that switches the input method on as a key-down, which the stopping rule counts as a press
from outside, together with an attempt with that field that the runner aborted before any key was sent, whose
reference log does not come from the reference field of the measurement. The start of the Windows measurement that the
runner refused before the first trial passes the same checks and is deposited. The error output of the programs
stays on the measuring machines, and checks typed by hand into the reference input field were not runs of the runner
and are not deposited. The table of attempts in the article states for every start of every run of a plan of the
measurement whether it was deposited.

**Files of the deposit.** The deposit holds one archive per run, made by the packing command (section "Preprocessing"),
the runtime samples and the shared files, each as a file of its own. The SHA-256 of each archive and of each runtime
sample is also recorded in `experiment/data.md` (macOS) and `experiment/data.windows.md` (Windows) of the code
repository. The SHA-256 of this datasheet cannot be given in the file itself; Zenodo gives a checksum of every file.

| File | Part | Contents | SHA-256 |
|---|---|---|---|
| `macos-standard-live-on-1.zip` | Measurement | `macos-standard-live-on`, attempt 1 | `77bd8a8e64563921a9d3e49fc5fa552a67d4633a66669354852dea651fed49b8` |
| `macos-standard-live-off-1.zip` | Measurement | `macos-standard-live-off`, attempt 1 | `e1cdf2e8b63ab402a950e37b8841ce302ccb280f396fefc148c8891971d73d10` |
| `windows-msime-tsf-2.zip` | Measurement | `windows-msime-tsf`, attempt 2 | `f2287a7494b108a7b827f96da48f523e84e1a9478121d03fa82dca2c8749cd06` |
| `windows-msime-tsf-refused-start.zip` | Measurement | The start of `windows-msime-tsf` that the runner refused before the first trial (administrator rights; no key sent) | `a5afe2dcbe57f37f92e115aa16be4b5dc5500894e4ce4cf13dab3615448a1447` |
| `macos-e3-working-1.zip` | Measurement | The `working` run of the runtime-cost study, macOS | `77b51736e0fb713b181748153927b12e0ff6c7734b97f8c058891836aed5e3ea` |
| `windows-e3-working-1.zip` | Measurement | The `working` run of the runtime-cost study, Windows | `39d40801197ce0e95f0271899c83d53b6bbd6dc977c6172e6e615385bddb1f45` |
| `macos-e3-idle-samples.tsv` | Measurement | Runtime samples, `idle` condition, macOS | `f2fc37ef12d520d0e757a9e1232c99975475017c75cc824815a79e236d261222` |
| `macos-e3-idle-conditions.txt` | Measurement | Conditions of the `idle` samples, macOS | `25a08f08c98ce4589ed34475b3dd803e178b8f52429ad66c2a71ec0c7fba680f` |
| `macos-e3-working-samples.tsv` | Measurement | Runtime samples, `working` condition, macOS | `32fb07829de7a4ac0dca0e128089dfe2eed977e26b044841884ef4c3b96921ec` |
| `macos-e3-working-conditions.txt` | Measurement | Conditions of the `working` samples, macOS | `95f29b15a7de9fc1b6334f804f1d8dd5f9377ec21002d12167d5d0f916dd7ae4` |
| `windows-e3-idle-samples.tsv` | Measurement | Runtime samples, `idle` condition, Windows | `43ab3b395635e0d5c78abc008db31368b48bcc6595c74f74f59e8e21debc5f59` |
| `windows-e3-idle-conditions.txt` | Measurement | Conditions of the `idle` samples, Windows | `13bf360fb1d0554b215db6896f522ea0efd4995b0c3c574bdf293c4d792f8122` |
| `windows-e3-working-samples.tsv` | Measurement | Runtime samples, `working` condition, Windows | `638da0eaafcf156a441068e91a36578199736c435f08e140391617e43fcca88f` |
| `windows-e3-working-conditions.txt` | Measurement | Conditions of the `working` samples, Windows | `9b6ae9391481a57cfee74654316203e7ef33152e72980af44bd022a5c8f68345` |
| `macos-pilot-short-1.zip` | Calibration pilot | macOS pilot run `short-1` | `cde623bdab677a997e494fecf97a7d091baf4e542a703172f1997592fe6238be` |
| `macos-pilot-pilot-1.zip` | Calibration pilot | macOS pilot run `pilot-1` | `6bd8583bfab9483d02b9b292697d2ac45ac836dad27e0f6946ef7569c663a749` |
| `macos-pilot-verify-1.zip` | Calibration pilot | macOS pilot run `verify-1` | `31e60d7627e6ddd5a2c7a7601fd9ef1c794cb31235395a8df4403d6dde7a80ae` |
| `macos-pilot-verify-s5.zip` | Calibration pilot | macOS pilot run `verify-s5` | `96c460291e65506b5b9187fefcd82bf0017290f2ec677cea6f055fb73807c5f4` |
| `macos-pilot-pilot2-on.zip` | Calibration pilot | macOS pilot run `pilot2-on` | `3bdf963d8812f63af3a2e10ec72af2b7adf6693da74dd85a19a443a8fdb1e40b` |
| `macos-pilot-pilot2-off.zip` | Calibration pilot | macOS pilot run `pilot2-off` | `1e4563d2b196f974407c911fe96be84a50e7916055623d91cf1af2f307c26187` |
| `macos-pilot-verify-off.zip` | Calibration pilot | macOS pilot run `verify-off` | `28ac532edf171217b240ee7a75bb4118f584ce2b9581964b3d764c95dcb5fe11` |
| `macos-pilot-dryrun-1.zip` | Calibration pilot | macOS run `dryrun-1` of the runner before the first pilot (no key sent; interrupted) | `fdbcd130d1f2b3209add450cd3fb03b9c10774f0cf96377a56454824ec43f78e` |
| `windows-pilot-short-tsf.zip` | Calibration pilot | Windows pilot run `short-tsf` | `a31d48d1c797535ee92c9b03c48be8e83bc5705202e15859f69d1f41cd977ced` |
| `windows-pilot-pilot-tsf.zip` | Calibration pilot | Windows pilot run `pilot-tsf` | `634ceff63ca0f43d65d49e506e877ae48bf238ec9c43330228d2cb4c915a1111` |
| `windows-pilot-verify-tsf.zip` | Calibration pilot | Windows pilot run `verify-tsf` | `546ab6ed5b76bb79a40c679177167c26be6fa4461a45acdd17d656a0f840823c` |
| `windows-pilot-toolcheck-tsf.zip` | Calibration pilot | Windows pilot run `toolcheck-tsf` | `c066d36bcb90562c30a11ee84decfd37ffa9c4d88b31b4fcad5873884b4adbf5` |
| `windows-pilot-aborted-a.zip` | Calibration pilot | Windows pilot attempt with plan `short` that the runner aborted (the foreground window was refused) | `1f8ae344c9ea8f95fafa85e26b20c8b782974df05204fe1df79fb6d1ce2e10f9` |
| `windows-pilot-aborted-b.zip` | Calibration pilot | Windows pilot attempt with plan `pilot` that the runner aborted (the foreground window was refused) | `7565058faf6f8cf794586d836d59167901540960ac7321ad7b9d4cef91e76432` |
| `windows-check-verify.zip` | Calibration pilot | The check before the Windows measurement (plan `verify` of the pilot, 39 trials) | `e980074f56032f3c264f82df2d0b4f4f8a10eeddd3b2f8e4cc5d607323f1a217` |
| `windows-check-verify-start-1.zip` | Calibration pilot | The first start of that check (aborted by the runner before any key was sent) | `6a910ea2ad4b2d27b4ef86c971edfa2090f8e2e2c3ddadd021748bb4e40f07ba` |
| `phrases.jsonl` | Shared | The phrase corpus | `c8f890cb4d0848bf04e6d7d95efe4e4d3dfd02a23eb1c7f103344f14d13e4620` |
| `plan_config.macos.json` | Shared | Settings of the plan generator, macOS | `eb0ff955834d135a99d1dff3ae4cb1d58a8734c819835b3afbc5db08edf02209` |
| `plan_config.windows.json` | Shared | Settings of the plan generator, Windows | `400fe5314d0f660f2e776a7f86deb471ae1235a3bea4c1be457b26ebe0a9181f` |
| `recipes.json` | Shared | Procedures of the scenarios | `430ec52c67f0592bc1c9d038a972876a2608ed7c39eb2aabcf68efc4ff908411` |
| `recipes.macos-live-off.json` | Shared | Procedures of the scenarios, macOS with live conversion off | `8560f7bd59d04b0a2629af2beabdfd6fabc0aef92d35a2a9ad73a89bfd74d19a` |
| `SEED` | Shared | The seed | `e36ada0b3705d3a26adb9de6c05e0cacf29e47b2cb77685a779bfccd33ff1bfc` |
| `FORMATS.md` | Shared | Specification of the formats | `2b06ef8d5b28278850b6dfe40d942b5b1557d3b5aca86a45e224ba4a495be6f8` |
| `datasheet.md` | Shared | This datasheet | not given (this file) |

**Labels.** The reference log is the ground truth: the conversion sessions of the reference are assembled from its
calls by the registered rules, which differ by system (`experiment/gt_rules.json` of the code repository). The plan
states the intended outcome of every trial. The session rows of the logger are the data under test, not labels.

**Missing information, errors and noise.** Every planned trial of the three measurement runs was measured. The records
were not corrected. Known departures are reported per run in the article and in the run records. In the run of
`macos-standard-live-off` the reference field lost the focus once outside the plan, for 2.7 s; by the operator's report
this was a pointer click on another window, which the logs cannot confirm. The logs show no key press during it other
than those sent, and the 11 presses that the executor sent in it did not reach the reference field. Key presses are
injected, and their times depend on the scheduling of the machine. Other software ran on the measuring machines: on
macOS a runner of a continuous-integration service (`experiment/env.md`), on Windows what the event logs show
(`experiment/data.windows.md`). The definitions of the reference differ between the two systems, so measures defined
differently are not compared across systems.

**Relationships between files.** Trials and blocks are linked across the plan and the executor's record by their
identifiers. The reference log and the logger's log carry no trial identifiers; the registered analyses match them with
the executor's record by wall-clock time, without clock correction (sections 4.4 and 6.9 of the registration). The
differences between the clocks of the three programs are measured per block at the block-start markers and reported;
they are not used to correct the matching. A marker is four arrow-key presses (left, right, left, right) in the plans
of the measurement and of the Windows pilot, and three presses in the macOS pilot plans (F13, F14, F13 in `pilot-1`,
which leave no row in the logger's log, and left, right, left from then on). The analysis of text recoverability uses
the markers to assign the logger's key rows to trials.

**Splits.** The dataset defines none. The registered analysis of text recoverability splits the phrase corpus: whole
works, in an order drawn with the seed, are held out until they hold at least a fifth of the phrases (three of the
twelve works), and a second attacker splits by phrase instead (section 6.8 of the registration).

**Self-containment.** The fields of every file can be read from the deposit alone, with `FORMATS.md` and this datasheet.
The analyses need the code release (DOI 10.5281/zenodo.23183441), which is archived separately.

**People and personal data.** No participants were recruited, and the author operated the measuring machines. No file
holds text that a person typed during the study: the only text is the public-domain phrase corpus, whose
readings the plans and the executor's record spell out key by key. The runs were driven by key presses that a program
generated, and no person was meant to type during them. Both the reference log and the logger's log would record a
person's key presses made during a run (the reference by key code, the logger by category). The runs of the
measurement were checked for key presses that the program did not send by the stopping rule of the registration
(sections 3.4 and 10.8) right after each run; the pilot runs were checked before the freezes (the macOS runs partly
with earlier forms of the rule, the Windows runs with the form registered for macOS); and every deposited run is
checked again at deposit, by the registered form. A run that the rule catches is not deposited. The rule has blind
spots, so the absence of a person's key presses is not guaranteed: a press of a person that replaces a sent press that
did not reach the reference is not detected; a press made from 20 ms before to 300 ms after a sent press whose row the
logger missed is not detected; presses that reach neither the reference nor another application, such as a shortcut
that the system consumes, are not caught; and presses into an input field that takes keys without taking the key
window from the reference input field may not be caught. That the rule catches a person's key presses was checked
only on cases constructed in the tests of the code (section 7 of the registration, "The stopping rule counts presses,
not keys"). A press that escaped the rule would appear as a key code in the reference log and as a category in the
logger's log; no field stores text, but a sequence of key codes can reveal what was typed. The observation logs of
the `idle` condition, which hold such presses, were not deposited.

The packing command refuses a run whose files hold a path that begins with a drive letter, a UNC prefix, `/Users/`,
`/home/`, `/Volumes/`, `/root/`, `/mnt/`, an MSYS drive or `file:`, the home directory or the login name of the user
who runs it or of those given to it, or the path of the repository. It does not search for host names, e-mail
addresses or other personal text, does not find a login name of two characters or fewer or one joined to a letter,
digit, underscore, dot or hyphen, and does not recognise paths that begin elsewhere (such as `/tmp/`); it is a
safeguard against slips, not a proof of anonymity. The runtime samples (`samples.tsv`, `conditions.txt`) are not bundled
by the packing command and are not checked by it; they hold numbers, the version of the operating system, the name of
the sampled process and the SHA-256 of its executable, and no path.

**Offensive content.** The phrases are clauses of literary works whose copyright has expired, joined until their
readings reached a length and selected by the characters of the reading and the text; they were not selected or
screened for their content, and some may contain expressions that some readers find offensive.

## Collection process

**How the records were made.** The plan generator made each plan from the phrase corpus, the procedures of the
scenarios, the settings and the seed. The runner played the plan: the executor injected the presses through the
interfaces of the operating system into the reference input field, which wrote the reference log, while the
stand-alone logger (`kit/logger` of the code repository) wrote its log. All files were written by these programs
during the runs.

**Machines and versions.** macOS: an Apple Silicon laptop with a JIS keyboard, macOS 27.0 in the pilots and 27.0.1
(26A434) in the measurement, with the macOS Japanese input method in Romaji input (version 6.3 of its Romaji typing
component in the measurement). Windows: an x86-64 tablet computer with a JIS keyboard and Windows 11 Home 25H2, with
Microsoft IME (Japanese) in Romaji input: build 26200.8875 with `IMJPTIP.DLL` 10.0.26100.8875 in the pilot, and build
26200.9457 with `IMJPTIP.DLL` 10.0.26100.9278 in the check of section 10.8 and the measurement. The learning of the
input methods was not reset. The versions of every run are in its manifest; the machines are described in
`experiment/env.md` (macOS), `experiment/data.windows.md` (Windows) and sections 2.2 and 10.4 of the registration.

**Sampling.** The plans were generated from the registered settings with the seed; the plans, their randomisation and
the number of trials per scenario and speed are set out in sections 2.4 and 3.3 of the registration (section 10.6 for
Windows) and in the settings of the generator. The phrases were drawn from the corpus by the generator. The pilot plans
used provisional settings and other seeds (section 3.1 of the registration).

**Who was involved.** The author operated the measuring machines (the operator of the run records). An AI
coding assistant started and monitored the measurement runs and ran the checks of the runs and the packing, under the
author's instructions (see the AI declaration of the article). No one was paid to take part.

**Timeframe.** The macOS pilots ran before the freeze of the registration of the macOS cells on 2 October 2026, and the
Windows pilot ran after it and before the freeze of the Windows addendum on 3 October 2026. The macOS runs of the
measurement and of the runtime-cost study were made from 2 October 2026, 16:03, to 5 October 2026, 01:04 (UTC), and the
Windows runs, from the check of section 10.8 to the end of the `working` condition, from 3 October 2026, 15:27, to 4
October 2026, 23:35 (UTC; 4 October, 00:27, to 5 October, 08:35, in Japan Standard Time).

**Ethical review.** Not applicable: no participants were recruited.

## Preprocessing, cleaning and labelling

**The logs.** None. The deposited logs are the files that the programs wrote; no value was changed, removed or added.
During the pilots, the observation logs were sealed: each was moved aside unread, and only a copy of its key rows was
analysed. At deposit they are unsealed, which restores the file as the logger wrote it, and deposited whole; their
session rows were not analysed. At deposit, the packing command (`kit/analysis/pipeline.py --pack` of the code release)
bundles only the manifest and the four logs of a run, stops when a log does not match the SHA-256 recorded in the run
manifest, applies the checks described under "People and personal data", the stopping rule and the check of the
content-free formats, refuses a run that fails or cannot be checked, writes the files in a fixed order with fixed times
so that packing twice gives the same archive, and prints the SHA-256 of the archive. The runtime samples are the
samples as the sampler wrote them; their summaries are in the code repository.

**The phrase corpus.** The corpus was built once from the source files listed under "Distribution". From each file the
title and author lines, the explanation of the symbols, the colophon (from the line naming the base edition onwards),
the annotations and the ruby were removed, keeping the kanji under the ruby. The text was split into sentences and
clauses; clauses whose readings are in hiragana were joined until the reading reached 6 characters, without exceeding
30; phrases whose reading or text held other characters were dropped; and up to 150 phrases per work were kept, ranked
by the SHA-256 of the seed and the phrase identifier. The readings were assigned by SudachiPy 0.7.0 with
sudachidict-core 20260723.1. The result has 1,800 phrases, 150 from each of 12 works, with readings of 6 to 30
characters (median 16). A spot check of 40 phrases found the readings to be valid key sequences; a few differ from the
reading the author of the work intended, and nine readings occur twice under different phrase identifiers. The
procedure is `kit/corpus/build_corpus.py` of the code release, and the settings are in `experiment/data.md`.

## Uses

**Uses so far.** The registered analyses of the study: detection of conversion sessions, agreement of their duration,
of the number of arrow-key presses and of abandonment with the reference, per session and per text, how far the key
rows reveal the typed text, and the runtime cost of the logger; and exploratory analyses reported as such. The result
files are in the code repository (`experiment/results`). An article that reports the study is in preparation.

**Uses that the records support from the deposit alone.** (a) Examining the calls that an input method makes to an
input field, per system and setting, as a basis for re-examining definitions of a conversion. (b) Evaluating another
rule that infers sessions from key presses against the same reference, by applying it to the executor's record or to
the key rows of the logger's log; the prediction of the study is a worked example. (c) Studying how far a key log
without text reveals the text, with the typed phrases known.

**What limits other uses.** The input is synthetic: fixed phrases typed by a program without errors or corrections,
with injected key events, on one machine per system, with one version of each input method in the measurement (the
Windows pilot used an earlier version), one kind of reference input field per system, and the learning of the input
methods not reset. Evaluating a logger that needs other signals, such as the windows of the input method, is a use of
the code rather than of the dataset: it means running the kit again, which does not reproduce the same reference,
because the learning of the input method and the versions differ.

**Uses for which the dataset is not suited.** As a record of human writing, as a benchmark of natural typing, or for
inferences about writers.

## Distribution

The dataset is distributed through Zenodo under DOI 10.5281/zenodo.23183438, under the Creative Commons Attribution
4.0 International licence. The code that made the records and reads them is distributed separately under the Apache
License 2.0 (DOI 10.5281/zenodo.23183441; https://github.com/mikotomiura/ime-conversion-logging). Date of release:
7 October 2026.

The phrases come from works whose copyright has expired, obtained from Aozora Bunko (https://www.aozora.gr.jp/). Its
terms for such works allow the files to be copied, redistributed and adapted freely; they ask that the information
recorded in the files (the title, the author, the base edition, the names of those who entered and proofread the text,
the dates and the note on their origin) not be removed, and expect a record of what was changed in which file
(Aozora Bunko, 2022). The corpus keeps only phrases and does not carry that information; it records the
identifier of the source work of every phrase, the source files are listed below with the SHA-256 of the archives
downloaded, the information is in those files, and the changes are recorded under "Preprocessing". Each archive holds
the ruby-annotated text in modern orthography; paths are relative to https://www.aozora.gr.jp/.

| `work_id` | Author | Title | Aozora work ID | Text file | SHA-256 of the archive |
|---|---|---|---|---|---|
| `akutagawa_kappa` | Akutagawa Ryunosuke | Kappa | 000069 | `cards/000879/files/69_ruby_1321.zip` | `8daf7b994cebd17dff5a5447fd18d0408a153855ea821b9bec138ee3e68a49e9` |
| `dazai_ningen_shikkaku` | Dazai Osamu | Ningen Shikkaku | 000301 | `cards/000035/files/301_ruby_5915.zip` | `fb37d694f86687f9e84e26643804fa84b4346895f5f56a90dd8843a12e2bc9c8` |
| `dazai_shayo` | Dazai Osamu | Shayo | 001565 | `cards/000035/files/1565_ruby_8220.zip` | `712a759c175c7341bb2916f747f95d0d900923b353d511001ad677b7411eabc8` |
| `hayashi_horoki` | Hayashi Fumiko | Horoki (first edition) | 045649 | `cards/000291/files/45649_ruby_26492.zip` | `179eaee1bd51bfc1987daf16f293d6819083759ba5db0ffc95307f060b988947` |
| `miyazawa_ginga_tetsudo` | Miyazawa Kenji | Ginga Tetsudo no Yoru | 000456 | `cards/000081/files/456_ruby_145.zip` | `8bc90c0c7f97e069d02c4783ce905fa7b959cf60a74df816aeccf94079555076` |
| `nakaya_yuki` | Nakaya Ukichiro | Yuki | 052468 | `cards/001569/files/52468_ruby_49668.zip` | `2d7b144a0f219e8b8e0e861745deba270a37246108fab6271854d4e7653db848` |
| `oda_meoto_zenzai` | Oda Sakunosuke | Meoto Zenzai | 000545 | `cards/000040/files/545_ruby_33101.zip` | `4a88e427c77bcebe21b04bc49aa6cf57e381f618caf7c945a4cc98b42347b89d` |
| `okamoto_hanshichi_01` | Okamoto Kido | Hanshichi Torimonocho 01: Ofumi no Tamashii | 001293 | `cards/000082/files/1293_ruby_5382.zip` | `f409f1374648dbb29d91d50960f95d35e6f94eee14c3a01aa3044f2190f3452e` |
| `sakaguchi_darakuron` | Sakaguchi Ango | Darakuron | 042620 | `cards/001095/files/42620_ruby_21051.zip` | `82db555d429391899b8110b2416739825689596b5196ff5fa3fe87f05abcec40` |
| `soseki_botchan` | Natsume Soseki | Botchan | 000752 | `cards/000148/files/752_ruby_2438.zip` | `53c4d86033d0e4590c0354df5fec2709820ef21c09fa402826ff33376ec21228` |
| `soseki_kokoro` | Natsume Soseki | Kokoro | 000773 | `cards/000148/files/773_ruby_5968.zip` | `c55d7bf6c7cc5bd960ce291949f38cae9ba6a672a6142c3b9e1a9f5a159fa4c4` |
| `terada_kaki_no_tane` | Terada Torahiko | Kaki no Tane | 001684 | `cards/000042/files/1684_ruby_11273.zip` | `ce6792f93e0b98a747b9dd452e4bdb8519b3439975bf68f06c9c605dae747c54` |

No other restriction is known to the author.

## Maintenance

The dataset is hosted by Zenodo. The contact is the author, Mikoto Miura (mmiura.network@gmail.com). Corrections will
be released as new versions under the same concept identifier, with a record of what changed. No participants were
recruited, so no limit is set on how long the dataset is kept. Others may build on the dataset under its licence;
additions are not merged into it.

## References

Aozora Bunko (2022). Aozora Bunko shuroku fairu no toriatsukai kijun [Standards for the handling of the files held by
Aozora Bunko]. In Japanese. https://www.aozora.gr.jp/guide/kijyunn.html (retrieved 6 October 2026).

Gebru, T., Morgenstern, J., Vecchione, B., Vaughan, J. W., Wallach, H., Daumé III, H., and Crawford, K. (2021).
Datasheets for datasets. Communications of the ACM, 64(12), 86-92. https://doi.org/10.1145/3458723
