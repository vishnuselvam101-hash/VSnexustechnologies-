import os
import socket
import pytest
from pathlib import Path

from vnxdna.pipeline.encode import encode_container, DNAOptions
from vnxdna.pipeline.decode import decode_reads, DecodeOptions
from vnxdna.pipeline.stream import encode_stream, decode_stream, StreamOptions

# Helper to create a small binary file
def create_binary_file(path: Path, data: bytes = b"hello world"):
    path.write_bytes(data)
    return data

@pytest.fixture
def binary_file(tmp_path):
    file_path = tmp_path / "data.bin"
    data = create_binary_file(file_path)
    return file_path, data

def test_container_encode_decode_offline(monkeypatch, work, binary_file):
    # Monkeypatch socket functions to raise errors, simulating offline environment
    for fn in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, fn, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    src_path, original = binary_file
    # Build a valid container from the binary data
    from vnxdna.container.builder import build_container, StoreOptions
    from vnxdna.container.vxdna import serialize, atomic_write
    cont = build_container(original, StoreOptions())
    container_path = work / "container.bin"
    atomic_write(container_path, serialize(cont.manifest_bytes, cont.body))
    strands_path = work / "strands.fasta"
    # Encode container to strands
    encode_container(container_path, strands_path, DNAOptions())
    # Decode back and compare bytes
    out_path = work / "out.bin"
    decode_reads(strands_path, out_path, DecodeOptions())
    assert out_path.read_bytes() == original

def test_stream_encode_decode_offline(monkeypatch, work, binary_file):
    # Monkeypatch socket as above
    for fn in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, fn, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    src_path, original = binary_file
    # Use a small stream (extent size 4KB) to ensure at least one extent
    options = StreamOptions(extent_bytes=4096, workers=1, dna=DNAOptions())
    sink = work / "stream.fasta"
    manifest = work / "stream.json"
    encode_stream(src_path, sink, options=options, manifest=manifest, overwrite=True)
    # Decode the stream back
    out_path = work / "stream_out.bin"
    decode_stream(sink, manifest, out_path, options=DecodeOptions())
    assert out_path.read_bytes() == original

def test_no_network_imports():
    # Ensure disallowed network modules are not imported in the source tree.
    disallowed = {"urllib.request", "http.client", "requests", "httpx", "aiohttp", "ftplib", "smtplib"}
    import pathlib, re
    imported = set()
    for py in pathlib.Path('src').rglob('*.py'):
        text = py.read_text()
        for mod in disallowed:
            pattern = rf'^(?:import|from)\s+{re.escape(mod.split(".")[0])}'
            if re.search(pattern, text, re.M):
                imported.add(mod)
    assert not imported, f"Disallowed network modules imported: {imported}"


def test_socket_import_allowlist():
    # Find real imports of socket in the source tree and ensure they are listed in the allowlist file.
    allowlist_path = Path(__file__).with_name("ap17_network_allowlist.txt")
    allowlist = set()
    if allowlist_path.is_file():
        allowlist = {line.strip() for line in allowlist_path.read_text().splitlines() if line.strip()}
    # Use grep to find files importing socket
    import subprocess, sys
    result = subprocess.run(["python", "-c", "import pathlib, sys, re; [print(p) for p in pathlib.Path('src').rglob('*.py') if re.search(r'^(import socket|from socket)', open(p).read(), re.M)]"], capture_output=True, text=True)
    files = set(result.stdout.strip().splitlines()) if result.stdout else set()
    unexpected = files - allowlist
    assert not unexpected, f"Socket imports found outside allowlist: {sorted(unexpected)}"
