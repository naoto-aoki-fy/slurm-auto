# slurm-auto-sbatch

Inspect Slurm resources and submit jobs with automatic CPU/GPU selection using
Python 3.9 or later. Submission also requires `sbatch` on `PATH`, a configured
Slurm account, and permission to submit to the selected partition.

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

```json
{
  "partition": "gpu",
  "sbatch_options": ["-A", "myaccount", "-t", "01:00:00", "--wrap", "sleep infinity"]
}
```

The partition is optional in the file if supplied on the command line. Unknown
configuration fields are rejected. Each array element is one argument; keep the
entire wrapped command in a single string. Account and time options above are
examples that must match your cluster's policies.

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
Command construction lives in the pure `jobs.build_sbatch_command` function,
which checks the template even when called outside the CLI.

## Task semantics and limitations

Slurm CPU counts may include logical hardware threads; they do not necessarily
describe physical cores. `--cpus-per-node` is this tool's name for selecting the
CPU-only task count; it generates `--ntasks-per-node`, not `--cpus-per-task`.
`--ntasks-per-node` specifies tasks; it does not automatically launch that many
processes from an sbatch script or wrapped command. For example, `sleep infinity`
is one wrapped command. Use an appropriate job step such as `srun` in the wrapped
payload when you want Slurm to launch tasks across the allocation.

Heterogeneous partitions are unsupported in this version. GPU types are counted
together, so equal counts do not establish that GPU models or memory sizes match.
GPU requests use `--gpus-per-node` and depend on the Slurm cluster's supported GPU
scheduling configuration (typically `select/cons_tres`); confirm support with your
administrator. No alternate GRES request mode is selected automatically.

Discovery describes configured node capacity. The tool does not predict resource
availability or scheduling time, or account for drained/down nodes, reservations,
account limits, or partition-specific resource limits. Slurm may reject an
otherwise valid calculated request. Slurm environment defaults (`SBATCH_*`) and
site plugins can also affect the final allocation; review those defaults on the
submission host. Unknown long template options are passed through to Slurm, which
validates their availability and operands. Commands have no execution timeout.

## Development and follow-up

The existing dataclasses and pure parsing, aggregation, calculation, and command
construction functions provide extension points without a framework. Future
memory, CPUs-per-task, GPU-type, and partition-policy support should add explicit
fields and calculation rules, then render their options in one place. Supporting
heterogeneous partitions will require a node-selection policy rather than using
an arbitrary member's capacity. Additional sbatch options should preserve the
template conflict checks.

The unit suite includes discovery fixtures and mocked execution, CPU/GPU defaults
and boundaries, proportional rounding, template conflicts, argument ordering,
wrapped payloads, dry-run behavior, and submission errors. No test, build, lint,
or runtime validation was executed for this hardening task, as requested; the
added tests and implementation remain unverified. No Slurm jobs were submitted.
Recommended follow-up: run the unit command above, then check discovery and
dry-run output on the target cluster before an explicitly authorized submission.
