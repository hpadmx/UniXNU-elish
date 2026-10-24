"""Qualify macOS/Xcode, build the generic baseline or source platform link.

This is not the device build command: no elish prelink, payload, boot image,
donor extraction or USB operation. board.example stays true. All fetched
sources and build side effects belong to this repository's ignored out/.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from upstream_source import qualify
from header_inputs import prepare as prepare_header_inputs

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'out/ci/macos'
EVIDENCE = WORK / 'evidence'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(argv, cwd=ROOT):
    return subprocess.check_output([str(x) for x in argv], cwd=cwd, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preflight', 'bootstrap', 'platform'), default='preflight')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise SystemExit('Requires macOS with Xcode; local firmware builds use separate scripts')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'toolchain qualification; optional generic bootstrap or source platform link; no prelink/boot',
              'mode': args.mode, 'status': 'running', 'device_boot_claimed': False,
              'elish_kernel_link_claimed': False,
              'device_commit': capture(['git', 'rev-parse', 'HEAD']),
              'runner_image': os.environ.get('ImageVersion'),
              'runner_arch': platform.machine(), 'os': platform.platform(),
              'run_id': os.environ.get('GITHUB_RUN_ID')}

    def save():
        (EVIDENCE / 'result.json').write_text(json.dumps(report, indent=2) + '\n')

    try:
        save()
        board = tomllib.loads((ROOT / 'board.toml').read_text())
        assert board['board']['name'] == 'elish' and board['board']['example'] is True
        assert board['ipsw']['version'] == '17.7.11' and board['ipsw']['build'] == '21H461'
        report['xcode'] = capture(['xcodebuild', '-version'])
        report['sdk_version'] = capture(['xcrun', '--sdk', 'iphoneos', '--show-sdk-version'])
        sdk = capture(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'])
        if not report['xcode'].startswith('Xcode 16.4\n') or report['sdk_version'] != '18.5':
            raise ValueError('Expected Xcode 16.4 and iPhoneOS SDK 18.5; requalify a changed runner')
        for tool in ('git', 'make', 'clang', 'cmake', 'ninja', 'unifdef', 'ditto'):
            if not shutil.which(tool):
                raise ValueError('Missing build tool: ' + tool)
        lock = json.loads((ROOT / 'config/sources.lock.json').read_text())['sources']['UniXNU']
        source = WORK / 'UniXNU'
        if not source.exists():
            source.mkdir()
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', lock['repository']], check=True)
        subprocess.run(['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', lock['commit']], check=True)
        subprocess.run(['git', '-C', str(source), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
        if capture(['git', 'rev-parse', 'HEAD'], source) != lock['commit']:
            raise ValueError('UniXNU revision mismatch')
        subprocess.run(['git', '-C', str(source), 'submodule', 'update', '--init',
                        '--recursive', '--depth', '1', '--jobs', '4'], check=True)
        report['qualified_source'] = qualify(source, lock)
        feeds = tomllib.loads((source / 'feeds.toml').read_text())['source']
        xnu = next(item for item in feeds if item['name'] == 'xnu')
        if xnu['version'] != '10063.141.1' or xnu['commit'] != 'd8b80295118ef25ac3a784134bcf95cd8e88109f':
            raise ValueError('Unexpected XNU version')
        report['unixnu_commit'] = lock['commit']
        report['feeds'] = feeds
        report['defconfig_sha256'] = digest(ROOT / 'configs/elish_defconfig')
        probe = EVIDENCE / 'sdk_probe.c'
        probe.write_text('unsigned long elish_sdk_probe(unsigned long x) { return x + 1; }\n')
        subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-target', 'arm64-apple-ios17.0',
                        '-isysroot', sdk, '-ffreestanding', '-c', str(probe),
                        '-o', str(EVIDENCE / 'sdk_probe.o')], check=True)
        report['sdk_arm64_compile_passed'] = True
        save()
        if args.mode in ('bootstrap', 'platform'):
            # Deliberately compile the generic baseline with all device features off.
            # This does not bypass the device CLI's example/prelink safety checks.
            sys.path.insert(0, str(source / 'scripts'))
            from unixnu.config import parse_config
            from unixnu.kernel import version_env
            from unixnu.feeds import Module
            config = parse_config((ROOT / 'configs/elish_defconfig').read_text())
            disabled = {key: '0' for key in config if key.startswith('XNU_')}
            modules = {('refer', item['name']): Module('refer', item['name'], source=item) for item in feeds}
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('XNU_', 'UNIXNU_'))}
            env.update(version_env(modules))
            env.update(disabled)
            env['JOBS'] = '2'
            report['effective_bootstrap_switches'] = disabled
            save()
            reference_names = ['xnu', 'libplatform', 'libdispatch', 'AvailabilityVersions']
            if args.mode == 'platform':
                reference_names += ['corecrypto-2023', 'libpthread']
            for name in reference_names:
                item = next(entry for entry in feeds if entry['name'] == name)
                target = source / 'refer' / name
                target.mkdir(parents=True, exist_ok=True)
                subprocess.run(['git', 'init', '-q', str(target)], check=True)
                subprocess.run(['git', '-C', str(target), 'remote', 'add', 'origin', item['git']], check=True)
                subprocess.run(['git', '-C', str(target), 'fetch', '--depth', '1', 'origin', item['commit']], check=True)
                subprocess.run(['git', '-C', str(target), 'checkout', '--detach', 'FETCH_HEAD'], check=True)
                if capture(['git', 'rev-parse', 'HEAD'], target) != item['commit']:
                    raise ValueError('Reference revision mismatch: ' + name)
            report['header_inputs'] = prepare_header_inputs(source, xnu)
            save()
            log = EVIDENCE / 'bootstrap.log'
            with log.open('w') as stream:
                result = subprocess.run(['bash', 'scripts/build-xnu-4k-baseline.sh'],
                                        cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
            report['bootstrap_exit_code'] = result.returncode
            if result.returncode:
                print('\n'.join(log.read_text(errors='replace').splitlines()[-50:]), flush=True)
                raise ValueError('Bootstrap build failed; see bootstrap.log')
            kernel = source / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf'
            subprocess.run([sys.executable, '-B', 'scripts/audit-xnu-kernel.py', str(kernel)], cwd=source, check=True)
            shutil.copyfile(source / 'build/xnu/kernel-audit.json', EVIDENCE / 'kernel-audit.json')
            shutil.copyfile(kernel, EVIDENCE / 'bootstrap-kernel.macho')
            report['kernel_sha256'] = digest(kernel)
            report['bootstrap_build_and_audit_passed'] = True
            if args.mode == 'platform':
                from platform_archives import build as build_platform
                from unixnu.kernel import xnu_env
                from types import SimpleNamespace
                platform_env = dict(env)
                platform_env.update(xnu_env(SimpleNamespace(need_config=lambda: config, tree=source)))
                report['effective_platform_switches'] = {
                    key: value for key, value in platform_env.items() if key.startswith('XNU_')}
                save()
                build_platform(source, EVIDENCE, platform_env, report, save)
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        save()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
