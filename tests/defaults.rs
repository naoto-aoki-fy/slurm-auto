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
        Command::new(env!("CARGO_BIN_EXE_slurm-auto"))
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
        "--partition=gpu --nodes=1 --gpus-per-node=2 --ntasks-per-node=32 --mem=131072M"
    ));
    assert!(text.contains("--account=global-account --time=24:00:00 --job-name=global-job '--wrap=echo '\"'\"'hello'\"'\"'; $(exit 99)'"), "{text}");
}

#[test]
fn cli_overrides_all_defaults_and_template_aliases_without_duplicates() {
    for template in [
        "[templates.default]\nsbatch_args = ['-Atemplate', '-t', '01:00:00', '-Jtemplate', '--wrap=template']",
        "[templates.default]\nsbatch_args = ['--account=template', '--time', '01:00:00', '--job-name', 'template', '--wrap', 'template']",
    ] {
        let h = Harness::new(&format!("{DEFAULTS}\n{template}"));
        let text = h.success(&["submit", "-p", "cpu", "-A", "cli", "-t", "02:00:00", "-J", "cli", "--wrap", "cli", "--dry-run"]);
        let command = text.lines().last().unwrap();
        assert_eq!(command, "sbatch --partition=cpu --nodes=1 --ntasks-per-node=64 --mem=262144M --account=cli --time=02:00:00 --job-name=cli --wrap=cli");
        let text = h.success(&["submit", "--partition", "cpu", "--account", "long", "--time", "03:00:00", "--job-name", "long", "--wrap", "long", "--dry-run"]);
        assert!(text.contains("--account=long --time=03:00:00 --job-name=long --wrap=long"));
        let text = h.success(&["submit", "--dry-run"]);
        let command = text.lines().last().unwrap();
        assert!(!command.contains("global-") && !command.contains("24:00:00"));
        assert!(command.contains("01:00:00") && command.contains("template"));
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
        "{DEFAULTS}\n[templates.default]\nsbatch_args = ['--wrap=template']"
    ));
    h.failure(&["submit", "--", "job.sh"], "exactly one workload");
    // Wrapped shell syntax is forwarded as a single literal argv entry.
    let h = Harness::new(DEFAULTS);
    assert!(h
        .success(&["submit"])
        .ends_with("--wrap=echo 'hello'; $(exit 99)\n"));
}

#[test]
fn legacy_config_and_missing_values() {
    let h = Harness::new(
        "[templates.default]\nsbatch_args = ['-A', 'old', '--wrap', 'sleep infinity']",
    );
    assert!(h
        .success(&["submit", "-p", "cpu", "--dry-run"])
        .contains("-A old --wrap 'sleep infinity'"));
    h.failure(&["submit", "--dry-run"], "provide --partition");
    h.failure(
        &["submit", "-p", "cpu", "--template", "missing"],
        "not found",
    );
    let h = Harness::new("[templates.default]\nsbatch_args = []");
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
    ] {
        Harness::new(source).failure(
            &["submit", "-p", "cpu", "--wrap", "cli"],
            "invalid configuration",
        );
    }
}

#[test]
fn templates_cannot_override_managed_resources_or_hide_invalid_options() {
    for arg in ["-p", "--partition=cpu", "--part=cpu", "--mem=1M", "--wrap="] {
        let h = Harness::new(&format!(
            "{DEFAULTS}\n[templates.default]\nsbatch_args = ['{arg}']"
        ));
        h.failure(
            &["submit", "--wrap", "cli", "--dry-run"],
            "invalid template argument",
        );
    }
    let h = Harness::new(&format!(
        "{DEFAULTS}\n[templates.default]\nsbatch_args = ['--wrap=a', '--wrap=b']"
    ));
    h.failure(&["submit", "--wrap", "cli"], "only once");
}

#[test]
fn plan_uses_only_partition_and_explicit_partition_needs_no_config() {
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
    h.success(&["plan", "-p", "cpu", "--config", "does-not-exist"]);
    h.failure(&["plan"], "cannot read");
}

#[test]
fn repeated_template_values_merge_with_defaults_and_keep_other_options() {
    let h = Harness::new(&format!(
        "{DEFAULTS}\n[templates.default]\nsbatch_args = ['--account=first', '-Alast', '-t01:00:00', '--output=job.log', '--hold']"
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
fn config_lookup_falls_back_to_home_and_rejects_unknown_explicit_template() {
    let h = Harness::new(DEFAULTS);
    fs::create_dir_all(h.0.join(".config/slurm-auto")).unwrap();
    fs::rename(
        h.0.join("slurm-auto/config.toml"),
        h.0.join(".config/slurm-auto/config.toml"),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_slurm-auto"))
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
    let h = Harness::new(DEFAULTS);
    h.failure(
        &["submit", "--template", "default", "--dry-run"],
        "not found",
    );
}
