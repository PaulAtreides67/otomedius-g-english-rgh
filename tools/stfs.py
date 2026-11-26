"""stfs.py: list and extract every file of an STFS (LIVE / PIRS / CON) package.

  python stfs.py <package> <outdir>

Port of Xenia's STFS reader (xenia-project/xenia, master):
  src/xenia/vfs/devices/stfs_container_device.cc
    ReadHeaderAndVerify  (block_step / blocks_per_hash_table_)  line 162
    ReadSTFS             (directory walk, file block chains)      line 521
    BlockToOffsetSTFS                                             line 641
    BlockToHashBlockNumberSTFS / BlockToHashBlockOffsetSTFS       line 661
    GetBlockHash         (L0/L1/L2 hash tables, secondary copies) line 699
  src/xenia/vfs/devices/stfs_xbox.h
    XContentHeader (0x344 bytes), XContentMetadata (packed), StfsVolumeDescriptor (0x24),
    StfsHashEntry (0x18), StfsHashTable (0x1000), StfsDirectoryEntry (0x40)
  src/xenia/vfs/devices/stfs_container_device.h
    kBlockSize = 0x1000, kBlocksPerHashLevel = {170, 28900, 4913000}, kEndOfChain = 0xFFFFFF

Field offsets are the packed C layouts, written out explicitly (all big-endian unless noted):
  0x000 magic, 0x340 header_size, 0x344 metadata:
    +0x00 content_type, +0x10 execution_info (0x18 bytes), +0x35 volume_descriptor (0x24 bytes):
      +0 descriptor_length, +2 flags (bit0 read_only_format, bit1 root_active_index),
      +3 file_table_block_count (u16 LITTLE endian), +5 file_table_block_number (u24 LE),
      +0x1C total_block_count (be32)
    +0x59 data_file_count (be32), +0x65 volume_type (be32)

Directory entries (0x40 bytes): name[40] at +0, flags at +0x28 (bits 0-5 name length, bit 7 directory),
valid blocks u24 LE at +0x29, allocated blocks u24 LE at +0x2C, start block u24 LE at +0x2F,
directory_index be16 at +0x32, length be32 at +0x34.

Every extracted data block is also checked against the SHA-1 kept in its level-0 hash entry, so a
wrong block-number formula shows up as a count of mismatches rather than as silently wrong bytes.
"""
import hashlib
import os
import struct
import sys

BLOCK = 0x1000
BLOCKS_PER_LEVEL = (170, 28900, 4913000)  # kBlocksPerHashLevel
END_OF_CHAIN = 0xFFFFFF
DIR_ENTRY_SIZE = 0x40
MAGICS = (b'LIVE', b'PIRS', b'CON ')
HEADER_STRUCT_SIZE = 0x971A  # sizeof(StfsHeader)


def round_up(v, a):
    return (v + a - 1) // a * a


def u24le(b):
    return b[0] | (b[1] << 8) | (b[2] << 16)


class Entry:
    def __init__(self, name, parent, is_dir):
        self.name = name
        self.parent = parent
        self.is_dir = is_dir
        self.size = 0
        self.children = []
        self.blocks = []  # list of (file offset, byte count) for data blocks, in order
        self.start_block = None

    def path(self):
        parts = []
        e = self
        while e is not None and e.parent is not None:
            parts.append(e.name)
            e = e.parent
        return '/'.join(reversed(parts))


class Stfs:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        hdr = self.f.read(HEADER_STRUCT_SIZE)
        if len(hdr) < 0x344 or hdr[0:4] not in MAGICS:
            raise ValueError('not an STFS package (magic %r)' % hdr[0:4])
        self.magic = hdr[0:4]
        self.header_size = struct.unpack('>I', hdr[0x340:0x344])[0]
        self.content_type = struct.unpack('>I', hdr[0x344:0x348])[0]
        self.exec_info = hdr[0x354:0x36C]
        vd = 0x379  # volume_descriptor (metadata base 0x344 + 0x35)
        self.vd_len = hdr[vd]
        self.read_only = hdr[vd + 2] & 1
        self.root_active = (hdr[vd + 2] >> 1) & 1
        self.file_table_block_count = struct.unpack('<H', hdr[vd + 3:vd + 5])[0]
        self.file_table_block = u24le(hdr[vd + 5:vd + 8])
        self.total_blocks = struct.unpack('>I', hdr[vd + 0x1C:vd + 0x20])[0]
        self.data_file_count = struct.unpack('>I', hdr[0x344 + 0x59:0x344 + 0x5D])[0]
        self.volume_type = struct.unpack('>I', hdr[0x344 + 0x65:0x344 + 0x69])[0]
        if self.volume_type != 0:
            raise ValueError('SVOD packages are not handled by this tool (volume_type %d)' % self.volume_type)
        # blocks_per_hash_table_ and block_step[] from ReadHeaderAndVerify
        self.bpht = 1 if self.read_only else 2
        self.step0 = BLOCKS_PER_LEVEL[0] + self.bpht
        self.step1 = BLOCKS_PER_LEVEL[1] + (BLOCKS_PER_LEVEL[0] + 1) * self.bpht
        self.cache = {}
        self.file_size = os.fstat(self.f.fileno()).st_size
        self.hash_checks = [0, 0]  # [matching, total] data-block SHA-1 checks

    # ---- address arithmetic (ported) ----
    def block_to_offset(self, block):
        """BlockToOffsetSTFS: file offset of a data block, hash-table blocks skipped."""
        base = BLOCKS_PER_LEVEL[0]
        blk = block
        for _ in range(3):
            blk += ((block + base) // base) * self.bpht
            if block < base:
                break
            base *= BLOCKS_PER_LEVEL[0]
        return round_up(self.header_size, BLOCK) + (blk << 12)

    def hash_block_number(self, block, level):
        """BlockToHashBlockNumberSTFS."""
        if level == 0:
            if block < BLOCKS_PER_LEVEL[0]:
                return 0
            blk = (block // BLOCKS_PER_LEVEL[0]) * self.step0
            blk += ((block // BLOCKS_PER_LEVEL[1]) + 1) * self.bpht
            if block < BLOCKS_PER_LEVEL[1]:
                return blk
            return blk + self.bpht
        if level == 1:
            if block < BLOCKS_PER_LEVEL[1]:
                return self.step0
            return (block // BLOCKS_PER_LEVEL[1]) * self.step1 + self.bpht
        return self.step1

    def hash_block_offset(self, block, level):
        return round_up(self.header_size, BLOCK) + (self.hash_block_number(block, level) << 12)

    # ---- raw reads ----
    def read_at(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def read_table(self, off):
        raw = self.read_at(off, BLOCK)
        if len(raw) != BLOCK:
            raise IOError('short hash table read at 0x%X' % off)
        return raw

    @staticmethod
    def entry_fields(table, rec):
        e = table[rec * 0x18:(rec + 1) * 0x18]
        info = struct.unpack('>I', e[0x14:0x18])[0]
        return e[0:0x14], info & 0xFFFFFF, bool(info & 0x40000000)

    def get_hash_entry(self, block):
        """GetBlockHash: returns (sha1, next_block) of the level-0 hash entry for a data block."""
        sec = BLOCK if self.root_active else 0
        off0 = self.hash_block_offset(block, 0)
        if off0 not in self.cache:
            if self.read_only:
                sec = 0
            elif self.total_blocks > BLOCKS_PER_LEVEL[0]:
                off1 = self.hash_block_offset(block, 1)
                if off1 not in self.cache:
                    if self.total_blocks > BLOCKS_PER_LEVEL[1]:
                        off2 = self.hash_block_offset(block, 2)
                        if off2 not in self.cache:
                            self.cache[off2] = self.read_table(off2 + sec)
                        rec2 = (block // BLOCKS_PER_LEVEL[1]) % BLOCKS_PER_LEVEL[0]
                        sec = BLOCK if self.entry_fields(self.cache[off2], rec2)[2] else 0
                    self.cache[off1] = self.read_table(off1 + sec)
                rec1 = (block // BLOCKS_PER_LEVEL[0]) % BLOCKS_PER_LEVEL[0]
                sec = BLOCK if self.entry_fields(self.cache[off1], rec1)[2] else 0
            self.cache[off0] = self.read_table(off0 + sec)
        sha, nxt, _ = self.entry_fields(self.cache[off0], block % BLOCKS_PER_LEVEL[0])
        return sha, nxt

    # ---- directory walk (ReadSTFS) ----
    def read_tree(self):
        root = Entry('', None, True)
        all_entries = []
        tbl = self.file_table_block
        for n in range(self.file_table_block_count):
            raw = self.read_at(self.block_to_offset(tbl), BLOCK)
            for m in range(BLOCK // DIR_ENTRY_SIZE):
                e = raw[m * DIR_ENTRY_SIZE:(m + 1) * DIR_ENTRY_SIZE]
                if e[0] == 0:
                    break
                dir_index = struct.unpack('>H', e[0x32:0x34])[0]
                parent = root if dir_index == 0xFFFF else all_entries[dir_index]
                flags = e[0x28]
                name_len = flags & 0x3F
                is_dir = bool((flags >> 7) & 1)
                name = e[0:name_len].decode('latin-1')
                ent = Entry(name, parent, is_dir)
                ent.valid_blocks = u24le(e[0x29:0x2C])
                ent.allocated_blocks = u24le(e[0x2C:0x2F])
                ent.start_block = u24le(e[0x2F:0x32])
                ent.size = struct.unpack('>I', e[0x34:0x38])[0]
                if not is_dir:
                    self._chain_blocks(ent)
                all_entries.append(ent)
                parent.children.append(ent)
            tbl = self.get_hash_entry(tbl)[1]
            if tbl == END_OF_CHAIN:
                break
        return root

    def _chain_blocks(self, ent):
        blk = ent.start_block
        remaining = ent.size
        while remaining and blk != END_OF_CHAIN:
            n = min(BLOCK, remaining)
            ent.blocks.append((self.block_to_offset(blk), n, blk))
            remaining -= n
            blk = self.get_hash_entry(blk)[1]
        if remaining:
            print('WARNING: %s: %d bytes missing from block chain' % (ent.name, remaining))
        if len(ent.blocks) != ent.allocated_blocks:
            print('WARNING: %s: %d blocks read, directory says %d' % (ent.name, len(ent.blocks),
                                                                     ent.allocated_blocks))

    def read_file(self, ent, verify=True):
        out = bytearray()
        for off, n, blk in ent.blocks:
            raw = self.read_at(off, BLOCK)
            if verify:
                want = self.get_hash_entry(blk)[0]
                self.hash_checks[1] += 1
                if hashlib.sha1(raw).digest() == want:
                    self.hash_checks[0] += 1
                else:
                    print('  hash mismatch: %s data block %d (file offset 0x%X)' % (ent.path(), blk, off))
            out += raw[:n]
        return bytes(out)


def walk(node):
    for c in node.children:
        yield c
        if c.is_dir:
            yield from walk(c)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    pkg, outdir = argv
    st = Stfs(pkg)
    print('package %s: magic %s, content type 0x%08X, %d file-table block(s), read_only=%d, '
          'total blocks %d' % (os.path.basename(pkg), st.magic.decode('latin-1'), st.content_type,
                              st.file_table_block_count, st.read_only, st.total_blocks))
    root = st.read_tree()
    os.makedirs(outdir, exist_ok=True)
    files = 0
    for ent in walk(root):
        rel = ent.path()
        dst = os.path.join(outdir, *rel.split('/'))
        if ent.is_dir:
            os.makedirs(dst, exist_ok=True)
            print('  [dir]  %s/' % rel)
            continue
        data = st.read_file(ent)
        if len(data) != ent.size:
            raise IOError('%s: got %d bytes, expected %d' % (rel, len(data), ent.size))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        files += 1
        print('  %10d  %s  (blocks %d)' % (ent.size, rel, len(ent.blocks)))
    print('extracted %d files to %s' % (files, outdir))
    ok, total = st.hash_checks
    print('data-block SHA-1 checks against level-0 hash entries: %d/%d match' % (ok, total))
    return 0 if ok == total else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
