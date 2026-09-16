"""Fix executable permissions inside the cached dependencies zip.

Zips built on Windows carry no POSIX mode bits, so binaries that the runtime
has to *execute* (playwright's node driver) land as 0644 and fail with
`PermissionError: [Errno 13] ... '/var/task/playwright/driver/node'`.

The toolkit merges this cached zip into deployment.zip preserving each entry's
external_attr, so fixing the cache is enough for the next deploy.

Run from the starter folder:  uv run python ../scripts/fix_zip_permissions.py
"""
import re
import shutil
import zipfile
from pathlib import Path

ZIP = Path(".bedrock_agentcore/customer_support_agent/dependencies.zip")
TMP = ZIP.with_suffix(".fixed.zip")

# files that must be executable on Linux
EXEC_PATTERNS = [
    re.compile(r"^playwright/driver/node$"),
    re.compile(r"^playwright/driver/.*\.sh$"),
    re.compile(r"^bin/[^/]+$"),  # already 755 for our injected scripts, keep it
]

if not ZIP.exists():
    raise SystemExit(f"no existe {ZIP}")

fixed = 0
with zipfile.ZipFile(ZIP) as src, zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED) as dst:
    for info in src.infolist():
        new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        new_info.compress_type = info.compress_type
        new_info.external_attr = info.external_attr

        if any(p.match(info.filename) for p in EXEC_PATTERNS):
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111 == 0:
                new_info.external_attr = (0o100755 << 16)
                fixed += 1

        with src.open(info) as fsrc, dst.open(new_info, "w") as fdst:
            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)

ZIP.unlink()
TMP.rename(ZIP)

print(f"entradas con permiso de ejecución corregido: {fixed}")
with zipfile.ZipFile(ZIP) as z:
    for entry in ("playwright/driver/node", "bin/opentelemetry-instrument"):
        if entry in z.namelist():
            info = z.getinfo(entry)
            print(f"  {entry}: modo={oct((info.external_attr >> 16) & 0o777)}")
