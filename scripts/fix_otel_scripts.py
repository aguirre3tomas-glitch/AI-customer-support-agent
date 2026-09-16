"""Inject POSIX console-script wrappers into the cached dependencies zip.

uv on Windows emits `bin/*.exe` shims even when cross-installing for Linux.
The AgentCore Runtime requires a `bin/opentelemetry-instrument` executable in
the deployment zip, so we add the standard POSIX wrapper (what a Linux install
of the same wheel would produce) before the toolkit merges the cache into
deployment.zip.
"""
import zipfile
from pathlib import Path

ZIP = Path(".bedrock_agentcore/customer_support_agent/dependencies.zip")

TEMPLATE = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re
import sys
from {module} import run
if __name__ == '__main__':
    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])
    sys.exit(run())
"""

SCRIPTS = {
    "bin/opentelemetry-instrument": "opentelemetry.instrumentation.auto_instrumentation",
    "bin/opentelemetry-bootstrap": "opentelemetry.instrumentation.bootstrap",
}

if not ZIP.exists():
    raise SystemExit(f"no existe {ZIP}")

with zipfile.ZipFile(ZIP, "a") as zf:
    names = set(zf.namelist())
    for entry, module in SCRIPTS.items():
        if entry in names:
            print(f"ya existe: {entry}")
            continue
        info = zipfile.ZipInfo(entry)
        info.external_attr = (0o100755 << 16)  # -rwxr-xr-x
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, TEMPLATE.format(module=module))
        print(f"inyectado: {entry}")

with zipfile.ZipFile(ZIP) as zf:
    for entry in SCRIPTS:
        if entry in zf.namelist():
            info = zf.getinfo(entry)
            mode = (info.external_attr >> 16) & 0o777
            print(f"verificado: {entry} modo={oct(mode)} bytes={info.file_size}")
