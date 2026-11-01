#!/usr/bin/env python3
"""CI pre_build hook: inject a READ-ONLY SMMU census into NativeUfs.cpp.

elish apps_smmu facts measured on hardware today:
  * SMR[86] ignored its write (read back 0) while S2CR/CBAR/SCTLR stuck;
  * a probe sweep writing every stream-match group raised an immediate
    machine reset (the board lands in the firmware update flow), so NO
    SMR/S2CR stores are safe from this context.

This hook therefore replaces the sweep with a strictly read-only census:
every implemented stream-match group is rendered with its validity,
stream id, mask and S2CR target, plus the global registers. The census
text goes into gRootMediaText, which rootDiagnosticFailure appends to
the rendered panic screen (the declaration is moved above
smmuBypassStream so the census can fill it).
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

# Move the gRootMediaText declaration above smmuBypassStream so the census
# can write into it; rootDiagnosticFailure appends it to the rendered text.
later_decl = """static char gRootDiagnosticText[2500];
static char gRootMediaText[1000];"""
early_decl = """static char gRootMediaText[1000];
static char gRootDiagnosticText[2500];"""
if later_decl not in src:
    raise SystemExit('smmu census: gRootMediaText declaration not found')
src = src.replace(later_decl, 'static char gRootDiagnosticText[2500];', 1)

anchor_fn = 'static void smmuBypassStream (uint16_t stream, OSSharedPtr<IOMemoryMap> &keep, uint32_t *report,'
if anchor_fn not in src:
    raise SystemExit('smmu census: smmuBypassStream not found')
src = src.replace(anchor_fn, 'static char gRootMediaText[1000];\n\n' + anchor_fn, 1)

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
    int n = snprintf (gRootMediaText, sizeof (gRootMediaText),
                      "SMMU census CR0=%x IDR0=%x IDR1=%x IDR2=%x groups=%u\\n",
                      read32 (smmu, 0x0), read32 (smmu, 0x20),
                      read32 (smmu, 0x24), read32 (smmu, 0x28),
                      (unsigned) ((read32 (smmu, 0x20) & 255) > 128
                                  ? 128 : (read32 (smmu, 0x20) & 255)));
    for (unsigned i = 0; i < 128 && n > 0 && n < (int) sizeof (gRootMediaText) - 48; ++i) {
      uint32_t smr = read32 (smmu, 0x800 + 4 * i);
      if (!(smr & 0x80000000U)) continue;
      uint32_t s2cr = read32 (smmu, 0xc00 + 4 * i);
      n += snprintf (gRootMediaText + n, sizeof (gRootMediaText) - n,
                     "%u:sm=%x s2=%x\\n", i, smr, s2cr);
    }
    rootDiagnosticFailure (failure);
    panic ("NATIVE_UFS smmu census idx=%u bank=%u", index, bank);
  }"""

if anchor not in src:
    raise SystemExit('smmu census: anchor block not found in NativeUfs.cpp')
target.write_text(src.replace(anchor, replacement, 1))
print('smmu census injected into', target)
