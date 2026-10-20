import base64
import copy
from pathlib import Path
import tempfile
import unittest
from qq_desktop.media import MediaStore
from qq_desktop.bridge import Bridge
from qq_desktop.atspi import UiError,UiUncertain
from test_bridge import config,Driver

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')
# Container-header fixture, not a playable/codec acceptance sample.
MP4=(24).to_bytes(4,'big')+b'ftypisom'+bytes(4)+b'isommp42'+(8).to_bytes(4,'big')+b'mdat'


def calibrated():
    c=config();c['bindings'][0]['attachments']={'verified':True,'kinds':['image','file','video']}
    return c


class MediaTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=MediaStore(Path(self.tmp.name)/'media')
    def tearDown(self):self.tmp.cleanup()
    def test_image_bytes_are_private_immutable_and_content_addressed(self):
        r=self.store.prepare('base64://'+base64.b64encode(PNG).decode())
        self.assertEqual(Path(r['path']).read_bytes(),PNG);self.assertEqual(self.store.get(r['id'])['id'],r['id'])
        self.assertEqual(self.store.stats()['files'],1)
        Path(r['path']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.store.get(r['id'])
    def test_never_reads_arbitrary_path_url_or_traversal(self):
        secret=Path(self.tmp.name)/'secret.png';secret.write_bytes(PNG)
        for value in (str(secret),secret.as_uri(),'https://example.com/p.png','../secret.png','base64://!!!!'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.store.prepare(value)
        with self.assertRaises(ValueError):self.store.observed(str(secret),'image')
        self.assertEqual(self.store.stats()['files'],0)
    def test_downloaded_attachment_must_be_under_explicit_inbox(self):
        p=self.store.root/'inbox'/'收到的图.png';p.write_bytes(PNG)
        r=self.store.observed(str(p),'image');self.assertEqual(r['name'],'收到的图.png')
        self.assertEqual(Path(r['path']).read_bytes(),PNG)
    def test_content_type_filename_size_and_quota_are_checked(self):
        for raw,kind,name in ((b'not image','image',None),(b'x','file','run.exe'),(b'x','file','a.pdf'),(b'x','file','a.zip'),(PNG,'image','wrong.jpg'),(PNG,'image','../x.png'),(b'x'*(8*1024*1024+1),'file','x.txt')):
            with self.subTest(kind=kind,name=name),self.assertRaises(ValueError):self.store.ingest(raw,kind,name)
        import qq_desktop.media as module
        previous=module.QUOTA;module.QUOTA=1
        try:
            with self.assertRaises(ValueError):self.store.ingest(PNG,'image')
        finally:module.QUOTA=previous
    def test_linked_inbox_or_entry_is_rejected(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir();p=outside/'x.png';p.write_bytes(PNG)
        link=self.store.root/'inbox'/'linked.png'
        try:link.symlink_to(p)
        except OSError:self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):self.store.observed(str(link),'image')
    def test_mp4_handle_revalidates_kind_and_does_not_accept_renamed_bytes(self):
        record=self.store.ingest(MP4,'video','small.mp4')
        self.assertEqual(self.store.prepare(record['id'],'video')['mime'],'video/mp4')
        for raw,name in ((PNG,'fake.mp4'),(MP4,'wrong.txt'),(b'\xff\xff\xff\xffftypisom'+bytes(20),'x.mp4')):
            with self.subTest(name=name),self.assertRaises(ValueError):self.store.ingest(raw,'video',name)
        archive=self.store.ingest(b'PK\x05\x06'+bytes(18),'file','x.zip')
        with self.assertRaises(ValueError):self.store.prepare(archive['id'],'video')
        with self.assertRaises(ValueError):self.store.ingest(MP4+b'x'*(8*1024*1024),'video','big.mp4')


class MediaDriver(Driver):
    def send_attachment(self,binding,record):
        self.sent.append((binding['id'],record['id']))
        if self.failure:raise self.failure
        return 'attachment-'+str(len(self.sent))


class MediaBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.driver=MediaDriver();self.bridge=Bridge(calibrated(),self.driver,Path(self.tmp.name)/'state')
    async def asyncTearDown(self):await self.bridge.close();self.tmp.cleanup()
    async def test_image_segment_and_file_extension_action_are_once_only(self):
        reference='base64://'+base64.b64encode(PNG).decode()
        a=await self.bridge.send('private','10002',[{'type':'image','data':{'file':reference}}],'img-1')
        b=await self.bridge.send('private','10002',[{'type':'image','data':{'file':reference}}],'img-1')
        self.assertEqual(a['message_id'],b['message_id']);self.assertEqual(len(self.driver.sent),1)
        self.bridge.last_send=0
        z=self.bridge.media.ingest(b'PK\x05\x06'+bytes(18),'file','comic.zip')
        result=await self.bridge.action('upload_private_file',{'user_id':10002,'file':z['id'],'idempotency_key':'zip-1'})
        self.assertTrue(result['ui_confirmed']);self.assertEqual(len(self.driver.sent),2)
    async def test_unverified_media_is_not_advertised_or_sent(self):
        self.bridge.config['bindings'][0]['attachments']['verified']=False
        self.assertFalse((await self.bridge.action('can_send_image',{}))['yes'])
        with self.assertRaises(UiError):await self.bridge.send('private','10002',[{'type':'image','data':{'file':'anything'}}])
        self.assertEqual(self.driver.sent,[])
    async def test_uncertain_attachment_is_not_resent(self):
        self.driver.failure=UiUncertain('uncertain');r=self.bridge.media.ingest(PNG,'image')
        for _ in range(2):
            with self.assertRaises(UiUncertain):await self.bridge.send('private','10002',[{'type':'image','data':{'file':r['id']}}],'maybe-1')
        self.assertEqual(len(self.driver.sent),1);self.assertEqual(self.bridge.metrics['sends_uncertain'],1)
    async def test_downloaded_incoming_image_has_authenticated_media_handle(self):
        await self.bridge.poll();p=self.bridge.media.root/'inbox'/'received.png';p.write_bytes(PNG)
        self.driver.rows=[{'key':'image-row','user_id':'10002','text':'','attachment':{'kind':'image','name':'received.png','path':str(p)}}]
        event=(await self.bridge.poll())[0];self.assertTrue(event['ui_attachment']['download_available'])
        key=event['message'][0]['data']['file'];r=await self.bridge.action('get_image',{'file':key})
        self.assertEqual(Path(r['file']).read_bytes(),PNG);self.assertTrue(r['requires_bearer_auth'])
        self.assertEqual(await self.bridge.poll(),[])
    async def test_missing_download_is_reported_as_metadata_instead_of_fake_image(self):
        await self.bridge.poll();self.driver.rows=[{'key':'missing','user_id':'10002','text':'','attachment':{'kind':'image','name':'not-downloaded.png'}}]
        event=(await self.bridge.poll())[0];self.assertFalse(event['ui_attachment']['download_available'])
        self.assertEqual(event['message'][0]['type'],'text')
    async def test_video_send_is_calibrated_idempotent_and_uncertain_is_not_retried(self):
        r=self.bridge.media.ingest(MP4,'video','small.mp4');segment=[{'type':'video','data':{'file':r['id']}}]
        self.assertTrue((await self.bridge.capabilities())['video']['ready'])
        await self.bridge.send('private','10002',segment,'video-1');await self.bridge.send('private','10002',segment,'video-1')
        self.assertEqual(len(self.driver.sent),1);self.bridge.last_send=0;self.driver.failure=UiUncertain('video uncertain')
        for _ in range(2):
            with self.assertRaises(UiUncertain):await self.bridge.send('private','10002',segment,'video-2')
        self.assertEqual(len(self.driver.sent),2)
    async def test_video_requires_its_own_calibrated_kind(self):
        self.bridge.config['bindings'][0]['attachments']['kinds']=['image','file']
        self.assertFalse((await self.bridge.capabilities())['video']['ready'])
        with self.assertRaises(UiError):await self.bridge.send('private','10002',[{'type':'video','data':{'file':'not-a-handle'}}])
        self.assertEqual(self.driver.sent,[])
    async def test_received_video_exposes_private_handle_and_missing_video_stays_text(self):
        await self.bridge.poll();p=self.bridge.media.root/'inbox'/'small.mp4';p.write_bytes(MP4)
        self.driver.rows=[{'key':'video-row','user_id':'10002','text':'','attachment':{'kind':'video','name':'small.mp4','path':str(p)}}]
        e=(await self.bridge.poll())[0];self.assertEqual(e['message'][0]['type'],'video')
        self.assertTrue(e['ui_attachment']['download_available'])
        self.driver.rows.append({'key':'missing-video','user_id':'10002','text':'','attachment':{'kind':'video','name':'later.mp4'}})
        e=(await self.bridge.poll())[0];self.assertEqual(e['message'][0]['type'],'text');self.assertIn('视频',e['message'][0]['data']['text'])


if __name__=='__main__':unittest.main()
