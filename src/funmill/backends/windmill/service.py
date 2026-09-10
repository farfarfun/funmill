import hashlib
import os
import platform
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

VERSION = "v1.808.0"
URL = f"https://github.com/windmill-labs/windmill/releases/download/{VERSION}/windmill-amd64"
SHA256 = "ed48bfb9a391daa437f0c867376f009c7186855530de7fe2f58cf557ff1f7c3a"


def _target() -> Path:
    home = Path(os.getenv("FUNMILL_HOME", Path.home() / ".farfarfun" / "funmill"))
    return home / "services" / "windmill" / "windmill"


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def install(force: bool = False) -> Path:
    if platform.system() != "Linux" or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise RuntimeError("Windmill v1.808.0 installer only supports Linux x86_64")

    target = _target()
    if target.exists() and _sha256(target) == SHA256:
        return target
    if target.exists() and not force:
        raise RuntimeError(
            f"{target} exists but has an unexpected checksum; use --force"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        print(f"downloading Windmill {VERSION}...", flush=True)
        request = Request(URL, headers={"User-Agent": "funmill"})
        with urlopen(request, timeout=30) as response, temporary.open("wb") as file:
            shutil.copyfileobj(response, file)
        if _sha256(temporary) != SHA256:
            raise RuntimeError("downloaded Windmill binary failed SHA-256 verification")
        temporary.chmod(0o755)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def start() -> None:
    executable = _target()
    if not executable.exists():
        system_executable = shutil.which("windmill")
        if system_executable is None:
            raise RuntimeError(
                "Windmill is not installed; run: funmill install windmill"
            )
        executable = Path(system_executable)

    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required")

    environment = os.environ.copy()
    mode = environment.setdefault("MODE", "standalone")
    if mode in {"standalone", "server"}:
        environment.setdefault("PORT", "8805")
        environment.setdefault("BASE_URL", "http://127.0.0.1:8805")
    environment.setdefault("SERVER_BIND_ADDR", "127.0.0.1")
    os.execve(executable, [str(executable)], environment)
