# slurm-auto

A Rust CLI that inspects configured Slurm node capacity and generates proportional
`sbatch` resource arguments. It supports inspection, planning, and submission
using named TOML templates. Only `submit` without `--dry-run` starts `sbatch`.

## Installation

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
slurm-auto plan -p gpu  # All GPUs, effective CPUs, and memory on one node
slurm-auto plan -p cpu  # All effective CPUs and memory on one node
slurm-auto plan --help
```

`inspect` displays each partition's name, CPU/GPU type, configured node count,
effective CPUs per node, configured GPUs per node, and memory per node in MiB.

| Option | Meaning | Default |
| --- | --- | --- |
| `-p`, `--partition` | Partition name | Required for `plan` and `submit` |
| `-N`, `--nodes` | Number of configured nodes to request | 1 |
| `-g`, `--gpus-per-node` | GPUs per node in a GPU partition | All configured GPUs |
| `-c`, `--cpus-per-node` | CPUs per node in a CPU partition | All effective CPUs |

**The wrapper's `-c` means CPUs per node. Slurm's `-c` means CPUs per task.**
This tool assumes one CPU per task and generates `--ntasks-per-node`; it does
not generate `--cpus-per-task`. GPU and CPU count options are mutually exclusive
and must match the partition type. All requested counts must be positive.

For 64 effective CPUs, 4 GPUs, and 262144 MiB per node, requesting two GPUs
on each of two nodes prints an allocation and this resource-only preview:

```sh
sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32 --mem=131072M
```

For a CPU node with 64 effective CPUs and 262144 MiB, requesting 16 CPUs prints:

```sh
sbatch --partition=cpu --nodes=1 --ntasks-per-node=16 --mem=65536M
```

The preview uses POSIX shell escaping for each argument, including unusual
partition names. It contains no script or payload: add a job script yourself
when using the arguments outside this tool. No `sbatch` process is started.

## Submission templates

Copy [config.example.toml](config.example.toml) to
`~/.config/slurm-auto/config.toml` and replace the account with your cluster account.
An absolute `$XDG_CONFIG_HOME` takes precedence over `~/.config`; `--config <path>`
overrides the default location. Explicit paths are used literally (the shell can
expand `~`); missing files, unknown fields, and missing templates are errors.
`inspect` and `plan` do not load configuration.

```toml
[templates.default]
sbatch_args = ["-A", "hoge", "-t", "24:00:00", "--wrap", "sleep infinity"]

[templates.batch]
sbatch_args = ["--account=hoge", "--time=01:00:00"]
```

Arguments are strings in an array: `sleep infinity` is one argument. Do not add
shell quotes inside a value. Every template requires `sbatch_args` (which can be
empty for a script template). The default template name is `default`.

```sh
slurm-auto submit -p gpu -N 2 -g 2
slurm-auto submit -p gpu -N 2 -g 2 --dry-run
slurm-auto submit --config ./config.example.toml --template default -p cpu -c 16 --dry-run
slurm-auto submit --template batch -p cpu -c 16 -- ./job.sh 'one argument' --job-option
```

A submission requires exactly one workload: one template `--wrap` option, or a
batch script and arguments following `--`. Script operands do not belong in
`sbatch_args`. The wrapper inserts an option terminator before the script so its
arguments cannot be interpreted as submission options. Slurm handles script
reading and execution. The wrapper never executes a shell; Slurm itself executes
`--wrap` contents according to its normal behavior.

For the GPU capacity example above, the default template resolves to:

```sh
sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32 --mem=131072M -A hoge -t 24:00:00 --wrap 'sleep infinity'
```

Dry-run queries `scontrol`, loads and validates the selected template, calculates
resources, and displays both the allocation and a POSIX shell-escaped full command.
It never invokes `sbatch`. Real submission forwards sbatch stdout/stderr, including
its normal `Submitted batch job <ID>` response; `--parsable` numeric responses also
receive a labeled job ID. Failures to start `sbatch` and nonzero exits are errors.

Templates cannot set partition, node/task counts, CPU or GPU allocation, memory,
or incompatible CPU topology/sharing controls. The policy rejects long options
(including `--option=value` and abbreviations), resource short aliases such as
`-p`, `-N`, `-n`, `-G`, `-g`, `-c`, and GPU-related `--gres` (`gpu`, `mps`, `shard`).
Non-GPU GRES remain allowed. Conflict messages identify the offending argument.
Supported non-resource short options include `-A`, `-a`, `-b`, `-C`, `-D`, `-d`,
`-e`, `-i`, `-J`, `-L`, `-M`, `-m`, `-o`, `-q`, `-t`, `-w`, `-x`, `-H`, `-Q`,
`-v`, `-W`, `-k`, and `-r`. Values may be separate or attached, such as `-Ahoge`.
Options that exit without submitting, such as `--help` or `--test-only`, are rejected.

Configuration, template argument policy, allocation calculation, command rendering,
and process execution are separate modules. To exercise dry-run without a cluster,
put a fake `scontrol` executable on `PATH` that returns
`tests/fixtures/cluster.json` for `show node --json`. A fake `sbatch` on `PATH` can
record argv and return controlled stdout, stderr, and exit codes. No production
fixture switch or cluster connection is required for this approach.

## Calculation and discovery assumptions

For effective CPUs `C`, configured GPUs `G`, configured memory `M` in MiB,
and requested GPUs `g` on a GPU node:

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
above configured partition node count, or producing zero tasks or zero memory
are rejected. Omitting the applicable resource count requests the full node.

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
- Dry-run validates template structure, workload selection, owned resource options,
  and calculated resources. It does not ask Slurm to validate site-specific options,
  account permissions, script existence, or scheduler policy.
- Template options use complete long names or supported individual short aliases.
  Short flag clusters are rejected. Additional site-specific long options are
  forwarded to Slurm; use `--option=value` to remove value/flag ambiguity.
- Output is captured and forwarded after `sbatch` exits; `--wait` therefore delays
  output until the job completes. A process killed by a signal returns wrapper
  status 1; ordinary sbatch nonzero exit codes are preserved.

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

No formatting, builds, linting, tests, or runtime validation were run for Phase 2,
as requested. New submission behavior remains unverified.
