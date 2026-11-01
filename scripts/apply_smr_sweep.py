#!/usr/bin/env python3
"""CI pre_build hook: inject a READ-ONLY SMMU census into NativeUfs.cpp.

elish apps_smmu facts measured on hardware today:
  * SMR[86] ignored its write (read back 0) while S2CR/CBAR/SCTLR stuck;
  * a probe sweep writing every stream-match group raised an immediate
    machine reset (the board lands in the firmware update flow), so NO
    SMMR/S2CR stores are safe from this context.

This hook therefore replaces the sweep with a strictly read-only census:
every implemented stream-match group is rendered with its validity,
stream id, mask and S2CR target, plus the global registers, so the DMA
path can be designed against the state the firmware actually left.
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
    raise SystemExit(f'smmu census: {target} still missing after submodule init')

src = target.read_text()
if 'SMMU census' in src:
    print('smmu census already applied')
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
    // READ-ONLY SMMU census. A store to a locked stream-match group raised
    // an immediate reset on this board, so not a single SMMU write happens
    // here. Render the complete firmware state instead: every implemented
    // stream-match group with its validity, stream id, mask and S2CR
    // target, plus the global registers, so the DMA path can be designed
    // against what the firmware actually left behind.
    //
    static char census[1800];
    uint32_t cr0 = read32 (smmu, 0x0);
    uint32_t idr0 = read32 (smmu, 0x20);
    uint32_t idr1 = read32 (smmu, 0x24);
    uint32_t idr2 = read32 (smmu, 0x28);
    unsigned groups = idr0 & 255;
    if (groups > 128) groups = 128;
    unsigned valid = 0, freeg = 0;
    int n = snprintf (census, sizeof (census),
                      "SMMU census CR0=%x IDR0=%x IDR1=%x IDR2=%x groups=%u\\n",
                      cr0, idr0, idr1, idr2, groups);
    for (unsigned i = 0; i < groups && n > 0 && n < (int) sizeof (census) - 48; ++i) {
      uint32_t smr = read32 (smmu, 0x800 + 4 * i);
      if (!(smr & 0x80000000U)) { freeg++; continue; }
      uint32_t s2cr = read32 (smmu, 0xc00 + 4 * i);
      valid++;
      n += snprintf (census + n, sizeof (census) - n,
                     "%u:sm=%x s2=%x\\n", i, smr, s2cr);
    }
    n += snprintf (census + n, sizeof (census) - n,
                   "valid=%u free=%u idx=%u bank=%u\\n", valid, freeg, index, bank);
    rootDiagnosticFailure (failure);
    panic ("NATIVE_UFS smmu census: %s", census);
  }"""

if anchor not in src:
    raise SystemExit('smmu census: anchor block not found in NativeUfs.cpp')
target.write_text(src.replace(anchor, replacement, 1))
print('smmu census injected into', target)
