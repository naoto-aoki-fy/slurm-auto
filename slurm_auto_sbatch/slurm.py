"""Execute Slurm commands without interpreting their output."""

import subprocess
from typing import List

from .errors import DiscoveryError, JobError


def retrieve_node_json() -> str:
    command = ["scontrol", "show", "node", "--json"]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except FileNotFoundError as exc:
        raise DiscoveryError("scontrol was not found; install Slurm and add it to PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "no diagnostic output").strip()
        raise DiscoveryError(
            f"{' '.join(command)} failed (exit {exc.returncode}): {detail}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise DiscoveryError(f"Unable to read scontrol node information: {exc}") from exc
    return result.stdout


def submit_job(command: List[str]) -> subprocess.CompletedProcess:
    """Execute an already constructed sbatch argument list and return its output."""
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise JobError(f"Unable to execute sbatch: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise JobError(f"sbatch failed (exit {result.returncode}): {detail}")
    return result
