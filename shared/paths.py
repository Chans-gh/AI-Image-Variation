from pathlib import Path, PurePosixPath


def safe_path(root, relative):
    """Canonical POSIX relative paths only; reject every symlink in the chain."""
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or ":" in relative
        or "\0" in relative
    ):
        raise ValueError("UNSAFE_PATH")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or any(p in {".", ".."} for p in relative.split("/")) or "//" in relative:
        raise ValueError("UNSAFE_PATH")
    root = Path(root).absolute()
    current = root
    if root.is_symlink():
        raise ValueError("UNSAFE_PATH")
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("UNSAFE_PATH")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("UNSAFE_PATH")
    return current
