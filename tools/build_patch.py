#!/usr/bin/env python3
"""Build an English patch from GameStone's translation for Otomedius G.

Usage:
  python tools/build_patch.py --translation DIR --out DIR [--tu FILE] [--dlc DIR]
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stfs  # noqa: E402
from repack_dlc import repack  # noqa: E402

TU_BASENAME = "TU_15KS1VE_0000004000000.0000000000081"
TU_KNOWN_SHA1 = "604ed535e80639f1d9558318f1411934e9622f7f"
TU_OUT_SUBDIR = os.path.join("Content", "0000000000000000", "4B4E07EE", "000B0000")
DLC_OUT_SUBDIR = os.path.join("Content", "0000000000000000", "4B4E07EE", "00000002")

DLC_RULES = [
    ("PlayerG11.pak", "Catalog Extra Characters"),
    ("PlayerG09Snd.pak", "St. Gradius School Uniform Aoba Anoa"),
    ("PulseLaser.pak", "Esmeralda and Poini"),
    ("stage05_stream.xwb", "Easter Island"),
]
ALL_DLC_NAMES = [name for _, name in DLC_RULES]


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_live_file(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"LIVE"
    except OSError:
        return False


def identify_dlc(path):
    st = stfs.Stfs(path)
    paths = set()
    for e in stfs.walk(st.read_tree()):
        if not e.is_dir:
            paths.add(e.path())
    for marker, name in DLC_RULES:
        if marker in paths:
            return name
    return None


def path_is_inside(child, parent):
    child = os.path.abspath(child)
    parent = os.path.abspath(parent)
    try:
        common = os.path.commonpath([child, parent])
    except ValueError:
        return False
    return common == parent


def write_install_txt(out_dir, tu_entries, dlc_entries, missing_dlcs):
    lines = []
    for orig_name in tu_entries:
        lines.append(
            "Title Update: replace %s in Content/0000000000000000/4B4E07EE/000B0000/ with the new file of the same name"
            % orig_name
        )
    for dlc_name, orig_name, new_name in dlc_entries:
        lines.append(
            "%s: delete %s from Content/0000000000000000/4B4E07EE/00000002/ and copy in %s"
            % (dlc_name, orig_name, new_name)
        )
    if missing_dlcs:
        lines.append("DLCs not included: " + ", ".join(missing_dlcs))
    text = "\n".join(lines) + "\n"
    install_path = os.path.join(out_dir, "INSTALL.txt")
    with open(install_path, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    print(text, end="")
    return install_path


def main(argv):
    parser = argparse.ArgumentParser(
        description="Build an English patch from GameStone's translation for Otomedius G."
    )
    parser.add_argument("--translation", required=True, help="GameStone's extracted English_Translation_v1.81 folder")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--tu", help="Title Update package file")
    parser.add_argument("--dlc", help="Directory containing DLC package files")
    args = parser.parse_args(argv)

    if not args.tu and not args.dlc:
        parser.error("at least one of --tu / --dlc is required")

    translation = args.translation
    out_dir = args.out
    dlc_dir = args.dlc

    if dlc_dir and path_is_inside(out_dir, dlc_dir):
        parser.error("--out must not be inside --dlc")

    os.makedirs(out_dir, exist_ok=True)

    had_failure = False
    tu_entries = []
    dlc_entries = []
    produced_dlcs = set()
    written_output_names = set()

    if args.tu:
        tu_path = args.tu
        tu_base = os.path.basename(tu_path)
        if tu_base != TU_BASENAME:
            print("WARNING: TU basename %r != expected %r; continuing" % (tu_base, TU_BASENAME))
        try:
            tu_sha1 = sha1_file(tu_path)
        except (Exception, SystemExit) as e:
            print("FAILED TU %s: %s" % (tu_path, e))
            had_failure = True
        else:
            print("TU SHA-1: %s" % tu_sha1)
            if tu_sha1.lower() != TU_KNOWN_SHA1:
                print("WARNING: TU SHA-1 %s != known good %s; continuing" % (tu_sha1, TU_KNOWN_SHA1))
            repl = os.path.join(translation, "4B4E07EE", "000B0000", "Title Update #1")
            tu_out_dir = os.path.join(out_dir, TU_OUT_SUBDIR)
            try:
                out_path, _ = repack(tu_path, repl, tu_out_dir, keep_name=True)
            except (Exception, SystemExit) as e:
                print("FAILED TU %s: %s" % (tu_path, e))
                had_failure = True
            else:
                written_output_names.add(os.path.basename(out_path))
                tu_entries.append(os.path.basename(tu_path))

    if dlc_dir:
        dlc_out_dir = os.path.join(out_dir, DLC_OUT_SUBDIR)
        try:
            names = sorted(os.listdir(dlc_dir))
        except Exception as e:
            print("FAILED DLC dir %s: %s" % (dlc_dir, e))
            had_failure = True
            names = []

        inputs = []
        for name in names:
            path = os.path.join(dlc_dir, name)
            if not os.path.isfile(path):
                continue
            if not is_live_file(path):
                continue
            if name in written_output_names:
                continue
            inputs.append((name, path))

        mapped = []
        seen_dlc = {}
        for name, path in inputs:
            try:
                dlc_name = identify_dlc(path)
            except (Exception, SystemExit) as e:
                print("FAILED DLC %s: %s" % (path, e))
                had_failure = True
                continue
            if dlc_name is None:
                print("skipped %s (no translation for it)" % name)
                continue
            if dlc_name in seen_dlc:
                print("ERROR: two inputs map to the same DLC %r: %s and %s" % (dlc_name, seen_dlc[dlc_name], name))
                return 1
            seen_dlc[dlc_name] = name
            mapped.append((name, path, dlc_name))

        for name, path, dlc_name in mapped:
            repl = os.path.join(translation, "4B4E07EE", "00000002", dlc_name)
            try:
                out_path, _ = repack(path, repl, dlc_out_dir, keep_name=False)
            except (Exception, SystemExit) as e:
                print("FAILED DLC %s (%s): %s" % (dlc_name, path, e))
                had_failure = True
                continue
            out_name = os.path.basename(out_path)
            written_output_names.add(out_name)
            produced_dlcs.add(dlc_name)
            dlc_entries.append((dlc_name, name, out_name))

    missing_dlcs = [name for name in ALL_DLC_NAMES if name not in produced_dlcs]
    write_install_txt(out_dir, tu_entries, dlc_entries, missing_dlcs)

    return 1 if had_failure else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
