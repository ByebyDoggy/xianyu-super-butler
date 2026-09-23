"""在线更新模块。

从配置的 GitHub 仓库检查并拉取最新代码，实现「一键在线更新」。

安全设计：
- 仅允许更新到系统设置中配置的仓库/分支（防止 SSRF/任意来源），仓库与分支均做严格格式校验；
- 所有子进程调用均使用参数数组、禁用 shell，避免命令注入；
- 更新流程中的敏感信息（token）不写入日志；
- 归档解压做路径穿越校验，且不会覆盖 data/logs/backups/上传文件等运行数据；
- 接口层仅允许管理员调用（见 reply_server.py）。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"

DEFAULT_REPO = "ByebyDoggy/xianyu-super-butler"
DEFAULT_BRANCH = "main"
GITHUB_API = "https://api.github.com"
USER_AGENT = "xianyu-super-butler-updater"

_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

# 更新时永不覆盖的运行数据/配置（相对项目根目录）
_PROTECTED_TOP = {"data", "logs", "backups", ".git", ".env", "venv", ".venv",
                  ".venv-test", ".venv-sec", "node_modules", "global_config.yml"}
_PROTECTED_ANY = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".venv",
                  ".venv-test", ".venv-sec", "venv"}
_PROTECTED_PREFIXES = ("static/uploads",)


class UpdateError(Exception):
    """在线更新过程中的可预期错误"""


def get_local_version() -> str:
    """读取本地版本号（VERSION 文件，缺失时返回 unknown）"""
    try:
        if VERSION_FILE.exists():
            return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def is_docker() -> bool:
    """判断是否运行在容器中"""
    if os.getenv("DOCKER_ENV", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return Path("/.dockerenv").exists()
    except Exception:
        return False


def get_local_commit() -> Optional[str]:
    """获取当前部署的提交号：优先 git，其次镜像构建时注入的 APP_COMMIT"""
    if _is_git_repo():
        commit = _git_output(["rev-parse", "HEAD"])
        if commit:
            return commit
    commit = (os.getenv("APP_COMMIT") or "").strip()
    return commit or None


def get_watchtower_config() -> Dict[str, str]:
    """读取 Watchtower HTTP API 配置（用于 Docker 镜像级更新）"""
    return {
        "url": (os.getenv("WATCHTOWER_URL") or "").strip().rstrip("/"),
        "token": (os.getenv("WATCHTOWER_TOKEN") or "").strip(),
    }


def trigger_watchtower() -> Optional[Dict[str, Any]]:
    """触发 Watchtower 拉取最新镜像并重建容器；未配置时返回 None"""
    cfg = get_watchtower_config()
    if not cfg["url"]:
        return None
    headers = {"Content-Type": "application/json"}
    if cfg["token"]:
        headers["Authorization"] = f"Bearer {cfg['token']}"
    try:
        resp = requests.post(f"{cfg['url']}/v1/update", headers=headers, timeout=30)
    except requests.RequestException:
        # Watchtower 未运行/不可达：返回 None，由调用方回退到容器内更新
        logger.warning("Watchtower 不可达，回退为容器内更新")
        return None
    if resp.status_code >= 400:
        raise UpdateError(f"Watchtower 返回错误: HTTP {resp.status_code}")
    logger.info("已触发 Watchtower 镜像更新")
    return {"triggered": True, "url": cfg["url"]}


def get_update_config(db_manager) -> Dict[str, str]:
    """从系统设置读取（并经校验的）更新配置"""
    repo = (db_manager.get_system_setting("github_repo") or DEFAULT_REPO).strip()
    branch = (db_manager.get_system_setting("github_branch") or DEFAULT_BRANCH).strip()
    token = (db_manager.get_system_setting("github_token") or "").strip()

    # 允许通过环境变量覆盖（便于部署时固定来源）
    repo = os.getenv("UPDATE_GITHUB_REPO", repo).strip() or DEFAULT_REPO
    branch = os.getenv("UPDATE_GITHUB_BRANCH", branch).strip() or DEFAULT_BRANCH
    token = os.getenv("UPDATE_GITHUB_TOKEN", token).strip() or token

    _validate_repo(repo)
    _validate_branch(branch)
    return {"repo": repo, "branch": branch, "token": token}


def _validate_repo(repo: str) -> None:
    if not repo or not _REPO_RE.match(repo):
        raise UpdateError("GitHub 仓库格式非法，应为 owner/name")
    if ".." in repo:
        raise UpdateError("GitHub 仓库格式非法")


def _validate_branch(branch: str) -> None:
    if not branch or not _BRANCH_RE.match(branch) or ".." in branch or branch.startswith("/"):
        raise UpdateError("GitHub 分支名非法")


def _headers(token: str) -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str, token: str = "", timeout: int = 20) -> Any:
    try:
        resp = requests.get(url, headers=_headers(token), timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise UpdateError(f"访问 GitHub 失败: {e.__class__.__name__}") from e
    if resp.status_code == 404:
        raise UpdateError("仓库或分支不存在（404）")
    if resp.status_code == 401:
        raise UpdateError("GitHub 鉴权失败，请检查 github_token")
    if resp.status_code == 403:
        raise UpdateError("GitHub 访问被拒绝（可能触发限流，可配置 github_token）")
    if resp.status_code >= 400:
        raise UpdateError(f"GitHub 返回错误: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise UpdateError("GitHub 返回内容无法解析") from e


def _run_git(args, timeout: int = 120) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        raise UpdateError("未找到 git 可执行文件")
    return subprocess.run(
        [git, *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _is_git_repo() -> bool:
    return (PROJECT_ROOT / ".git").exists() and shutil.which("git") is not None


def _git_output(args, timeout: int = 30) -> Optional[str]:
    try:
        proc = _run_git(args, timeout=timeout)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def check_update(db_manager) -> Dict[str, Any]:
    """检查是否有可用更新。"""
    cfg = get_update_config(db_manager)
    repo, branch, token = cfg["repo"], cfg["branch"], cfg["token"]

    local_commit = get_local_commit()

    data = _get_json(f"{GITHUB_API}/repos/{repo}/commits/{branch}", token=token)
    latest_commit = data.get("sha") or ""
    commit_info = data.get("commit") or {}
    message = (commit_info.get("message") or "").splitlines()[0]
    author = (commit_info.get("author") or {}).get("name") or ""
    date = (commit_info.get("author") or {}).get("date") or ""

    # 通过 compare 判断本地相对远端是领先/落后，避免“本地领先”时误报可更新
    compare_status: Optional[str] = None
    ahead_by = behind_by = None
    if local_commit:
        try:
            compare = _get_json(
                f"{GITHUB_API}/repos/{repo}/compare/{local_commit}...{branch}", token=token
            )
            compare_status = compare.get("status")
            ahead_by = compare.get("ahead_by")
            behind_by = compare.get("behind_by")
        except UpdateError:
            # 本地提交不在远端（如本地分支未推送）时忽略 compare
            compare_status = None

    if compare_status in ("ahead", "diverged"):
        update_available: Optional[bool] = True
    elif compare_status in ("behind", "identical"):
        update_available = False
    elif local_commit:
        update_available = bool(latest_commit) and latest_commit != local_commit
    else:
        # 非 git 部署（如 Docker）无法比对提交号，仅提示可强制同步
        update_available = None

    return {
        "success": True,
        "repo": repo,
        "branch": branch,
        "current_version": get_local_version(),
        "current_commit": local_commit,
        "latest_commit": latest_commit,
        "latest_message": message,
        "latest_author": author,
        "latest_date": date,
        "compare_status": compare_status,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
        "html_url": data.get("html_url") or f"https://github.com/{repo}/commit/{latest_commit}",
        "update_available": update_available,
        "can_git_update": _is_git_repo(),
        "deployment": "docker" if is_docker() else ("git" if _is_git_repo() else "source"),
        "watchtower_enabled": bool(get_watchtower_config()["url"]),
        "persistent_update": (not is_docker()) or bool(get_watchtower_config()["url"]),
    }


def _apply_git_update(branch: str, force: bool) -> str:
    fetch = _run_git(["fetch", "--prune", "origin", branch], timeout=180)
    if fetch.returncode != 0:
        raise UpdateError(f"git fetch 失败: {fetch.stderr.strip()[:300]}")

    if force:
        reset = _run_git(["reset", "--hard", "FETCH_HEAD"], timeout=60)
        if reset.returncode != 0:
            raise UpdateError(f"git reset 失败: {reset.stderr.strip()[:300]}")
        return f"git reset --hard origin/{branch}"

    merge = _run_git(["merge", "--ff-only", "FETCH_HEAD"], timeout=60)
    if merge.returncode != 0:
        raise UpdateError(
            "无法快进合并（本地可能存在未提交修改）。可在确认后使用强制更新。"
            f" 详情: {merge.stderr.strip()[:300]}"
        )
    return f"git merge --ff-only origin/{branch}"


def _is_protected(relpath: str) -> bool:
    parts = Path(relpath).parts
    if not parts:
        return False
    if any(p in _PROTECTED_ANY for p in parts):
        return True
    if parts[0] in _PROTECTED_TOP:
        return True
    if relpath in _PROTECTED_TOP:
        return True
    if any(relpath.startswith(p) for p in _PROTECTED_PREFIXES):
        return True
    return False


def _safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> Path:
    """安全解压（拒绝路径穿越），返回压缩包顶层目录。"""
    members = tar.getmembers()
    top_dirs = set()
    for member in members:
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise UpdateError("归档包含非法路径，已拒绝")
        # 拒绝符号链接/硬链接/设备文件，防止通过链接写到项目目录之外
        if member.issym() or member.islnk() or member.isdev():
            raise UpdateError("归档包含不安全的链接或设备文件，已拒绝")
        top_dirs.add(Path(name).parts[0])

    dest.mkdir(parents=True, exist_ok=True)
    for member in members:
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise UpdateError("归档路径越界，已拒绝")
        tar.extract(member, path=str(dest))

    if len(top_dirs) != 1:
        raise UpdateError("归档结构异常")
    return dest / top_dirs.pop()


def _copy_update_tree(src: Path, dst: Path) -> int:
    """将源码树覆盖拷贝到项目目录，跳过受保护路径。"""
    copied = 0
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")

        # 过滤受保护目录
        dirs[:] = [d for d in dirs if not _is_protected(f"{rel_root}/{d}".lstrip("/"))]

        for name in files:
            rel = f"{rel_root}/{name}".lstrip("/")
            if _is_protected(rel):
                continue
            src_file = Path(root) / name
            dst_file = dst / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1
    return copied


def _apply_tarball_update(repo: str, branch: str, token: str) -> str:
    url = f"{GITHUB_API}/repos/{repo}/tarball/{branch}"
    logger.info(f"开始下载更新包: {repo}@{branch}")
    try:
        resp = requests.get(url, headers=_headers(token), timeout=120,
                            allow_redirects=True, stream=True)
    except requests.RequestException as e:
        raise UpdateError(f"下载更新包失败: {e.__class__.__name__}") from e
    if resp.status_code >= 400:
        raise UpdateError(f"下载更新包失败: HTTP {resp.status_code}")

    raw = io.BytesIO()
    for chunk in resp.iter_content(chunk_size=1024 * 256):
        if chunk:
            raw.write(chunk)
    raw.seek(0)

    tmp_dir = Path(tempfile.mkdtemp(prefix="xianyu_update_"))
    try:
        with tarfile.open(fileobj=raw, mode="r:gz") as tar:
            src_root = _safe_extract_tar(tar, tmp_dir)
        copied = _copy_update_tree(src_root, PROJECT_ROOT)
        logger.info(f"在线更新完成，覆盖 {copied} 个文件")
        return f"已通过源码包更新（覆盖 {copied} 个文件）"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def apply_update(db_manager, force: bool = False) -> Dict[str, Any]:
    """执行在线更新。

    - Docker 且配置了 Watchtower：触发镜像级更新（可持久化，容器重建后仍为新版本）；
    - 其它 Docker 环境：回退为容器内源码覆盖（仅当前容器有效，重建后会回退）；
    - 非 Docker：git 快进合并或源码包覆盖。
    """
    cfg = get_update_config(db_manager)
    repo, branch, token = cfg["repo"], cfg["branch"], cfg["token"]
    docker = is_docker()

    # Docker 优先走 Watchtower（镜像级、可持久化）
    if docker:
        wt = trigger_watchtower()
        if wt:
            return {
                "success": True,
                "method": "watchtower",
                "repo": repo,
                "branch": branch,
                "detail": "已触发 Watchtower 拉取最新镜像并重建容器（数据卷保持不变）",
                "persistent": True,
                "restart_required": False,
            }

    if _is_git_repo():
        detail = _apply_git_update(branch, force=force)
        method = "git"
        persistent = True
    else:
        detail = _apply_tarball_update(repo, branch, token)
        method = "tarball"
        persistent = not docker

    result: Dict[str, Any] = {
        "success": True,
        "method": method,
        "repo": repo,
        "branch": branch,
        "detail": detail,
        "persistent": persistent,
        "restart_required": True,
    }

    if docker and not persistent:
        result["warning"] = (
            "当前为 Docker 部署且未启用 Watchtower：本次更新仅写入容器可写层，"
            "容器被重建或镜像重新拉取后会回退到镜像版本。"
            "建议启用 Watchtower（docker compose --profile auto-update up -d）"
            "或使用 docker compose pull && docker compose up -d。"
        )

    return result


def schedule_restart(delay: float = 2.0) -> bool:
    """在响应返回后重启进程（Docker/systemd 等由外部策略拉起）。"""
    if os.getenv("XIANYU_DISABLE_RESTART", "").lower() in ("1", "true", "yes"):
        logger.warning("已禁用自动重启（XIANYU_DISABLE_RESTART）")
        return False

    def _restart():
        logger.warning("在线更新完成，进程即将退出以完成重启...")
        time.sleep(0.1)
        os._exit(0)

    threading.Timer(delay, _restart).start()
    return True
