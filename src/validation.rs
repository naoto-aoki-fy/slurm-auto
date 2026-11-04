use std::collections::{BTreeMap, BTreeSet};

use crate::model::{Node, Partition, Partitions, Request};
use crate::Result;

pub fn aggregate(nodes: Vec<Node>) -> Result<Partitions> {
    let mut grouped: BTreeMap<String, Vec<Node>> = BTreeMap::new();
    let mut names = BTreeSet::new();
    for node in nodes {
        if !names.insert(node.name.clone()) {
            return Err(format!("duplicate node name {:?}", node.name));
        }
        if node.capacity.cpus == 0 || node.capacity.memory_mib == 0 {
            return Err(format!("node {:?} must have positive effective CPUs and real_memory", node.name));
        }
        for partition in &node.partitions {
            grouped.entry(partition.clone()).or_default().push(node.clone());
        }
    }
    let mut partitions = BTreeMap::new();
    for (name, members) in grouped {
        let capacity = members[0].capacity.clone();
        if members.iter().any(|node| node.capacity != capacity) {
            let details = members.iter().map(|node| format!(
                "{}: CPUs={}, GPUs={}, memory={} MiB",
                node.name, node.capacity.cpus, node.capacity.gpus, node.capacity.memory_mib
            )).collect::<Vec<_>>().join("; ");
            return Err(format!("heterogeneous partition {name:?} is unsupported: {details}"));
        }
        partitions.insert(name.clone(), Partition {
            name,
            nodes: members.into_iter().map(|node| node.name).collect(),
            capacity,
        });
    }
    Ok(partitions)
}

pub fn validate_request(request: &Request, partition: &Partition) -> Result<()> {
    if request.nodes == 0 {
        return Err("requested node count must be positive".into());
    }
    if request.nodes as u128 > partition.nodes.len() as u128 {
        return Err(format!("partition {:?} has {} configured nodes; requested {}", partition.name, partition.nodes.len(), request.nodes));
    }
    if partition.capacity.cpus == 0 || partition.capacity.memory_mib == 0 {
        return Err(format!("partition {:?} has zero CPU or memory capacity", partition.name));
    }
    if partition.capacity.gpus == 0 {
        if request.gpus_per_node.is_some() {
            return Err(format!("--gpus-per-node is invalid for CPU partition {:?}", partition.name));
        }
        validate_count("CPUs per node", request.cpus_per_node, partition.capacity.cpus)?;
    } else {
        if request.cpus_per_node.is_some() {
            return Err(format!("--cpus-per-node is invalid for GPU partition {:?}; CPUs are proportional to GPUs", partition.name));
        }
        validate_count("GPUs per node", request.gpus_per_node, partition.capacity.gpus)?;
    }
    Ok(())
}

fn validate_count(label: &str, requested: Option<u64>, capacity: u64) -> Result<()> {
    if let Some(count) = requested {
        if count == 0 || count > capacity {
            return Err(format!("requested {label} must be between 1 and {capacity}; got {count}"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Capacity;

    fn node(name: &str, cpus: u64, gpus: u64, memory_mib: u64) -> Node {
        Node { name: name.into(), partitions: vec!["p".into()], capacity: Capacity { cpus, gpus, memory_mib } }
    }

    #[test]
    fn classification_and_multi_partition_membership() {
        let mut gpu = node("gpu", 64, 4, 262144);
        gpu.partitions = vec!["gpu".into(), "shared".into()];
        let partitions = aggregate(vec![gpu, Node { partitions: vec!["cpu".into()], ..node("cpu", 64, 0, 262144) }]).unwrap();
        assert_eq!(partitions["gpu"].capacity.kind(), "GPU");
        assert_eq!(partitions["cpu"].capacity.kind(), "CPU");
        assert_eq!(partitions["shared"].nodes, vec!["gpu"]);
    }

    #[test]
    fn rejects_each_heterogeneous_resource_and_mixed_nodes() {
        for other in [node("b", 32, 4, 262144), node("b", 64, 2, 262144), node("b", 64, 4, 131072), node("b", 64, 0, 262144)] {
            let error = aggregate(vec![node("a", 64, 4, 262144), other]).unwrap_err();
            for expected in ["partition \"p\"", "a:", "b:", "CPUs=", "GPUs=", "memory="] {
                assert!(error.contains(expected), "{error}");
            }
        }
    }
}
