"""Construct submission arguments independently of command execution."""

from typing import List

from .config import JobConfig, validate_template
from .resources import JobResources


def build_sbatch_command(resources: JobResources, config: JobConfig) -> List[str]:
    """Preserve template arguments after the tool's calculated resource options."""
    validate_template(config.sbatch_options)
    return ["sbatch", *resources.sbatch_options(), *config.sbatch_options]
