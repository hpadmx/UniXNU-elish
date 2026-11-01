#!/usr/bin/env python3
"""CI pre_build hook: give the UFS stream (0x5c0, stock elish DT) an SMMU
bypass mapping by sweeping the free stream-match groups.

Hardware facts measured today (boot_full34..39):
  * SMR[0] probe write  -> immediate machine reset (secure/reserved group);
  * SMR[86] probe write -> silently ignored (last declared group, RAZ/WI);
  * S2CR[86] write      -> sticks (the S2CR class is writable);
  * CBAR/CBA2R/SCTLR    -> stick;
  * census: only groups 4 and 103..127 hold valid firmware entries, and the
    103..127 reads are beyond the declared 87 groups (aliased/garbage);
  * stock elish DT: UFS host iommus = <apps_smmu 0x5c0 0x0>, and no SMR
    entry matches 0x5c0 (XBL cleaned its own entry at hand-off).

So the takeover must find a WRITABLE free group among 5..86 and program
bypass for stream 0x5c0 there: S2CR first (bypass flavor, known to stick),
then SMR valid with the stream id. Every step is read-verified and the
group is restored if either step fails, so the sweep cannot leave a half
configuration behind.
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
    // SMR sweep: give stream 0x5c0 (the stock elish DT's iommus for the UFS
    // host controller) a bypass S2CR in the first FREE stream-match group
    // that accepts writes. Groups 0..4 are poison (0 resets the machine,
    // 4 is the firmware's live entry); 86 ignores writes; 5..85 were never
    // reached before. Every step is read-verified and restored on failure,
    // so the sweep cannot leave a half configuration behind. A compact
    // census rides along on the panic screen either way.
    //
    char     census[600];
    unsigned chosen = 0xffffffffU, writable = 0, locked = 0;
    unsigned first = 5, last = 86;
    int n = snprintf (census, sizeof (census), "SMMU sweep CR0=%x:\\n",
                      read32 (smmu, 0x0));
    for (unsigned i = first; i <= last; ++i) {
      uint32_t smrBefore = read32 (smmu, 0x800 + 4 * i);
      if (smrBefore & 0x80000000U) continue;
      uint32_t s2crBefore = read32 (smmu, 0xc00 + 4 * i);
      write32 (smmu, 0xc00 + 4 * i, 0x49);
      if (read32 (smmu, 0xc00 + 4 * i) != 0x49) {
        write32 (smmu, 0xc00 + 4 * i, s2crBefore);
        locked++;
        n += snprintf (census + n, sizeof (census) - n, "s%u:X\\n", i);
        continue;
      }
      write32 (smmu, 0x800 + 4 * i, 0x80000000U | 0x5c0);
      uint32_t smrAfter = read32 (smmu, 0x800 + 4 * i);
      write32 (smmu, 0x800 + 4 * i, smrBefore);
      write32 (smmu, 0xc00 + 4 * i, s2crBefore);
      if (smrAfter == (0x80000000U | 0x5c0)) {
        chosen = i;
        writable++;
        n += snprintf (census + n, sizeof (census) - n, "g%u:W\\n", i);
        break;
      }
      locked++;
      n += snprintf (census + n, sizeof (census) - n, "m%u:X\\n", i);
    }
    if (chosen != 0xffffffffU) {
      write32 (smmu, 0xc00 + 4 * chosen, 0x49);
      write32 (smmu, 0x800 + 4 * chosen, 0x80000000U | 0x5c0);
      if (read32 (smmu, 0xc00 + 4 * chosen) == 0x49 &&
          read32 (smmu, 0x800 + 4 * chosen) == (0x80000000U | 0x5c0)) {
        report[0] = chosen | (bank << 16);
        report[1] = read32 (smmu, 0x1000 + 4 * bank);
        report[2] = read32 (cb, 0x0);
        return;
      }
    }
    n += snprintf (census + n, sizeof (census) - n,
                   "chosen=%u writable=%u locked=%u\\n",
                   chosen, writable, locked);
    snprintf (gRootMediaText, sizeof (gRootMediaText), "%s", census);
    gRootDiagnostic.stage = "SMMU readback";
    gRootDiagnostic.result = static_cast<uint32_t> (kIOReturnNotReady);
    rootDiagnosticFailure (failure);
    panic ("NATIVE_UFS smmu: %s idx=%u bank=%u sweep=%u/%u/%u", failure,
           index, bank, chosen, writable, locked);
  }"""

if anchor not in src:
    raise SystemExit('smr sweep: anchor block not found in NativeUfs.cpp')
target.write_text(src.replace(anchor, replacement, 1))
print('smr sweep injected into', target)
