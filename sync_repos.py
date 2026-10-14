"""
GitHub Repository Sync - full bidirectional sync.

  - Clones new GitHub repos, pulls updates for existing ones
  - Commits and pushes local changes
  - Auto-creates GitHub repos for local-only folders
  - Repairs detached-HEAD repos (with forced-checkout fallback)
  - Skips empty repos without error spam
  - Deletes GitHub repos whose local folder the user removed
  - Moves local folders to _trash/ whose GitHub repo no longer exists
  - State tracked in .sync_state.json next to the script

HOW LOCAL DELETIONS WORK:
  Every run, the script snapshots the set of folders present in BASE_DIR
  into state["known_local"]. On the next run, any folder that was in the
  previous snapshot but is now missing is treated as "user deleted it" -
  the script will NOT re-clone it, and will delete the GitHub repo
  instead. This is what makes "delete locally -> delete on GitHub" work.

SAFETY GUARD:
  If a run finds that a suspiciously large fraction of previously-known
  local folders have disappeared at once, the script refuses to run.
  This prevents an unmounted drive (or a moved folder) from being
  interpreted as "user deleted everything" and wiping every GitHub repo.

NOTE ON BASE_DIR:
  BASE_DIR is relative ("repos"). When launched via sync_repos.bat, the
  .bat does `cd /d "%~dp0"` first, so this resolves to
  <folder containing sync_repos.bat>\\repos.

NOTE ON TOKEN:
  GITHUB_TOKEN is read from the environment. sync_repos.bat prompts for
  it on each run, so it is never stored on disk.

REQUIREMENTS:
  - Token must have both 'repo' and 'delete_repo' scopes. Without
    'delete_repo', remote deletions will fail with HTTP 403/404.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Never let git hang on a credential prompt. If auth fails, we want a
# clean non-zero exit so run_git() can log and move on.
os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")

# ============ CONFIGURATION ============
GITHUB_USER = "mahditaharb-maker"

# Token comes from the environment. sync_repos.bat prompts for it and
# sets it before running this script. Never hardcode a token here.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

GIT_USER_NAME = "mahditaharb-maker"
GIT_USER_EMAIL = "mahditaharb@gmail.com"
GIT_EXE = r"D:\PortableGit\bin\git.exe"
BASE_DIR = Path(r"repos")

PUSH_LOCAL_CHANGES = True
AUTO_CREATE_NEW_REPOS = True
NEW_REPOS_PRIVATE = False
MAX_FILE_MB = 500
COMMIT_PREFIX = "Auto-sync"

GIT_RETRIES = 3
GIT_RETRY_DELAY = 6
API_RETRIES = 3
API_RETRY_DELAY = 6
API_TIMEOUT = 30

RECOVERY_BRANCHES = ["main", "master"]

# ---- Deletion / safety settings ----
GRACE_DAYS = 0
DRY_RUN = False
TRASH_DIR = BASE_DIR / "_trash"
ONLY_DELETE_AUTO_CREATED_GITHUB = False
DELETE_LOCAL_WHEN_GITHUB_GONE = True
REQUIRE_DELETE_REPO_SCOPE = True

# ---- Safety guard thresholds ----
SAFETY_MIN_PREV = 3
SAFETY_MIN_MISSING = 2

STATE_FILE = Path(__file__).with_name(".sync_state.json")
# =======================================


def safe_print(text):
    try:
        print(text)
    except Exception:
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def ensure_token_not_tracked():
    here = Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / ".git").exists():
            safe_print("[WARN] %s is inside a git repo (%s)."
                       % (here.name, parent))
            safe_print("       Make sure it is in .gitignore.")
            return


# ---------- Token scope check ----------

def get_token_scopes():
    try:
        req = urllib.request.Request("https://api.github.com/user")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "python-sync-script")
        req.add_header("Authorization", "token " + GITHUB_TOKEN)
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as response:
            scopes = response.headers.get("X-OAuth-Scopes", "")
            return [s.strip() for s in scopes.split(",") if s.strip()]
    except Exception as e:
        safe_print("[WARN] could not read token scopes: %s" % e)
        return None


def verify_token(require_delete_scope):
    if not GITHUB_TOKEN or not GITHUB_TOKEN.startswith("ghp_"):
        safe_print("[FATAL] GITHUB_TOKEN does not look like a valid ghp_ token.")
        safe_print("        Set it via the .bat prompt, or export it manually.")
        return False

    scopes = get_token_scopes()
    if scopes is None:
        safe_print("[WARN] Could not verify token scopes. Proceeding anyway.")
        return True

    safe_print("Token scopes:   %s"
               % (", ".join(scopes) if scopes else "(none reported)"))

    if "repo" not in scopes and "public_repo" not in scopes:
        safe_print("[FATAL] Token is missing 'repo' scope. Add it at")
        safe_print("        https://github.com/settings/tokens")
        return False

    if require_delete_scope and "delete_repo" not in scopes:
        safe_print("[FATAL] Token is missing 'delete_repo' scope.")
        safe_print("        Remote deletions will fail with HTTP 403/404.")
        safe_print("        Fix at https://github.com/settings/tokens")
        safe_print("        Then re-run.")
        return False

    if "delete_repo" not in scopes:
        safe_print("[WARN] 'delete_repo' scope missing - remote deletions")
        safe_print("       will be skipped with an error.")
    return True


# ---------- State ----------

def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.setdefault("local_missing", {})
            data.setdefault("github_missing", {})
            data.setdefault("known_local", [])
            return data
        except Exception:
            pass
    return {"local_missing": {}, "github_missing": {}, "known_local": []}


def save_state(state):
    try:
        STATE_FILE.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception as e:
        safe_print("    [WARN] could not save state: %s" % e)


def days_since(iso_ts):
    try:
        then = datetime.fromisoformat(iso_ts)
    except Exception:
        return 0
    return (datetime.now() - then).days


def list_local_folder_names():
    if not BASE_DIR.exists():
        return set()
    names = set()
    for entry in BASE_DIR.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name == TRASH_DIR.name:
            continue
        names.add(entry.name)
    return names


def safety_guard(prev_known, now_present, user_deleted):
    prev_count = len(prev_known)
    missing_count = len(user_deleted)

    if prev_count < SAFETY_MIN_PREV:
        return True
    if missing_count < SAFETY_MIN_MISSING:
        return True

    half = max(SAFETY_MIN_MISSING, prev_count // 2)
    if missing_count < half:
        return True

    safe_print("")
    safe_print("!" * 60)
    safe_print("[ABORT] Safety guard triggered.")
    safe_print("!" * 60)
    safe_print("  Previous run saw %d local folder(s)." % prev_count)
    safe_print("  This run sees %d missing (>= half)." % missing_count)
    safe_print("")
    safe_print("  Missing: %s" % ", ".join(sorted(user_deleted)))
    safe_print("")
    safe_print("  This pattern almost always means:")
    safe_print("    - The drive or network location is not mounted,")
    safe_print("    - or the parent folder was renamed/moved,")
    safe_print("    - or a cloud-sync client hid the folder temporarily.")
    safe_print("")
    safe_print("  Refusing to run. Nothing has been deleted or changed.")
    safe_print("")
    safe_print("  If you really did delete these folders:")
    safe_print("    1. Open %s" % STATE_FILE.name)
    safe_print("    2. Remove the corresponding names from 'known_local'")
    safe_print("    3. Re-run this script.")
    safe_print("")
    safe_print("  To bypass this one time, set SAFETY_MIN_PREV = 9999 at")
    safe_print("  the top of this file (not recommended).")
    safe_print("!" * 60)
    safe_print("")
    return False


# ---------- GitHub API ----------

def api_request(url, method="GET", payload=None, allow_404=False):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    last_error = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("User-Agent", "python-sync-script")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if GITHUB_TOKEN:
                req.add_header("Authorization", "token " + GITHUB_TOKEN)
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404 and allow_404:
                return None
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last_error = e
            if attempt < API_RETRIES:
                safe_print("    (API attempt %d/%d failed, retrying in %ds...)"
                           % (attempt, API_RETRIES, API_RETRY_DELAY))
                time.sleep(API_RETRY_DELAY)
        except Exception as e:
            last_error = e
            if attempt < API_RETRIES:
                safe_print("    (API attempt %d/%d failed, retrying in %ds...)"
                           % (attempt, API_RETRIES, API_RETRY_DELAY))
                time.sleep(API_RETRY_DELAY)
    raise last_error


def get_all_repos():
    repos = []
    page = 1
    while True:
        if GITHUB_TOKEN:
            url = "https://api.github.com/user/repos"
        else:
            url = "https://api.github.com/users/%s/repos" % GITHUB_USER
        url += "?per_page=100&page=%d&sort=updated&affiliation=owner,collaborator" % page
        try:
            data = api_request(url)
        except Exception as e:
            safe_print("[ERROR] Failed to fetch page %d: %s" % (page, e))
            break
        if not data:
            break
        for r in data:
            repos.append({
                "name": r["name"],
                "clone_url": r["clone_url"],
                "description": r.get("description") or "",
                "fork": bool(r.get("fork")),
                "private": bool(r.get("private")),
            })
        if len(data) < 100:
            break
        page += 1
    return repos


def create_github_repo(name):
    url = "https://api.github.com/user/repos"
    payload = {
        "name": name,
        "private": NEW_REPOS_PRIVATE,
        "auto_init": False,
        "description": "Auto-created by sync script",
    }
    try:
        api_request(url, method="POST", payload=payload)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return True
        safe_print("    [ERROR] create repo failed: HTTP %d %s"
                   % (e.code, e.reason))
        return False
    except Exception as e:
        safe_print("    [ERROR] create repo failed: %s" % e)
        return False


def delete_github_repo(name):
    url = "https://api.github.com/repos/%s/%s" % (GITHUB_USER, name)
    try:
        api_request(url, method="DELETE", allow_404=True)
        return True, 204, "deleted"
    except urllib.error.HTTPError as e:
        msg = "HTTP %d %s" % (e.code, e.reason)
        if e.code == 403:
            msg += " -- token likely missing 'delete_repo' scope"
        elif e.code == 404:
            msg += " -- token missing scope OR repo already gone"
        return False, e.code, msg
    except Exception as e:
        return False, 0, str(e)


def repos_from_local_folders():
    results = []
    if not BASE_DIR.exists():
        return results
    for entry in BASE_DIR.iterdir():
        if entry.is_dir() and (entry / ".git").exists():
            results.append({
                "name": entry.name,
                "clone_url": None,
                "description": "",
                "fork": False,
                "private": True,
            })
    return results


# ---------- Git helpers ----------

def run_git(args, cwd=None, retries=GIT_RETRIES, delay=GIT_RETRY_DELAY, quiet=False):
    for attempt in range(1, retries + 1):
        try:
            stdout = subprocess.DEVNULL if quiet else None
            stderr = subprocess.DEVNULL if quiet else None
            result = subprocess.call(
                [GIT_EXE] + args, cwd=cwd,
                stdout=stdout, stderr=stderr,
            )
            if result == 0:
                return True
            if attempt < retries:
                if not quiet:
                    safe_print("      attempt %d failed, retrying in %ds..."
                               % (attempt, delay))
                time.sleep(delay)
        except Exception as e:
            if not quiet:
                safe_print("[ERROR] git %s failed: %s" % (" ".join(args), e))
            if attempt < retries:
                time.sleep(delay)
    return False


def git_output(args, cwd=None):
    try:
        result = subprocess.run(
            [GIT_EXE] + args, cwd=cwd,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def inject_token(url):
    if not GITHUB_TOKEN or not url:
        return url
    if url.startswith("https://github.com/"):
        return url.replace(
            "https://github.com/",
            "https://%s:%s@github.com/" % (GITHUB_USER, GITHUB_TOKEN),
        )
    return url


def clean_remote_url(url):
    if not url:
        return url
    return re.sub(r"://[^/@]+@", "://", url)


def get_remote_url(repo_path):
    return git_output(["remote", "get-url", "origin"], cwd=str(repo_path))


def set_remote_url(repo_path, url):
    if get_remote_url(repo_path):
        run_git(["remote", "set-url", "origin", url],
                cwd=str(repo_path), quiet=True)
    else:
        run_git(["remote", "add", "origin", url],
                cwd=str(repo_path), quiet=True)


def configure_git_identity(repo_path):
    run_git(["config", "user.name", GIT_USER_NAME], cwd=str(repo_path), quiet=True)
    run_git(["config", "user.email", GIT_USER_EMAIL], cwd=str(repo_path), quiet=True)


def is_valid_repo(repo_path):
    if not (repo_path / ".git").exists():
        return False
    return git_output(["rev-parse", "--git-dir"], cwd=str(repo_path)) is not None


def get_current_branch(repo_path):
    branch = git_output(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_path))
    return branch if branch and branch != "HEAD" else None


def has_upstream(repo_path):
    return git_output(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=str(repo_path),
    ) is not None


def count_commits_ahead(repo_path):
    out = git_output(["rev-list", "--count", "@{u}..HEAD"], cwd=str(repo_path))
    try:
        return int(out) if out else 0
    except ValueError:
        return 0


def count_commits_behind(repo_path):
    out = git_output(["rev-list", "--count", "HEAD..@{u}"], cwd=str(repo_path))
    try:
        return int(out) if out else 0
    except ValueError:
        return 0


def get_status_short(repo_path):
    out = git_output(["status", "--porcelain"], cwd=str(repo_path))
    if out is None:
        return []
    return [l for l in out.split("\n") if l.strip()]


def find_oversized_files(repo_path, max_mb=MAX_FILE_MB):
    if max_mb is None:
        return []
    max_bytes = max_mb * 1024 * 1024
    oversized = []
    out = git_output(["ls-files", "-co", "--exclude-standard"], cwd=str(repo_path))
    if out is None:
        return oversized
    for rel in out.split("\n"):
        rel = rel.strip()
        if not rel:
            continue
        full = repo_path / rel
        try:
            if full.is_file():
                size = full.stat().st_size
                if size > max_bytes:
                    oversized.append((rel, size / (1024 * 1024)))
        except OSError:
            pass
    return oversized


def branch_has_commits(repo_path, branch):
    out = git_output(["rev-list", "-n", "1", branch], cwd=str(repo_path))
    return bool(out)


# ---------- Detached HEAD recovery ----------

def list_remote_branches(repo_path):
    out = git_output(
        ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
        cwd=str(repo_path),
    )
    if not out:
        return []
    branches = []
    for line in out.split("\n"):
        line = line.strip()
        if not line or line.endswith("/HEAD"):
            continue
        if line.startswith("origin/"):
            branches.append(line[len("origin/"):])
    return branches


def recover_detached_head(repo_path):
    run_git(["fetch", "--all", "--prune"], cwd=str(repo_path), quiet=True)
    remote_branches = list_remote_branches(repo_path)

    candidates = []
    for name in ("main", "master"):
        if git_output(["rev-parse", "--verify", "refs/heads/" + name],
                      cwd=str(repo_path)):
            candidates.append(("local", name))
    for name in ("main", "master"):
        if name in remote_branches:
            candidates.append(("remote", name))
    for name in remote_branches:
        if name not in ("main", "master"):
            candidates.append(("remote", name))

    for kind, name in candidates:
        ref = name if kind == "local" else "origin/" + name

        if run_git(["checkout", name] if kind == "local"
                   else ["checkout", "-B", name, ref],
                   cwd=str(repo_path), quiet=True):
            safe_print("    [recover] checked out '%s' (%s)" % (name, kind))
            return name

        safe_print("    [recover] gentle checkout of '%s' failed; "
                   "stashing + forcing" % name)
        run_git(["stash", "push", "-u", "-m",
                 "auto-sync detached-recovery stash"],
                cwd=str(repo_path), quiet=True)

        if run_git(["checkout", "-f", name] if kind == "local"
                   else ["checkout", "-f", "-B", name, ref],
                   cwd=str(repo_path), quiet=True):
            safe_print("    [recover] force-checked out '%s' (%s) - prior "
                       "uncommitted work saved in 'git stash'" % (name, kind))
            return name

    if not remote_branches and not git_output(
        ["rev-list", "-n", "1", "--all"], cwd=str(repo_path)
    ):
        if run_git(["checkout", "-B", "main"], cwd=str(repo_path), quiet=True):
            safe_print("    [recover] empty repo - created 'main'")
            return "main"

    safe_print("    [recover] FAILED - no usable branch found")
    return None


# ---------- Sync one existing repo ----------

def sync_repo(name, clone_url):
    repo_path = BASE_DIR / name

    if not clone_url:
        existing_url = get_remote_url(repo_path)
        if not existing_url:
            safe_print("  [skip]   %s - no remote URL found" % name)
            return
        clone_url = clean_remote_url(existing_url)
    auth_url = inject_token(clone_url)

    if not is_valid_repo(repo_path):
        if repo_path.exists():
            safe_print("  [clean]  removing broken folder for %s" % name)
            try:
                shutil.rmtree(str(repo_path))
            except Exception as e:
                safe_print("    [WARN] could not remove: %s" % e)
                return
        safe_print("  [clone]  %s" % name)
        if run_git(["clone", auth_url, str(repo_path)]):
            configure_git_identity(repo_path)
            safe_print("    cloned")
        return

    if get_remote_url(repo_path) != auth_url:
        set_remote_url(repo_path, auth_url)

    configure_git_identity(repo_path)

    branch = get_current_branch(repo_path)

    if not branch:
        run_git(["fetch", "--all"], cwd=str(repo_path), quiet=True)
        if not list_remote_branches(repo_path) and not git_output(
            ["rev-list", "-n", "1", "--all"], cwd=str(repo_path)
        ):
            safe_print("  [skip]   %s - empty repo" % name)
            return

        safe_print("  [sync]   %s (detached HEAD - attempting recovery)" % name)
        branch = recover_detached_head(repo_path)
        if not branch:
            safe_print("  [skip]   %s - recovery failed" % name)
            return

    safe_print("  [sync]   %s (branch: %s)" % (name, branch))

    status_lines = get_status_short(repo_path)
    if status_lines and PUSH_LOCAL_CHANGES:
        oversized = find_oversized_files(repo_path)

        if oversized:
            safe_print("    [BLOCKED] %d file(s) over %d MB:"
                       % (len(oversized), MAX_FILE_MB))
            for path, size in oversized[:5]:
                safe_print("      %.1f MB  %s" % (size, path))
            safe_print("    Not committing. Add them to .gitignore.")
        else:
            safe_print("    local changes: %d file(s)" % len(status_lines))
            for line in status_lines[:8]:
                safe_print("      %s" % line)
            if len(status_lines) > 8:
                safe_print("      ... and %d more" % (len(status_lines) - 8))

            if not run_git(["add", "-A"], cwd=str(repo_path)):
                safe_print("    [ERROR] git add failed")
                return
            msg = "%s: %s" % (COMMIT_PREFIX, time.strftime("%Y-%m-%d %H:%M:%S"))
            if run_git(["commit", "-m", msg], cwd=str(repo_path), quiet=True):
                safe_print("    committed: %s" % msg)
            else:
                safe_print("    [ERROR] commit failed")
                return

    fetch_ok = run_git(["fetch"], cwd=str(repo_path), quiet=True)
    if not fetch_ok:
        safe_print("    [WARN] fetch failed - continuing anyway")

    if not branch_has_commits(repo_path, branch):
        safe_print("    [skip] branch '%s' has no commits (empty repo)"
                   % branch)
        return

    if not has_upstream(repo_path):
        safe_print("    setting upstream for '%s'" % branch)
        if run_git(["push", "-u", "origin", branch], cwd=str(repo_path)):
            safe_print("    pushed (new upstream)")
        else:
            safe_print("    [ERROR] could not set upstream")
        return

    ahead = count_commits_ahead(repo_path)
    behind = count_commits_behind(repo_path)

    if ahead > 0:
        if behind > 0 and fetch_ok:
            safe_print("    diverged: %d ahead / %d behind - rebasing"
                       % (ahead, behind))
            if not run_git(["pull", "--rebase"], cwd=str(repo_path)):
                safe_print("    [CONFLICT] rebase failed - aborting cleanly")
                run_git(["rebase", "--abort"], cwd=str(repo_path), quiet=True)
                safe_print("    To resolve manually:")
                safe_print("      cd %s" % repo_path)
                safe_print("      git pull --rebase")
                return
        if run_git(["push"], cwd=str(repo_path)):
            safe_print("    pushed %d commit(s)" % ahead)
        else:
            safe_print("    [ERROR] push failed")
    elif behind > 0:
        if fetch_ok:
            safe_print("    pulling %d commit(s)" % behind)
            run_git(["pull", "--ff-only"], cwd=str(repo_path))
        else:
            safe_print("    [WARN] behind %d commit(s) but fetch failed - "
                       "will retry next run" % behind)
    else:
        safe_print("    up to date")


# ---------- Sync local-only folders ----------

def sync_local_only_folder(folder_path, github_names):
    name = folder_path.name

    if name.startswith(".") or name.startswith("-"):
        return False
    if name in github_names:
        return False

    safe_print("  [new]    %s (local-only, not on GitHub)" % name)

    if not (folder_path / ".git").exists():
        safe_print("    initializing git repo")
        if not run_git(["init"], cwd=str(folder_path), quiet=True):
            safe_print("    [ERROR] git init failed")
            return False

    configure_git_identity(folder_path)
    run_git(["branch", "-M", "main"], cwd=str(folder_path), quiet=True)

    oversized = find_oversized_files(folder_path)
    if oversized:
        safe_print("    [BLOCKED] %d file(s) over %d MB:"
                   % (len(oversized), MAX_FILE_MB))
        for path, size in oversized[:5]:
            safe_print("      %.1f MB  %s" % (size, path))
        safe_print("    Skipping this folder.")
        return False

    safe_print("    creating GitHub repo '%s'..." % name)
    if not create_github_repo(name):
        safe_print("    [ERROR] could not create GitHub repo")
        return False

    remote_url = "https://%s:%s@github.com/%s/%s.git" % (
        GITHUB_USER, GITHUB_TOKEN, GITHUB_USER, name
    )
    set_remote_url(folder_path, remote_url)

    if not run_git(["add", "-A"], cwd=str(folder_path)):
        safe_print("    [ERROR] git add failed")
        return False

    staged = git_output(["diff", "--cached", "--name-only"],
                        cwd=str(folder_path))
    if staged:
        msg = "%s: initial import %s" % (
            COMMIT_PREFIX, time.strftime("%Y-%m-%d %H:%M:%S")
        )
        if not run_git(["commit", "-m", msg], cwd=str(folder_path), quiet=True):
            safe_print("    [ERROR] commit failed")
            return False
        safe_print("    committed: %s" % msg)
    else:
        safe_print("    nothing to commit (folder is empty)")

    if not branch_has_commits(folder_path, "main"):
        safe_print("    [skip] no commits to push")
        return False

    safe_print("    pushing to GitHub...")
    if run_git(["push", "-u", "origin", "main"], cwd=str(folder_path)):
        safe_print("    pushed successfully")
        return True

    safe_print("    [ERROR] push failed")
    return False


def sync_all_local_only(github_names):
    if not BASE_DIR.exists():
        return 0
    created = 0
    for entry in sorted(BASE_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == TRASH_DIR.name:
            continue
        if sync_local_only_folder(entry, github_names):
            github_names.add(entry.name)   # so phase 3 knows about it
            created += 1
    return created


# ---------- Deletion: local folder gone -> delete GitHub repo ----------

def move_to_trash(folder_path):
    try:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        safe_print("    [ERROR] cannot create trash dir: %s" % e)
        return False
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = TRASH_DIR / ("%s-%s" % (folder_path.name, stamp))
    try:
        shutil.move(str(folder_path), str(dest))
        safe_print("    moved to trash: %s" % dest)
        return True
    except Exception as e:
        safe_print("    [ERROR] could not move to trash: %s" % e)
        return False


def handle_missing_local(repos_from_github, state, user_deleted):
    deleted = []
    seen_names = set()
    changed = False
    scope_errors = 0

    for repo in repos_from_github:
        name = repo["name"]
        seen_names.add(name)

        if (BASE_DIR / name).exists():
            if name in state["local_missing"]:
                del state["local_missing"][name]
                changed = True
            continue

        if name not in user_deleted:
            continue

        if repo.get("fork"):
            continue

        if ONLY_DELETE_AUTO_CREATED_GITHUB:
            if not (repo.get("description") or "").startswith(
                "Auto-created by sync script"
            ):
                safe_print("  [keep]   %s - local folder missing but repo was "
                           "not auto-created (skipping)" % name)
                continue

        rec = state["local_missing"].get(name)
        if rec is None:
            state["local_missing"][name] = {
                "first_seen": datetime.now().isoformat(timespec="seconds"),
                "runs_missing": 1,
            }
            changed = True
            if GRACE_DAYS > 0:
                safe_print("  [missing] %s - local folder gone, starting "
                           "grace period (%d day(s))" % (name, GRACE_DAYS))
                continue
            rec = state["local_missing"][name]
        else:
            rec["runs_missing"] = rec.get("runs_missing", 1) + 1
            changed = True

        if days_since(rec["first_seen"]) < GRACE_DAYS:
            safe_print("  [missing] %s - local folder gone for %d day(s) "
                       "(grace %d) - waiting"
                       % (name, days_since(rec["first_seen"]), GRACE_DAYS))
            continue

        safe_print("  [delete]  %s - deleting GitHub repo (user removed "
                   "local folder)" % name)

        if DRY_RUN:
            safe_print("    [DRY-RUN] would call DELETE /repos/%s/%s"
                       % (GITHUB_USER, name))
            deleted.append(name)
            continue

        ok, code, msg = delete_github_repo(name)
        if ok:
            safe_print("    deleted on GitHub")
            deleted.append(name)
            state["local_missing"].pop(name, None)
            changed = True
        else:
            safe_print("    [ERROR] delete failed: %s" % msg)
            if code in (403, 404):
                scope_errors += 1

    for name in list(state["local_missing"].keys()):
        if name not in seen_names:
            del state["local_missing"][name]
            changed = True

    if scope_errors:
        safe_print("")
        safe_print("[HELP] %d deletion(s) failed with 403/404." % scope_errors)
        safe_print("       Your token is missing the 'delete_repo' scope.")
        safe_print("       Fix it at https://github.com/settings/tokens")

    return len(deleted), deleted


# ---------- Deletion: GitHub repo gone -> delete local folder ----------

def handle_missing_github(github_names, state):
    if not DELETE_LOCAL_WHEN_GITHUB_GONE:
        return 0, []

    deleted = []
    changed = False

    if not BASE_DIR.exists():
        return 0, []

    for entry in sorted(BASE_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name == TRASH_DIR.name:
            continue
        if entry.name in github_names:
            if entry.name in state["github_missing"]:
                del state["github_missing"][entry.name]
                changed = True
            continue

        if not (entry / ".git").exists():
            continue

        rec = state["github_missing"].get(entry.name)
        if rec is None:
            state["github_missing"][entry.name] = {
                "first_seen": datetime.now().isoformat(timespec="seconds"),
                "runs_missing": 1,
            }
            changed = True
            if GRACE_DAYS > 0:
                safe_print("  [missing] %s - GitHub repo gone, starting "
                           "grace period (%d day(s))"
                           % (entry.name, GRACE_DAYS))
                continue
            rec = state["github_missing"][entry.name]
        else:
            rec["runs_missing"] = rec.get("runs_missing", 1) + 1
            changed = True

        if days_since(rec["first_seen"]) < GRACE_DAYS:
            safe_print("  [missing] %s - GitHub repo gone for %d day(s) "
                       "(grace %d) - waiting"
                       % (entry.name, days_since(rec["first_seen"]), GRACE_DAYS))
            continue

        safe_print("  [delete]  %s - moving local folder to trash (GitHub "
                   "repo gone for %d day(s))"
                   % (entry.name, days_since(rec["first_seen"])))

        if DRY_RUN:
            safe_print("    [DRY-RUN] would move %s to %s"
                       % (entry, TRASH_DIR))
            deleted.append(entry.name)
            continue

        if move_to_trash(entry):
            deleted.append(entry.name)
            state["github_missing"].pop(entry.name, None)
            changed = True

    for name in list(state["github_missing"].keys()):
        if (BASE_DIR / name).exists():
            del state["github_missing"][name]
            changed = True

    return len(deleted), deleted


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted, take no action")
    parser.add_argument("--no-delete", action="store_true",
                        help="Skip all deletion steps entirely")
    parser.add_argument("--skip-scope-check", action="store_true",
                        help="Do not verify token scopes at startup")
    args = parser.parse_args()

    global DRY_RUN
    dry_run = DRY_RUN or args.dry_run
    no_delete = args.no_delete

    safe_print("=" * 60)
    safe_print(" GitHub Repository Sync (full bidirectional)")
    safe_print("=" * 60)
    safe_print("Using git:       %s" % GIT_EXE)
    safe_print("Base directory:  %s" % (BASE_DIR.resolve()
                                        if BASE_DIR.exists() else BASE_DIR))
    safe_print("Push local:      %s" % ("ON" if PUSH_LOCAL_CHANGES else "OFF"))
    safe_print("Auto-create:     %s" % ("ON" if AUTO_CREATE_NEW_REPOS else "OFF"))
    safe_print("Delete remote:   %s" % ("DISABLED" if no_delete else
                                        ("DRY-RUN" if dry_run else "ON")))
    safe_print("Delete local:    %s" % ("DISABLED" if no_delete else
                                        ("DRY-RUN" if dry_run else
                                         ("ON" if DELETE_LOCAL_WHEN_GITHUB_GONE
                                          else "OFF"))))
    safe_print("Grace period:    %d day(s)" % GRACE_DAYS)
    size_label = "unlimited" if MAX_FILE_MB is None else ("%d MB" % MAX_FILE_MB)
    safe_print("Max file size:   %s" % size_label)
    ensure_token_not_tracked()
    safe_print("")

    if not args.skip_scope_check and not no_delete:
        if not verify_token(require_delete_scope=REQUIRE_DELETE_REPO_SCOPE):
            safe_print("")
            safe_print("Aborting. Fix the token or re-run with --skip-scope-check.")
            return 1
        safe_print("")

    if not Path(GIT_EXE).exists():
        safe_print("[FATAL] git.exe not found at %s" % GIT_EXE)
        return 1

    state = load_state()
    prev_known = set(state.get("known_local", []))
    now_present = list_local_folder_names()
    user_deleted = prev_known - now_present

    if prev_known:
        safe_print("Previous snapshot: %d local folder(s)" % len(prev_known))
        safe_print("Currently present: %d local folder(s)" % len(now_present))
        if user_deleted:
            safe_print("Missing since last run: %s"
                       % ", ".join(sorted(user_deleted)))
        safe_print("")

    if not safety_guard(prev_known, now_present, user_deleted):
        return 1

    if not BASE_DIR.exists():
        BASE_DIR.mkdir(parents=True)
        safe_print("Created %s" % BASE_DIR.resolve())

    safe_print("Fetching repository list for '%s'..." % GITHUB_USER)
    try:
        repos = get_all_repos()
    except Exception as e:
        safe_print("[FATAL] %s" % e)
        return 1

    api_ok = bool(repos)

    if not repos:
        safe_print("")
        safe_print("[FALLBACK] Using local folder scan instead of API.")
        repos = repos_from_local_folders()
        if not repos:
            safe_print("[FATAL] No local repos found either.")
            return 1

    github_names = set(r["name"] for r in repos)
    safe_print("Found %d repositories.\n" % len(repos))

    succeeded = 0
    failed = []
    for repo in repos:
        name = repo["name"]
        if name in user_deleted:
            safe_print("  [skip]   %s - user deleted locally, will be "
                       "deleted on GitHub below" % name)
            continue
        try:
            sync_repo(name, repo["clone_url"])
            succeeded += 1
        except KeyboardInterrupt:
            safe_print("\n[ABORT] interrupted by user.")
            save_state(state)
            return 3
        except Exception as e:
            safe_print("  [FAIL]   %s -- %s" % (name, e))
            failed.append(name)

    new_repos_created = 0
    if api_ok and AUTO_CREATE_NEW_REPOS:
        safe_print("")
        safe_print("Scanning for local-only folders...")
        try:
            new_repos_created = sync_all_local_only(github_names)
        except KeyboardInterrupt:
            safe_print("\n[ABORT] interrupted by user.")
            save_state(state)
            return 3

    deleted_remote = []
    deleted_local = []

    if not no_delete:
        safe_print("")
        safe_print("Checking for local folders that no longer exist on disk...")
        try:
            n, names = handle_missing_local(repos, state, user_deleted)
            deleted_remote = names
        except KeyboardInterrupt:
            safe_print("\n[ABORT] interrupted by user.")
            save_state(state)
            return 3

        safe_print("")
        safe_print("Checking for GitHub repos that no longer exist remotely...")
        try:
            n, names = handle_missing_github(github_names, state)
            deleted_local = names
        except KeyboardInterrupt:
            safe_print("\n[ABORT] interrupted by user.")
            save_state(state)
            return 3
    else:
        safe_print("")
        safe_print("Deletion phase skipped (--no-delete).")

    state["known_local"] = sorted(list_local_folder_names())
    save_state(state)

    total = len(repos) + new_repos_created
    total_succeeded = succeeded + new_repos_created

    safe_print("\n" + "=" * 60)
    safe_print(" Sync complete. %d/%d repositories processed."
               % (total_succeeded, total))
    if new_repos_created:
        safe_print(" New GitHub repos created: %d" % new_repos_created)
    if deleted_remote:
        safe_print(" GitHub repos %s: %d"
                   % ("to delete (dry-run)" if dry_run else "deleted",
                      len(deleted_remote)))
        for n in deleted_remote:
            safe_print("   - %s" % n)
    if deleted_local:
        safe_print(" Local folders %s: %d"
                   % ("to trash (dry-run)" if dry_run else "moved to trash",
                      len(deleted_local)))
        for n in deleted_local:
            safe_print("   - %s" % n)
    if failed:
        safe_print(" Failed: %s" % ", ".join(failed))
        safe_print(" Re-run the script to retry only the failed ones.")
    safe_print(" Local folders in snapshot: %d" % len(state["known_local"]))
    safe_print(" Location: %s" % (BASE_DIR.resolve()
                                   if BASE_DIR.exists() else BASE_DIR))
    safe_print("=" * 60)
    return 0 if not failed else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        safe_print("\n[ABORT] cancelled.")
        sys.exit(130)