import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from slurm_auto_sbatch.aggregation import aggregate_partitions
from slurm_auto_sbatch.cli import format_partitions, main
from slurm_auto_sbatch.errors import DiscoveryError
from slurm_auto_sbatch.parsing import parse_gpu_count, parse_nodes
from slurm_auto_sbatch.slurm import retrieve_node_json


FIXTURE = Path(__file__).parent / "fixtures" / "nodes.json"


class ParsingTests(unittest.TestCase):
    def test_gres_formats(self):
        cases = [
            ("gpu:4", 4), ("gpu:a100:4", 4), ("gpu:a100:4(S:0-1)", 4),
            ("gpu:a100:2,gpu:h100:2", 4), ("gpu:8,nic:1", 8),
            ("gpu:a100:4(S:0,1),gpu:h100:2(S:2)", 6),
            ("(null)", 0), ("null", 0), (None, 0), ("", 0), ("  ", 0),
            ("nic:1,shard:8,mps:100", 0), ("gpu:0", 0),
        ]
        for gres, expected in cases:
            with self.subTest(gres=gres):
                self.assertEqual(parse_gpu_count(gres), expected)

    def test_malformed_gres(self):
        for gres in [4, [], {}, "gpu", "gpu:a100", "gpu:-1", "gpu:1.5",
                     "gpu:4(S:0", "gpu:4)", "gpu:4,", "gpu:4,junk("]:
            with self.subTest(gres=gres), self.assertRaises(DiscoveryError):
                parse_gpu_count(gres)

    def test_effective_cpus_fallback_and_unassigned_nodes(self):
        nodes = parse_nodes(FIXTURE.read_text())
        self.assertEqual(nodes[0].cpus, 60)
        self.assertEqual(nodes[3].cpus, 64)
        self.assertEqual(nodes[4].partitions, ())
        self.assertEqual(nodes[0].partitions, ("cpu", "mixed"))
        self.assertEqual(nodes[2].partitions, ("gpu", "mixed"))

    def test_explicit_null_effective_cpus_and_zero_gpus(self):
        node = {"name": "n1", "partitions": "p,p", "effective_cpus": None,
                "cpus": 16, "gres": "gpu:0"}
        result = parse_nodes(json.dumps({"nodes": [node]}))[0]
        self.assertEqual(result.cpus, 16)
        self.assertEqual(result.partitions, ("p",))
        self.assertFalse(result.is_gpu)

    def test_invalid_document(self):
        for payload in ["{", "[]", "{}", '{"nodes": null}',
                        '{"nodes": [], "errors": [{"error": "denied"}]}']:
            with self.subTest(payload=payload), self.assertRaises(DiscoveryError):
                parse_nodes(payload)

    def test_malformed_node_fields_have_context(self):
        base = {"name": "n1", "partitions": ["p"], "cpus": 16, "gres": "gpu:4"}
        mutations = [
            {"name": ""}, {"name": 3}, {"cpus": None}, {"cpus": True},
            {"cpus": -1}, {"cpus": "16"}, {"effective_cpus": "16"},
            {"effective_cpus": 0}, {"partitions": ["p", 1]},
            {"partitions": "p,"}, {"partitions": {}}, {"gres": []},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(DiscoveryError, r"nodes\[0\]"):
                    parse_nodes(json.dumps({"nodes": [dict(base, **mutation)]}))
        for field in ("name", "cpus", "partitions", "gres"):
            node = dict(base)
            del node[field]
            with self.subTest(missing=field), self.assertRaises(DiscoveryError):
                parse_nodes(json.dumps({"nodes": [node]}))
        with self.assertRaisesRegex(DiscoveryError, "duplicate node name"):
            parse_nodes(json.dumps({"nodes": [base, base]}))
        with self.assertRaisesRegex(DiscoveryError, "node must be an object"):
            parse_nodes('{"nodes": [null]}')


class AggregationTests(unittest.TestCase):
    def test_homogeneous_and_heterogeneous_partitions_coexist(self):
        partitions = {p.name: p for p in aggregate_partitions(parse_nodes(FIXTURE.read_text()))}
        self.assertEqual(set(partitions), {"cpu", "gpu", "mixed"})
        cpu, gpu, mixed = (partitions[name] for name in ("cpu", "gpu", "mixed"))
        self.assertTrue(cpu.supported)
        self.assertEqual((cpu.node_count, cpu.cpus_per_node, cpu.gpus_per_node), (2, 60, 0))
        self.assertTrue(cpu.has_cpu_only_nodes)
        self.assertFalse(cpu.has_gpu_nodes)
        self.assertTrue(gpu.supported)
        self.assertEqual((gpu.node_count, gpu.cpus_per_node, gpu.gpus_per_node), (2, 64, 4))
        self.assertFalse(gpu.has_cpu_only_nodes)
        self.assertTrue(gpu.has_gpu_nodes)
        self.assertFalse(mixed.supported)
        self.assertIsNone(mixed.cpus_per_node)
        self.assertIsNone(mixed.gpus_per_node)
        self.assertEqual(len(mixed.errors), 2)
        for error in mixed.errors:
            self.assertIn("cpu01", error)
            self.assertIn("gpu01", error)
        self.assertTrue(mixed.has_cpu_only_nodes and mixed.has_gpu_nodes)

    def test_single_resource_difference(self):
        for cpus, gpus, expected in [(16, 8, "GPU"), (32, 4, "CPU")]:
            data = {"nodes": [
                {"name": "n1", "partitions": "p", "cpus": 16, "gres": "gpu:4"},
                {"name": "n2", "partitions": "p", "cpus": cpus, "gres": f"gpu:{gpus}"},
            ]}
            with self.subTest(expected=expected):
                partition = aggregate_partitions(parse_nodes(json.dumps(data)))[0]
                self.assertEqual(len(partition.errors), 1)
                self.assertIn(expected, partition.errors[0])
                self.assertIn("n1", partition.errors[0])
                self.assertIn("n2", partition.errors[0])

    def test_empty_cluster(self):
        self.assertEqual(aggregate_partitions(parse_nodes('{"nodes": []}')), ())
        self.assertIn("No partitions discovered", format_partitions(()))


class CommandTests(unittest.TestCase):
    @patch("slurm_auto_sbatch.slurm.subprocess.run")
    def test_command_arguments(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        self.assertEqual(retrieve_node_json(), "{}")
        run.assert_called_once_with(["scontrol", "show", "node", "--json"],
                                    check=True, capture_output=True, text=True, encoding="utf-8")

    @patch("slurm_auto_sbatch.slurm.subprocess.run")
    def test_command_errors(self, run):
        failures = [
            (FileNotFoundError(), "not found"),
            (PermissionError("denied"), "denied"),
            (subprocess.CalledProcessError(2, "scontrol", stderr="controller unavailable"),
             "exit 2.*controller unavailable"),
            (subprocess.CalledProcessError(1, "scontrol", output="access denied"),
             "exit 1.*access denied"),
            (UnicodeError("invalid encoding"), "invalid encoding"),
        ]
        for failure, message in failures:
            with self.subTest(failure=failure):
                run.side_effect = failure
                with self.assertRaisesRegex(DiscoveryError, message):
                    retrieve_node_json()

    @patch("slurm_auto_sbatch.cli.retrieve_node_json")
    def test_cli_inspects_all_partitions(self, retrieve):
        retrieve.return_value = FIXTURE.read_text()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["--list-partitions"])
        self.assertEqual(status, 0)
        self.assertIn("cpu\t2\t60\t0\tCPU-only\tsupported", output.getvalue())
        self.assertIn("gpu\t2\t64\t4\tGPU\tsupported", output.getvalue())
        self.assertIn("mixed\t2\tvaries\tvaries", output.getvalue())
        self.assertIn("UNSUPPORTED", output.getvalue())
        self.assertIn("Nodes: cpu01, gpu01", output.getvalue())

    @patch("slurm_auto_sbatch.cli.retrieve_node_json", return_value="invalid")
    def test_cli_error(self, retrieve):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(main(["--list-partitions"]), 1)
        self.assertIn("Invalid Slurm node JSON", error.getvalue())

    @patch("slurm_auto_sbatch.cli.retrieve_node_json")
    def test_help_does_not_contact_slurm(self, retrieve):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["--help"])
        self.assertEqual(result.exception.code, 0)
        retrieve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
