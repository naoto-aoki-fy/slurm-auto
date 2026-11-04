use serde_json::{json, Value};
use slurm_auto::{calculation::calculate, command::{preview, sbatch_args}, discovery::parse_cluster, model::{MemoryMode, Request}};

const CLUSTER: &str = include_str!("fixtures/cluster.json");

#[test]
fn fixture_gpu_acceptance_plan_uses_configured_capacity_even_when_busy() {
    let partitions = parse_cluster(CLUSTER).unwrap();
    assert_eq!(partitions["gpu"].nodes.len(), 2);
    assert_eq!(partitions["shared"].nodes.len(), 2);
    let allocation = calculate(&Request { partition: "gpu".into(), nodes: 2, gpus_per_node: Some(2), cpus_per_node: None, memory: MemoryMode::Proportional }, &partitions).unwrap();
    assert_eq!(sbatch_args(&allocation), vec!["--partition=gpu", "--nodes=2", "--gpus-per-node=2", "--ntasks-per-node=32", "--mem=131072M"]);
    assert_eq!(preview(&allocation), "sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32 --mem=131072M");
}

#[test]
fn fixture_cpu_acceptance_plan() {
    let partitions = parse_cluster(CLUSTER).unwrap();
    let allocation = calculate(&Request { partition: "cpu".into(), nodes: 1, gpus_per_node: None, cpus_per_node: Some(16), memory: MemoryMode::Proportional }, &partitions).unwrap();
    assert_eq!(sbatch_args(&allocation), vec!["--partition=cpu", "--nodes=1", "--ntasks-per-node=16", "--mem=65536M"]);
    assert_eq!(preview(&allocation), "sbatch --partition=cpu --nodes=1 --ntasks-per-node=16 --mem=65536M");
}

#[test]
fn fixture_diagnostics_identify_nodes_and_partitions() {
    let error = parse_cluster(include_str!("fixtures/heterogeneous.json")).unwrap_err();
    for expected in ["mixed", "gpu01", "cpu01", "64", "32", "262144", "131072"] {
        assert!(error.contains(expected), "{error}");
    }
    let error = parse_cluster(include_str!("fixtures/malformed.json")).unwrap_err();
    assert!(error.contains("broken01") && error.contains("GRES"), "{error}");
}

fn minimal_node() -> Value {
    json!({"name": "node01", "partitions": ["cpu"], "cpus": 64, "gres": "", "real_memory": 262144})
}

fn parse_node(node: Value) -> slurm_auto::Result<slurm_auto::model::Partitions> {
    parse_cluster(&json!({"nodes": [node]}).to_string())
}

#[test]
fn missing_required_fields_are_diagnosed() {
    for field in ["name", "partitions", "cpus", "gres", "real_memory"] {
        let mut node = minimal_node();
        node.as_object_mut().unwrap().remove(field);
        assert!(parse_node(node).is_err(), "{field}");
    }
    for text in ["not JSON", "{}", "{\"nodes\":null}", "{\"nodes\":[null]}", "{\"nodes\":[],\"errors\":[{\"error\":\"access denied\"}]}"] {
        assert!(parse_cluster(text).is_err(), "{text}");
    }
}

#[test]
fn malformed_field_types_and_invalid_capacities_are_diagnosed() {
    for (field, value) in [("name", json!(null)), ("partitions", json!([12])), ("partitions", json!("cpu,")), ("cpus", json!(-1)), ("cpus", json!("64")), ("cpus", json!(0)), ("real_memory", json!(1.5)), ("real_memory", json!(0)), ("gres", json!(["gpu:4"])), ("effective_cpus", json!("64")), ("real_memory", json!({"set": true, "infinite": true, "number": 262144}))] {
        let mut node = minimal_node();
        node[field] = value;
        assert!(parse_node(node).is_err(), "{field}");
    }
}

#[test]
fn effective_cpus_fallback_and_null_gres() {
    for effective in [Value::Null, json!({"set": false, "infinite": false, "number": 0})] {
        let mut node = minimal_node();
        node["effective_cpus"] = effective;
        node["gres"] = Value::Null;
        let partitions = parse_node(node).unwrap();
        assert_eq!(partitions["cpu"].capacity.cpus, 64);
        assert_eq!(partitions["cpu"].capacity.gpus, 0);
    }
    let mut node = minimal_node();
    node["effective_cpus"] = json!(32);
    assert_eq!(parse_node(node).unwrap()["cpu"].capacity.cpus, 32);
}

#[test]
fn duplicate_node_records_are_rejected() {
    assert!(parse_cluster(&json!({"nodes": [minimal_node(), minimal_node()]}).to_string()).unwrap_err().contains("duplicate"));
}

// Exercise the real CLI through a fake executable, without a Slurm installation.
#[cfg(unix)]
mod cli {
    use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf, process::{Command, Output}, sync::atomic::{AtomicU64, Ordering}};

    struct FakeSlurm(PathBuf);

    impl FakeSlurm {
        fn new(script: &str) -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let directory = std::env::temp_dir().join(format!("slurm-auto-{}-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos(), NEXT.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir(&directory).unwrap();
            let executable = directory.join("scontrol");
            fs::write(&executable, script).unwrap();
            fs::set_permissions(executable, fs::Permissions::from_mode(0o755)).unwrap();
            Self(directory)
        }

        fn run(&self, args: &[&str]) -> Output {
            Command::new(env!("CARGO_BIN_EXE_sbatch-auto"))
                .args(args).env("PATH", &self.0)
                .env("SLURM_AUTO_TEST_FIXTURE", concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/cluster.json"))
                .output().unwrap()
        }
    }

    impl Drop for FakeSlurm {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    fn working_slurm() -> FakeSlurm {
        FakeSlurm::new("#!/bin/sh\n[ \"$#\" -eq 3 ] && [ \"$1\" = show ] && [ \"$2\" = node ] && [ \"$3\" = --json ] || exit 9\n/bin/cat \"$SLURM_AUTO_TEST_FIXTURE\"\n")
    }

    #[test]
    fn cli_inspects_and_plans_without_sbatch_on_path() {
        let slurm = working_slurm();
        for args in [vec!["inspect"], vec!["plan", "-p", "gpu", "-N", "2", "-g", "2"], vec!["plan", "--partition", "cpu", "--nodes", "1", "--cpus-per-node", "16"], vec!["plan", "-p", "gpu"]] {
            let output = slurm.run(&args);
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
            let text = String::from_utf8(output.stdout).unwrap();
            if args[0] == "inspect" {
                for expected in ["PARTITION", "gpu", "cpu", "shared", "262144"] { assert!(text.contains(expected)); }
            } else if args.contains(&"16") {
                assert!(text.contains("sbatch --partition=cpu --nodes=1 --ntasks-per-node=16"));
            } else if args.contains(&"-g") {
                assert!(text.contains("sbatch --partition=gpu --nodes=2 --gpus-per-node=2 --ntasks-per-node=32"));
            } else {
                assert!(text.contains("--nodes=1 --gpus-per-node=4 --ntasks-per-node=64"));
            }
        }
    }

    #[test]
    fn cli_rejects_invalid_arguments_and_requests() {
        let slurm = working_slurm();
        for args in [vec!["plan"], vec!["plan", "-p", "missing"], vec!["plan", "-p", "gpu", "-N", "0"], vec!["plan", "-p", "gpu", "-N", "-1"], vec!["plan", "-p", "gpu", "-N", "3"], vec!["plan", "-p", "gpu", "-g", "0"], vec!["plan", "-p", "gpu", "-g", "-1"], vec!["plan", "-p", "gpu", "-g", "5"], vec!["plan", "-p", "cpu", "-c", "0"], vec!["plan", "-p", "cpu", "-c", "-1"], vec!["plan", "-p", "cpu", "-c", "65"], vec!["plan", "-p", "cpu", "-g", "1"], vec!["plan", "-p", "gpu", "-c", "1"], vec!["plan", "-p", "gpu", "-g", "1", "-c", "1"]] {
            let output = slurm.run(&args);
            assert!(!output.status.success(), "{args:?}");
            assert!(!output.stderr.is_empty());
        }
    }

    #[test]
    fn cli_reports_execution_exit_and_json_failures() {
        for (script, expected) in [("#!/bin/sh\necho permission-denied >&2\nexit 7\n", "permission-denied"), ("#!/bin/sh\necho not-json\n", "invalid scontrol node JSON")] {
            let output = FakeSlurm::new(script).run(&["inspect"]);
            assert!(!output.status.success());
            assert!(String::from_utf8_lossy(&output.stderr).contains(expected));
        }
        let slurm = FakeSlurm::new("#!/bin/sh\nexit 0\n");
        fs::remove_file(slurm.0.join("scontrol")).unwrap();
        let output = slurm.run(&["inspect"]);
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("failed to execute scontrol"));
    }
}
