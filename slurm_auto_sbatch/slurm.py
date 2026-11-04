"""Execute Slurm commands without interpreting their output."""

import subprocess

from .errors import DiscoveryError


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
