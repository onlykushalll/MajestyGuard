"""Regenerate requirements.lock.txt (sha256 of every PyPI file of each pinned version).

Needs network access to pypi.org. Run from anywhere:  python tools/gen_requirements_lock.py
"""
import concurrent.futures as cf
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pins=[]
for line in open(ROOT / 'requirements.txt', encoding='utf-8'):
    line=line.split('#')[0].strip()
    if not line: continue
    m=re.match(r'^([A-Za-z0-9_.\-]+)==([^\s;]+)',line)
    if not m: print('UNPARSED',line,file=sys.stderr); sys.exit(1)
    pins.append((m.group(1),m.group(2),line))
def fetch(p):
    name,ver,line=p
    url=f'https://pypi.org/pypi/{name}/{ver}/json'
    for _ in range(3):
        try:
            with urllib.request.urlopen(url,timeout=30) as r:
                d=json.load(r)
            hs=sorted({f['digests']['sha256'] for f in d['urls']})
            return name,ver,line,hs
        except Exception as e:
            err=e
    return name,ver,line,None
with cf.ThreadPoolExecutor(8) as ex:
    res=list(ex.map(fetch,pins))
bad=[r for r in res if not r[3]]
for r in bad: print('MISSING',r[0],r[1],file=sys.stderr)
out=['# Hash-pinned lock for requirements.txt (every wheel/sdist PyPI lists for each pinned version).',
     '# Install with:  python -m pip install --require-hashes -r requirements.lock.txt',
     '# Regenerate after changing requirements.txt:  python tools/gen_requirements_lock.py',
     '# Hard rules in requirements.txt (single opencv, protobuf bound, ...) still apply.']
for name,ver,line,hs in res:
    if not hs: continue
    out.append(f'{name}=={ver} \\')
    out.append(' \\\n'.join(f'    --hash=sha256:{h}' for h in hs))
open(ROOT / 'requirements.lock.txt', 'w', encoding='utf-8', newline='\n').write('\n'.join(out)+'\n')
print(len(res)-len(bad),'locked',len(bad),'missing')
