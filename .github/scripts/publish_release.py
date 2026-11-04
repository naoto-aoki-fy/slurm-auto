"""Publish downloaded musl executables; called under the release job's concurrency lock."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


TARGETS = ("x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl")
VERSION = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def release_info(tag):
    return json.loads(run("gh", "release", "view", tag, "--json", "isDraft,assets,url"))


def require_assets(assets, expected):
    available = {asset["name"] for asset in assets if asset["size"] > 0}
    missing = set(expected) - available
    if missing:
        raise RuntimeError(f"Release is missing nonempty assets: {sorted(missing)}")


def main():
    # Check both inputs before creating any remote tag or draft release.
    inputs = [Path("artifacts") / f"slurm-auto-{target}" for target in TARGETS]
    for binary in inputs:
        if not binary.is_file() or binary.stat().st_size == 0:
            raise RuntimeError(f"Required build artifact missing or empty: {binary}")

    commit = os.environ["GITHUB_SHA"]
    if run("git", "rev-parse", "HEAD") != commit:
        raise RuntimeError("Checkout does not match the triggering commit")

    # Refresh inside the publishing lock, after all required jobs have succeeded.
    run("git", "fetch", "--tags", "origin")
    versions = []
    for tag in run("git", "tag", "--list").splitlines():
        match = VERSION.fullmatch(tag)
        if match:
            versions.append((tuple(int(part) for part in match.groups()), tag))

    # Peel annotated tags as well as lightweight tags. Reuse a tag for this
    # exact commit after an interrupted run rather than allocating another one.
    same_commit = [
        (version, tag)
        for version, tag in versions
        if run("git", "rev-parse", f"refs/tags/{tag}^{{commit}}") == commit
    ]
    if same_commit:
        tag = max(same_commit)[1]
    else:
        highest = max(versions)[0] if versions else None
        next_version = (highest[0], highest[1], highest[2] + 1) if highest else (0, 0, 1)
        tag = "v" + ".".join(str(part) for part in next_version)

    # Listing releases avoids treating API/network failures as an absent release.
    pages = json.loads(run(
        "gh", "api", "--paginate", "--slurp",
        f"repos/{os.environ['GH_REPO']}/releases?per_page=100",
    ))
    existing = next(
        (release for page in pages for release in page if release["tag_name"] == tag),
        None,
    )
    expected = [f"slurm-auto-{tag}-{target}" for target in TARGETS]
    if existing and not same_commit:
        raise RuntimeError(f"Release {tag} already exists; refusing to overwrite it")
    if existing and not existing["draft"]:
        info = release_info(tag)
        require_assets(info["assets"], expected)
        print(f"Commit already released: {info['url']}")
        return

    Path("dist").mkdir(exist_ok=True)
    binaries = []
    for source, name in zip(inputs, expected):
        destination = Path("dist") / name
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
        binaries.append(destination)

    if not same_commit:
        # No force: a competing external tag creator causes failure, never replacement.
        run("git", "push", "origin", f"{commit}:refs/tags/{tag}")
    if existing is None:
        run(
            "gh", "release", "create", tag, "--verify-tag", "--draft",
            "--title", tag, "--generate-notes", "--target", commit,
        )

    # Interrupted uploads can be resumed. Never clobber an existing asset.
    info = release_info(tag)
    uploaded = {asset["name"]: asset for asset in info["assets"]}
    for binary in binaries:
        if binary.name in uploaded:
            if uploaded[binary.name]["size"] <= 0:
                raise RuntimeError(f"Existing asset is empty: {binary.name}")
        else:
            run("gh", "release", "upload", tag, str(binary))

    info = release_info(tag)
    require_assets(info["assets"], expected)
    run("gh", "release", "edit", tag, "--draft=false")
    print(f"Published {tag}: {info['url']}")


if __name__ == "__main__":
    main()
