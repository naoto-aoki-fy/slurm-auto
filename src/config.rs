use std::{collections::BTreeMap, path::{Path, PathBuf}};

use serde::Deserialize;

use crate::submission::Error;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub templates: BTreeMap<String, Template>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Template {
    pub sbatch_args: Vec<String>,
}

pub fn default_path() -> Result<PathBuf, Error> {
    if let Some(root) = std::env::var_os("XDG_CONFIG_HOME").filter(|p| !p.is_empty()) {
        let root = PathBuf::from(root);
        if root.is_absolute() {
            return Ok(root.join("slurm-auto/config.toml"));
        }
    }
    let home = std::env::var_os("HOME").filter(|p| !p.is_empty())
        .ok_or_else(|| Error::Config("HOME is unset; provide --config <path>".into()))?;
    Ok(PathBuf::from(home).join(".config/slurm-auto/config.toml"))
}

pub fn load(path: &Path, name: &str) -> Result<Template, Error> {
    let source = std::fs::read_to_string(path)
        .map_err(|error| Error::Config(format!("cannot read {}: {error}; provide --config or install a configuration (see config.example.toml)", path.display())))?;
    let config: Config = toml::from_str(&source)
        .map_err(|error| Error::Config(format!("invalid configuration {}: {error}", path.display())))?;
    config.templates.get(name).cloned().ok_or_else(|| Error::Config(format!(
        "template {name:?} not found in {}; available templates: {}",
        path.display(), config.templates.keys().cloned().collect::<Vec<_>>().join(", ")
    )))
}
