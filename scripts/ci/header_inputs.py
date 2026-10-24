"""Prepare upstream header metadata from fixed Git objects, avoiding anonymous API limits.

The unchanged upstream restorer consumes the same metadata format and still
checks every Git blob SHA-1. download_url names the fixed commit, never a tag.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def prepare(source, xnu_feed):
    lock = json.loads((ROOT / 'config/ci-header-sources.lock.json').read_text())
    reference = source / 'refer/xnu'
    cache = source / '.cache/sources'; cache.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.check_output(['git', '-c', 'safe.directory=' + reference.as_posix(),
                                        '-C', str(reference), *args])

    if git('remote', 'get-url', 'origin').decode().strip() != lock['repository']:
        raise ValueError('Unexpected Apple source remote')
    evidence = []
    for item in lock['sources']:
        if xnu_feed[item['feed_key']] != item['tag']:
            raise ValueError('Upstream header tag changed; requalify its commit')
        exists = subprocess.run(['git', '-c', 'safe.directory=' + reference.as_posix(),
                                 '-C', str(reference), 'cat-file', '-e', item['commit'] + '^{commit}'],
                                capture_output=True)
        if exists.returncode:
            git('fetch', '--filter=blob:none', '--depth', '1', 'origin', item['commit'])
        if git('rev-parse', item['commit'] + '^{commit}').decode().strip() != item['commit']:
            raise ValueError('Header commit mismatch')
        for directory, filename in item['directories'].items():
            entries = []
            for line in git('ls-tree', item['commit'] + ':' + directory).decode().splitlines():
                _, kind, sha, name = line.split(None, 3)
                if kind != 'blob' or not name.endswith('.h'):
                    continue
                url = ('https://raw.githubusercontent.com/apple-oss-distributions/xnu/'
                       + item['commit'] + '/' + directory + '/' + name)
                entries.append({'name': name, 'type': 'file', 'sha': sha,
                                'download_url': url})
                evidence.append({'path': directory + '/' + name, 'commit': item['commit'],
                                 'git_blob': sha})
            if not entries:
                raise ValueError('No headers in fixed tree: ' + directory)
            (cache / filename).write_text(json.dumps(entries, indent=2) + '\n')
    return evidence
