"""Fixed renderer worker. Run only through document_layout.check_layout."""
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import zipfile


def inspect():
    from defusedxml.ElementTree import fromstring
    from pypdf import PdfReader
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20_000_000, 20_000_000))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    source = Path("input.docx")
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if (len(members) > 2000 or sum(x.file_size for x in members) > 50_000_000
                or len({x.filename for x in members}) != len(members)):
            raise ValueError("package limits")
        for member in members:
            name = member.filename.lower()
            if member.flag_bits & 1 or "vbaproject" in name or "/embeddings/" in name:
                raise ValueError("active or embedded content")
            if name.endswith((".xml", ".rels")):
                node = fromstring(archive.read(member), forbid_dtd=True, forbid_entities=True, forbid_external=True)
                if name.endswith(".rels") and any(
                        item.get("TargetMode", "").lower() == "external" for item in node.iter()):
                    raise ValueError("external relationships")
    subprocess.run([sys.argv[1], "-env:UserInstallation=" + Path("profile").resolve().as_uri(),
        "--headless", "--nologo", "--nodefault", "--norestore", "--convert-to", "pdf:writer_pdf_Export",
        "--outdir", str(Path.cwd()), str(source.resolve())], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pdf = Path("input.pdf")
    raw = pdf.read_bytes()
    if len(raw) > 20_000_000:
        raise ValueError("PDF size")
    with pdf.open("rb") as handle:
        pages = len(PdfReader(handle, strict=True).pages)
    return {"status": "success", "pages": pages, "pdf_sha256": hashlib.sha256(raw).hexdigest()}


if __name__ == "__main__":
    try:
        result = inspect()
    except ImportError:
        result = {"status": "failure", "reason": "Install praxile[documents] for layout parsing"}
    except Exception:
        result = {"status": "failure", "reason": "Renderer failed or document contains unsupported content"}
    print(json.dumps(result))
