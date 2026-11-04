pub mod calculation;
pub mod command;
pub mod discovery;
pub mod model;
pub mod validation;
pub mod config;
pub mod submission;

pub type Result<T> = std::result::Result<T, String>;
