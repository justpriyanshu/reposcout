"""Download a gitleaks release binary for Docker images and check it against the release's
published SHA-256 checksums. Usage: python deploy/fetch_gitleaks.py VERSION DEST [ARCH]"""

import hashlib
import io
import os
import sys
import tarfile
import urllib.request


def main() -> None:
    version, dest = sys.argv[1], sys.argv[2]
    docker_arch = (sys.argv[3] if len(sys.argv) > 3 else "") or "amd64"
    arch = {"amd64": "x64", "arm64": "arm64"}[docker_arch]
    name = f"gitleaks_{version}_linux_{arch}.tar.gz"
    base = f"https://github.com/gitleaks/gitleaks/releases/download/v{version}"
    archive = urllib.request.urlopen(f"{base}/{name}", timeout=60).read()
    sums = urllib.request.urlopen(f"{base}/gitleaks_{version}_checksums.txt", timeout=60).read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.endswith(name))
    if hashlib.sha256(archive).hexdigest() != expected:
        sys.exit(f"gitleaks {version}: checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        binary = tar.extractfile(tar.getmember("gitleaks"))
        assert binary is not None
        with open(dest, "wb") as out:
            out.write(binary.read())
    os.chmod(dest, 0o755)


if __name__ == "__main__":
    main()
