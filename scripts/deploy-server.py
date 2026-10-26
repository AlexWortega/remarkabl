"""Stage only server source. Backups, SSH keys and build output never enter uploads."""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--service", default="diary")
parser.add_argument("--project", default="045103b6-07cd-4c46-a902-22a087215bd0")
parser.add_argument("--environment", default="production")
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="remarkabl-server-") as temporary:
    stage = Path(temporary)
    for name in ("Dockerfile", "pyproject.toml", "uv.lock", "railway.toml"):
        shutil.copyfile(root / name, stage / name)
    shutil.copytree(root / "remarkabl", stage / "remarkabl",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(root / "assets", stage / "assets")
    (stage / "deploy").mkdir()
    for name in ("supervisord.conf", "start-bot-api.py"):
        shutil.copyfile(root / "deploy" / name, stage / "deploy" / name)
    total = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
    print(f"Uploading {total / 1024 / 1024:.1f} MB of server source (no private files).", flush=True)
    subprocess.run(["railway", "up", str(stage), "--path-as-root", "--service", args.service,
                    "--project", args.project, "--environment", args.environment,
                    "--detach", "--json"], check=True, cwd=root)
