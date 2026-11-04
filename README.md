# slurm-auto

A Rust CLI that inspects configured Slurm node capacity and generates proportional
`sbatch` resource arguments. It supports inspection, planning, and submission
using TOML sbatch defaults. Only `submit` without `--dry-run` starts `sbatch`.

## Installation

Automatic releases provide prebuilt static Linux binaries on the repository's
[GitHub Releases page](https://github.com/naoto-aoki-fy/slurm-utility/releases).
Choose the executable matching your machine (initial release: `v0.0.1`):

| Target | Release asset |
| --- | --- |
| x86-64 | `slurm-auto-vMAJOR.MINOR.PATCH-x86_64-unknown-linux-musl` |
| ARM64 | `slurm-auto-vMAJOR.MINOR.PATCH-aarch64-unknown-linux-musl` |

Assets are raw, uncompressed executables. Download the matching asset, rename it
to `slurm-auto`, mark it executable, and place it in a directory on your `PATH`.
For example, on x86-64 Linux:

```sh
curl -fL -o slurm-auto https://github.com/naoto-aoki-fy/slurm-utility/releases/download/v0.0.1/slurm-auto-v0.0.1-x86_64-unknown-linux-musl
chmod +x slurm-auto
./slurm-auto --version
```

Raw downloads do not retain Unix executable permissions, so `chmod +x` is required.
The initial package version in `Cargo.toml` is `0.0.1`.

Install a current stable Rust toolchain, then run:

```sh
cargo install --path .
```

At runtime, `scontrol` must be on `PATH`, connected to the intended cluster, and
support `scontrol show node --json`. The CLI invokes that executable directly
with separate arguments, without a shell. Cargo needs access to crates.io on
the first build to obtain `clap`, `serde`, `serde_json`, and `toml`.

## Usage

```sh
slurm-auto inspect
slurm-auto plan -p gpu -N 2 -g 2
slurm-auto plan --partition cpu --nodes 1 --cpus-per-node 16
slurm-auto plan -p gpu  # All GPUs and effective CPUs on one node
slurm-auto plan -p cpu  # All effective CPUs on one node
slurm-auto plan -p gpu -g 2 --memory proportional  # Also request proportional memory
slurm-auto plan --help
```

`inspect` displays each partition's name, CPU/GPU type, configured node count,
effective CPUs per node, configured GPUs per node, and memory per node in MiB.

| Option | Meaning | Default |
| --- | --- | --- |
| `-p`, `--partition` | Partition name | `sbatch.defaults.partition`, otherwise required |
| `-N`, `--nodes` | Number of configured nodes to request | 1 |
| `-g`, `--gpus-per-node` | GPUs per node in a GPU partition | All configured GPUs |
| `-c`, `--cpus-per-node` | CPUs per node in a CPU partition | All effective CPUs |
| `--memory` | `omit` (no `--mem`) or `proportional` (calculated MiB) | `sbatch.defaults.memory`, otherwise `omit` |

**The wrapper's `-c` means CPUs per node. Slurm's `-c` means CPUs per task.**
This tool assumes one CPU per task and generates `--ntasks-per-node`; it does
not generate `--cpus-per-task`. GPU and CPU count options are mutually exclusive
and must match the partition type. All requested counts must be positive.

For 64 effective CPUs, 4 GPUs, and 262144 MiB per node, requesting two GPUs
on each of two nodes prints an allocation and this resource-only preview:

```sh
sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32
```

For a CPU node with 64 effective CPUs and 262144 MiB, requesting 16 CPUs prints:

```sh
sbatch --partition=cpu --nodes=1 --ntasks-per-node=16
```

The preview uses POSIX shell escaping for each argument, including unusual
partition names. It contains no script or payload: add a job script yourself
when using the arguments outside this tool. No `sbatch` process is started.

## Submission configuration

Copy [config.example.toml](config.example.toml) to
`~/.config/slurm-auto/config.toml` and replace the account with your cluster account.
An absolute `$XDG_CONFIG_HOME` takes precedence over `~/.config`; `--config <path>`
overrides the default location. Explicit paths are used literally (the shell can
expand `~`); missing files and unknown fields are errors.
Obsolete `[templates.*]` sections are rejected; `--template` is unsupported.
`inspect` does not load configuration. `plan` loads configuration for partition
and memory defaults; `plan --config <path>` selects a custom file and requires it
to exist. An explicit `plan -p ...` works without an installed default configuration
file; if one exists, it is loaded and validated. Planning remains a resource-only
preview, ignoring workload and other submission defaults.

```toml
[sbatch.defaults]
account = "hoge"
partition = "gpu"
memory = "omit"
time = "24:00:00"
wrap = "sleep infinity"
job_name = "my-job"
sbatch_args = ["--output=slurm-%j.out", "--qos=normal"]
```

Arguments are strings in an array: `sleep infinity` is one argument. Do not add
shell quotes inside a value. The optional `sbatch_args` array defaults to empty.

The five dedicated string defaults are optional and must be nonempty when present.
`memory` accepts `"omit"` (default) or `"proportional"`. Both `plan` and `submit`
accept `--memory omit|proportional`, overriding the configuration. With `omit`,
no memory size is passed to sbatch; Slurm applies its defaults. With `proportional`,
the calculated memory is passed as `--mem=<MiB>M`.
For account, time, job name, and wrap, precedence is explicit CLI options, then
`sbatch.defaults.sbatch_args`, then the dedicated `[sbatch.defaults]` fields.
`submit` accepts `-A`/`--account`, `-t`/`--time`, `-J`/`--job-name`, and `--wrap`
as overrides. Short and long argument spellings, including attached values, are
recognized; only the winning value is forwarded for each of these options.
Repeated account/time/job-name options in `sbatch_args` use the last value;
duplicate wraps in the array remain an error. Abbreviated `--account`, `--time`,
`--job-name`, `--wrap`, and `--gres` options are rejected so they cannot bypass
precedence or argument checks. Invalid configuration arguments
are rejected even when a CLI option would override them.
Partition selection uses `-p`/`--partition` or `sbatch.defaults.partition`;
`sbatch_args` cannot set partition options.

```sh
slurm-auto submit -N 2 -g 2  # Uses the default partition
slurm-auto submit -A other-account --wrap "hostname" --dry-run
slurm-auto submit -p gpu -N 2 -g 2 --dry-run
slurm-auto submit --config ./config.example.toml -p cpu -c 16 --dry-run
slurm-auto submit -p cpu -c 16 -- ./job.sh 'one argument' --job-option
```

A submission requires exactly one workload: `--wrap` from the CLI, `sbatch_args`,
or the dedicated defaults, or a batch script and arguments following `--`. A script
replaces the dedicated default wrap. A CLI or `sbatch_args` wrap still conflicts with
a script. A CLI wrap overrides a `sbatch_args` wrap. Script operands do not belong in
`sbatch_args`. The wrapper inserts an option terminator before the script so its
arguments cannot be interpreted as submission options. Slurm handles script
reading and execution. The wrapper never executes a shell; Slurm itself executes
`--wrap` contents according to its normal behavior.

For the GPU capacity example above, the example configuration resolves to:

```sh
sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32 --account=hoge --time=24:00:00 --job-name=my-job '--wrap=sleep infinity' --output=slurm-%j.out --qos=normal
```

Dry-run queries `scontrol`, loads and validates the sbatch configuration, calculates
resources, and displays both the allocation and a POSIX shell-escaped full command.
It never invokes `sbatch`. Real submission forwards sbatch stdout/stderr, including
its normal `Submitted batch job <ID>` response; `--parsable` numeric responses also
receive a labeled job ID. Failures to start `sbatch` and nonzero exits are errors.

`sbatch_args` cannot set partition, node/task counts, CPU or GPU allocation, memory,
or incompatible CPU topology/sharing controls. The policy rejects long options
(including `--option=value` and abbreviations), resource short aliases such as
`-p`, `-N`, `-n`, `-G`, `-g`, `-c`, and GPU-related `--gres` (`gpu`, `mps`, `shard`).
Non-GPU GRES remain allowed. Conflict messages identify the offending argument.
Supported non-resource short options include `-A`, `-a`, `-b`, `-C`, `-D`, `-d`,
`-e`, `-i`, `-J`, `-L`, `-M`, `-m`, `-o`, `-q`, `-t`, `-w`, `-x`, `-H`, `-Q`,
`-v`, `-W`, `-k`, and `-r`. Values may be separate or attached, such as `-Ahoge`.
Options that exit without submitting, such as `--help` or `--test-only`, are rejected.

Configuration, sbatch argument policy, allocation calculation, command rendering,
and process execution are separate modules. To exercise dry-run without a cluster,
put a fake `scontrol` executable on `PATH` that returns
`tests/fixtures/cluster.json` for `show node --json`. A fake `sbatch` on `PATH` can
record argv and return controlled stdout, stderr, and exit codes. No production
fixture switch or cluster connection is required for this approach.

## Calculation and discovery assumptions

For effective CPUs `C`, configured GPUs `G`, configured memory `M` in MiB,
and requested GPUs `g` on a GPU node (memory applies only with `--memory proportional`):

```text
tasks per node  = floor(C * g / G)
memory per node = floor(M * g / G)
```

For requested CPUs `c` on a CPU node:

```text
tasks per node  = c
memory per node = floor(M * c / C)
```

Calculations use widened integer arithmetic. Requests above per-node capacity,
above configured partition node count, or producing zero tasks are rejected.
Zero calculated memory is rejected only in `proportional` mode. Omitting the
applicable resource count requests all CPUs or GPUs on each node.

Discovery reads `nodes`, node `name`, `partitions`, `effective_cpus` (also accepting
`cpus_efctv`), `cpus`, `gres`, and `real_memory` from the Slurm JSON response.
Missing, null, or explicitly unset effective CPU values fall back to `cpus`.
Numeric fields accept unsigned integers and finite Slurm tagged numeric objects
(`set`, `infinite`, `number`). Malformed effective CPU values are errors.
Partition membership accepts a string array or comma-separated string; null or
empty membership means the node is unassigned and contributes to no partition.
Nodes in multiple partitions contribute to each. Duplicate node records fail.

GPU counts come exclusively from configured `gres`, summing entries with the
exact resource name `gpu`. Examples include `gpu:4`, `gpu:a100:4(S:0-3)`, and
`gpu:a100:2(IDX:0,1),gpu:h100:2(S:1),mps:400`. Other resource types such as
`mps`, `shard`, `gpu_mem`, or `bandwidth` do not count as GPUs. Empty, null, and
`(null)` GRES mean no configured GPUs. Missing GRES, malformed annotations,
unparseable GPU counts, and overflowing counts fail rather than guessing.

Every partition must have identical effective CPUs, GPU counts, and configured
memory on every member node. All commands reject discovery if **any** partition
is heterogeneous, including mixed GPU/CPU membership. Diagnostics show the
partition, member node names, and differing capacities.

## Limitations

- Capacity is configured capacity, including allocated, drained, and otherwise
  busy nodes. State, free memory, CPU allocation, and `gres_used` are ignored.
  A successful plan does not guarantee immediate scheduling or node availability.
- The tool discovers partitions through node membership, not a separate partition
  query. Empty partitions cannot be discovered this way.
- GPU models are counted together; GPU type selection, MIG/shard interpretation,
  heterogeneous allocations, and additional resource policies are unsupported.
- Account/QOS restrictions, partition memory limits, reservations, topology,
  licenses, and other scheduler policy are not modeled. CPU counts are Slurm
  CPUs and may represent hardware threads according to cluster configuration.
- Script contents, embedded `#SBATCH` directives, and inherited Slurm environment
  variables are not inspected. Avoid resource directives in scripts and conflicting
  `SBATCH_*` settings; use the wrapper's resource options instead.
- Dry-run validates configuration structure, workload selection, owned resource options,
  and calculated resources. It does not ask Slurm to validate site-specific options,
  account permissions, script existence, or scheduler policy.
- `sbatch_args` options use complete long names or supported individual short aliases.
  Short flag clusters are rejected. Additional site-specific long options are
  forwarded to Slurm; use `--option=value` to remove value/flag ambiguity.
- Output is captured and forwarded after `sbatch` exits; `--wait` therefore delays
  output until the job completes. A process killed by a signal returns wrapper
  status 1; ordinary sbatch nonzero exit codes are preserved.

## Automatic releases

Every push to `main` triggers [.github/workflows/release.yml](.github/workflows/release.yml).
The workflow checks out the exact triggering commit, runs the existing tests on
Linux without a Slurm cluster, and uses a build matrix with `cross` 0.2.5 and
Rust 1.93.0 to build both musl targets in release mode. `Cargo.lock` fixes the
application dependency versions; builds use `--locked`. Neither a manually
created tag nor a separate publishing step is required.

Only after tests and both builds succeed does the publishing job download both
executables and allocate a version. It fetches the full tag history and considers
only exact `vMAJOR.MINOR.PATCH` tags containing non-negative integers. Versions
are compared numerically, ignoring prereleases and other tag formats. The highest
version's PATCH increases by one, keeping MAJOR and MINOR unchanged; without a
matching tag the first release is `v0.0.1`. This release version is independent
of the package version in `Cargo.toml`.

Publishing jobs share a concurrency group with queued execution, so version
allocation and publication happen sequentially. The built-in `GITHUB_TOKEN`
has read access during builds and `contents: write` only in the publishing job.
Repository rules must allow that token to create release tags. The tag points
to the triggering commit, and the release includes automatically generated notes
and both versioned raw executables listed above.

A release remains a draft until both assets have been uploaded. A rerun reuses
an existing version tag for the same commit, resumes a draft by uploading missing
assets, or skips an already published release after checking both assets exist.
Existing tags, releases, and uploaded assets are never overwritten. Interrupted
publishing can leave a tag or draft; rerun the failed workflow to finish it. A
published release with a missing or empty asset causes a rerun to fail rather
than modify the release. Build or test failures create no tag or release.

## Development

The library separates discovery/GRES parsing, models, partition/request validation,
resource calculation, and command generation. The binary handles CLI parsing and
readable output. Future calculated options can extend the allocation model and
argument generation without mixing discovery with submission behavior.

Tests use JSON fixtures and, on Unix, a temporary fake `scontrol` executable.
No real Slurm cluster or GPU is needed. Both documented resource examples are
explicit acceptance tests. Unix CLI tests also cover argument validation,
command failures, and planning with no `sbatch` executable available.

```sh
cargo fmt
cargo clippy --all-targets -- -D warnings
cargo test
```

Submission tests use fake `scontrol` and `sbatch` executables to verify defaults,
CLI/configuration precedence, workload conflicts, literal argv forwarding, and dry-run
rendering without a Slurm cluster.
