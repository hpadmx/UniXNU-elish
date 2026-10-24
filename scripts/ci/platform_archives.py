"""Reuse fixed source builders for a platform kernel link, without donor prelink."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_inputs(source, sheng):
    lock = json.loads((ROOT / 'config/kernel-archives.lock.json').read_text())
    if sha(ROOT / lock['staged_recipe_patch']['path']) != lock['staged_recipe_patch']['sha256']:
        raise ValueError('Staged recipe patch hash changed')
    for entry in lock['inputs']:
        base = source if entry['source'] == 'UniXNU' else sheng
        actual = subprocess.check_output(['git', '-c', f'safe.directory={base.as_posix()}',
                                          '-C', str(base), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != entry['commit'] or sha(base / entry['path']) != entry['sha256']:
            raise ValueError('Archive recipe mismatch: ' + entry['path'])
        blob = subprocess.check_output(['git', '-c', f'safe.directory={base.as_posix()}',
                                       '-C', str(base), 'show', f"{actual}:{entry['path']}"])
        if blob != (base / entry['path']).read_bytes():
            raise ValueError('Archive recipe differs from Git blob')
    return lock


def stage_recipe(source, sheng, lock):
    staged = source / 'scripts/elish-build-arm-driver.py'
    staged.write_bytes((sheng / 'scripts/build-arm-driver.py').read_bytes())
    patch = ROOT / lock['staged_recipe_patch']['path']
    subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)
    if sha(staged) != lock['staged_recipe_patch']['output_sha256']:
        raise ValueError('Staged generic recipe differs from reviewed patch output')
    return staged


def build(source, evidence, env, report, save):
    """Invoked only after a real bootstrap produced the required compile records."""
    from unixnu.kernel import toolchain_path
    locks = json.loads((ROOT / 'config/sources.lock.json').read_text())['sources']
    reference = locks['UniXNU-sheng']
    sheng = source.parent / 'archive-recipes/UniXNU-sheng'
    sheng.mkdir(parents=True, exist_ok=True)
    if not (sheng / '.git').exists():
        subprocess.run(['git', 'init', '-q', str(sheng)], check=True)
        subprocess.run(['git', '-C', str(sheng), 'remote', 'add', 'origin', reference['repository']], check=True)
    subprocess.run(['git', '-C', str(sheng), 'fetch', '--depth', '1', 'origin', reference['commit']], check=True)
    subprocess.run(['git', '-C', str(sheng), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
    lock = checked_inputs(source, sheng)
    # Only the generic recipe plus the hash-locked compiler compatibility patch.
    # No sheng board/config/addresses are imported.
    staged = stage_recipe(source, sheng, lock)
    report['archive_recipe_lock'] = lock
    report['staged_generic_recipe_sha256'] = sha(staged)
    records = ['iokit/DEVELOPMENT/IOPlatformExpert.cpo.json', 'osfmk/DEVELOPMENT/prng_random.o.json']
    obj = source / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    report['archive_compile_records'] = {}
    for relative in records:
        path = obj / relative
        if not path.is_file():
            raise ValueError('Missing actual XNU compile record: ' + relative)
        report['archive_compile_records'][relative] = sha(path)
    report['archive_steps'] = []
    save()
    build_env = dict(env, PATH=toolchain_path())
    for index, step in enumerate(lock['steps'], 1):
        command = [sys.executable, '-B', str(source / 'scripts' / step[0]), *step[1:]]
        logfile = evidence / f'platform-archive-{index:02d}.log'
        with logfile.open('w') as stream:
            result = subprocess.run(command, cwd=source, env=build_env, stdout=stream, stderr=subprocess.STDOUT)
        report['archive_steps'].append({'recipe': step, 'exit_code': result.returncode, 'log': logfile.name})
        save()
        if result.returncode:
            print('\n'.join(logfile.read_text(errors='replace').splitlines()[-40:]), flush=True)
            raise ValueError(f'Archive step {index} failed')
    report['platform_archive_sha256'] = {}
    for relative in lock['archives']:
        report['platform_archive_sha256'][relative] = sha(source / relative)
    save()
    logfile = evidence / 'platform-kernel.log'
    with logfile.open('w') as stream:
        result = subprocess.run(['bash', 'scripts/build-xnu-4k-baseline.sh'], cwd=source,
                                env=build_env, stdout=stream, stderr=subprocess.STDOUT)
    report['platform_kernel_exit_code'] = result.returncode
    save()
    if result.returncode:
        print('\n'.join(logfile.read_text(errors='replace').splitlines()[-60:]), flush=True)
        raise ValueError('Platform kernel link failed')
    kernel = obj / 'mach.development.bcm2837.unstripped.noctf'
    subprocess.run([sys.executable, '-B', 'scripts/audit-xnu-kernel.py', str(kernel)], cwd=source, check=True)
    shutil.copyfile(source / 'build/xnu/kernel-audit.json', evidence / 'platform-kernel-audit.json')
    names = subprocess.check_output(['xcrun', 'nm', '-g', '-U', '-j', str(kernel)], text=True).splitlines()
    required = {'_armdt_platform_configured', '_armcpu_registered', '_source_pthread_status'}
    report['platform_required_symbols'] = {name: name in names for name in sorted(required)}
    if not required.issubset(names):
        raise ValueError('Platform kernel lacks expected real provider symbols')
    shutil.copyfile(kernel, evidence / 'platform-kernel.macho')
    report['platform_kernel_sha256'] = sha(kernel)
    report['platform_build_and_link_passed'] = True
    report['elish_kernel_link_claimed'] = True
    report['donor_prelink_generated'] = False
    report['boot_candidate_generated'] = False
    save()


if __name__ == '__main__':
    # Offline local audit only; the actual build is guarded by the macOS runner.
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unixnu', type=Path, required=True)
    parser.add_argument('--sheng', type=Path, required=True)
    args = parser.parse_args()
    lock = checked_inputs(args.unixnu.resolve(), args.sheng.resolve())
    print(f"PASS: {len(lock['inputs'])} pinned recipe inputs, {len(lock['steps'])} ordered steps, four archives")
