#![cfg(unix)]

use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicU64, Ordering},
};

struct Harness(PathBuf);
impl Harness {
    fn new(config: &str) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "slurm-defaults-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(dir.join("slurm-auto")).unwrap();
        fs::write(dir.join("slurm-auto/config.toml"), config).unwrap();
        fs::write(
            dir.join("scontrol"),
            format!(
                "#!/bin/sh\n/bin/cat '{}/tests/fixtures/cluster.json'\n",
                env!("CARGO_MANIFEST_DIR")
            ),
        )
        .unwrap();
        fs::set_permissions(dir.join("scontrol"), fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(dir.join("sbatch"), "#!/bin/sh\nprintf '%s\\n' \"$@\"\n").unwrap();
        fs::set_permissions(dir.join("sbatch"), fs::Permissions::from_mode(0o755)).unwrap();
        Self(dir)
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_sbatch-auto"))
            .args(args)
            .env("PATH", &self.0)
            .env("HOME", &self.0)
            .env("XDG_CONFIG_HOME", &self.0)
            .output()
            .unwrap()
    }
    fn success(&self, args: &[&str]) -> String {
        let output = self.run(args);
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8(output.stdout).unwrap()
    }
    fn failure(&self, args: &[&str], message: &str) {
        let output = self.run(args);
        assert!(!output.status.success());
        assert!(
            String::from_utf8_lossy(&output.stderr).contains(message),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}
impl Drop for Harness {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
const DEFAULTS: &str = r#"
[sbatch.defaults]
account = "global-account"
partition = "gpu"
time = "24:00:00"
job_name = "global-job"
wrap = "echo 'hello'; $(exit 99)"
"#;

#[test]
fn loads_defaults_and_renders_shell_escaped_dry_run() {
    let h = Harness::new(DEFAULTS);
    let text = h.success(&["submit", "--dry-run", "-g", "2"]);
    assert!(text.contains(
        "--partition=gpu --nodes=1 --gpus-per-node=2 --ntasks-per-node=32"
    ));
    assert!(text.contains("--account=global-account --time=24:00:00 --job-name=global-job '--wrap=echo '\"'\"'hello'\"'\"'; $(exit 99)'"), "{text}");
}

#[test]
fn cli_overrides_all_defaults_and_argument_aliases_without_duplicates() {
    for arguments in [
        "sbatch_args = ['-Aconfigured', '-t', '01:00:00', '-Jconfigured', '--wrap=configured']",
        "sbatch_args = ['--account=configured', '--time', '01:00:00', '--job-name', 'configured', '--wrap', 'configured']",
    ] {
        let h = Harness::new(&format!("{DEFAULTS}\n{arguments}"));
        let text = h.success(&["submit", "-p", "cpu", "-A", "cli", "-t", "02:00:00", "-J", "cli", "--wrap", "cli", "--dry-run"]);
        let command = text.lines().last().unwrap();
        assert_eq!(command, "sbatch --partition=cpu --nodes=1 --ntasks-per-node=64 --account=cli --time=02:00:00 --job-name=cli --wrap=cli");
        let text = h.success(&["submit", "--partition", "cpu", "--account", "long", "--time", "03:00:00", "--job-name", "long", "--wrap", "long", "--dry-run"]);
        assert!(text.contains("--account=long --time=03:00:00 --job-name=long --wrap=long"));
        let text = h.success(&["submit", "--dry-run"]);
        let command = text.lines().last().unwrap();
        assert!(!command.contains("global-") && !command.contains("24:00:00"));
        assert!(command.contains("01:00:00") && command.contains("configured"));
    }
}

#[test]
fn script_replaces_only_default_wrap_and_preserves_argv() {
    let h = Harness::new(DEFAULTS);
    let text = h.success(&["submit", "--", "./job.sh", "one argument", "--flag"]);
    assert!(
        text.ends_with("--\n./job.sh\none argument\n--flag\n"),
        "{text}"
    );
    assert!(!text.contains("--wrap"));
    h.failure(
        &["submit", "--wrap", "cli", "--", "job.sh"],
        "exactly one workload",
    );
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--wrap=configured']"
    ));
    h.failure(&["submit", "--", "job.sh"], "exactly one workload");
    // Wrapped shell syntax is forwarded as a single literal argv entry.
    let h = Harness::new(DEFAULTS);
    assert!(h
        .success(&["submit"])
        .ends_with("--wrap=echo 'hello'; $(exit 99)\n"));
}

#[test]
fn obsolete_templates_and_cli_option_are_rejected() {
    for source in [
        "[templates.default]\nsbatch_args = []",
        "[templates]",
        "[templates.other]\nsbatch_args = ['--wrap=true']",
    ] {
        let h = Harness::new(&format!("{DEFAULTS}\n{source}"));
        h.failure(&["submit", "--dry-run"], "unknown field `templates`");
        h.failure(&["plan"], "unknown field `templates`");
    }
    Harness::new(DEFAULTS).failure(
        &["submit", "--template", "default", "--dry-run"],
        "unexpected argument '--template'",
    );
}

#[test]
fn missing_defaults_and_invalid_configuration() {
    let h = Harness::new("[sbatch.defaults]\nsbatch_args = []");
    h.failure(&["submit", "--dry-run"], "provide --partition");
    h.failure(
        &["submit", "-p", "cpu", "--dry-run"],
        "exactly one workload",
    );
    h.success(&["submit", "-p", "cpu", "--dry-run", "--", "job.sh"]);
    for source in [
        "[sbatch.defaults]\npartition = ''",
        "[sbatch.defaults]\naccount = 2",
        "[sbatch.defaults]\nunknown = 'x'",
        "[sbatch.defaults]\nwrap = ''",
        "[sbatch.defaults]\nmemory = 'invalid'",
        "[sbatch.defaults]\nmemory = true",
        "[sbatch.defaults]\nsbatch_args = '--hold'",
        "[sbatch.defaults]\nsbatch_args = [2]",
    ] {
        Harness::new(source).failure(
            &["submit", "-p", "cpu", "--wrap", "cli"],
            "invalid configuration",
        );
    }
}

#[test]
fn arguments_cannot_override_managed_resources_or_hide_invalid_options() {
    for arg in [
        "-p",
        "--partition=cpu",
        "--part=cpu",
        "--mem=1M",
        "--gres=gpu:1",
        "--gres=mps:100",
        "--gres=shard:1",
        "--wrap=",
        "--account=",
        "--time=",
        "--job-name=",
        "--help",
        "job.sh",
    ] {
        let h = Harness::new(&format!("{DEFAULTS}\nsbatch_args = ['{arg}']"));
        h.failure(
            &[
                "submit", "--account", "cli", "--time", "02:00:00", "--job-name", "cli",
                "--wrap", "cli", "--dry-run",
            ],
            "invalid sbatch argument",
        );
    }
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--wrap=a', '--wrap=b']"
    ));
    h.failure(&["submit", "--wrap", "cli"], "only once");
}

#[test]
fn plan_uses_resource_defaults_and_explicit_partition_needs_no_config() {
    let h = Harness::new(DEFAULTS);
    let text = h.success(&["plan"]);
    assert!(text.contains("sbatch --partition=gpu"));
    assert!(!text.contains("--wrap") && !text.contains("--account"));
    let path = h.0.join("custom.toml");
    fs::write(&path, "[sbatch.defaults]\npartition = 'cpu'").unwrap();
    assert!(h
        .success(&["plan", "--config", path.to_str().unwrap()])
        .contains("--partition=cpu"));
    assert!(h
        .success(&[
            "submit",
            "--config",
            path.to_str().unwrap(),
            "--wrap",
            "true",
            "--dry-run"
        ])
        .contains("--partition=cpu"));
    fs::remove_file(h.0.join("slurm-auto/config.toml")).unwrap();
    h.success(&["plan", "-p", "cpu"]);
    h.failure(&["plan", "-p", "cpu", "--config", "does-not-exist"], "cannot read");
    h.failure(&["plan"], "cannot read");
}

#[test]
fn repeated_argument_values_merge_with_defaults_and_keep_other_options() {
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--account=first', '-Alast', '-t01:00:00', '--output=job.log', '--hold']"
    ));
    let text = h.success(&["submit", "--dry-run"]);
    let command = text.lines().last().unwrap();
    assert!(!command.contains("first") && !command.contains("global-account"));
    assert!(command.contains("-Alast -t01:00:00 --output=job.log --hold"));
    assert!(command.contains("--job-name=global-job"));
    for args in [
        vec!["submit", "--account", "", "--dry-run"],
        vec!["submit", "--time", "", "--dry-run"],
        vec!["submit", "--job-name", "", "--dry-run"],
        vec!["submit", "--wrap", "", "--dry-run"],
        vec!["submit", "--wrap"],
    ] {
        assert!(!h.run(&args).status.success(), "{args:?}");
    }
}

#[test]
fn config_lookup_falls_back_to_home() {
    let h = Harness::new(DEFAULTS);
    fs::create_dir_all(h.0.join(".config/slurm-auto")).unwrap();
    fs::rename(
        h.0.join("slurm-auto/config.toml"),
        h.0.join(".config/slurm-auto/config.toml"),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_sbatch-auto"))
        .args(["submit", "--dry-run"])
        .env("PATH", &h.0)
        .env("HOME", &h.0)
        .env("XDG_CONFIG_HOME", "relative")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn dry_run_and_submission_use_identical_literal_arguments() {
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--output=job log; $(exit 99)', '--qos=normal', '--gres=bandwidth:1', '--site-option=literal value']"
    ));
    let dry_run = h.success(&["submit", "--dry-run", "-A", "cli"]);
    let submitted = h.success(&["submit", "-A", "cli"]);
    let arguments: Vec<String> = submitted
        .lines()
        .skip_while(|line| !line.starts_with("--partition="))
        .map(str::to_owned)
        .collect();
    assert!(arguments.contains(&"--output=job log; $(exit 99)".to_owned()));
    assert_eq!(
        dry_run.lines().last().unwrap(),
        slurm_auto::command::render(&arguments)
    );
}

#[test]
fn abbreviated_options_cannot_bypass_precedence_or_validation() {
    for option in ["acc", "acco", "ti", "tim", "job", "job-n", "wra", "gre"] {
        for suffix in ["=configured", "", "=", "', 'configured"] {
            let h = Harness::new(&format!(
                "{DEFAULTS}\nsbatch_args = ['--{option}{suffix}']"
            ));
            for dry_run in [false, true] {
                let mut args = vec![
                    "submit",
                    "--account",
                    "cli",
                    "--time",
                    "02:00:00",
                    "--job-name",
                    "cli",
                    "--wrap",
                    "cli",
                ];
                if dry_run {
                    args.push("--dry-run");
                }
                h.failure(&args, "use the complete");
            }
        }
    }
}

#[test]
fn complete_option_names_keep_similarly_named_options_and_literal_values() {
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--account=configured', '--time=01:00:00', '--job-name=configured', '--acctg-freq=task=30', '--time-min=00:30:00', '--comment=--nodes=99', '--site-option=--mem=1M']"
    ));
    let text = h.success(&["submit", "--dry-run"]);
    let command = text.lines().last().unwrap();
    assert_eq!(command.matches("--account=").count(), 1);
    assert_eq!(command.matches("--time=").count(), 1);
    assert_eq!(command.matches("--job-name=").count(), 1);
    assert!(command.contains("--acctg-freq=task=30 --time-min=00:30:00"));
    assert!(command.contains("--comment=--nodes=99 --site-option=--mem=1M"));
}

#[test]
fn script_and_wrap_conflicts_are_identical_for_dry_run_and_submission() {
    for options in ["['--wrap=first']", "['--wrap', 'first']"] {
        let h = Harness::new(&format!("{DEFAULTS}\nsbatch_args = {options}"));
        for args in [
            vec!["submit", "--", "job.sh"],
            vec!["submit", "--dry-run", "--", "job.sh"],
            vec!["submit", "--wrap", "cli", "--", "job.sh"],
            vec!["submit", "--dry-run", "--wrap", "cli", "--", "job.sh"],
        ] {
            h.failure(&args, "exactly one workload");
        }
    }
}

#[test]
fn dry_run_never_requires_sbatch_and_script_arguments_remain_operands() {
    let h = Harness::new(DEFAULTS);
    fs::remove_file(h.0.join("sbatch")).unwrap();
    let text = h.success(&[
        "submit", "--dry-run", "--", "job.sh", "--template", "old", "--nodes=99",
    ]);
    assert!(text.ends_with("-- job.sh --template old --nodes=99\n"));
    h.failure(&["submit"], "failed to execute sbatch");
}

#[test]
fn inspection_and_planning_do_not_apply_submission_argument_policy() {
    let h = Harness::new(&format!(
        "{DEFAULTS}\nsbatch_args = ['--nodes=99', '--wrap=first', '--wrap=second']"
    ));
    h.success(&["inspect"]);
    let text = h.success(&["plan"]);
    assert!(text.contains("sbatch --partition=gpu --nodes=1"));
    assert!(!text.contains("--wrap") && !text.contains("--nodes=99"));
    let h = Harness::new("[templates.default]\nsbatch_args = []");
    h.success(&["inspect"]);
    h.failure(&["plan", "-p", "cpu"], "unknown field `templates`");
}

#[test]
fn memory_policy_defaults_config_and_cli_apply_to_plans_and_submissions() {
    for configured in [None, Some("omit"), Some("proportional")] {
        let source = match configured {
            Some(mode) => format!("{DEFAULTS}\nmemory = '{mode}'"),
            None => DEFAULTS.to_owned(),
        };
        let h = Harness::new(&source);
        for cli_mode in [None, Some("omit"), Some("proportional")] {
            let proportional = cli_mode.or(configured) == Some("proportional");
            for (partition, count_flag, count, memory) in [
                ("cpu", "-c", "16", "--mem=65536M"),
                ("gpu", "-g", "2", "--mem=131072M"),
            ] {
                for action in ["plan", "dry-run", "submit"] {
                    let mut args = vec![
                        if action == "dry-run" { "submit" } else { action },
                        "-p",
                        partition,
                        count_flag,
                        count,
                    ];
                    if action == "dry-run" {
                        args.push("--dry-run");
                    }
                    if let Some(mode) = cli_mode {
                        args.extend(["--memory", mode]);
                    }
                    let text = h.success(&args);
                    assert_eq!(text.contains(memory), proportional, "{args:?}: {text}");
                    assert_eq!(text.contains("--mem="), proportional, "{args:?}: {text}");
                    assert_eq!(text.contains("unspecified (Slurm default)"), !proportional);
                }
            }
        }
        // Configuration also applies when it supplies the partition, or an explicit path is used.
        let path = h.0.join("slurm-auto/config.toml");
        for args in [
            vec!["plan", "-g", "2"],
            vec![
                "plan", "-p", "gpu", "-g", "2", "--config", path.to_str().unwrap(),
            ],
        ] {
            let text = h.success(&args);
            assert_eq!(
                text.contains("--mem=131072M"),
                configured == Some("proportional")
            );
        }
        for action in ["plan", "submit"] {
            h.failure(&[action, "--memory", "invalid"], "invalid value");
        }
    }
}
