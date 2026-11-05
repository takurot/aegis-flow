#!/usr/bin/env python3
"""Record Sourcify exact/partial match status for the fixed-block dependency targets."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = ROOT / 'forks' / 'aave-v3-dependencies-19000000.json'


def query(address):
    url = f'https://sourcify.dev/server/v2/contract/1/{address}'
    request = urllib.request.Request(url, headers={'User-Agent': 'aegis-flow-verify'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
    except Exception as error:
        return {'address': address, 'error': type(error).__name__}
    return {
        'address': address,
        'match': body.get('match'),
        'runtimeMatch': body.get('runtimeMatch'),
        'creationMatch': body.get('creationMatch'),
        'matchId': body.get('matchId'),
        'verifiedAt': body.get('verifiedAt'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'forks/aave-v3-sourcify-19000000.json')
    args = parser.parse_args()
    dependencies = json.loads(DEPENDENCIES.read_text())
    targets = {
        name: query(target['address'])
        for name, target in dependencies['targets'].items()
        if target.get('address')
    }
    for result in targets.values():
        time.sleep(0.2)
    summary = {
        'total': len(targets),
        'exact': sum(1 for item in targets.values() if item.get('runtimeMatch') == 'exact_match'),
        'partial': sum(1 for item in targets.values() if item.get('runtimeMatch') == 'match'),
        'errors': sum(1 for item in targets.values() if item.get('error')),
    }
    args.out.write_text(json.dumps({
        'chain_id': 1,
        'block': 19000000,
        'source': 'Sourcify v2',
        'targets': targets,
        'summary': summary,
    }, indent=2) + '\n')
    print(f'Wrote {args.out}')
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
