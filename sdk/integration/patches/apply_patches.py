#!/usr/bin/env python3
"""
Apply the aauth_sdk integration patches to a freshly cloned copy of
christian-posta/aauth-full-demo.

Called from scripts/06-deploy-apps.sh after the upstream clone. Idempotent —
safe to run multiple times.

Strategy (deliberately simple for a demo):
  - Replace the upstream `aauth_interceptor.py` file with a thin stub that
    imports from aauth_sdk and exposes the symbols the entrypoint expects.
  - Prepend a few lines to each agent's entrypoint to construct an Agent
    and mount endpoints + middleware.
  - Drop in an enroll-on-boot startup hook.

Files modified:
  backend/app/main.py
  backend/app/services/aauth_interceptor.py  (replaced)
  supply-chain-agent/__main__.py
  supply-chain-agent/aauth_interceptor.py    (replaced)
  market-analysis-agent/__main__.py
  market-analysis-agent/aauth_interceptor.py (replaced)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from textwrap import dedent

# ---------- patch templates -------------------------------------------------- #

STUB_INTERCEPTOR = dedent(
    '''
    """
    aauth_interceptor — compatibility shim.

    The original hand-rolled interceptor has been replaced by aauth_sdk.
    This stub re-exports the symbols the upstream code still imports, so
    upstream changes that touch the entrypoint compile without manual
    rewrites. See sdk/python/README.md for the new API.
    """
    from aauth_sdk import Agent, MissionMiddleware  # noqa: F401

    def sign_outbound(*args, **kwargs):
        raise RuntimeError("sign_outbound() is replaced by Agent.client(...) — see sdk/python/README.md")

    def verify_inbound(*args, **kwargs):
        raise RuntimeError("verify_inbound() is replaced by Agent.verifier().verify(...) — see sdk/python/README.md")
    '''
).lstrip()


BOOT_BLOCK_TEMPLATE = dedent(
    '''
    # ---- aauth_sdk boot wiring (added by sdk/integration/patches/apply_patches.py) ----
    from aauth_sdk import Agent, MissionMiddleware

    agent = Agent.from_env()
    # ----------------------------------------------------------------------------------
    '''
).strip("\n")


APP_WIRING_TEMPLATE = dedent(
    '''
    # ---- aauth_sdk app wiring (added by apply_patches.py) ----
    {app_var}.add_middleware(MissionMiddleware)
    agent.mount_endpoints({app_var})

    @{app_var}.on_event("startup")
    async def _aauth_boot() -> None:
        await agent.enroll()
    # ----------------------------------------------------------
    '''
).strip("\n")


# ---------- helpers --------------------------------------------------------- #

def _strip_app_wiring(src: str) -> str:
    """Remove previous app-wiring blocks, including the old unindented variant."""
    start_marker = "# ---- aauth_sdk app wiring (added by apply_patches.py) ----"
    end_marker = "# ----------------------------------------------------------"
    while start_marker in src:
        start = src.find(start_marker)
        line_start = src.rfind("\n", 0, start) + 1
        end = src.find(end_marker, start)
        if end < 0:
            break
        line_end = src.find("\n", end)
        if line_end < 0:
            line_end = len(src)
        src = src[:line_start] + src[line_end + 1 :]
    return src


def _indent_block(block: str, indent: str) -> str:
    return "\n".join((indent + line) if line else "" for line in block.splitlines())


def _find_call_end(src: str, open_paren: int) -> int:
    depth = 0
    in_string: str | None = None
    escaped = False
    for i in range(open_paren, len(src)):
        ch = src[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in ("'", '"'):
            in_string = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise RuntimeError("could not find closing ')' for FastAPI(...)")

def _insert_after_app_construct(src: str, block: str) -> str:
    """Insert `block` after the first FastAPI construction, preserving scope."""
    src = _strip_app_wiring(src)
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*FastAPI\s*\(",
        re.MULTILINE,
    )
    m = pattern.search(src)
    if not m:
        raise RuntimeError("could not find `<name> = FastAPI(...)` to anchor wiring")

    open_paren = src.find("(", m.start())
    end = _find_call_end(src, open_paren)
    line_end = src.find("\n", end)
    if line_end < 0:
        line_end = end

    rendered = block.format(app_var=m.group("var"))
    rendered = _indent_block(rendered, m.group("indent"))
    return src[:line_end] + "\n\n" + rendered + src[line_end:]


def patch_file(path: Path, *, add_boot: bool, add_app_wiring: bool) -> None:
    if not path.exists():
        print(f"[skip] {path} does not exist (upstream layout may have changed)")
        return
    src = path.read_text()
    original = src
    if add_boot:
        src = _insert_after_imports(src, BOOT_BLOCK_TEMPLATE)
    if add_app_wiring:
        src = _insert_after_app_construct(src, APP_WIRING_TEMPLATE)
    if src != original:
        path.write_text(src)
        print(f"[patched] {path}")
    else:
        print(f"[unchanged] {path} (already patched or no anchor found)")


def replace_interceptor(path: Path) -> None:
    if not path.exists():
        print(f"[skip] interceptor at {path} does not exist (already removed?)")
        return
    path.write_text(STUB_INTERCEPTOR)
    print(f"[stubbed] {path}")
  
def patch_first_existing(paths: list[Path], *, add_boot: bool, add_app_wiring: bool) -> None:
    for path in paths:
        if path.exists():
            patch_file(path, add_boot=add_boot, add_app_wiring=add_app_wiring)
            return
    print(f"[skip] none of these entrypoints exist: {', '.join(str(p) for p in paths)}")


# ---------- main ------------------------------------------------------------ #

def main(repo_root: Path) -> int:
    if not repo_root.is_dir():
        print(f"error: {repo_root} is not a directory", file=sys.stderr)
        return 2

    # Backend
    patch_file(
        repo_root / "backend" / "app" / "main.py",
        add_boot=True, add_app_wiring=True,
    )
    replace_interceptor(repo_root / "backend" / "app" / "services" / "aauth_interceptor.py")

    # Supply chain agent
   patch_first_existing(
        [
            repo_root / "supply-chain-agent" / "__main__.py",
            repo_root / "supply-chain-agent" / "_main_.py",
        ],
        add_boot=True, add_app_wiring=True,
    )
    replace_interceptor(repo_root / "supply-chain-agent" / "aauth_interceptor.py")

    # Market analysis agent
    patch_first_existing(
        [
            repo_root / "market-analysis-agent" / "__main__.py",
            repo_root / "market-analysis-agent" / "_main_.py",
        ],
        add_boot=True, add_app_wiring=True,
    )
    replace_interceptor(repo_root / "market-analysis-agent" / "aauth_interceptor.py")

    print("done — agent code patched for aauth_sdk")
    print("  next: outbound call sites still need the hand edits described in")
    print("        sdk/integration/{backend,supply-chain-agent,market-analysis-agent}.md")
    print("        (the call-site rewrites are too varied to script reliably)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("repo_root", help="path to a fresh clone of aauth-full-demo")
    args = p.parse_args()
    sys.exit(main(Path(args.repo_root)))
