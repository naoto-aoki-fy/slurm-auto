import unittest

from slurm_auto.model import Allocation, SlurmAutoError
from slurm_auto.submission import MANAGED, MANAGED_SHORT, merge, parse_arguments


class ArgumentPolicyTests(unittest.TestCase):
    def test_every_managed_option_and_short_alias_is_rejected(self):
        for option in MANAGED:
            for args in ([f"--{option}"], [f"--{option}=1"], [f"--{option}", "1"]):
                with self.subTest(args=args), self.assertRaisesRegex(SlurmAutoError, "managed resources"):
                    parse_arguments(args)
            if len(option) > 1:
                with self.assertRaisesRegex(SlurmAutoError, "managed resources"):
                    parse_arguments([f"--{option[:-1]}=1"])
        for option in sorted(MANAGED_SHORT):
            for args in ([f"-{option}", "1"], [f"-{option}1"]):
                with self.subTest(args=args), self.assertRaisesRegex(SlurmAutoError, "managed resources"):
                    parse_arguments(args)

    def test_gpu_related_gres_conflict_even_in_a_list(self):
        for value in (
            "gpu:1", "mps:100", "shard:1", "bandwidth:1,gpu:a100:2", "gpu",
            "gres/gpu:2", "bandwidth:1,gres/mps:2", "gres/shard:1", "GPU:1", "bandwidth:1, shard:2",
        ):
            for args in (["--gres", value], [f"--gres={value}"]):
                with self.subTest(args=args), self.assertRaisesRegex(SlurmAutoError, "managed resources"):
                    parse_arguments(args)
        props = ["--gres=bandwidth:1,gres/gpu_mem:80", "--gres-flags=disable-binding"]
        self.assertEqual(parse_arguments(props), [("gres", props[:1]), ("gres-flags", props[1:])])

    def test_rejects_options_that_do_not_submit_and_ambiguous_or_malformed_tokens(self):
        for args in (
            ["--help"], ["--usag"], ["--version"], ["--test-only"], ["--complet"],
            ["--"], ["job.sh"], ["-"], ["-h"], ["-Hv"], ["--hold=yes"], ["--hold", "job.sh"],
            ["--output"], ["--output", "--hold"], ["--output="], ["--output", "job\0log"],
            ["--out_put=value"], ["--é=value"], ["--wrap=a", "--wrap", "b"],
        ):
            with self.subTest(args=args), self.assertRaisesRegex(SlurmAutoError, "invalid sbatch argument"):
                parse_arguments(args)

    def test_allowed_short_and_site_option_values(self):
        args = ["-Aacct", "-a", "1-10", "-t01:00:00", "-v", "--site-option=--nodes=2",
                "--site-flag", "--other-option", "literal value"]
        self.assertEqual(parse_arguments(args), [
            ("account", ["-Aacct"]), ("array", ["-a", "1-10"]), ("time", ["-t01:00:00"]),
            ("verbose", ["-v"]), ("site-option", ["--site-option=--nodes=2"]),
            ("site-flag", ["--site-flag"]), ("other-option", ["--other-option=literal value"]),
        ])
        self.assertEqual(parse_arguments(["-o", ":"]), [("output", ["-o:"])])
        allocation = Allocation("cpu", 1, None, 64, None)
        args = merge(allocation, ["--nice", "1"], ["job.sh"])
        self.assertEqual(args[-3:], ["--nice=1", "--", "job.sh"])

    def test_rejects_invalid_script_operands(self):
        allocation = Allocation("cpu", 1, None, 64, None)
        for script in ([""], ["job.sh", "\0"]):
            with self.subTest(script=script), self.assertRaisesRegex(SlurmAutoError, "script path"):
                merge(allocation, [], script)
        args = merge(allocation, [], ["--foo", "", "--nodes=99", "--"])
        self.assertEqual(args[-5:], ["--", "--foo", "", "--nodes=99", "--"])
