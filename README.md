# Otomedius G (Gorgeous!) English patch for RGH/JTAG Xbox 360

This lets **GameStone's English translation v1.81** of *Otomedius G (Gorgeous!)* (Japan, 2008) run on a
modded Xbox 360 together with **Title Update #1 and all four DLC packs**.

The translation itself is GameStone's work. This repository adds no translation and contains **no game
files**: you bring your own disc, title update and DLC, plus GameStone's download. What's here is the tooling
that puts the translated files into your own title update and DLC packages so the console accepts them.

## Why this is needed

- If you bake Title Update #1 into `default.xex`, the game crashes right after "Select a Device". It needs the
  real TU package.
- The Esmeralda & Poini and Easter Island DLC need the game updated to v1.0.1 (the TU).
- Two of the translated menu files (`dialog.swf`, `menu.swf`) are only used from inside the TU, and the DLC
  translations only work from inside the DLC packages. Loose files won't do.

`tools/build_patch.py` rebuilds your TU and DLC packages with GameStone's translated files inside. It keeps
their original header and licence data, so they're for RGH/JTAG consoles only.

## What you need

- An RGH/JTAG Xbox 360 (tested with Aurora).
- Your own copy of the Japanese disc (VK016-J1), extracted to the hard drive.
- Your own **Title Update #1**: `TU_15KS1VE_0000004000000.0000000000081`
  (SHA-1 `604ed535e80639f1d9558318f1411934e9622f7f`).
- Any of the four DLC packs you own (all optional):
  Catalog Extra Characters, St. Gradius School Uniform Aoba Anoa, Esmeralda and Poini, Easter Island.
- GameStone's translation v1.81 from Gradius Home World:
  <https://www.gamestone.co.uk/gradiushomeworld/downloads.php>, extracted (folder `English_Translation_v1.81`).
- Python 3 on a PC (standard library only).

## Steps

1. **Game files.** Copy everything in GameStone's `DVD/media/Data/` over the `media/Data/` folder of your
   extracted game. Keep the original `default.xex`; don't apply the TU to it.

2. **Copy from the console to the PC:** your TU file and your DLC packages
   (`Content/0000000000000000/4B4E07EE/00000002/`). Put the DLC packages in one folder by themselves, and
   keep these copies as your backup.

3. **Build:**

   ```
   python tools/build_patch.py --translation <English_Translation_v1.81> --tu <TU file> --dlc <DLC folder> --out <output folder>
   ```

   The script recognises each DLC pack by what's inside it, so file names don't matter. Packs it doesn't
   know (for example the trailer) are skipped. Every rebuilt package is read back and its hashes checked
   before the script prints `PASS`. The output folder ends up with a `Content/...` tree and an `INSTALL.txt`.

4. **Install on the console** (follow `INSTALL.txt`):
   - **Title update:** replace your TU #1 file with the rebuilt one (same file name) and make sure it's
     enabled in Aurora. The Xbox guide shows "TU 1" in its lower right corner when it's loaded.
   - **DLC:** each rebuilt pack gets a new file name. In `Content/0000000000000000/4B4E07EE/00000002/`,
     **delete the original** and copy in the new file. If you keep both, the game may load the Japanese one.

## Known limits

- Some text is stored in the game program itself, not in the data files, so it stays Japanese, for example
  the navigator's message on the weapon CHANGE screen. GameStone's notes say the same.
- The intro and ending movies and the voices aren't translated or subtitled.

## Tested

Tested on a real console (RGH, Aurora) with the TU and all four DLC: menus, character select, weapon select
and the DLC characters all show in English. The rebuilt packages also fixed the texture glitches that the
previously modified DLC packages caused in character and weapon select.

## Tools

- `tools/build_patch.py`: does everything above.
- `tools/repack_dlc.py <package> <replacement_dir> <out_dir> [--keep-name]`: rebuilds a single STFS LIVE
  package with files replaced (rewrites the file table, hash tables and header hash; keeps the original
  header and licence; doesn't re-sign).
- `tools/stfs.py <package> <outdir>`: lists and extracts any STFS (LIVE/PIRS/CON) package.

## Credits

- Translation, translated graphics and English logos: **GameStone** (Gradius Home World), with extra
  translations by deadmario and Sasamisa1806.
- `tools/stfs.py` is a port of Xenia's STFS reader (BSD licence, see `tools/LICENSE-xenia.txt`).
- Otomedius G is © Konami. This project isn't affiliated with Konami or GameStone.

## Licence

The code in this repository is under the MIT licence (`LICENSE`), except where `tools/LICENSE-xenia.txt`
applies.
