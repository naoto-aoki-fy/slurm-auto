# slurm-auto-sbatch

Inspect Slurm nodes and partition resources with Python 3.9 or later. This first
phase discovers resources only; it does not submit jobs.

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
