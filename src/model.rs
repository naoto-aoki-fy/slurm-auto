use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Capacity {
    pub cpus: u64,
    pub gpus: u64,
    pub memory_mib: u64,
}

impl Capacity {
    pub fn kind(&self) -> &'static str {
        if self.gpus == 0 { "CPU" } else { "GPU" }
    }
}

#[derive(Clone, Debug)]
pub struct Node {
    pub name: String,
    pub partitions: Vec<String>,
    pub capacity: Capacity,
}

#[derive(Clone, Debug)]
pub struct Partition {
    pub name: String,
    pub nodes: Vec<String>,
    pub capacity: Capacity,
}

pub type Partitions = BTreeMap<String, Partition>;

#[derive(Clone, Debug)]
pub struct Request {
    pub partition: String,
    pub nodes: u64,
    pub gpus_per_node: Option<u64>,
    pub cpus_per_node: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Allocation {
    pub partition: String,
    pub nodes: u64,
    pub gpus_per_node: Option<u64>,
    pub tasks_per_node: u64,
    pub memory_mib_per_node: u64,
}
