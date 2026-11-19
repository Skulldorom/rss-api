import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load_workflow(name):
    # BaseLoader preserves YAML keys like "on" and GitHub's expression strings.
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_ci_covers_prs_and_main_with_read_only_access():
    ci = load_workflow("ci.yml")

    assert ci["name"] == "CI"
    assert ci["on"] == {"pull_request": "", "push": {"branches": ["main"]}}
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"]["test"]["name"] == "test"
    assert "permissions" not in ci["jobs"]["test"]


def test_docker_publishes_only_the_verified_main_push():
    workflow = load_workflow("docker.yml")
    job = workflow["jobs"]["build-push"]
    assert workflow["on"] == {
        "workflow_run": {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
    }
    assert workflow["permissions"] == {}
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert job["if"].strip().split(" && ") == [
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.path == '.github/workflows/ci.yml'",
        "github.event.workflow_run.head_repository.id == github.event.repository.id",
    ]
    assert job["env"]["TESTED_SHA"] == "${{ github.event.workflow_run.head_sha }}"
    checkout = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"] == {"ref": "${{ env.TESTED_SHA }}", "persist-credentials": "false"}
    build = next(
        step for step in job["steps"] if step.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build["if"] == "steps.commit-image.outputs.missing == 'true'"
    assert build["with"]["context"] == "."
    assert build["with"]["push"] == "true"
    assert build["with"]["tags"] == "${{ env.IMAGE }}:${{ env.TESTED_SHA }}"
    assert job["concurrency"] == {
        "group": "publish-docker-main", "cancel-in-progress": "false", "queue": "max"
    }


@pytest.mark.parametrize(
    ("main_lookup", "expect_promotion", "exit_code"),
    [
        ('printf "%s\\n" "$TESTED_SHA"', True, 0),
        ('printf "%s\\n" "newer-commit"', False, 0),
        ("exit 1", False, 1),
    ],
    ids=["current", "stale", "lookup-failed"],
)
def test_latest_promotion_uses_tested_tag_only_if_main_is_current(
    tmp_path, main_lookup, expect_promotion, exit_code
):
    job = load_workflow("docker.yml")["jobs"]["build-push"]
    step = next(
        step for step in job["steps"] if step["name"] == "Promote current main to latest"
    )
    assert job["defaults"]["run"]["shell"] == "bash"

    tested_sha = "a" * 40
    image = "ghcr.io/example/rss-api"
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is needed to run the GitHub Actions shell step")

    fake_commands = (
        f"gh() {{ {main_lookup}; }}\n"
        'docker() { printf "%s\\n" "$@" > docker-args; }\n'
    )
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", fake_commands + step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "TESTED_SHA": tested_sha,
            "IMAGE": image,
            "GITHUB_REPOSITORY": "example/rss-api",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == exit_code, result.stderr
    docker_args = tmp_path / "docker-args"
    assert docker_args.exists() == expect_promotion
    if expect_promotion:
        assert docker_args.read_text().splitlines() == [
            "buildx", "imagetools", "create", "--prefer-index=false",
            "--tag", f"{image}:latest", f"{image}:{tested_sha}",
        ]
