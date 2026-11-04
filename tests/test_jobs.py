"""Resource and submission regressions; all external commands are mocked."""

import contextlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from slurm_auto_sbatch.aggregation import aggregate_partitions
from slurm_auto_sbatch.cli import main
from slurm_auto_sbatch.config import JobConfig, load_config, validate_template
from slurm_auto_sbatch.errors import JobError
from slurm_auto_sbatch.jobs import build_sbatch_command
from slurm_auto_sbatch.parsing import parse_nodes
from slurm_auto_sbatch.resources import calculate_resources
from slurm_auto_sbatch.slurm import submit_job


FIXTURE = Path(__file__).parent / "fixtures" / "nodes.json"


def partition(cpus=64, gpus=8, nodes=3):
    payload = {"nodes": [
        {"name": f"n{i}", "partitions": "p", "cpus": cpus, "gres": f"gpu:{gpus}"}
        for i in range(nodes)
    ]}
    return aggregate_partitions(parse_nodes(json.dumps(payload)))[0]


class ResourceTests(unittest.TestCase):
    def test_gpu_calculations(self):
        for cpus, gpus, requested, expected in [
            (64, 8, None, 64), (64, 8, 8, 64), (64, 8, 2, 16),
            (40, 6, 2, 13), (40, 6, 6, 40), (2, 8, 1, 1),
        ]:
            with self.subTest(cpus=cpus, gpus=gpus, requested=requested):
                job = calculate_resources(partition(cpus, gpus), nodes=2,
                                          gpus_per_node=requested)
                self.assertEqual(job.ntasks_per_node, expected)
                self.assertEqual(job.gpus_per_node, gpus if requested is None else requested)
                self.assertEqual(job.nodes, 2)

    def test_cpu_calculations(self):
        for requested, expected in [(None, 48), (1, 1), (12, 12), (48, 48)]:
            with self.subTest(requested=requested):
                job = calculate_resources(partition(48, 0), nodes=3,
                                          cpus_per_node=requested)
                self.assertEqual(job.ntasks_per_node, expected)
                self.assertIsNone(job.gpus_per_node)
                self.assertEqual(job.nodes, 3)

    def test_invalid_counts(self):
        for field, maximum, gpu_count in [
            ("nodes", 3, 8), ("gpus_per_node", 8, 8), ("cpus_per_node", 64, 0),
        ]:
            for value in (0, -1, maximum + 1, True, 1.5, "2"):
                with self.subTest(field=field, value=value), self.assertRaises(JobError):
                    calculate_resources(partition(gpus=gpu_count), **{field: value})

    def test_incompatible_controls(self):
        with self.assertRaisesRegex(JobError, "CPU-only"):
            calculate_resources(partition(), cpus_per_node=1)
        with self.assertRaisesRegex(JobError, "CPU-only"):
            calculate_resources(partition(gpus=0), gpus_per_node=1)

    def test_heterogeneous_partition_rejected(self):
        mixed = next(p for p in aggregate_partitions(parse_nodes(FIXTURE.read_text()))
                     if p.name == "mixed")
        with self.assertRaisesRegex(JobError, "unsupported"):
            calculate_resources(mixed)


class TemplateTests(unittest.TestCase):
    def test_preserves_arguments_and_wrap_value(self):
        options = ("-A", "hoge", "-t", "24:00:00", "--wrap", "sleep infinity")
        command = build_sbatch_command(calculate_resources(partition(), 2, 2),
                                       JobConfig("p", options))
        self.assertEqual(command, [
            "sbatch", "--partition=p", "--nodes=2", "--gpus-per-node=2",
            "--ntasks-per-node=16", *options,
        ])
        self.assertEqual(command[command.index("--wrap") + 1], "sleep infinity")
        self.assertEqual(shlex.split(shlex.join(command)), command)

    def test_cpu_command(self):
        command = build_sbatch_command(calculate_resources(partition(48, 0), 3, cpus_per_node=12),
                                       JobConfig(sbatch_options=("--wrap=sleep infinity",)))
        self.assertEqual(command, ["sbatch", "--partition=p", "--nodes=3",
                                   "--ntasks-per-node=12", "--wrap=sleep infinity"])

    def test_conflicting_options(self):
        for option in (
            "--nodes=2", "--nodes", "--node", "--ntasks=4", "--ntasks-per-node=4",
            "--ntasks-per-gpu=2", "--gpus=4", "--gpus-per-node=4", "--gres=gpu:4",
            "--cpus-per-task=2", "--partition=p", "--core-spec=2", "--thread-spec=2",
            "--mincpus=2", "-N2", "-n4", "-G4", "-c2", "-pp", "-vN2", "-S2",
        ):
            with self.subTest(option=option), self.assertRaisesRegex(JobError, "conflicting"):
                validate_template((option, "--wrap", "sleep infinity"))

    def test_invalid_payloads_and_tokens(self):
        for options in [(), ("--wrap",), ("--wrap", " "), ("--wrap=",),
                        ("job.sh",), ("--wrap=x", "--wrap=y"), ("-Z", "--wrap=x"),
                        ("-A",), ("--wrap", "bad\0command"), (None,)]:
            with self.subTest(options=options), self.assertRaises(JobError):
                validate_template(options)

    def test_option_values_are_not_treated_as_options(self):
        validate_template(("--job-name=--nodes=2", "-Aaccount", "-v", "--exclusive=user",
                           "--wrap", "echo --nodes=2"))

    def test_load_configuration_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for value in ["{", "[]", '{"extra": 1}', '{"partition": ""}',
                          '{"partition": 1}', '{"sbatch_options": "--wrap=x"}',
                          '{"sbatch_options": [1]}', '{"sbatch_options": ["x\\u0000"]}']:
                path.write_text(value)
                with self.subTest(value=value), self.assertRaises(JobError):
                    load_config(str(path))
            with self.assertRaises(JobError):
                load_config(str(Path(directory) / "missing.json"))


class SubmissionTests(unittest.TestCase):
    @patch("slurm_auto_sbatch.slurm.subprocess.run")
    def test_success_uses_argument_list_without_shell(self, run):
        command = ["sbatch", "--wrap", "sleep infinity"]
        run.return_value = subprocess.CompletedProcess(command, 0, "Submitted batch job 42\n", "")
        self.assertEqual(submit_job(command).stdout, "Submitted batch job 42\n")
        run.assert_called_once_with(command, check=False, capture_output=True,
                                    text=True, encoding="utf-8")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    @patch("slurm_auto_sbatch.slurm.subprocess.run")
    def test_submission_failure_diagnostics(self, run):
        for stderr, stdout, expected in [("denied", "", "denied"),
                                          ("", "unavailable", "unavailable"),
                                          ("", "", "no diagnostic output")]:
            run.return_value = subprocess.CompletedProcess([], 3, stdout, stderr)
            with self.subTest(stderr=stderr), self.assertRaisesRegex(JobError, f"exit 3.*{expected}"):
                submit_job(["sbatch", "--wrap=x"])
        for error in (FileNotFoundError("missing"), PermissionError("denied"),
                      UnicodeError("bad encoding")):
            run.side_effect = error
            with self.subTest(error=error), self.assertRaisesRegex(JobError, "Unable to execute sbatch"):
                submit_job(["sbatch", "--wrap=x"])

    def test_cli_preview_and_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"partition": "cpu", "sbatch_options": [
                "-A", "account", "--wrap", "sleep infinity"]}))
            for preview in (True, False):
                with self.subTest(preview=preview), patch("slurm_auto_sbatch.slurm.subprocess.run") as run:
                    run.side_effect = [
                        subprocess.CompletedProcess([], 0, FIXTURE.read_text(), ""),
                        subprocess.CompletedProcess([], 0, "Submitted batch job 42\n", ""),
                    ]
                    output = io.StringIO()
                    args = ["--config", str(path), "--partition", "gpu", "--nodes", "2",
                            "--gpus-per-node", "2"]
                    if preview:
                        args.append("--dry-run")
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(main(args), 0)
                    self.assertEqual(run.call_count, 1 if preview else 2)
                    if preview:
                        command = shlex.split(output.getvalue().splitlines()[-1])
                    else:
                        command = run.call_args.args[0]
                        self.assertIn("Submitted batch job 42", output.getvalue())
                    self.assertEqual(command, ["sbatch", "--partition=gpu", "--nodes=2",
                                              "--gpus-per-node=2", "--ntasks-per-node=32",
                                              "-A", "account", "--wrap", "sleep infinity"])
                    for call in run.call_args_list:
                        self.assertFalse(call.kwargs.get("shell", False))

    @patch("slurm_auto_sbatch.cli.submit_job", side_effect=JobError("sbatch failed (exit 9): denied"))
    @patch("slurm_auto_sbatch.cli.retrieve_node_json")
    @patch("slurm_auto_sbatch.cli.load_config", return_value=JobConfig("gpu", ("--wrap=x",)))
    def test_cli_submission_failure(self, config, retrieve, submit):
        retrieve.return_value = FIXTURE.read_text()
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(main([]), 1)
        self.assertIn("exit 9", error.getvalue())
        self.assertIn("denied", error.getvalue())
