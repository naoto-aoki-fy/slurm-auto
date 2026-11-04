use std::collections::BTreeSet;
use std::process::Command;

use serde::Deserialize;
use serde_json::Value;

use crate::model::{Capacity, Node, Partitions};
use crate::validation::aggregate;
use crate::Result;

pub fn discover() -> Result<Partitions> {
    let output = Command::new("scontrol").args(["show", "node", "--json"]).output()
        .map_err(|error| format!("failed to execute scontrol show node --json: {error}"))?;
    if !output.status.success() {
        return Err(format!("scontrol show node --json failed ({}): {}", output.status, String::from_utf8_lossy(&output.stderr).trim()));
    }
    let json = std::str::from_utf8(&output.stdout).map_err(|error| format!("scontrol returned non-UTF-8 JSON: {error}"))?;
    parse_cluster(json)
}

#[derive(Deserialize)]
struct Response {
    nodes: Vec<Value>,
    #[serde(default)]
    errors: Vec<Value>,
}

pub fn parse_cluster(json: &str) -> Result<Partitions> {
    let response: Response = serde_json::from_str(json).map_err(|error| format!("invalid scontrol node JSON: {error}"))?;
    if !response.errors.is_empty() {
        return Err(format!("scontrol reported errors: {}", Value::Array(response.errors)));
    }
    let mut nodes = Vec::new();
    for (index, value) in response.nodes.iter().enumerate() {
        nodes.push(parse_node(value).map_err(|error| format!("node record {index}: {error}"))?);
    }
    aggregate(nodes)
}

fn parse_node(value: &Value) -> Result<Node> {
    let object = value.as_object().ok_or("expected a node object")?;
    let name = object.get("name").and_then(Value::as_str).filter(|s| !s.trim().is_empty())
        .ok_or("missing or malformed node name")?.to_owned();
    let parse = || -> Result<Node> {
        let partitions = parse_partitions(object.get("partitions").ok_or("missing partitions field")?)?;
        let effective = object.get("effective_cpus").or_else(|| object.get("cpus_efctv"));
        let cpus = match effective {
            None | Some(Value::Null) => number(object.get("cpus"), "cpus")?,
            Some(value) if value.get("set") == Some(&Value::Bool(false)) => number(object.get("cpus"), "cpus")?,
            Some(value) => number(Some(value), "effective_cpus")?,
        };
        let memory_mib = number(object.get("real_memory"), "real_memory")?;
        let gres = object.get("gres").ok_or("missing gres field; configured GPU capacity is unknown")?;
        let gpus = match gres {
            Value::Null => 0,
            Value::String(text) => parse_gres(text)?,
            _ => return Err("malformed gres field: expected a string or null".into()),
        };
        Ok(Node { name: name.clone(), partitions, capacity: Capacity { cpus, gpus, memory_mib } })
    };
    parse().map_err(|error| format!("node {name:?}: {error}"))
}

fn number(value: Option<&Value>, field: &str) -> Result<u64> {
    let value = value.ok_or_else(|| format!("missing {field} field"))?;
    if let Some(number) = value.as_u64() {
        return Ok(number);
    }
    // Recent Slurm data parsers can represent numeric fields as tagged values.
    if let Some(object) = value.as_object() {
        if object.get("set") == Some(&Value::Bool(true))
            && object.get("infinite").is_none_or(|v| v == &Value::Bool(false))
        {
            if let Some(number) = object.get("number").and_then(Value::as_u64) {
                return Ok(number);
            }
        }
    }
    Err(format!("malformed {field}: expected a finite, nonnegative integer, got {value}"))
}

fn parse_partitions(value: &Value) -> Result<Vec<String>> {
    let items: Vec<&str> = match value {
        Value::Null => vec![],
        Value::String(text) if text.is_empty() || text == "(null)" => vec![],
        Value::String(text) => text.split(',').collect(),
        Value::Array(items) => items.iter().map(|item| item.as_str().ok_or_else(|| "malformed partition membership: expected strings".to_owned())).collect::<Result<_>>()?,
        _ => return Err("malformed partitions: expected an array of names or comma-separated string".into()),
    };
    let mut names = BTreeSet::new();
    for item in items {
        let item = item.trim();
        if item.is_empty() || item.chars().any(char::is_control) {
            return Err("malformed partition membership: empty name or control character".into());
        }
        names.insert(item.to_owned());
    }
    Ok(names.into_iter().collect())
}

/// Count only configured `gpu` GRES, never gres_used/gres_drained or lookalike names.
pub fn parse_gres(text: &str) -> Result<u64> {
    let text = text.trim();
    if text.is_empty() || text == "(null)" { return Ok(0); }
    let invalid = || format!("cannot reliably interpret configured GRES {text:?}; expected gpu[:type]:count with optional metadata annotations");
    // Metadata can contain commas, e.g. (IDX:0,1). Split only outside annotations.
    let mut entries = Vec::new();
    let mut start = 0;
    let mut inside = false;
    for (index, character) in text.char_indices() {
        match character {
            '(' if !inside => inside = true,
            ')' if inside => inside = false,
            '(' | ')' => return Err(invalid()),
            ',' if !inside => { entries.push(&text[start..index]); start = index + 1; }
            _ => {}
        }
    }
    if inside { return Err(invalid()); }
    entries.push(&text[start..]);
    let mut total = 0_u64;
    for entry in entries {
        let entry = entry.trim();
        let base = if let Some(index) = entry.find('(') {
            let suffix = &entry[index..];
            // Only annotations may follow the count; reject trailing unparsed text.
            let mut remainder = suffix;
            while !remainder.is_empty() {
                if !remainder.starts_with('(') { return Err(invalid()); }
                let end = remainder.find(')').ok_or_else(invalid)?;
                remainder = remainder[end + 1..].trim();
            }
            entry[..index].trim()
        } else { entry };
        let parts = base.split(':').collect::<Vec<_>>();
        if parts.iter().any(|part| part.is_empty() || part.chars().any(char::is_whitespace)) {
            return Err(invalid());
        }
        if parts[0] != "gpu" {
            // Other resources may use units and flags (e.g. bandwidth:lustre:no_consume:4G).
            if parts.len() < 2 { return Err(invalid()); }
            continue;
        }
        if !(parts.len() == 2 || parts.len() == 3) { return Err(invalid()); }
        let count = parts.last().unwrap();
        if !count.bytes().all(|byte| byte.is_ascii_digit()) { return Err(invalid()); }
        let count = count.parse::<u64>().map_err(|_| invalid())?;
        total = total.checked_add(count).ok_or_else(invalid)?;
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn common_gpu_and_non_gpu_gres() {
        for (text, expected) in [("", 0), ("(null)", 0), ("gpu:4", 4), ("gpu:a100:4(S:0-3)", 4), ("gpu:a100:2(IDX:0,1),gpu:h100:2(S:1),mps:400", 4), ("bandwidth:lustre:no_consume:4G,gpu_mem:80", 0), ("shard:100,gpu:4(S:0-1)", 4)] {
            assert_eq!(parse_gres(text).unwrap(), expected, "{text}");
        }
    }

    #[test]
    fn rejects_ambiguous_gpu_counts_and_malformed_gres() {
        for text in ["gpu", "gpu:a100", "gpu:-1", "gpu:1.5", "gpu:a100:2:3", "gpu:18446744073709551616", "gpu:18446744073709551615,gpu:1", "gpu:4(S:0", "gpu:4)ending", "gpu:4(S:0)junk", "gpu:4,", "gpu::4"] {
            assert!(parse_gres(text).is_err(), "{text}");
        }
    }
}
