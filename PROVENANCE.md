# elish port provenance

This repository (`UniXNU-elish`, Xiaomi Pad 5 Pro Wi-Fi, SM8250-AC / SD870) was derived on
2026-10-24 by scripted adaptation (rename + targeted value resets) of:

- `Tlrenhb/UniXNU-enuma` — device repo of the Pad 5 Pro 5G (same board family, k81).
  All board.toml citations referring to "elish" inside comments originally documented the
  enuma device; consult the upstream repo for the original wording and measurements.
- `wh1stl3/UniXNU-enuma` — second enuma port (UEFI Boot v3 bring-up methodology).
- `reAAAq/UniXNU-umi` — hardware-proven SM8250 reference (iPadOS 17.7.11 Hello, UFS, Data).
- `UniXNU/UniXNU` — the base project (BSD-2-Clause-Patent).

Values deliberately reset to placeholders (must come from THIS device or ITS firmware):
- `[userdata] first_lba/last_lba` — elish GPT (rawprogram.xml of
  elish_images_OS1.0.2.0.TKYCNXM or on-device probe).
- `[payload] dram_size` — 6 GiB vs 8 GiB variant of this unit.
- uefi-elish Binaries + MemoryMap/ConfigurationMap — to be regenerated from the elish
  ROM's xbl.elf via UEFIReader (see uefi-elish/scripts/extract-vendor-dxe.sh), not
  inherited from enuma dumps.

Everything else (IPSW pins, kernel archives, defconfigs, SoC-level values such as
chip_id 0x164 / kona-v2.1 base / cpu_regs) carries over: same SoC, same board family,
same target image (iPad_Pro_HFR 17.7.11 21H461, iPad7,1/j120ap).
