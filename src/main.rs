use clap::{Parser, Subcommand};
use slurm_auto::{calculation, command, discovery, model::Request, Result};

#[derive(Parser)]
#[command(version, about = "Inspect Slurm capacity and generate sbatch resource plans without submitting jobs")]
struct Cli {
    #[command(subcommand)]
    command: Action,
}

#[derive(Subcommand)]
enum Action {
    /// Show homogeneous configured partition capacities, including busy nodes.
    Inspect,
    /// Calculate a resource allocation and print an sbatch preview; never submit.
    Plan {
        #[arg(short = 'p', long)]
        partition: String,
        #[arg(short = 'N', long, default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..))]
        nodes: u64,
        #[arg(short = 'g', long, value_parser = clap::value_parser!(u64).range(1..), conflicts_with = "cpus_per_node")]
        gpus_per_node: Option<u64>,
        /// CPUs per node for CPU partitions (wrapper option; NOT Slurm --cpus-per-task).
        #[arg(short = 'c', long, value_parser = clap::value_parser!(u64).range(1..))]
        cpus_per_node: Option<u64>,
    },
}

fn run(cli: Cli) -> Result<()> {
    let partitions = discovery::discover()?;
    match cli.command {
        Action::Inspect => {
            println!("{:<20} {:<4} {:>8} {:>12} {:>12} {:>18}", "PARTITION", "TYPE", "NODES", "CPUs/NODE", "GPUs/NODE", "MEMORY/NODE (MiB)");
            for partition in partitions.values() {
                println!("{:<20} {:<4} {:>8} {:>12} {:>12} {:>18}", partition.name, partition.capacity.kind(), partition.nodes.len(), partition.capacity.cpus, partition.capacity.gpus, partition.capacity.memory_mib);
            }
            if partitions.is_empty() { println!("No configured partitions found in node membership."); }
        }
        Action::Plan { partition, nodes, gpus_per_node, cpus_per_node } => {
            let allocation = calculation::calculate(&Request { partition, nodes, gpus_per_node, cpus_per_node }, &partitions)?;
            println!("Partition: {}\nNodes: {}\nTasks per node (one CPU per task): {}\nMemory per node: {} MiB", allocation.partition, allocation.nodes, allocation.tasks_per_node, allocation.memory_mib_per_node);
            if let Some(gpus) = allocation.gpus_per_node { println!("GPUs per node: {gpus}"); }
            println!("\nPreview (resource arguments only; no job submitted):\n{}", command::preview(&allocation));
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run(Cli::parse()) {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}
