import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from slurm_auto.command import render
from slurm_auto.model import U64_MAX

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEFAULTS = {
    "account": "global-account",
    "partition": "gpu",
    "time": "24:00:00",
    "job_name": "global-job",
    "wrap": "echo 'hello'; $(exit 99)",
}
FAKE_SBATCH = """\
import json
import os
import signal
import sys
from pathlib import Path

Path(os.environ["SBATCH_LOG"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if sys.stdin.buffer.read():
    sys.exit("sbatch received unexpected stdin")
sys.stdout.buffer.write(bytes.fromhex(os.environ.get("SBATCH_STDOUT", "")))
sys.stderr.buffer.write(bytes.fromhex(os.environ.get("SBATCH_STDERR", "")))
if os.environ.get("SBATCH_SIGNAL"):
    os.kill(os.getpid(), signal.SIGTERM)
sys.exit(int(os.environ.get("SBATCH_EXIT", "0")))
"""


@unittest.skipUnless(os.name == "posix", "requires POSIX fake Slurm executables")
class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.config_path = self.directory / "slurm-auto" / "config.json"
        self.config_path.parent.mkdir()
        self.sbatch_log = self.directory / "sbatch.log"
        self.env = {
            **os.environ,
            "PATH": directory.name,
            "HOME": directory.name,
            "XDG_CONFIG_HOME": directory.name,
            "PYTHONPATH": str(ROOT / "src"),
            "FIXTURE": str(FIXTURES / "cluster.json"),
            "SBATCH_LOG": str(self.sbatch_log),
        }
        for name in ("SBATCH_STDOUT", "SBATCH_STDERR", "SBATCH_SIGNAL", "SBATCH_EXIT"):
            self.env.pop(name, None)
        self.write_executable("scontrol", """\
#!/bin/sh
[ "$#" -eq 3 ] && [ "$1" = show ] && [ "$2" = node ] && [ "$3" = --json ] || exit 9
/bin/cat "$FIXTURE"
""")
        fake_sbatch = self.directory / "fake_sbatch.py"
        fake_sbatch.write_text(FAKE_SBATCH, encoding="utf-8")
        self.write_executable(
            "sbatch", f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake_sbatch))} "$@"\n'
        )

    def write_executable(self, name, source):
        path = self.directory / name
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def write_config(self, defaults=None, path=None):
        path = self.config_path if path is None else path
        path.write_text(json.dumps({"sbatch": {"defaults": DEFAULTS if defaults is None else defaults}}),
                        encoding="utf-8")

    def run_cli(self, *args, input_bytes=None):
        return subprocess.run(
            [sys.executable, "-m", "slurm_auto", *args], cwd=self.directory, env=self.env,
            input=input_bytes, capture_output=True, timeout=10,
        )

    def success(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8")

    def failure(self, *args, message=None, status=None):
        result = self.run_cli(*args)
        self.assertNotEqual(result.returncode, 0, args)
        if status is not None:
            self.assertEqual(result.returncode, status, args)
        error = result.stderr.decode("utf-8", errors="replace")
        if message is not None:
            self.assertIn(message, error, args)
        self.assertNotIn("Traceback", error)
        return error

    def argv(self):
        return json.loads(self.sbatch_log.read_text(encoding="utf-8"))

    def test_help_version_and_no_subcommand(self):
        for args in (("--help",), ("-h",), ("help",)):
            text = self.success(*args)
            for subcommand in ("inspect", "plan", "submit"):
                self.assertIn(subcommand, text)
        self.assertEqual(self.success("help", "submit"), self.success("submit", "--help"))
        self.assertRegex(self.success("-V"), r"^sbatch-auto \d+\.\d+\.\d+\n$")
        self.failure(status=2)

    def test_inspect_and_plan_without_sbatch_or_config(self):
        (self.directory / "sbatch").unlink()
        text = self.success("inspect")
        for detail in ("PARTITION", "cpu", "gpu", "shared", "262144"):
            self.assertIn(detail, text)
        self.assertIn("sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32",
                      self.success("plan", "-p", "gpu", "-N", "2", "-g", "2"))
        self.assertIn("sbatch --partition=cpu --nodes=1 --ntasks-per-node=16",
                      self.success("plan", "--partition", "cpu", "--nodes", "1", "--cpus-per-node", "16"))
        self.assertIn("--nodes=1 --gpus-per-node=4 --ntasks-per-node=64", self.success("plan", "-p", "gpu"))
        self.assertFalse(self.sbatch_log.exists())

    def test_resource_validation_and_cli_parse_failures(self):
        for args in (
            ("-p", "gpu", "-N", "0"), ("-p", "gpu", "-N", "-1"), ("-p", "gpu", "-N", str(U64_MAX + 1)),
            ("-p", "gpu", "-g", "0"), ("-p", "gpu", "-g", "-1"),
            ("-p", "cpu", "-c", "0"), ("-p", "cpu", "-c", "-1"), ("-p", "cpu", "-c", "1_0"),
            ("-p", "gpu", "-g", "1", "-c", "1"), ("-p", "gpu", "--partition", "cpu"),
            ("-p", "gpu", "-N1", "--nodes=1"), ("--part", "gpu"), ("--cpus-per-task", "2"),
        ):
            with self.subTest(args=args):
                self.failure("plan", *args, status=2)
        for args in (
            (), ("-p", "missing"), ("-p", "gpu", "-N", "3"), ("-p", "gpu", "-g", "5"),
            ("-p", "cpu", "-c", "65"), ("-p", "cpu", "-g", "1"), ("-p", "gpu", "-c", "1"),
        ):
            with self.subTest(args=args):
                self.failure("plan", *args, status=1)

    def test_discovery_reports_process_exit_json_and_utf8_failures(self):
        for source, message in (
            ("echo permission-denied >&2\nexit 7\n", "permission-denied"),
            ("echo not-json\n", "invalid scontrol node JSON"),
            ("printf '\\377'\n", "non-UTF-8 JSON"),
        ):
            self.write_executable("scontrol", "#!/bin/sh\n" + source)
            self.failure("inspect", message=message, status=1)
        (self.directory / "scontrol").unlink()
        self.failure("inspect", message="failed to execute scontrol", status=1)

    def test_dry_run_uses_defaults_and_never_invokes_sbatch(self):
        self.write_config()
        text = self.success("submit", "--dry-run", "-g", "2")
        self.assertIn("Dry run (no job submitted):", text)
        preview = text.splitlines()[-1]
        self.assertEqual(preview, "sbatch --partition=gpu --nodes=1 --gpus-per-node=2 "
                                  "--ntasks-per-node=32 --account=global-account --time=24:00:00 "
                                  "--job-name=global-job '--wrap=echo '\"'\"'hello'\"'\"'; $(exit 99)'")
        self.assertFalse(self.sbatch_log.exists())

    def test_cli_overrides_both_spelling_variants_without_duplicates(self):
        expected = ("sbatch --partition=cpu --nodes=1 --ntasks-per-node=64 "
                    "--account=cli --time=02:00:00 --job-name=cli --wrap=cli")
        for args in (
            ["-Aconfigured", "-t", "01:00:00", "-Jconfigured", "--wrap=configured"],
            ["--account=configured", "--time", "01:00:00", "--job-name", "configured", "--wrap", "configured"],
        ):
            with self.subTest(args=args):
                self.write_config(dict(DEFAULTS, sbatch_args=args))
                for overrides in (
                    ("-p", "cpu", "-A", "cli", "-t", "02:00:00", "-J", "cli"),
                    ("--partition", "cpu", "--account", "cli", "--time", "02:00:00", "--job-name", "cli"),
                ):
                    text = self.success("submit", *overrides, "--wrap", "cli", "--dry-run")
                    self.assertEqual(text.splitlines()[-1], expected)
                preview = self.success("submit", "--dry-run").splitlines()[-1]
                self.assertNotIn("global-", preview)
                self.assertNotIn("24:00:00", preview)
                self.assertIn("01:00:00", preview)
                self.assertIn("configured", preview)

    def test_repeated_values_and_similarly_named_options(self):
        self.write_config(dict(DEFAULTS, sbatch_args=[
            "--account=first", "-Alast", "-t01:00:00", "--output=job.log", "--hold",
            "--acctg-freq=task=30", "--time-min=00:30:00", "--comment=--nodes=99", "--site-option=--mem=1M",
        ]))
        preview = self.success("submit", "--dry-run").splitlines()[-1]
        self.assertNotIn("first", preview)
        self.assertNotIn("global-account", preview)
        self.assertIn("--job-name=global-job", preview)
        self.assertIn("-Alast -t01:00:00 --output=job.log --hold", preview)
        self.assertIn("--acctg-freq=task=30 --time-min=00:30:00", preview)
        self.assertIn("--comment=--nodes=99 --site-option=--mem=1M", preview)
        for option in ("--account", "--time", "--job-name", "--wrap"):
            self.failure("submit", option, "", "--dry-run", message="must be nonempty")
        self.failure("submit", "--wrap", status=2)
        self.failure("submit", "--dry-run", "--dry-run", status=2)

    def test_script_replaces_only_default_wrap_and_preserves_argv(self):
        self.write_config()
        self.success("submit", "--", "./job.sh", "one argument", "--flag", "", "--")
        self.assertEqual(self.argv()[-6:], ["--", "./job.sh", "one argument", "--flag", "", "--"])
        self.assertFalse(any(arg.startswith("--wrap") for arg in self.argv()))
        self.success("submit")
        self.assertIn("--wrap=echo 'hello'; $(exit 99)", self.argv())
        self.failure("submit", "job.sh", status=2)
        for args in ((), ("--wrap=configured",), ("--wrap", "configured")):
            self.write_config(dict(DEFAULTS, sbatch_args=args))
            for flags in ((), ("--dry-run",)):
                if args:
                    self.failure("submit", *flags, "--", "job.sh", message="exactly one workload")
                self.failure("submit", *flags, "--wrap", "cli", "--", "job.sh", message="exactly one workload")

    def test_dry_run_works_without_sbatch_and_preserves_script_options(self):
        self.write_config()
        (self.directory / "sbatch").unlink()
        text = self.success("submit", "--dry-run", "--", "job.sh", "--template", "old", "--nodes=99")
        self.assertTrue(text.endswith("-- job.sh --template old --nodes=99\n"))
        self.failure("submit", message="failed to execute sbatch", status=1)

    def test_empty_defaults_and_invalid_configuration(self):
        self.write_config({})
        self.failure("submit", "--dry-run", message="provide --partition")
        self.failure("submit", "-p", "cpu", "--dry-run", message="exactly one workload")
        self.success("submit", "-p", "cpu", "--dry-run", "--", "job.sh")
        for fields in (
            {"partition": ""}, {"account": 2}, {"unknown": "x"}, {"wrap": ""},
            {"memory": "invalid"}, {"memory": True}, {"sbatch_args": "--hold"}, {"sbatch_args": [2]},
            {"account": None}, {"time": " \n"}, {"job_name": "a\0b"},
        ):
            self.write_config(fields)
            self.failure("submit", "-p", "cpu", "--wrap", "cli", message="invalid configuration")
        for source in (
            "[]", '{"sbatch":null}', '{"sbatch":{"defaults":[]}}', '{"sbatch":{"extra":{}}}',
            '{"sbatch":{"defaults":{"partition":"gpu","partition":"cpu"}}}',
            '{"sbatch":{"defaults":{"memory":NaN}}}', '{"sbatch":{"defaults":{"job_name":"\\ud800"}}}',
        ):
            self.config_path.write_text(source, encoding="utf-8")
            self.failure("plan", "-p", "cpu", message="invalid configuration")

    def test_obsolete_templates_and_option_are_rejected(self):
        for templates in ({"default": {"sbatch_args": []}}, {}, {"other": {"sbatch_args": ["--wrap=true"]}}):
            self.config_path.write_text(json.dumps({
                "sbatch": {"defaults": DEFAULTS}, "templates": templates,
            }), encoding="utf-8")
            self.failure("submit", "--dry-run", message="unknown field `templates`")
            self.failure("plan", "-p", "cpu", message="unknown field `templates`")
            self.success("inspect")
        self.failure("submit", "--template", "default", "--dry-run", message="--template", status=2)

    def test_invalid_sbatch_arguments_cannot_be_hidden_by_cli_overrides(self):
        overrides = ("--account", "cli", "--time", "02:00:00", "--job-name", "cli", "--wrap", "cli")
        for option in ("-p", "--partition=cpu", "--part=cpu", "--mem=1M", "--gres=gpu:1", "--gres=mps:100",
                       "--gres=shard:1", "--wrap=", "--account=", "--time=", "--job-name=", "--help", "job.sh"):
            with self.subTest(option=option):
                self.write_config(dict(DEFAULTS, sbatch_args=[option]))
                self.failure("submit", *overrides, "--dry-run", message="invalid sbatch argument")
        self.write_config(dict(DEFAULTS, sbatch_args=["--wrap=a", "--wrap=b"]))
        self.failure("submit", *overrides, message="only once")
        for option in ("acc", "acco", "ti", "tim", "job", "job-n", "wra", "gre"):
            for suffix in (["=configured"], [""], ["="], ["", "configured"]):
                self.write_config(dict(DEFAULTS, sbatch_args=["--" + option + suffix[0], *suffix[1:]]))
                for flags in ((), ("--dry-run",)):
                    with self.subTest(option=option, suffix=suffix, flags=flags):
                        self.failure("submit", *overrides, *flags, message="use the complete")
        self.assertFalse(self.sbatch_log.exists())

    def test_inspection_and_planning_skip_submission_argument_policy(self):
        self.write_config(dict(DEFAULTS, sbatch_args=["--nodes=99", "--wrap=a", "--wrap=b"]))
        self.success("inspect")
        text = self.success("plan")
        self.assertIn("sbatch --partition=gpu --nodes=1", text)
        for option in ("--nodes=99", "--wrap", "--account", "--time", "--job-name"):
            self.assertNotIn(option, text)

    def test_config_lookup(self):
        self.write_config()
        self.assertIn("--partition=gpu", self.success("plan"))
        custom = self.directory / "custom.json"
        self.write_config({"partition": "cpu"}, custom)
        self.assertIn("--partition=cpu", self.success("plan", "--config", "custom.json"))
        self.assertIn("--partition=cpu", self.success("submit", "--config", "custom.json",
                                                       "--wrap", "true", "--dry-run"))
        home_config = self.directory / ".config" / "slurm-auto" / "config.json"
        home_config.parent.mkdir(parents=True)
        self.write_config(dict(DEFAULTS, partition="cpu"), home_config)
        self.assertIn("--partition=gpu", self.success("plan"))
        self.env["XDG_CONFIG_HOME"] = "relative"
        self.assertIn("--partition=cpu", self.success("submit", "--dry-run"))
        home_config.unlink()
        self.success("plan", "-p", "cpu")
        self.failure("plan", "-p", "cpu", "--config", "does-not-exist", message="cannot read")
        self.failure("plan", message="cannot read")
        self.failure("submit", "-p", "cpu", "--wrap", "true", "--dry-run", message="cannot read")
        del self.env["HOME"]
        self.failure("plan", "-p", "cpu", message="HOME is unset")
        self.success("inspect")
        self.success("plan", "--config", str(custom))

    def test_memory_precedence_for_plan_dry_run_and_submission(self):
        for configured in (None, "omit", "proportional"):
            fields = DEFAULTS if configured is None else dict(DEFAULTS, memory=configured)
            self.write_config(fields)
            for cli_mode in (None, "omit", "proportional"):
                proportional = (cli_mode or configured) == "proportional"
                for partition, flag, count, memory in (
                    ("cpu", "-c", "16", "--mem=65536M"), ("gpu", "-g", "2", "--mem=131072M"),
                ):
                    for action, flags in (("plan", []), ("submit", ["--dry-run"]), ("submit", [])):
                        args = [action, "-p", partition, flag, count, *flags]
                        if cli_mode is not None:
                            args += ["--memory", cli_mode]
                        with self.subTest(configured=configured, args=args):
                            text = self.success(*args)
                            self.assertEqual("unspecified (Slurm default)" in text, not proportional)
                            if action == "submit" and not flags:
                                argv = self.argv()
                                self.assertNotIn("--cpus-per-task", argv)
                                text = render(argv)
                            self.assertEqual(memory in text, proportional)
                            self.assertEqual("--mem=" in text, proportional)
            for args in ((), ("-p", "gpu", "--config", str(self.config_path))):
                preview = self.success("plan", "-g", "2", *args)
                self.assertEqual("--mem=131072M" in preview, configured == "proportional")
        for action in ("plan", "submit"):
            self.failure(action, "--memory", "invalid", status=2)

    def test_dry_run_and_submission_use_identical_literal_arguments(self):
        self.write_config(dict(DEFAULTS, sbatch_args=[
            "--output=job log; $(exit 99)", "--qos=normal", "--gres=bandwidth:1", "--site-option=literal value",
        ]))
        text = self.success("submit", "--dry-run", "-A", "cli")
        self.assertFalse(self.sbatch_log.exists())
        self.success("submit", "-A", "cli")
        self.assertIn("--output=job log; $(exit 99)", self.argv())
        self.assertEqual(shlex.split(text.splitlines()[-1]), ["sbatch", *self.argv()])

    def test_forwards_binary_streams_and_preserves_exit_status_and_job_id(self):
        self.write_config(dict(DEFAULTS, sbatch_args=["--parsable"]))
        self.env["SBATCH_STDOUT"] = b"12345;cluster\n".hex()
        self.env["SBATCH_STDERR"] = b"warning\xff\n".hex()
        result = self.run_cli("submit", input_bytes=b"wrapper stdin must stay private")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith(b"12345;cluster\nSubmitted job ID: 12345\n"))
        self.assertEqual(result.stderr, b"warning\xff\n")

        self.env["SBATCH_STDOUT"] = b"Submitted batch job 56789\n".hex()
        result = self.run_cli("submit")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.endswith(b"Submitted batch job 56789\n"))

        self.env["SBATCH_STDOUT"] = b"12345;cluster\nprevious output\xfe\n".hex()
        self.env["SBATCH_EXIT"] = "37"
        result = self.run_cli("submit")
        self.assertEqual(result.returncode, 37)
        self.assertTrue(result.stdout.endswith(b"previous output\xfe\n"))
        self.assertNotIn(b"Submitted job ID", result.stdout)
        self.assertTrue(result.stderr.startswith(b"warning\xff\n"))
        self.assertIn(b"sbatch failed (exit status: 37)", result.stderr)

        self.env["SBATCH_SIGNAL"] = "1"
        self.failure("submit", message="sbatch failed (signal:", status=1)
