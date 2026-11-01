#!/usr/bin/env python3
"""CI pre_build hook: run the UFS probe with ZERO SMMU writes.

Hardware facts measured today (boot_full34..40):
  * the UFS host controller is alive (HC ver 3.0) but its stream (0x5c0 per
    the stock elish DT) has no SMMU mapping, so its first DMA faults;
  * NS stores to the stream-match registers are firmware-owned: SMR[0] and
    SMR[5] probes reset the machine instantly, SMR[86] is silently ignored,
    and the CR0 CLIENTPD store also resets;
  * S2CR/CBAR/CBA2R/SCTLR stores stick, but without an SMR they are dead.

So this hook makes smmuBypassStream a no-op (the takeover is skipped
entirely, nothing in the SMMU is written) and teaches the failure renderer
to also dump the SMMU global fault registers (GFSR/GFSYNR0..2) - the
faulting UFS DMA records its real stream ID there, which is the ground
truth every further fix needs.
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
    raise SystemExit(f'smmu skip: {target} still missing after submodule init')

src = target.read_text()
if 'SMMU SKIP' in src:
    print('smmu skip already applied')
    sys.exit(0)

# 1. smmuBypassStream: take no SMMU action at all.
fn_anchor = """static void smmuBypassStream (uint16_t stream, OSSharedPtr<IOMemoryMap> &keep, uint32_t *report,
                             uint64_t dma, uint64_t bytes)
{"""
fn_skip = """static void smmuBypassStream (uint16_t stream, OSSharedPtr<IOMemoryMap> &keep, uint32_t *report,
                             uint64_t dma, uint64_t bytes)
{
  //
  // SMMU SKIP: every useful SMMU store (SMR entries, CR0 flags) is owned by
  // the firmware on this board and resets the machine. Run the UFS probe
  // with the SMMU untouched instead; the first UFS DMA will fault, the
  // global fault registers will record the real stream ID, and
  // rootDiagnosticFailure renders them.
  //
  snprintf (gRootMediaText, sizeof (gRootMediaText),
            "SMMU SKIP (no writes; stream 0x%x unmapped)", stream);
  report[0] = 0;
  report[1] = 0;
  report[2] = 0;
  return;
"""
if fn_anchor not in src:
    raise SystemExit('smmu skip: smmuBypassStream anchor not found')
src = src.replace(fn_anchor, fn_skip, 1)

# 2. rootDiagnosticFailure: append the SMMU global fault registers.
fail_anchor = """  formatUfsRootDiagnostics (gRootDiagnosticText, sizeof (gRootDiagnosticText), gRootDiagnostic, reason);
  strlcat (gRootDiagnosticText, gRootMediaText, sizeof (gRootDiagnosticText));"""
fail_add = """  formatUfsRootDiagnostics (gRootDiagnosticText, sizeof (gRootDiagnosticText), gRootDiagnostic, reason);
  strlcat (gRootDiagnosticText, gRootMediaText, sizeof (gRootDiagnosticText));
  {
    //
    // The SMMU's own fault record: GFSYNR1 carries the stream ID of the
    // faulting DMA - the ground truth for the UFS stream question.
    //
    OSSharedPtr<IOMemoryMap> fkeep;
    uintptr_t fb = mapPhys (0x15000000ULL, 0xd0000, fkeep);
    if (fb) {
      char ftxt[192];
      snprintf (ftxt, sizeof (ftxt), "\\nSMMUFLT gfsr=%x syn0=%x syn1=%x syn2=%x",
                read32 (fb, 0x8f0), read32 (fb, 0x8f8),
                read32 (fb, 0x8fc), read32 (fb, 0x900));
      strlcat (gRootDiagnosticText, ftxt, sizeof (gRootDiagnosticText));
    }
  }"""
if fail_anchor not in src:
    raise SystemExit('smmu skip: rootDiagnosticFailure anchor not found')
src = src.replace(fail_anchor, fail_add, 1)

target.write_text(src)
print('smmu skip + fault register dump injected into', target)
