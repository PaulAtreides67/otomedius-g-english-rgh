#!/usr/bin/env python3
"""Rebuild an Xbox 360 STFS LIVE package with selected files replaced.

Usage:
  python repack_dlc.py <orig_package> <replacement_dir> <out_dir> [--keep-name]
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stfs  # noqa: E402

BLOCK = 0x1000
BPHT = 1                  # read-only format (flag bit 0 of the volume descriptor)
L0_PER = 170
L1_PER = 28900
STEP0 = L0_PER + BPHT
STEP1 = L1_PER + (L0_PER + 1) * BPHT
END = 0xFFFFFF


def be32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def round_up(v, a):
    return (v + a - 1) // a * a


def diff_ranges(a, b):
    ranges = []
    i = 0
    while i < len(a):
        if a[i] != b[i]:
            j = i + 1
            while j < len(a) and a[j] != b[j]:
                j += 1
            ranges.append((i, j))
            i = j
        else:
            i += 1
    return ranges


def read_dir_table(orig, st):
    vd = 0x379
    count = struct.unpack("<H", orig[vd + 3:vd + 5])[0]
    block = stfs.u24le(orig[vd + 5:vd + 8])
    assert count == 1, "expected 1 file table block, got %d" % count
    raw = st.read_at(st.block_to_offset(block), 0x1000)
    recs = []
    for i in range(0x1000 // 0x40):
        e = raw[i * 0x40:(i + 1) * 0x40]
        name_len = e[0x28] & 0x3F
        if name_len == 0:
            break
        recs.append({
            "index": i, "name": e[:name_len].decode("latin-1"), "flags": e[0x28],
            "parent": struct.unpack(">H", e[0x32:0x34])[0], "stamp": bytes(e[0x38:0x40]),
        })
    return recs


def build_paths(recs):
    paths = {}
    for r in recs:
        parent = r["parent"]
        r["path"] = r["name"] if parent == 0xFFFF else paths[parent] + "/" + r["name"]
        r["is_dir"] = bool(r["flags"] & 0x80)
        paths[r["index"]] = r["path"]
    return recs


def collect_replacements(repl_dir):
    repl = {}
    for root, _, files in os.walk(repl_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repl_dir).replace(os.sep, "/")
            repl[rel] = full
    return repl


class Layout:
    def __init__(self, header_size):
        self.hs = round_up(header_size, BLOCK)

    def block_to_offset(self, block):
        base = L0_PER
        blk = block
        for _ in range(3):
            blk += ((block + base) // base) * BPHT
            if block < base:
                break
            base *= L0_PER
        return self.hs + (blk << 12)

    def hash_number(self, block, level):
        if level == 0:
            if block < L0_PER:
                return 0
            blk = (block // L0_PER) * STEP0
            blk += ((block // L1_PER) + 1) * BPHT
            if block < L1_PER:
                return blk
            return blk + BPHT
        if level == 1:
            if block < L1_PER:
                return STEP0
            return (block // L1_PER) * STEP1 + BPHT
        return STEP1

    def hash_offset(self, block, level):
        return self.hs + (self.hash_number(block, level) << 12)


def u24le(v):
    return bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])


def stfs_write_tree(header_src, entries, magic):
    base_hdr = bytearray(header_src[:0xB000])
    header_size = be32(base_hdr, 0x340)
    lay = Layout(header_size)
    assert lay.hs == 0xB000, hex(lay.hs)

    blocks, nxt, dir_recs, cur = {}, {}, [], 1
    for ent in entries:
        if ent["is_dir"]:
            dir_recs.append(ent)
            continue
        data = ent["data"]
        n = (len(data) + BLOCK - 1) // BLOCK
        start = cur
        for k in range(n):
            b = cur + k
            chunk = data[k * BLOCK:(k + 1) * BLOCK]
            blocks[b] = chunk + bytes(BLOCK - len(chunk))
            nxt[b] = b + 1 if k + 1 < n else END
        rec = dict(ent)
        rec["size"], rec["nblocks"], rec["start"] = len(data), n, start
        dir_recs.append(rec)
        cur += n
    total_data = cur
    nxt[0] = END

    d = bytearray(BLOCK)
    for i, ent in enumerate(dir_recs):
        e = bytearray(0x40)
        nb = ent["name"].encode("latin-1")
        e[0:len(nb)] = nb
        e[0x28] = (ent["flags"] & 0xC0) | len(nb)
        if ent["is_dir"]:
            e[0x29:0x38] = b"\x00" * 15
        else:
            e[0x29:0x2C] = u24le(ent["nblocks"])
            e[0x2C:0x2F] = u24le(ent["nblocks"])
            e[0x2F:0x32] = u24le(ent["start"])
            e[0x34:0x38] = struct.pack(">I", ent["size"])
        e[0x32:0x34] = struct.pack(">H", ent["parent"])
        e[0x38:0x40] = ent["stamp"]
        d[i * 0x40:(i + 1) * 0x40] = e
    blocks[0] = bytes(d)

    offsets = {b: lay.block_to_offset(b) for b in range(total_data)}
    l0_count = (total_data + L0_PER - 1) // L0_PER
    l0_tables = {}
    for g in range(l0_count):
        t = bytearray(BLOCK)
        for k in range(L0_PER):
            b = g * L0_PER + k
            if b >= total_data:
                break
            sha = hashlib.sha1(blocks[b]).digest()
            info = 0x80000000 | (nxt[b] & 0xFFFFFF)
            t[k * 0x18:k * 0x18 + 0x14] = sha
            t[k * 0x18 + 0x14:k * 0x18 + 0x18] = struct.pack(">I", info)
        l0_tables[g] = bytes(t)
    assert total_data > L0_PER and total_data <= L1_PER, "layout written for 171..28900 blocks"
    l1 = bytearray(BLOCK)
    for g in range(l0_count):
        l1[g * 0x18:g * 0x18 + 0x14] = hashlib.sha1(l0_tables[g]).digest()
    top_hash = hashlib.sha1(bytes(l1)).digest()

    end = 0
    for b in range(total_data):
        end = max(end, offsets[b] + BLOCK)
    for g in range(l0_count):
        end = max(end, lay.hash_offset(g * L0_PER, 0) + BLOCK)
    end = max(end, lay.hash_offset(0, 1) + BLOCK)

    pkg = bytearray(end)
    pkg[0:0xB000] = base_hdr
    pkg[0:4] = magic
    vd = 0x379
    struct.pack_into("<H", pkg, vd + 3, 1)
    pkg[vd + 5:vd + 8] = u24le(0)
    pkg[vd + 2] = pkg[vd + 2] | 1
    pkg[vd + 8:vd + 0x1C] = top_hash
    struct.pack_into(">I", pkg, vd + 0x1C, total_data)
    struct.pack_into(">Q", pkg, 0x34C, end - lay.hs)
    pkg[0x32C:0x340] = hashlib.sha1(bytes(pkg[0x344:lay.hs])).digest()
    for b, chunk in blocks.items():
        pkg[offsets[b]:offsets[b] + BLOCK] = chunk
    for g in range(l0_count):
        o = lay.hash_offset(g * L0_PER, 0)
        pkg[o:o + BLOCK] = l0_tables[g]
    o = lay.hash_offset(0, 1)
    pkg[o:o + BLOCK] = bytes(l1)
    return bytes(pkg), {"total_data_blocks": total_data, "dir_recs": dir_recs}


def repack(orig_path, repl_dir, out_dir, keep_name=False):
    orig = open(orig_path, "rb").read()
    if orig[:4] != b"LIVE":
        raise SystemExit("expected LIVE magic, got %r" % (orig[:4],))
    header_size = struct.unpack(">I", orig[0x340:0x344])[0]
    if round_up(header_size, 0x1000) != 0xB000:
        raise SystemExit("header rounds to 0x%X, expected 0xB000" % round_up(header_size, 0x1000))

    st = stfs.Stfs(orig_path)
    root = st.read_tree()
    st_entries = list(stfs.walk(root))
    by_path = {e.path(): e for e in st_entries}
    file_paths = {e.path() for e in st_entries if not e.is_dir}

    repl = collect_replacements(repl_dir)
    for rp in repl:
        if rp not in file_paths:
            raise SystemExit("replacement file %r does not match any package file entry" % rp)

    recs = build_paths(read_dir_table(orig, st))
    intended = []
    for r in recs:
        ent = dict(r)
        if not r["is_dir"]:
            data = st.read_file(by_path[r["path"]], verify=False)
            if r["path"] in repl:
                new_data = open(repl[r["path"]], "rb").read()
                print("replaced %s %d -> %d" % (r["path"], len(data), len(new_data)))
                data = new_data
            ent["data"] = data
        intended.append(ent)

    pkg, rep = stfs_write_tree(orig[:0xB000], intended, orig[:4])
    base = os.path.basename(orig_path)
    suffix = base[40:] if len(base) == 42 and all(c in '0123456789ABCDEFabcdef' for c in base) else '43'
    content_id = pkg[0x32C:0x340].hex().upper() + suffix.upper()
    os.makedirs(out_dir, exist_ok=True)
    out_name = os.path.basename(orig_path) if keep_name else content_id
    out_path = os.path.join(out_dir, out_name)
    with open(out_path, "wb") as f:
        f.write(pkg)

    vst = stfs.Stfs(out_path)
    ventries = list(stfs.walk(vst.read_tree()))
    got = [(e.path(), e.is_dir, 0 if e.is_dir else e.size) for e in ventries]
    want = [(r["path"], r["is_dir"], 0 if r["is_dir"] else len(r["data"])) for r in intended]
    assert got == want, "entry list mismatch: got %r want %r" % (got, want)
    for r in intended:
        if r["is_dir"]:
            continue
        ve = [e for e in ventries if e.path() == r["path"]][0]
        assert vst.read_file(ve) == r["data"], "file bytes mismatch: %s" % r["path"]
    ok, total = vst.hash_checks
    assert ok == total and total > 0, "hash checks failed: %d/%d" % (ok, total)

    allowed = set(range(0x379, 0x379 + 0x24)) | set(range(0x34C, 0x354))
    for start, end in diff_ranges(pkg[0x344:0xB000], orig[0x344:0xB000]):
        rs, re = start + 0x344, end + 0x344
        print("header diff 0x%X..0x%X" % (rs, re))
        for off in range(rs, re):
            if off not in allowed:
                raise SystemExit("unexpected header diff at 0x%X" % off)

    orig_raw = st.read_at(st.block_to_offset(stfs.u24le(orig[0x379 + 5:0x379 + 8])), 0x1000)
    new_raw = vst.read_at(vst.block_to_offset(stfs.u24le(pkg[0x379 + 5:0x379 + 8])), 0x1000)
    for i, r in enumerate(intended):
        oe, ne = orig_raw[i * 0x40:(i + 1) * 0x40], new_raw[i * 0x40:(i + 1) * 0x40]
        assert oe[:0x29] == ne[:0x29], "dir rec name/flags changed: %s" % r["path"]
        assert oe[0x32:0x34] == ne[0x32:0x34], "dir rec parent changed: %s" % r["path"]
        assert oe[0x38:0x40] == ne[0x38:0x40], "dir rec stamp changed: %s" % r["path"]
        os_, ns = struct.unpack(">I", oe[0x34:0x38])[0], struct.unpack(">I", ne[0x34:0x38])[0]
        ob, nb = stfs.u24le(oe[0x2F:0x32]), stfs.u24le(ne[0x2F:0x32])
        if os_ != ns or ob != nb:
            print("dir %s start %d->%d size %d->%d" % (r["path"], ob, nb, os_, ns))

    old_blocks = struct.unpack(">I", orig[0x379 + 0x1C:0x379 + 0x20])[0]
    print("content id:", content_id)
    print("total blocks: %d -> %d" % (old_blocks, rep["total_data_blocks"]))
    print("PASS")
    return out_path, content_id


def main(argv):
    keep_name = "--keep-name" in argv
    args = [a for a in argv if a != "--keep-name"]
    if len(args) != 3:
        print(__doc__.strip())
        return 2
    orig_path, repl_dir, out_dir = args
    repack(orig_path, repl_dir, out_dir, keep_name=keep_name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
