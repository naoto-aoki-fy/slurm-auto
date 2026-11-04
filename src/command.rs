use crate::model::Allocation;

/// Structured arguments are kept separate from their display-only shell rendering.
pub fn sbatch_args(allocation: &Allocation) -> Vec<String> {
    let mut args = vec![format!("--partition={}", allocation.partition), format!("--nodes={}", allocation.nodes)];
    if let Some(gpus) = allocation.gpus_per_node {
        args.push(format!("--gpus-per-node={gpus}"));
    }
    args.push(format!("--ntasks-per-node={}", allocation.tasks_per_node));
    args.push(format!("--mem={}M", allocation.memory_mib_per_node));
    args
}

pub fn preview(allocation: &Allocation) -> String {
    render(&sbatch_args(allocation))
}

pub fn render(args: &[String]) -> String {
    std::iter::once("sbatch".to_owned()).chain(args.iter().cloned())
        .map(|argument| shell_quote(&argument)).collect::<Vec<_>>().join(" ")
}

fn shell_quote(argument: &str) -> String {
    if !argument.is_empty() && argument.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&byte)) {
        argument.to_owned()
    } else {
        format!("'{}'", argument.replace('\'', "'\"'\"'"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_shell_metacharacters_and_apostrophes() {
        assert_eq!(shell_quote("--partition=a'b;$(id)"), "'--partition=a'\"'\"'b;$(id)'");
        assert_eq!(shell_quote("--nodes=2"), "--nodes=2");
    }
}
