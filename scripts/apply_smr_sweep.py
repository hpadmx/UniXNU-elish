#!/usr/bin/env python3
"""CI pre_build hook: run the UFS probe with ZERO SMMU writes, and render
the SMMU fault registers on any diagnostic panic.

Hardware facts measured today (boot_full34..41):
  * the UFS host controller is alive (HC ver 3.0) but its stream (0x5c0 per
    the stock elish DT) has no SMMU mapping, so its first DMA faults;
  * NS stores to the stream-match registers are firmware-owned: SMR[0] and
    SMR[5] probes reset the machine instantly, SMR[86] is silently ignored,
    and the CR0 CLIENTPD store also resets;
  * with the takeover skipped entirely, the kernel reaches the UFS DMA and
    the SMMU faults it (expected); the driver's diagnostic path then panics
    with a deliberate BRK ("Panic (by design)").

This hook keeps the takeover skipped and teaches the driver's failure
renderer to also dump the SMMU global fault registers (GFSR/GFSYNR0..2) -
GFSYNR1 carries the stream ID of the faulting UFS DMA, the ground truth
for the mapping fix.
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

lines = src.split('\n')

# 1. Move the gRootMediaText declaration above smmuBypassStream.
pair_idx = None
for i, l in enumerate(lines):
    if l.strip() == 'static char gRootDiagnosticText[2500];' and \
       i + 1 < len(lines) and lines[i + 1].strip() == 'static char gRootMediaText[1000];':
        pair_idx = i
        break
if pair_idx is not None:
    decl_line = lines[pair_idx + 1]
    del lines[pair_idx + 1]
    fn_idx = next(i for i, l in enumerate(lines) if l.startswith('static void smmuBypassStream ('))
    lines.insert(fn_idx, decl_line)

# 2. smmuBypassStream: take no SMMU action at all.
fn_idx = next(i for i, l in enumerate(lines) if l.startswith('static void smmuBypassStream ('))
brace_idx = fn_idx
while lines[brace_idx].rstrip() != '{':
    brace_idx += 1
skip_block = """  //
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
  return;"""
lines.insert(brace_idx + 1, skip_block)

# 3. rootDiagnosticFailure: append the SMMU global fault registers after the
#    gRootMediaText strlcat (line-anchored, alignment-immune).
cat_idx = next(i for i, l in enumerate(lines)
               if l.strip() == 'strlcat (gRootDiagnosticText, gRootMediaText, sizeof (gRootDiagnosticText));')
fault_block = """  {
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
lines[cat_idx + 1:cat_idx + 1] = fault_block.split('\n')

src = '\n'.join(lines)
if 'SMMU SKIP' not in src or 'SMMUFLT' not in src:
    raise SystemExit('smmu skip: injection verification failed')
target.write_text(src)
print('smmu skip + fault register dump injected into', target)
