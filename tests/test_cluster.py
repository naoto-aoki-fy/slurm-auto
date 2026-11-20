import json
import shlex
import unittest
from dataclasses import replace
from pathlib import Path

from slurm_auto.calculation import calculate
from slurm_auto.command import render, sbatch_args
from slurm_auto.discovery import parse_cluster, parse_gres
from slurm_auto.model import Capacity, MemoryMode, Node, Request, SlurmAutoError, U64_MAX
from slurm_auto.validation import aggregate

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def minimal_node():
    return {"name": "node01", "partitions": ["cpu"], "cpus": 64, "gres": "", "real_memory": 262144}


def parse_nodes(*nodes):
    return parse_cluster(json.dumps({"nodes": nodes}))


class DiscoveryTests(unittest.TestCase):
    def test_fixture_diagnostics(self):
        for filename, details in (
            ("heterogeneous.json", ("mixed", "gpu01", "cpu01", "64", "32", "262144", "131072")),
            ("malformed.json", ("broken01", "GRES")),
        ):
            with self.subTest(filename=filename):
                with self.assertRaises(SlurmAutoError) as caught:
                    parse_cluster((FIXTURES / filename).read_text(encoding="utf-8"))
                for detail in details:
                    self.assertIn(detail, str(caught.exception))

    def test_missing_required_fields(self):
        for field in minimal_node():
            node = minimal_node()
            del node[field]
            with self.subTest(field=field), self.assertRaisesRegex(SlurmAutoError, field):
                parse_nodes(node)

    def test_invalid_response(self):
        for text in (
            "not JSON", "{}", "[]", '{"nodes":null}', '{"nodes":[null]}',
            '{"nodes":[],"errors":[{"error":"access denied"}]}', '{"nodes":[],"errors":null}',
            '{"nodes":[],"nodes":[]}', '{"nodes":[],"meta":NaN}', '{"nodes":[],"meta":"\\ud800"}',
        ):
            with self.subTest(text=text), self.assertRaises(SlurmAutoError):
                parse_cluster(text)

    def test_malformed_node_fields_and_zero_capacity(self):
        for field, value in (
            ("name", None), ("name", " "), ("partitions", [12]), ("partitions", "cpu,"),
            ("partitions", ["cpu\x1c"]), ("partitions", "cpu\0other"), ("partitions", 2),
            ("cpus", -1), ("cpus", "64"), ("cpus", 0), ("cpus", True), ("cpus", U64_MAX + 1),
            ("real_memory", 1.5), ("real_memory", 0), ("gres", ["gpu:4"]),
            ("effective_cpus", "64"), ("effective_cpus", {"set": 0, "number": 0}),
            ("effective_cpus", {"set": True, "infinite": 0, "number": 32}),
            ("real_memory", {"set": True, "infinite": True, "number": 262144}),
            ("real_memory", {"set": True, "number": True}),
        ):
            node = minimal_node()
            node[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SlurmAutoError):
                parse_nodes(node)

    def test_effective_cpu_fallback_alias_and_null_gres(self):
        for fields, expected in (
            ({"effective_cpus": None}, 64),
            ({"effective_cpus": {"set": False, "infinite": False, "number": 0}}, 64),
            ({"effective_cpus": 32}, 32),
            ({"cpus_efctv": {"set": True, "number": 24}}, 24),
            ({"effective_cpus": None, "cpus_efctv": 32}, 64),
            ({"effective_cpus": 12, "cpus": "ignored"}, 12),
        ):
            with self.subTest(fields=fields):
                node = minimal_node()
                node.update(gres=None, **fields)
                self.assertEqual(parse_nodes(node)["cpu"].capacity, Capacity(expected, 0, 262144))

    def test_partition_membership_and_duplicate_nodes(self):
        node = dict(minimal_node(), partitions=["shared", "cpu", "shared", " cpu "])
        partitions = parse_nodes(node)
        self.assertEqual(list(partitions), ["cpu", "shared"])
        self.assertEqual(partitions["cpu"].nodes, ("node01",))
        self.assertEqual(partitions["shared"].capacity.kind, "CPU")
        for membership in (None, [], "", "(null)"):
            self.assertEqual(parse_nodes(dict(node, partitions=membership)), {})
        with self.assertRaisesRegex(SlurmAutoError, "duplicate"):
            parse_nodes(node, dict(node, partitions=[]))

    def test_each_heterogeneous_resource_and_mixed_nodes(self):
        first = Node("a", ("p",), Capacity(64, 4, 262144))
        for capacity in (Capacity(32, 4, 262144), Capacity(64, 2, 262144),
                         Capacity(64, 4, 131072), Capacity(64, 0, 262144)):
            with self.subTest(capacity=capacity):
                with self.assertRaises(SlurmAutoError) as caught:
                    aggregate([first, Node("b", ("p",), capacity)])
                for detail in ("partition 'p'", "a:", "b:", "CPUs=", "GPUs=", "memory="):
                    self.assertIn(detail, str(caught.exception))

    def test_configured_gres(self):
        for text, expected in (
            ("", 0), ("(null)", 0), ("gpu:4", 4), ("gpu:a100:4(S:0-3)", 4),
            ("gpu:a100:2(IDX:0,1),gpu:h100:2(S:1),mps:400", 4),
            ("bandwidth:lustre:no_consume:4G,gpu_mem:80", 0), ("shard:100,gpu:4(S:0-1)", 4),
            ("gpu:1 (S:0) (IDX:1), gpu:0", 1), (f"gpu:{U64_MAX}", U64_MAX),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_gres(text), expected)

    def test_ambiguous_counts_and_malformed_gres(self):
        for text in (
            "gpu", "gpu:a100", "gpu:-1", "gpu:1.5", "gpu:a100:2:3", f"gpu:{U64_MAX + 1}",
            f"gpu:{U64_MAX},gpu:1", "gpu:4(S:0", "gpu:4)ending", "gpu:4(S:0)junk",
            "gpu:4,", "gpu::4", "gpu:4((S:0))", "gpu:４", "shard",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(SlurmAutoError, "GRES"):
                parse_gres(text)


class AllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.partitions = parse_cluster((FIXTURES / "cluster.json").read_text(encoding="utf-8"))

    def test_gpu_fixture_uses_configured_capacity_even_when_busy(self):
        for name in ("gpu", "shared"):
            self.assertEqual(self.partitions[name].nodes, ("gpu01", "gpu02"))
            self.assertEqual(self.partitions[name].capacity.kind, "GPU")
        request = Request("gpu", nodes=2, gpus_per_node=2, memory=MemoryMode.PROPORTIONAL)
        args = sbatch_args(calculate(request, self.partitions))
        self.assertEqual(args, ["--partition=gpu", "--nodes=2", "--gpus-per-node=2",
                                "--ntasks-per-node=32", "--mem=131072M"])
        self.assertEqual(render(args), "sbatch --partition=gpu --nodes=2 --gpus-per-node=2 "
                                       "--ntasks-per-node=32 --mem=131072M")

    def test_cpu_fixture(self):
        request = Request("cpu", cpus_per_node=16, memory=MemoryMode.PROPORTIONAL)
        args = sbatch_args(calculate(request, self.partitions))
        self.assertEqual(args, ["--partition=cpu", "--nodes=1", "--ntasks-per-node=16", "--mem=65536M"])
        self.assertEqual(render(args), "sbatch --partition=cpu --nodes=1 --ntasks-per-node=16 --mem=65536M")

    def test_defaults_request_full_capacity(self):
        for name in ("cpu", "gpu"):
            with self.subTest(name=name):
                request = Request(name, memory=MemoryMode.PROPORTIONAL)
                allocation = calculate(request, self.partitions)
                self.assertEqual(allocation.nodes, 1)
                self.assertEqual(allocation.tasks_per_node, 64)
                self.assertEqual(allocation.gpus_per_node, 4 if name == "gpu" else None)
                self.assertEqual(allocation.memory_mib_per_node, 262144)
                allocation = calculate(replace(request, memory=MemoryMode.OMIT), self.partitions)
                self.assertIsNone(allocation.memory_mib_per_node)

    def test_invalid_requests(self):
        for request in (
            Request("missing"), Request("cpu", nodes=0), Request("cpu", nodes=2),
            Request("gpu", nodes=3), Request("cpu", cpus_per_node=0),
            Request("cpu", cpus_per_node=65), Request("cpu", gpus_per_node=1),
            Request("gpu", gpus_per_node=0), Request("gpu", gpus_per_node=5),
            Request("gpu", cpus_per_node=1),
        ):
            with self.subTest(request=request), self.assertRaises(SlurmAutoError):
                calculate(request, self.partitions)

    def test_flooring_and_wide_integer_arithmetic(self):
        def allocate(capacity, count):
            partitions = aggregate([Node("n", ("p",), capacity)])
            return calculate(Request("p", gpus_per_node=count, memory=MemoryMode.PROPORTIONAL), partitions)

        allocation = allocate(Capacity(65, 4, 11), 2)
        self.assertEqual((allocation.tasks_per_node, allocation.memory_mib_per_node), (32, 5))
        allocation = allocate(Capacity(U64_MAX, U64_MAX, U64_MAX), U64_MAX)
        self.assertEqual((allocation.tasks_per_node, allocation.memory_mib_per_node), (U64_MAX, U64_MAX))

    def test_omit_allows_zero_calculated_memory_but_never_zero_tasks(self):
        for capacity, request in (
            (Capacity(64, 0, 1), Request("p", cpus_per_node=1)),
            (Capacity(64, 4, 1), Request("p", gpus_per_node=1)),
        ):
            partitions = aggregate([Node("n", ("p",), capacity)])
            self.assertIsNone(calculate(request, partitions).memory_mib_per_node)
            with self.assertRaisesRegex(SlurmAutoError, "zero memory"):
                calculate(replace(request, memory=MemoryMode.PROPORTIONAL), partitions)
        partitions = aggregate([Node("n", ("p",), Capacity(1, 4, 262144))])
        with self.assertRaisesRegex(SlurmAutoError, "zero tasks"):
            calculate(Request("p", gpus_per_node=1), partitions)

    def test_shell_rendering_round_trips_literal_arguments(self):
        args = ["--partition=a'b;$(id)", "--nodes=2", "", "one argument", "one\ntwo", "\\path"]
        preview = render(args)
        self.assertIn("""'--partition=a'"'"'b;$(id)'""", preview)
        self.assertIn(" --nodes=2 ", preview)
        self.assertEqual(shlex.split(preview), ["sbatch", *args])
