use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

use serde::Deserialize;

use crate::submission::Error;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    #[serde(default)]
    pub templates: BTreeMap<String, Template>,
    #[serde(default)]
    pub sbatch: Sbatch,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sbatch {
    #[serde(default)]
    pub defaults: Defaults,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Defaults {
    pub account: Option<String>,
    pub partition: Option<String>,
    pub time: Option<String>,
    pub wrap: Option<String>,
    pub job_name: Option<String>,
}

impl Defaults {
    pub fn validate(&self) -> Result<(), Error> {
        for (name, value) in [
            ("account", &self.account),
            ("partition", &self.partition),
            ("time", &self.time),
            ("wrap", &self.wrap),
            ("job_name", &self.job_name),
        ] {
            if value
                .as_ref()
                .is_some_and(|value| value.trim().is_empty() || value.contains('\0'))
            {
                return Err(Error::Config(format!("invalid configuration: sbatch.defaults.{name} must be nonempty and contain no NUL bytes")));
            }
        }
        Ok(())
    }

    pub fn arguments(&self, script_supplied: bool) -> Template {
        let mut sbatch_args = Vec::new();
        for (name, value) in [
            ("account", &self.account),
            ("time", &self.time),
            ("job-name", &self.job_name),
            ("wrap", &self.wrap),
        ] {
            if name == "wrap" && script_supplied {
                continue;
            }
            if let Some(value) = value {
                sbatch_args.push(format!("--{name}={value}"));
            }
        }
        Template { sbatch_args }
    }
}

impl Config {
    pub fn template(&self, name: Option<&str>) -> Result<Template, Error> {
        let selected = name.unwrap_or("default");
        if let Some(template) = self.templates.get(selected) {
            return Ok(template.clone());
        }
        if name.is_none() && self.templates.is_empty() {
            return Ok(Template {
                sbatch_args: Vec::new(),
            });
        }
        Err(Error::Config(format!(
            "template {selected:?} not found; available templates: {}",
            self.templates
                .keys()
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        )))
    }
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
    let home = std::env::var_os("HOME")
        .filter(|p| !p.is_empty())
        .ok_or_else(|| Error::Config("HOME is unset; provide --config <path>".into()))?;
    Ok(PathBuf::from(home).join(".config/slurm-auto/config.toml"))
}

pub fn load_config(path: &Path) -> Result<Config, Error> {
    let source = std::fs::read_to_string(path)
        .map_err(|error| Error::Config(format!("cannot read {}: {error}; provide --config or install a configuration (see config.example.toml)", path.display())))?;
    let config: Config = toml::from_str(&source).map_err(|error| {
        Error::Config(format!("invalid configuration {}: {error}", path.display()))
    })?;
    config.sbatch.defaults.validate()?;
    Ok(config)
}

pub fn load(path: &Path, name: &str) -> Result<Template, Error> {
    load_config(path)?.template(Some(name))
}
