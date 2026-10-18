"""Fetch pinned official CPython and verify cached wheels against PyPI metadata."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import time
from pathlib import Path
from urllib.request import urlopen, Request
import zipfile

PYTHON_VERSION='3.14.8'
PYTHON_NAME=f'python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_URL=f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_NAME}'
PYTHON_SHA256='a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310'

def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'YukinoCore-Windows-Builder/1.0'}),timeout=45) as response:
                return response.read()
        except OSError:
            if attempt==2:raise
            time.sleep(1)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',required=True,type=Path)
    args=parser.parse_args();root=args.cache;root.mkdir(parents=True,exist_ok=True)
    python=root/PYTHON_NAME
    if not python.exists():python.write_bytes(fetch(PYTHON_URL))
    assert hashlib.sha256(python.read_bytes()).hexdigest()==PYTHON_SHA256,'Python checksum mismatch'
    sbom=python.with_name(PYTHON_NAME+'.spdx.json')
    sbom.write_bytes(fetch(PYTHON_URL+'.spdx.json'))
    wheels=[]
    for path in sorted(root.glob('*.whl')):
        with zipfile.ZipFile(path) as archive:
            name=next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            metadata=BytesParser().parsebytes(archive.read(name))
        project,version=metadata['Name'],metadata['Version']
        details=json.loads(fetch(f'https://pypi.org/pypi/{project}/{version}/json'))
        entry=next(e for e in details['urls'] if e['filename']==path.name)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest==entry['digests']['sha256'],'PyPI checksum mismatch: '+path.name
        wheels.append({'name':project,'version':version,'file':path.name,'sha256':digest,
                       'url':entry['url'],'license':metadata.get('License-Expression') or metadata.get('License','UNKNOWN')})
    assert wheels,'Download the requirements wheels before running this helper'
    lock={'python':{'version':PYTHON_VERSION,'file':PYTHON_NAME,'sha256':PYTHON_SHA256,'url':PYTHON_URL},'wheels':wheels}
    (root/'dependency-lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'python':PYTHON_VERSION,'verified_wheels':len(wheels)}))

if __name__=='__main__':main()
