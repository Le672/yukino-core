"""Bounded private media spool. No arbitrary paths, URL fetches or QQ cache reads."""
import base64
import hashlib
import os
from pathlib import Path
import re

LIMIT=8*1024*1024
QUOTA=256*1024*1024
TYPES={'.png':'image/png','.jpg':'image/jpeg','.gif':'image/gif','.webp':'image/webp',
       '.pdf':'application/pdf','.zip':'application/zip','.txt':'text/plain','.md':'text/plain','.cbz':'application/zip'}


def image_extension(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return '.png'
    if raw.startswith(b'\xff\xd8\xff'):return '.jpg'
    if raw.startswith((b'GIF87a',b'GIF89a')):return '.gif'
    if len(raw)>=12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':return '.webp'
    raise ValueError('Expected PNG, JPEG, GIF or WebP image bytes')


class MediaStore:
    def __init__(self,directory):
        self.root=Path(directory).absolute()
        if any(p.is_symlink() for p in (self.root,*self.root.parents)):raise ValueError('Linked media directory')
        self.root.mkdir(parents=True,exist_ok=True)
        for folder in ('inbox','outbox'):
            p=self.root/folder
            if p.is_symlink():raise ValueError('Linked media subdirectory')
            p.mkdir(exist_ok=True)

    def stats(self):
        files=0;size=0
        for p in self.root.rglob('*'):
            if p.is_symlink():raise ValueError('Linked spool entry')
            if p.is_file():files+=1;size+=p.stat().st_size
            if files>1000:raise ValueError('Media spool exceeds 1000 files; archive old media first')
        return {'files':files,'bytes':size,'quota_bytes':QUOTA,'max_file_bytes':LIMIT}

    def ingest(self,raw,kind='file',name=None):
        if not isinstance(raw,bytes) or not 0<len(raw)<=LIMIT:raise ValueError('Media must be 1..8 MiB')
        if kind=='image':
            ext=image_extension(raw)
            if name is None:name='image'+ext
            if Path(name).suffix.lower()!=ext:raise ValueError('Image filename and content type disagree')
        elif kind=='file':
            if not isinstance(name,str):raise ValueError('A file needs a filename')
            ext=Path(name).suffix.lower()
            if ext not in TYPES:raise ValueError('Allowed files: images, PDF, ZIP/CBZ, TXT/MD')
            if ext in ('.png','.jpg','.gif','.webp') and image_extension(raw)!=ext:raise ValueError('Invalid image signature')
            if ext=='.pdf' and not raw.startswith(b'%PDF-'):raise ValueError('Invalid PDF signature')
            if ext in ('.zip','.cbz') and not raw.startswith((b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08')):raise ValueError('Invalid ZIP signature')
        else:raise ValueError('Only image/file media is supported')
        if not isinstance(name,str) or not 1<=len(name)<=100 or name in ('.','..') or any(c in name for c in '/\\:<>|?*\x00\r\n') or name.endswith((' ','.')) or any(ord(c)<32 for c in name):
            raise ValueError('Invalid media filename')
        sha=hashlib.sha256(raw).hexdigest();key=sha+'/'+name
        folder=self.root/'outbox'/sha
        if any(p.is_symlink() for p in (folder,self.root/'outbox',self.root)):raise ValueError('Linked media entry')
        folder.mkdir(exist_ok=True)
        target=folder/name
        if target.is_symlink():raise ValueError('Linked media entry')
        if target.exists():
            if target.stat().st_nlink!=1 or target.read_bytes()!=raw:raise ValueError('Media spool collision')
        else:
            stats=self.stats()
            if stats['bytes']+len(raw)>QUOTA or stats['files']>=1000:raise ValueError('Media quota reached; archive old media first')
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw)
        return {'id':key,'path':str(target),'name':name,'size':len(raw),'sha256':sha,'kind':kind,'mime':TYPES[ext]}

    def get(self,key):
        if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}/[^/\\]{1,100}',key):raise ValueError('Invalid media handle')
        path=self.root/'outbox'/key
        if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or path.stat().st_nlink!=1:raise ValueError('Unknown media handle')
        raw=path.read_bytes()
        if not 0<len(raw)<=LIMIT or hashlib.sha256(raw).hexdigest()!=key.split('/')[0]:raise ValueError('Media integrity check failed')
        return self.ingest(raw,'file',path.name)

    def prepare(self,reference,kind='image',name=None):
        if not isinstance(reference,str):raise ValueError('Expected a media handle or base64:// content')
        if reference.startswith('base64://'):
            if len(reference)>((LIMIT+2)//3)*4+9:raise ValueError('Base64 media exceeds 8 MiB')
            try:raw=base64.b64decode(reference[9:],validate=True)
            except (ValueError,base64.binascii.Error):raise ValueError('Invalid Base64 media') from None
            return self.ingest(raw,kind,name)
        record=self.get(reference)
        if kind=='image':image_extension(Path(record['path']).read_bytes())
        return {**record,'kind':kind}

    def observed(self,path,kind):
        """Only a genuinely observed download path under the explicit inbox."""
        if not isinstance(path,str):raise ValueError('Invalid observed media path')
        p=Path(path)
        inbox=self.root/'inbox'
        if not p.is_absolute() or not p.resolve().is_relative_to(inbox.resolve()) or any(q.is_symlink() for q in (p,*p.parents)):
            raise ValueError('Observed attachment is outside the configured inbox')
        if not p.is_file() or p.stat().st_nlink!=1 or not 0<p.stat().st_size<=LIMIT:raise ValueError('Attachment is missing or exceeds 8 MiB')
        return self.ingest(p.read_bytes(),kind,p.name)
