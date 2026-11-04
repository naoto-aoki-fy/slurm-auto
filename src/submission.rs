use std::{
    fmt,
    io::{self, Write},
    process::{Command, ExitStatus},
};

use crate::{command, model::Allocation};

#[derive(Debug)]
pub enum Error {
    Config(String),
    Arguments(String),
    Execute(io::Error),
    Failed { status: ExitStatus },
    Output(io::Error),
}

impl Error {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::Failed { status } => status.code().filter(|code| *code > 0).unwrap_or(1),
            _ => 1,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Config(message) | Self::Arguments(message) => f.write_str(message),
            Self::Execute(error) => write!(f, "failed to execute sbatch: {error}"),
            Self::Failed { status } => write!(f, "sbatch failed ({status}); see its output above"),
            Self::Output(error) => write!(f, "failed to forward sbatch output: {error}"),
        }
    }
}

impl std::error::Error for Error {}

// Keep resource ownership in one place, independent of configuration and calculation.
// Reject abbreviations too: getopt-style long option prefixes must not bypass ownership.
const MANAGED: &[&str] = &[
    "partition",
    "nodes",
    "ntasks",
    "ntasks-per-node",
    "ntasks-per-core",
    "ntasks-per-socket",
    "ntasks-per-gpu",
    "gpus",
    "gpus-per-node",
    "gpus-per-task",
    "gpus-per-socket",
    "cpus-per-task",
    "cpus-per-gpu",
    "mem",
    "mem-per-cpu",
    "mem-per-gpu",
    "mem-per-socket",
    "mincpus",
    "sockets-per-node",
    "cores-per-socket",
    "threads-per-core",
    "extra-node-info",
    "hint",
    "overcommit",
    "oversubscribe",
    "exclusive",
    "core-spec",
    "thread-spec",
    "tres-per-task",
    "tres-per-node",
    "tres-per-job",
    "tres-per-socket",
];

fn argument_error(argument: &str, reason: &str) -> Error {
    Error::Arguments(format!("invalid sbatch argument {argument:?}: {reason}"))
}

fn managed(argument: &str) -> Error {
    argument_error(argument, "conflicts with automatically managed resources; remove it and use the submit resource options")
}

fn takes_value(name: &str) -> bool {
    matches!(
        name,
        "account"
            | "acctg-freq"
            | "array"
            | "batch"
            | "bb"
            | "bbf"
            | "begin"
            | "chdir"
            | "cluster-constraint"
            | "clusters"
            | "comment"
            | "constraint"
            | "container"
            | "container-id"
            | "cpu-freq"
            | "deadline"
            | "delay-boot"
            | "dependency"
            | "distribution"
            | "error"
            | "exclude"
            | "export"
            | "export-file"
            | "get-user-env"
            | "gid"
            | "gpu-bind"
            | "gpu-freq"
            | "gres"
            | "gres-flags"
            | "input"
            | "job-name"
            | "licenses"
            | "mail-type"
            | "mail-user"
            | "mcs-label"
            | "network"
            | "nice"
            | "nodelist"
            | "open-mode"
            | "output"
            | "power"
            | "priority"
            | "profile"
            | "propagate"
            | "qos"
            | "reservation"
            | "signal"
            | "switches"
            | "time"
            | "time-min"
            | "uid"
            | "wait-all-nodes"
            | "wckey"
            | "wrap"
    )
}

fn flag(name: &str) -> bool {
    matches!(
        name,
        "contiguous"
            | "hold"
            | "ignore-pbs"
            | "no-kill"
            | "no-requeue"
            | "parsable"
            | "quiet"
            | "reboot"
            | "requeue"
            | "use-min-nodes"
            | "verbose"
            | "wait"
            | "spread-job"
    )
}

/// Configured arguments contain options only. Script operands are supplied
/// separately so an unknown option's value cannot become the workload.
fn parse_arguments(args: &[String]) -> Result<Vec<(String, Vec<String>)>, Error> {
    let mut options = Vec::new();
    let mut index = 0;
    let mut wrap = false;
    while index < args.len() {
        let start = index;
        let argument = &args[index];
        if argument.contains('\0') {
            return Err(argument_error(
                argument,
                "NUL bytes cannot be passed to sbatch",
            ));
        }
        let mut extension_flag = false;
        let (name, attached) = if let Some(long) = argument.strip_prefix("--") {
            let (name, value) = long
                .split_once('=')
                .map_or((long, None), |(n, v)| (n, Some(v)));
            if name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
                return Err(argument_error(
                    argument,
                    "expected a complete sbatch option name",
                ));
            }
            if MANAGED.iter().any(|owned| owned.starts_with(name)) {
                return Err(managed(argument));
            }
            if ["account", "time", "job-name", "gres", "wrap"]
                .iter()
                .any(|full| *full != name && full.starts_with(name))
            {
                return Err(argument_error(
                    argument,
                    "use the complete --account, --time, --job-name, --gres, or --wrap option name",
                ));
            }
            // These alter submission semantics or can bypass the single workload.
            if ["help", "usage", "version", "test-only", "completion"]
                .iter()
                .any(|n| n.starts_with(name))
            {
                return Err(argument_error(argument, "does not submit a single job"));
            }
            // Site-specific options are forwarded. A bare extension option is a
            // flag unless followed by a non-option value; use '=' to disambiguate.
            extension_flag = !takes_value(name)
                && !flag(name)
                && value.is_none()
                && args.get(index + 1).is_none_or(|next| next.starts_with('-'));
            if flag(name) && value.is_some() {
                return Err(argument_error(argument, "this flag does not take a value"));
            }
            (name, value)
        } else if argument.starts_with('-') {
            let short = argument.as_bytes().get(1).copied().unwrap_or_default();
            if b"pNnGgcBOsS".contains(&short) {
                return Err(managed(argument));
            }
            let name = match short {
                b'A' => "account", b'a' => "array", b'b' => "begin",
                b'C' => "constraint", b'D' => "chdir", b'd' => "dependency",
                b'e' => "error", b'i' => "input", b'J' => "job-name",
                b'L' => "licenses", b'M' => "clusters", b'm' => "distribution",
                b'o' => "output", b'q' => "qos", b't' => "time",
                b'w' => "nodelist", b'x' => "exclude",
                b'H' => "hold", b'Q' => "quiet", b'v' => "verbose",
                b'W' => "wait", b'k' => "no-kill", b'r' => "no-requeue",
                _ => return Err(argument_error(argument, "unsupported short option; use its full long spelling (short option clusters are unsupported)")),
            };
            let value = (argument.len() > 2).then(|| &argument[2..]);
            if flag(name) && value.is_some() {
                return Err(argument_error(
                    argument,
                    "short option clusters are unsupported; separate each option",
                ));
            }
            (name, value)
        } else {
            return Err(argument_error(argument, "sbatch_args contains options only; pass a batch script after -- on the submit command"));
        };
        let value = if flag(name) || extension_flag {
            None
        } else if let Some(value) = attached {
            Some(value)
        } else {
            index += 1;
            let value = args
                .get(index)
                .ok_or_else(|| argument_error(argument, "missing option value"))?;
            if value.starts_with('-') {
                return Err(argument_error(
                    argument,
                    "missing value; use --option=value for a value beginning with '-'",
                ));
            }
            Some(value.as_str())
        };
        if let Some(value) = value {
            if value.is_empty() || value.contains('\0') {
                return Err(argument_error(
                    argument,
                    "option value must be nonempty and contain no NUL bytes",
                ));
            }
            if name == "gres"
                && value
                    .split(',')
                    .any(|entry| matches!(entry.split(':').next(), Some("gpu" | "mps" | "shard")))
            {
                return Err(managed(argument));
            }
        }
        if name == "wrap" {
            if wrap {
                return Err(argument_error(argument, "--wrap must appear only once"));
            }
            wrap = true;
        }
        options.push((name.to_owned(), args[start..=index].to_vec()));
        index += 1;
    }
    Ok(options)
}

pub fn validate_arguments(args: &[String]) -> Result<bool, Error> {
    Ok(parse_arguments(args)?
        .iter()
        .any(|(name, _)| name == "wrap"))
}

/// Resolve supported defaults using complete option names and short aliases.
/// Validate each layer before merging so overrides cannot hide invalid options.
pub fn resolve(
    defaults: &crate::config::Defaults,
    overrides: &[String],
    script_supplied: bool,
) -> Result<Vec<String>, Error> {
    defaults.validate()?;
    let mut merged: Vec<(String, Vec<String>)> = Vec::new();
    let dedicated = defaults.arguments(script_supplied);
    for layer in [
        dedicated.as_slice(),
        defaults.sbatch_args.as_slice(),
        overrides,
    ] {
        for (name, args) in parse_arguments(layer)? {
            if matches!(name.as_str(), "account" | "time" | "job-name" | "wrap") {
                merged.retain(|(existing, _)| existing != &name);
            }
            merged.push((name, args));
        }
    }
    Ok(merged.into_iter().flat_map(|(_, args)| args).collect())
}

pub fn merge(
    allocation: &Allocation,
    options: &[String],
    script: &[String],
) -> Result<Vec<String>, Error> {
    let wrap = validate_arguments(options)?;
    if wrap == !script.is_empty() {
        return Err(Error::Arguments("provide exactly one workload: --wrap (CLI, sbatch_args, or default) OR a batch script after --".into()));
    }
    if script.iter().any(|arg| arg.contains('\0'))
        || script.first().is_some_and(|path| path.is_empty())
    {
        return Err(Error::Arguments(
            "batch script path must be nonempty; script arguments cannot contain NUL bytes".into(),
        ));
    }
    let mut args = command::sbatch_args(allocation);
    args.extend_from_slice(options);
    if !script.is_empty() {
        args.push("--".into());
        args.extend_from_slice(script);
    }
    Ok(args)
}

/// Invoke argv directly and forward both streams, including on nonzero exits.
pub fn execute(args: &[String]) -> Result<(), Error> {
    let output = Command::new("sbatch")
        .args(args)
        .output()
        .map_err(Error::Execute)?;
    io::stdout()
        .write_all(&output.stdout)
        .map_err(Error::Output)?;
    io::stderr()
        .write_all(&output.stderr)
        .map_err(Error::Output)?;
    if !output.status.success() {
        return Err(Error::Failed {
            status: output.status,
        });
    }
    // Standard sbatch output already reports the job ID. Support --parsable too.
    let stdout = String::from_utf8_lossy(&output.stdout);
    let id = stdout.trim().split(';').next().unwrap_or("");
    if !id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()) {
        println!("Submitted job ID: {id}");
    }
    Ok(())
}
