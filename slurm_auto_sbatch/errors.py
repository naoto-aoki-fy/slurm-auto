class DiscoveryError(Exception):
    """Slurm execution or node data could not be interpreted safely."""


class JobError(Exception):
    """Job configuration, resource selection, or submission failed."""
