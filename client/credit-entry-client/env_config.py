"""Small standard-library .env reader/writer shared by CES host and client."""
import os
import re
import tempfile
from pathlib import Path


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path):
    """Load KEY=VALUE lines without overriding variables supplied by the OS."""
    path = Path(path)
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                raise ValueError("%s:%d: expected KEY=VALUE" % (path, line_number))
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not _KEY_RE.fullmatch(key):
                raise ValueError("%s:%d: invalid environment variable name" % (path, line_number))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].rstrip()
            os.environ.setdefault(key, value)


def get_path(name, default, root):
    """Return a configured path, resolving relative values from the repo root."""
    value = os.environ.get(name, default)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def save_env_values(path, values):
    """Atomically update selected KEY=VALUE entries while preserving other lines."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updates = {key: str(value) for key, value in values.items()}
    replaced = set()
    output = []
    for line in lines:
        stripped = line.strip()
        candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        key = candidate.split("=", 1)[0].strip() if "=" in candidate else ""
        if key in updates:
            output.append("%s=%s" % (key, updates[key]))
            replaced.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in replaced:
            output.append("%s=%s" % (key, value))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output) + "\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    for key, value in updates.items():
        os.environ[key] = value
