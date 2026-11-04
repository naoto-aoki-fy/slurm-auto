use crate::model::{Allocation, Partitions, Request};
use crate::validation::validate_request;
use crate::Result;

pub fn calculate(request: &Request, partitions: &Partitions) -> Result<Allocation> {
    let partition = partitions.get(&request.partition).ok_or_else(|| {
        format!("unknown partition {:?}; available partitions: {}", request.partition, partitions.keys().cloned().collect::<Vec<_>>().join(", "))
    })?;
    validate_request(request, partition)?;
    let capacity = &partition.capacity;
    let (gpus_per_node, tasks_per_node, memory_mib_per_node) = if capacity.gpus > 0 {
        let gpus = request.gpus_per_node.unwrap_or(capacity.gpus);
        (Some(gpus), proportion(capacity.cpus, gpus, capacity.gpus), proportion(capacity.memory_mib, gpus, capacity.gpus))
    } else {
        let cpus = request.cpus_per_node.unwrap_or(capacity.cpus);
        (None, cpus, proportion(capacity.memory_mib, cpus, capacity.cpus))
    };
    if tasks_per_node == 0 || memory_mib_per_node == 0 {
        return Err(format!("allocation for partition {:?} produces zero tasks or zero memory (tasks={tasks_per_node}, memory={memory_mib_per_node} MiB)", partition.name));
    }
    Ok(Allocation { partition: partition.name.clone(), nodes: request.nodes, gpus_per_node, tasks_per_node, memory_mib_per_node })
}

fn proportion(total: u64, requested: u64, configured: u64) -> u64 {
    // The validated requested <= configured bound also guarantees the result fits u64.
    ((u128::from(total) * u128::from(requested)) / u128::from(configured)) as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Capacity, Partition};

    fn setup(gpus: u64) -> (Partitions, Request) {
        let p = Partition { name: "p".into(), nodes: vec!["a".into(), "b".into()], capacity: Capacity { cpus: 64, gpus, memory_mib: 262144 } };
        let request = Request { partition: "p".into(), nodes: 1, gpus_per_node: None, cpus_per_node: None };
        ([("p".into(), p)].into_iter().collect(), request)
    }

    #[test]
    fn explicit_gpu_acceptance_example() {
        let (partitions, mut request) = setup(4);
        request.nodes = 2;
        request.gpus_per_node = Some(2);
        let allocation = calculate(&request, &partitions).unwrap();
        assert_eq!(allocation.tasks_per_node, 32);
        assert_eq!(allocation.memory_mib_per_node, 131072);
        assert_eq!(allocation.gpus_per_node, Some(2));
    }

    #[test]
    fn explicit_cpu_acceptance_example() {
        let (partitions, mut request) = setup(0);
        request.cpus_per_node = Some(16);
        let allocation = calculate(&request, &partitions).unwrap();
        assert_eq!(allocation.tasks_per_node, 16);
        assert_eq!(allocation.memory_mib_per_node, 65536);
        assert_eq!(allocation.gpus_per_node, None);
    }

    #[test]
    fn defaults_request_full_capacity() {
        for gpus in [0, 4] {
            let (partitions, request) = setup(gpus);
            let allocation = calculate(&request, &partitions).unwrap();
            assert_eq!(allocation.tasks_per_node, 64);
            assert_eq!(allocation.memory_mib_per_node, 262144);
        }
    }

    #[test]
    fn invalid_requests_are_rejected() {
        for gpus in [0, 4] {
            let (partitions, base) = setup(gpus);
            let mut cases = vec![];
            for nodes in [0, 3] { cases.push(Request { nodes, ..base.clone() }); }
            for count in [0, 65] { cases.push(Request { cpus_per_node: Some(count), ..base.clone() }); }
            for count in [0, 5] { cases.push(Request { gpus_per_node: Some(count), ..base.clone() }); }
            cases.push(Request { partition: "missing".into(), ..base });
            for request in cases { assert!(calculate(&request, &partitions).is_err(), "{request:?}"); }
        }
    }

    #[test]
    fn floors_without_intermediate_overflow_and_rejects_zero_results() {
        assert_eq!(proportion(65, 2, 4), 32);
        assert_eq!(proportion(u64::MAX, u64::MAX, u64::MAX), u64::MAX);
        for (cpus, memory) in [(1, 262144), (64, 1)] {
            let (mut partitions, mut request) = setup(4);
            let p = partitions.get_mut("p").unwrap();
            p.capacity.cpus = cpus;
            p.capacity.memory_mib = memory;
            request.gpus_per_node = Some(1);
            assert!(calculate(&request, &partitions).unwrap_err().contains("zero"));
        }
    }
}
