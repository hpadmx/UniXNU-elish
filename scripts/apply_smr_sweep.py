#!/usr/bin/env python3
"""CI pre_build hook: inject the SMR sweep + retry into NativeUfs.cpp.

elish apps_smmu: SMR[86] ignored its write (read back 0) while S2CR/CBAR/
SCTLR all stuck, so the group is likely locked by firmware. This hook
initializes the drivers/NativeUfs submodule if needed, then injects a
failure-path patch: sweep all 128 stream-match groups with a valid-bit-
clear probe write (cannot influence stream matching), retry the takeover
on the first writable group, and carry the writable mask on the panic
screen if nothing is writable.

The later "git submodule update --init --recursive" from the build is a
no-op once the submodule sits at its pinned commit, so the patch
survives into the compile.
"""
import os
import subprocess
import sys
from pathlib import Path

device = Path(os.environ.get('DEVICE_DIR')
              or os.environ.get('UNIXNU_DEVICE')
              or (Path(__file__).resolve().parent.parent))
unixnu = device.parent
sub = unixnu / 'drivers' / 'NativeUfs'
target = sub / 'NativeUfs.cpp'

if not target.is_file():
    subprocess.run(['git', '-C', str(unixnu), 'submodule', 'update', '--init',
                    '--depth', '1', 'drivers/NativeUfs'], check=True)
if not target.is_file():
    raise SystemExit(f'smr sweep: {target} still missing after submodule init')

src = target.read_text()
if 'SMR sweep' in src:
    print('smr sweep already applied')
    sys.exit(0)

anchor = """  if (failure) {
    gRootDiagnostic.stage = "SMMU readback";
    gRootDiagnostic.result = static_cast<uint32_t> (kIOReturnNotReady);
    rootDiagnosticFailure (failure);
    panic ("NATIVE_UFS smmu: %s idx=%u bank=%u cba2r=%x", failure, index, bank,
           gRootDiagnostic.cba2r);
  }"""

replacement = """  if (failure) {
    //
    // SMR sweep: some stream-match groups ignore NS writes on this board.
    // Probe all 128 groups with the VALID BIT CLEAR (such a write cannot
    // influence stream matching, so it is safe on a live SMMU), then retry
    // the takeover on the first group that accepts it.
    //
    uint64_t  writable[2] = {0, 0};
    unsigned  chosen = 0xffffffffU;
    for (unsigned i = 0; i < 128; ++i) {
      uint32_t before = read32 (smmu, 0x800 + 4 * i);
      if (before & 0x80000000U) continue;
      write32 (smmu, 0x800 + 4 * i, 0x60);
      uint32_t after = read32 (smmu, 0x800 + 4 * i);
      write32 (smmu, 0x800 + 4 * i, before);
      if (after == 0x60) {
        writable[i / 64] |= 1ULL << (i % 64);
        if (chosen == 0xffffffffU) chosen = i;
      }
    }
    if (chosen != 0xffffffffU) {
      write32 (smmu, 0xc00 + 4 * chosen, bank);
      write32 (smmu, 0x800 + 4 * chosen, 0x80000000U | stream);
      if (read32 (smmu, 0xc00 + 4 * chosen) == bank &&
          read32 (smmu, 0x800 + 4 * chosen) == (0x80000000U | stream)) {
        report[0] = chosen | (bank << 16);
        report[1] = read32 (smmu, 0x1000 + 4 * bank);
        report[2] = read32 (cb, 0x0);
        return;
      }
    }
    uint32_t  validLo = 0, validHi = 0;
    for (unsigned i = 0; i < 64; ++i) {
      if (read32 (smmu, 0x800 + 4 * i) & 0x80000000U) validLo |= 1U << i;
    }
    for (unsigned i = 64; i < 128; ++i) {
      if (read32 (smmu, 0x800 + 4 * i) & 0x80000000U) validHi |= 1U << (i - 64);
    }
    gRootDiagnostic.smr = writable[0];
    gRootDiagnostic.s2cr = writable[1];
    gRootDiagnostic.smmuId1 = validLo;
    gRootDiagnostic.smmuId2 = validHi;
    gRootDiagnostic.stage = "SMMU readback";
    gRootDiagnostic.result = static_cast<uint32_t> (kIOReturnNotReady);
    rootDiagnosticFailure (failure);
    panic ("NATIVE_UFS smmu: %s idx=%u bank=%u cba2r=%x wr=%016llx%016llx ch=%u",
           failure, index, bank, gRootDiagnostic.cba2r,
           writable[1], writable[0], chosen);
  }"""

if anchor not in src:
    raise SystemExit('smr sweep: anchor block not found in NativeUfs.cpp')
target.write_text(src.replace(anchor, replacement, 1))
print('smr sweep injected into', target)
