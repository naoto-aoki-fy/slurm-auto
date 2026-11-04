# slurm-auto-sbatch

Inspect Slurm resources and submit jobs with automatic CPU/GPU selection using
Python 3.9 or later.

From the repository root, no installation or Python runtime dependencies are required:

```sh
python3 -m slurm_auto_sbatch --list-partitions
```

Alternatively, install with `python3 -m pip install .` and run
`slurm-auto-sbatch --list-partitions`. Inspection requires `scontrol` on `PATH`
and access to a Slurm controller supporting `scontrol show node --json`.

The output lists each partition's node names, node count, CPUs and GPUs per node,
node types, and support status. CPU counts use `effective_cpus`, falling back to
`cpus` when absent or null. These are Slurm CPU resources, not necessarily physical
cores. GPU counts sum only `gpu` GRES entries, including typed GPUs and socket
annotations. Zero GPUs means a CPU-only node; `nic`, `shard`, and `mps` do not
contribute to GPU counts.

Nodes may belong to multiple partitions. A partition with different CPU or GPU
counts is marked `UNSUPPORTED`, with differing counts and affected node names.
The differing column shows `varies`; other partitions are still listed. An
inspection that lists unsupported partitions exits successfully (status 0).
Command failures, invalid JSON, and malformed node records print a descriptive
error to stderr and exit with status 1. Invalid arguments use argparse's status 2.

Input requires a top-level `nodes` array. Each node requires `name`, `partitions`,
`gres`, and a positive integer CPU count. Partition membership may be a list or
comma-separated string; null, empty, or `(null)` membership means unassigned.
Unassigned nodes are parsed but do not create partitions. Missing required fields
and duplicate node names fail discovery rather than producing partial totals.

The package separates command execution (`slurm.py`), JSON/GRES parsing
(`parsing.py`), structured resources (`models.py`), partition aggregation
(`aggregation.py`), homogeneity validation (`validation.py`), and presentation
(`cli.py`). Parsing and aggregation can be called directly with fixture data.

Unit tests use the standard library, JSON fixtures, and mocked subprocess calls;
they require no live Slurm cluster:

```sh
python3 -m unittest discover -s tests
```

Tests and runtime validation were not run during this implementation, as requested.

## Automatic resource selection and submission

Copy `config.example.json` and edit the account, time limit, partition, and wrapped
command for your cluster. Configuration uses JSON with an optional `partition`
and a `sbatch_options` array of argument strings. A single nonempty `--wrap`
payload is required for job construction; script files are not supported.

Preview a GPU job (this discovers configured resources through `scontrol`, but
never calls `sbatch`):

```sh
python3 -m slurm_auto_sbatch --config config.example.json --nodes 2 --gpus-per-node 2 --dry-run
```

Remove `--dry-run` to submit. Successful submission prints sbatch's output,
including the job ID when returned; failures report the exit code and diagnostic
output. Commands execute as argument lists without a shell. The shell-readable
preview quotes arguments for display only.

`--partition` overrides the configuration partition. A partition must be selected
for a job. Nodes default to 1 and must be positive, no greater than the selected
partition's configured node count. `--list-partitions` remains an inspection-only
mode and does not require configuration or construct a job.

For GPU partitions, GPUs default to all configured GPUs per node. The tool requests
`--ntasks-per-node=max(1, C * g // G)`, where C is the configured Slurm CPU count,
G is the full GPU count, and g is the requested GPU count per node. For 64 CPUs
and 8 GPUs, requesting 2 GPUs gives 16 tasks per node. For 40 CPUs and 6 GPUs,
requesting 2 GPUs gives 13 tasks. No `--ntasks-per-gpu` is generated.

For CPU-only partitions, `--cpus-per-node` chooses the task count, defaulting to all
configured CPUs per node. For example:

```sh
python3 -m slurm_auto_sbatch --config config.example.json --partition cpu --nodes 3 --cpus-per-node 12 --dry-run
```

GPU counts must be in 1..G and CPU counts in 1..C. `--cpus-per-node` is rejected
for GPU partitions; `--gpus-per-node` is rejected for CPU-only partitions.
Heterogeneous partitions cannot be used for jobs. Counts describe configured
capacity, without estimating free resources; Slurm schedules actual availability.

Templates cannot set partition or resource options such as `--nodes`, `--ntasks`,
`--ntasks-per-node`, `--gpus`, `--gpus-per-node`, `--gres`, or `--cpus-per-task`,
including short forms, attached values, flag clusters, and long abbreviations.
Related CPU/GPU topology and node-selection overrides are also rejected. Use the
partition field and CLI resource controls instead. Values for optional-argument
flags (such as `--exclusive=user`) must use `=`. Unrecognized short options must
use their full long form. Shell expressions are only interpreted by Slurm inside
the explicit `--wrap` payload.

Resource calculations live in `resources.py`, JSON loading and template checks in
`config.py`, command execution in `slurm.py`, and CLI orchestration in `cli.py`.
Tests and validation for this phase were skipped at the user's request. No Slurm
jobs were submitted during development.
