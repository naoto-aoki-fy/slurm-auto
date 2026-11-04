use std::path::PathBuf;

use clap::{Parser, Subcommand};
use slurm_auto::{
    calculation, command, config, discovery,
    model::{Allocation, Request},
    submission,
};

#[derive(Parser)]
#[command(
    version,
    about = "Inspect Slurm capacity, plan allocations, and submit templated jobs"
)]
struct Cli {
    #[command(subcommand)]
    command: Action,
}

#[derive(Subcommand)]
enum Action {
    /// Show homogeneous configured partition capacities, including busy nodes.
    Inspect,
    /// Submit calculated resources with a named sbatch template.
    Submit {
        #[arg(short = 'p', long)]
        partition: Option<String>,
        #[arg(short = 'N', long, default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..))]
        nodes: u64,
        #[arg(short = 'g', long, value_parser = clap::value_parser!(u64).range(1..), conflicts_with = "cpus_per_node")]
        gpus_per_node: Option<u64>,
        /// CPUs per node for CPU partitions (one CPU per task).
        #[arg(short = 'c', long, value_parser = clap::value_parser!(u64).range(1..))]
        cpus_per_node: Option<u64>,
        #[arg(long)]
        config: Option<PathBuf>,
        #[arg(long)]
        template: Option<String>,
        #[arg(short = 'A', long)]
        account: Option<String>,
        #[arg(short = 't', long)]
        time: Option<String>,
        #[arg(short = 'J', long)]
        job_name: Option<String>,
        #[arg(long)]
        wrap: Option<String>,
        /// Calculate and display the command without executing sbatch.
        #[arg(long)]
        dry_run: bool,
        /// Batch script and its arguments, following --; conflicts with CLI/template --wrap.
        #[arg(last = true)]
        script: Vec<String>,
    },
    /// Calculate a resource allocation and print an sbatch preview; never submit.
    Plan {
        #[arg(short = 'p', long)]
        partition: Option<String>,
        #[arg(short = 'N', long, default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..))]
        nodes: u64,
        #[arg(short = 'g', long, value_parser = clap::value_parser!(u64).range(1..), conflicts_with = "cpus_per_node")]
        gpus_per_node: Option<u64>,
        /// Configuration path, used for the default partition when -p is omitted.
        #[arg(long)]
        config: Option<PathBuf>,
        /// CPUs per node for CPU partitions (wrapper option; NOT Slurm --cpus-per-task).
        #[arg(short = 'c', long, value_parser = clap::value_parser!(u64).range(1..))]
        cpus_per_node: Option<u64>,
    },
}

#[derive(Debug)]
enum Error {
    Planning(String),
    Submission(submission::Error),
}

impl From<String> for Error {
    fn from(message: String) -> Self {
        Self::Planning(message)
    }
}

impl From<submission::Error> for Error {
    fn from(error: submission::Error) -> Self {
        Self::Submission(error)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Planning(message) => f.write_str(message),
            Self::Submission(error) => write!(f, "{error}"),
        }
    }
}

fn print_allocation(allocation: &Allocation) {
    println!(
        "Partition: {}\nNodes: {}\nTasks per node (one CPU per task): {}\nMemory per node: {} MiB",
        allocation.partition,
        allocation.nodes,
        allocation.tasks_per_node,
        allocation.memory_mib_per_node
    );
    if let Some(gpus) = allocation.gpus_per_node {
        println!("GPUs per node: {gpus}");
    }
}

fn select_partition(
    partition: Option<String>,
    defaults: &config::Defaults,
) -> std::result::Result<String, Error> {
    let partition = partition
        .or_else(|| defaults.partition.clone())
        .ok_or_else(|| {
            submission::Error::Arguments(
                "provide --partition (-p) or configure sbatch.defaults.partition".into(),
            )
        })?;
    if partition.trim().is_empty() || partition.contains('\0') {
        return Err(submission::Error::Arguments(
            "partition must be nonempty and contain no NUL bytes".into(),
        )
        .into());
    }
    Ok(partition)
}

fn run(cli: Cli) -> std::result::Result<(), Error> {
    match cli.command {
        Action::Inspect => {
            let partitions = discovery::discover()?;
            println!(
                "{:<20} {:<4} {:>8} {:>12} {:>12} {:>18}",
                "PARTITION", "TYPE", "NODES", "CPUs/NODE", "GPUs/NODE", "MEMORY/NODE (MiB)"
            );
            for partition in partitions.values() {
                println!(
                    "{:<20} {:<4} {:>8} {:>12} {:>12} {:>18}",
                    partition.name,
                    partition.capacity.kind(),
                    partition.nodes.len(),
                    partition.capacity.cpus,
                    partition.capacity.gpus,
                    partition.capacity.memory_mib
                );
            }
            if partitions.is_empty() {
                println!("No configured partitions found in node membership.");
            }
        }
        Action::Submit {
            partition,
            nodes,
            gpus_per_node,
            cpus_per_node,
            config: path,
            template,
            account,
            time,
            job_name,
            wrap,
            dry_run,
            script,
        } => {
            let path = match path {
                Some(path) => path,
                None => config::default_path()?,
            };
            let config = config::load_config(&path)?;
            let partition = select_partition(partition, &config.sbatch.defaults)?;
            let template = config.template(template.as_deref())?;
            let overrides = config::Defaults {
                account,
                time,
                job_name,
                wrap,
                ..Default::default()
            };
            overrides.validate()?;
            let template = submission::resolve(
                &config.sbatch.defaults,
                &template,
                &overrides.arguments(false),
                !script.is_empty(),
            )?;
            let partitions = discovery::discover()?;
            let allocation = calculation::calculate(
                &Request {
                    partition,
                    nodes,
                    gpus_per_node,
                    cpus_per_node,
                },
                &partitions,
            )?;
            let args = submission::merge(&allocation, &template, &script)?;
            print_allocation(&allocation);
            if dry_run {
                println!("\nDry run (no job submitted):\n{}", command::render(&args));
            } else {
                submission::execute(&args)?;
            }
        }
        Action::Plan {
            partition,
            nodes,
            gpus_per_node,
            cpus_per_node,
            config: path,
        } => {
            let partition = if let Some(partition) = partition {
                select_partition(Some(partition), &config::Defaults::default())?
            } else {
                let path = match path {
                    Some(path) => path,
                    None => config::default_path()?,
                };
                select_partition(None, &config::load_config(&path)?.sbatch.defaults)?
            };
            let partitions = discovery::discover()?;
            let allocation = calculation::calculate(
                &Request {
                    partition,
                    nodes,
                    gpus_per_node,
                    cpus_per_node,
                },
                &partitions,
            )?;
            print_allocation(&allocation);
            println!(
                "\nPreview (resource arguments only; no job submitted):\n{}",
                command::preview(&allocation)
            );
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run(Cli::parse()) {
        eprintln!("error: {error}");
        let code = match error {
            Error::Submission(error) => error.exit_code(),
            Error::Planning(_) => 1,
        };
        std::process::exit(code);
    }
}
