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

    class AAuthSigningInterceptor:
        def __init__(self, *args, **kwargs):
            pass

     async def intercept(self, method_name, request_payload, http_kwargs, agent_card=None, context=None):
            return request_payload, http_kwargs

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
    app.add_middleware(MissionMiddleware)
    agent.mount_endpoints(app)

    @app.on_event("startup")
    async def _aauth_boot() -> None:
        await agent.enroll()
    # ----------------------------------------------------------
    '''
).strip("\n")


STARLETTE_WIRING_TEMPLATE = dedent(
    '''
    # ---- aauth_sdk app wiring (added by apply_patches.py) ----
    from starlette.responses import JSONResponse

    async def _aauth_healthz(request):
        return JSONResponse({"status": "ok"})

    async def _aauth_boot() -> None:
        await agent.enroll()

    app.add_route("/healthz", _aauth_healthz)
    app.add_event_handler("startup", _aauth_boot)
    # ----------------------------------------------------------
    '''
).strip("\n")

# ---------- helpers --------------------------------------------------------- #

def _insert_after_imports(src: str, block: str) -> str:
    """Insert `block` after the import block at the top of the file."""
    if block in src:
        return src
    lines = src.splitlines()
    last_import_line = 0
    for i, line in enumerate(lines[:120]):  # only look near the top
        if re.match(r"^(from\s+\S+\s+import\s+|import\s+\S+)", line):
            last_import_line = i
    inject_at = last_import_line + 1
    return "\n".join(lines[:inject_at] + [""] + block.splitlines() + [""] + lines[inject_at:])


def _insert_after_app_construct(src: str, block: str) -> str:
    """Insert `block` after the first `app = FastAPI(...)` line."""
   if block in src or "aauth_sdk app wiring" in src:
      return _patch_lifespan_enroll(src)
from aauth_sdk import Agent, MissionMiddleware  # noqa: F401

    class AAuthSigningInterceptor:
        def __init__(self, *args, **kwargs):
            pass

        async def intercept(self, request_payload, http_kwargs):
            return request_payload, http_kwargs

    def sign_outbound(*args, **kwargs):
        raise RuntimeError("sign_outbound() is replaced by Agent.client(...) â€” see sdk/python/README.md")
    pattern = re.compile(r"^app\s*=\s*FastAPI\s*\(.*?\)\s*$", re.MULTILINE | re.DOTALL)
    m = pattern.search(src)
    if not m:
        starlette = re.search(r"^(?P<indent>\s*)app\s*=\s*server\.build\(\)\s*$", src, re.MULTILINE)
        if starlette:
           block_text = with_indent(STARLETTE_WIRING_TEMPLATE, starlette.group("indent"))
            return src[: starlette.end()] + "\n\n" + block_text + "\n" + src[starlette.end():]

        # Fallback: look for `app = FastAPI(` and find the matching `)`.
        idx = src.find("app = FastAPI(")
        if idx < 0:
            raise RuntimeError("could not find `app = FastAPI(...)` to anchor wiring")
        depth = 0
        end = idx
        for i in range(idx, len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
          return _patch_lifespan_enroll(src[:end] + "\n\n" + block + "\n" + src[end:])
    return _patch_lifespan_enroll(src[: m.end()] + "\n\n" + block + "\n" + src[m.end():])


def _patch_lifespan_enroll(src: str) -> str:
    """FastAPI lifespan handlers bypass @app.on_event startup hooks."""
    marker = "await agent.enroll()"
    lifespan = re.search(r"^async def lifespan\(app: FastAPI\):\n", src, re.MULTILINE)
    if not lifespan:
        return src
    next_def = re.search(r"^async def |\n\napp = FastAPI", src[lifespan.end():], re.MULTILINE)
    end = lifespan.end() + next_def.start() if next_def else len(src)
    body = src[lifespan.end():end]
    if marker in body:
        return src
    line_start = src.rfind("\n", 0, lifespan.start()) + 1
    indent = "    "
    insert_at = lifespan.end()
    return src[:insert_at] + f"{indent}{marker}\n" + src[insert_at:]


def patch_file(path: Path, *, add_boot: bool, add_app_wiring: bool) -> None:
    if not path.exists():
        print(f"[skip] {path} does not exist (upstream layout may have changed)")
        return
        src = path.read_text(encoding="utf-8")
       original = src
    if add_boot:
        src = _insert_after_imports(src, BOOT_BLOCK_TEMPLATE)
    if add_app_wiring:
        src = _insert_after_app_construct(src, APP_WIRING_TEMPLATE)
    if src != original:
        path.write_text(src, encoding="utf-8")
        print(f"[patched] {path}")
    else:
        print(f"[unchanged] {path} (already patched or no anchor found)")


def replace_interceptor(path: Path) -> None:
    if not path.exists():
        print(f"[skip] interceptor at {path} does not exist (already removed?)")
        return
  path.write_text(STUB_INTERCEPTOR, encoding="utf-8")
    print(f"[stubbed] {path}")


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
    patch_file(
        repo_root / "supply-chain-agent" / "__main__.py",
        add_boot=True, add_app_wiring=True,
    )
    replace_interceptor(repo_root / "supply-chain-agent" / "aauth_interceptor.py")

    # Market analysis agent
    patch_file(
        repo_root / "market-analysis-agent" / "__main__.py",
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
