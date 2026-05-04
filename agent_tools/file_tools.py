from pathlib import Path


IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
    "logs",
}

IGNORED_FILES = {
    ".env",
}


def is_ignored(path: Path) -> bool:
    if any(part in IGNORED_DIRS for part in path.parts):
        return True

    if path.name in IGNORED_FILES:
        return True

    return False


def list_files(project_dir: str, max_files: int = 80) -> dict:
    root = Path(project_dir).resolve()

    if not root.exists():
        return {
            "ok": False,
            "error": f"项目目录不存在: {root}",
            "files": []
        }

    files = []

    for path in root.rglob("*"):
        if is_ignored(path):
            continue

        if path.is_file():
            rel_path = path.relative_to(root)
            files.append(str(rel_path))

        if len(files) >= max_files:
            break

    return {
        "ok": True,
        "root": str(root),
        "files": files,
        "count": len(files)
    }


def read_file(project_dir: str, file_path: str, max_chars: int = 5000) -> dict:
    root = Path(project_dir).resolve()
    target = (root / file_path).resolve()

    if not str(target).startswith(str(root)):
        return {
            "ok": False,
            "error": "禁止读取项目目录外的文件"
        }

    if is_ignored(target):
        return {
            "ok": False,
            "error": f"该文件被安全策略禁止读取: {file_path}"
        }

    if not target.exists():
        return {
            "ok": False,
            "error": f"文件不存在: {file_path}"
        }

    if not target.is_file():
        return {
            "ok": False,
            "error": f"不是文件: {file_path}"
        }

    text = target.read_text(encoding="utf-8", errors="ignore")

    return {
        "ok": True,
        "file_path": file_path,
        "content": text[:max_chars],
        "truncated": len(text) > max_chars
    }


def search_text(project_dir: str, keyword: str, max_results: int = 30) -> dict:
    root = Path(project_dir).resolve()

    if not root.exists():
        return {
            "ok": False,
            "error": f"项目目录不存在: {root}",
            "matches": []
        }

    matches = []

    for path in root.rglob("*"):
        if is_ignored(path):
            continue

        if not path.is_file():
            continue

        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for line_no, line in enumerate(lines, 1):
            if keyword in line:
                matches.append({
                    "file": str(path.relative_to(root)),
                    "line": line_no,
                    "text": line.strip()
                })

                if len(matches) >= max_results:
                    return {
                        "ok": True,
                        "keyword": keyword,
                        "matches": matches
                    }

    return {
        "ok": True,
        "keyword": keyword,
        "matches": matches
    }
