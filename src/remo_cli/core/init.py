"""Initialization logic for the remo development environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from remo_cli.core.config import get_ansible_dir, get_project_root
from remo_cli.core.output import GREEN, NC, print_error, print_info, print_success

# Minimum Python version required for the virtual environment
_MIN_PYTHON = "3.11"

# Python packages to install in the venv
_PIP_PACKAGES = [
    "ansible-core>=2.18.0,<2.19.0",
    "hcloud",
    "boto3",
    "botocore",
]


def handle_init(force: bool = False) -> None:
    """Initialize the remo environment.

    Creates a virtual environment, installs Python packages (ansible-core,
    hcloud, boto3), and installs Ansible collections.

    Parameters
    ----------
    force:
        If ``True``, remove and recreate the virtual environment even if
        one already exists.
    """
    project_root = get_project_root()
    ansible_dir = get_ansible_dir()
    venv_dir = project_root / ".venv"

    print()
    print_info("Initializing remo...")
    print()

    # ---- Step 1: Virtual environment ----
    _setup_venv(venv_dir, force)

    # Use venv binaries directly
    venv_python = venv_dir / "bin" / "python"
    ansible_galaxy = venv_dir / "bin" / "ansible-galaxy"

    # ---- Step 2: Install Python packages ----
    _install_python_packages(venv_python)

    # ---- Step 3: Install Ansible collections ----
    _install_ansible_collections(ansible_galaxy, ansible_dir)

    # ---- Print success banner and next steps ----
    print()
    print_success(
        "\u2550" * 63
    )
    print_success("  remo initialized successfully!")
    print_success(
        "\u2550" * 63
    )
    print()
    print("Next steps:")
    print()
    print(
        f"  {GREEN}remo incus create dev1 --host <incus-host> --user <user>{NC}"
    )
    print(
        f"  {GREEN}remo hetzner create{NC}   (requires HETZNER_API_TOKEN env var)"
    )
    print(
        f"  {GREEN}remo aws create{NC}       (requires aws configure / AWS_PROFILE)"
    )
    print()


def _setup_venv(venv_dir: Path, force: bool) -> None:
    """Create the project virtual environment.

    Prefers ``uv`` (which can auto-download the right Python), falling back
    to ``python3 -m venv`` after verifying the system Python version.
    """
    if venv_dir.is_dir() and not force:
        print_success("\u2713 Virtual environment already exists (.venv)")
        return

    if force and venv_dir.is_dir():
        print_info("Removing existing virtual environment...")
        shutil.rmtree(venv_dir)

    print_info("Creating virtual environment...")

    if shutil.which("uv"):
        # uv auto-downloads Python if the system version is too old
        result = subprocess.run(
            ["uv", "venv", "--python", _MIN_PYTHON, str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print_error(f"Failed to create venv with uv: {result.stderr.strip()}")
            sys.exit(1)
    else:
        # Verify system Python meets minimum version
        _check_python_version()
        result = subprocess.run(
            ["python3", "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print_error(f"Failed to create venv: {result.stderr.strip()}")
            sys.exit(1)

    print_success("\u2713 Created virtual environment (.venv)")


def _check_python_version() -> None:
    """Verify that the system Python is >= 3.11.  Exit with guidance if not."""
    try:
        result = subprocess.run(
            [
                "python3",
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            capture_output=True,
            text=True,
        )
        py_version = result.stdout.strip() if result.returncode == 0 else "0.0"

        check = subprocess.run(
            [
                "python3",
                "-c",
                "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)",
            ],
            capture_output=True,
        )
        if check.returncode != 0:
            print_error(f"Python {_MIN_PYTHON}+ is required (found {py_version})")
            print("Install a newer Python or install uv (which auto-manages Python):")
            print("  brew install python@3.12    # macOS")
            print(
                "  curl -LsSf https://astral.sh/uv/install.sh | sh  # any platform"
            )
            sys.exit(1)
    except FileNotFoundError:
        print_error("python3 not found on PATH")
        print("Install Python 3.11+ or install uv (which auto-manages Python):")
        print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        sys.exit(1)


def _install_python_packages(venv_python: Path) -> None:
    """Install required Python packages into the venv."""
    print_info("Installing Python packages (ansible-core, hcloud, boto3)...")

    if shutil.which("uv"):
        cmd = [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(venv_python),
            *_PIP_PACKAGES,
        ]
    else:
        # Ensure pip is available, then upgrade it
        subprocess.run(
            [str(venv_python), "-m", "ensurepip", "--quiet"],
            capture_output=True,
        )
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            capture_output=True,
        )
        cmd = [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--quiet",
            *_PIP_PACKAGES,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print_error(f"Failed to install packages: {result.stderr.strip()}")
        sys.exit(1)

    print_success("\u2713 Installed ansible-core, hcloud, and boto3")


def _install_ansible_collections(ansible_galaxy: Path, ansible_dir: Path) -> None:
    """Install Ansible Galaxy collections from requirements.yml."""
    print_info("Installing Ansible collections...")

    requirements_file = ansible_dir / "requirements.yml"
    if not requirements_file.is_file():
        print_error(f"Ansible requirements file not found: {requirements_file}")
        sys.exit(1)

    result = subprocess.run(
        [
            str(ansible_galaxy),
            "collection",
            "install",
            "--upgrade",
            "-r",
            str(requirements_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print_error(f"Failed to install collections: {result.stderr.strip()}")
        sys.exit(1)

    print_success("\u2713 Installed Ansible collections")
