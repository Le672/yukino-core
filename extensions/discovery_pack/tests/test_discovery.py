import base64,contextlib,copy,hashlib,io,json,sys,tempfile,unittest,zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
PACK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PACK));sys.path.insert(0,str(PACK/'vendor'));sys.path.insert(0,str(PACK.parents[1]))
import common,graphics,images,network,build_pack
from yukino_core.plugin_results import validate_result
from yukino_core.plugin_packages import inspect_package
from yukino_core.plugins import PluginManager
from types import SimpleNamespace

def fixture_png():
    image=Image.new('RGB',(160,120),'#faf0d9');draw=ImageDraw.Draw(image);draw.rectangle((12,12,75,90),fill='#458f79');draw.ellipse((70,28,143,108),fill='#eeae55');b=io.BytesIO();image.save(b,'PNG');return b.getvalue()

def bytes_of(packet):return base64.b64decode(packet['media'][0]['data'])

class DiscoveryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.ctx=dict(data_dir=self.tmp.name,session_id='g:77',user_id='11',settings={})
    def tearDown(self):self.tmp.cleanup()

    def test_individual_packages_are_importable_and_pin_vendor_license(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest=build_pack.build(Path(folder));self.assertEqual(manifest['plugin_count'],7)
            seen=set()
            for row in manifest['plugins']:
                path=Path(folder)/row['archive'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),row['sha256']);record,files=inspect_package(path.read_bytes())
                self.assertFalse(seen.intersection(record['commands']));seen.update(record['commands']);self.assertIn('LICENSE',files);self.assertIn('NOTICE',files)
                manager=PluginManager(SimpleNamespace(plugin_flags={},plugin_settings={}),Path(folder)/'data')
                manager.items.append(record)
                self.assertEqual(manager.validate_settings(record['id'],row['settings']),row['settings'])
                if row['id']=='d_qrcode':
                    proof=json.loads(files['provenance.json']);self.assertEqual(hashlib.sha256(files['qrcodegen.py']).hexdigest(),proof['sha256']);self.assertIn(b'MIT License',files['qrcodegen.py'])

    def test_image_factory_makes_real_pet_spin_and_static_images(self):
        for command,kind,frames in [('/摸头','GIF',5),('/转圈','GIF',8),('/像素化','PNG',1),('/图片灰度','PNG',1),('/图片镜像','PNG',1)]:
            with self.subTest(command=command),patch.object(images,'get_source',return_value=fixture_png()):
                answer=validate_result(images.handle(command,self.ctx));raw=bytes_of(answer)
                with Image.open(io.BytesIO(raw)) as image:self.assertEqual(image.format,kind);self.assertEqual(getattr(image,'n_frames',1),frames)
                self.assertLess(len(answer['media'][0]['data']),49000)

    def test_gif_reverse_and_speed_preserve_actual_frames(self):
        frames=[Image.new('RGB',(20,20),c) for c in ('red','green','blue')];b=io.BytesIO();frames[0].save(b,'GIF',save_all=True,append_images=frames[1:],duration=[120,240,360],loop=0)
        with patch.object(images,'get_source',return_value=b.getvalue()):
            rev=images.handle('/动图倒放',self.ctx);sped=images.handle('/动图加速 2 [图片]',self.ctx)
        with Image.open(io.BytesIO(bytes_of(rev))) as im:self.assertEqual(im.convert('RGB').getpixel((0,0)),(0,0,255));self.assertEqual(im.info['duration'],360)
        with Image.open(io.BytesIO(bytes_of(sped))) as im:self.assertEqual(im.n_frames,3);self.assertEqual(im.info['duration'],60)

    def test_photo_texture_static_effects_shrink_to_real_sendable_images(self):
        photo=Image.effect_noise((640,480),60).convert('RGB');b=io.BytesIO();photo.save(b,'PNG')
        with patch.object(images,'get_source',return_value=b.getvalue()):
            for command in ('/图片灰度','/图片镜像'):
                with self.subTest(command=command):
                    answer=validate_result(images.handle(command,self.ctx));self.assertLess(len(answer['media'][0]['data']),49000)
                    with Image.open(io.BytesIO(bytes_of(answer))) as image:
                        self.assertEqual(image.format,'PNG');self.assertLess(image.width,480);self.assertGreaterEqual(image.width,160)
                        self.assertAlmostEqual(image.width/image.height,4/3,places=1)

    def test_image_url_boundaries_and_no_qq_avatar_lookup(self):
        for url in ('https://q1.qlogo.cn/g?nk=11','file:///private.png','https://127.0.0.1/image.png'):
            with self.subTest(url=url),patch.object(images.public_net,'_one') as request:
                ctx={**self.ctx,'image_urls':[url]};value=images.handle('/摸头',ctx);self.assertIsInstance(value,str);request.assert_not_called()
        with patch.object(images.public_net,'request') as request:
            value=images.handle('/摸头',self.ctx);self.assertIn('图片工坊',value);request.assert_not_called()

    def test_large_gifs_are_rejected_before_full_decode(self):
        frames=[Image.new('RGB',(10,10),c) for c in ('red','blue','green')];b=io.BytesIO();frames[0].save(b,'GIF',save_all=True,append_images=frames[1:])
        with self.assertRaises(common.UserError):images.open_frames(b.getvalue(),maximum=2)

    def test_chinese_meme_cards_are_actual_images_and_text_is_literal(self):
        for command in ('/举牌 今天就到这里','/记仇 他又偷吃我的蛋糕','/摸鱼牌 <script>摸鱼啦</script>'):
            answer=validate_result(graphics.handle('d_memes',command,self.ctx))
            with Image.open(io.BytesIO(bytes_of(answer))) as im:self.assertEqual(im.size,(620,480));self.assertGreater(len(im.getcolors(300000)),10)
        self.assertIsInstance(graphics.handle('d_memes','/举牌 '+('字'*300),self.ctx),str)

    def test_qr_generates_locally_and_has_quiet_zone(self):
        with patch.object(common.public_net,'request') as net:
            for value in ('https://example.com','你好，世界','https://127.0.0.1/private'):
                answer=validate_result(graphics.handle('d_qrcode','/二维码 '+value,self.ctx))
                with Image.open(io.BytesIO(bytes_of(answer))) as im:
                    self.assertEqual(im.format,'PNG');self.assertEqual(im.getpixel((0,0)),(255,255,255));self.assertEqual(im.getpixel((im.width//2,0)),(255,255,255))
            net.assert_not_called()
        self.assertIsInstance(graphics.handle('d_qrcode','/二维码 '+('中'*300),self.ctx),str)

    def test_cache_respects_disable_ttl_and_never_masks_a_network_failure(self):
        calls=[]
        def load():calls.append(1);return {'value':len(calls)}
        a,at,hit=common.cached(self.ctx,'x',load);b,at2,hit2=common.cached(self.ctx,'x',load);self.assertEqual(a,b);self.assertFalse(hit);self.assertTrue(hit2);self.assertEqual(at,at2)
        self.ctx['settings']={'cache_seconds':0};c,_,hit=common.cached(self.ctx,'x',load);self.assertEqual(c['value'],2);self.assertFalse(hit)
        with self.assertRaisesRegex(ValueError,'offline'):common.cached(self.ctx,'x',lambda:(_ for _ in ()).throw(ValueError('offline')))
        self.ctx['settings']={'online_enabled':False}
        with self.assertRaises(common.UserError):common.cached(self.ctx,'y',load)
        self.assertEqual(len(calls),2)

    def test_epic_filters_paid_permanent_free_expired_and_future_promos(self):
        def row(name,start,end,original=100,price=0,discount=0):return dict(id=name,title=name,description='source text',price={'totalPrice':{'originalPrice':original,'discountPrice':price}},promotions={'promotionalOffers':[{'promotionalOffers':[{'startDate':start,'endDate':end,'discountSetting':{'discountPercentage':discount}}]}]},keyImages=[])
        data={'data':{'Catalog':{'searchStore':{'elements':[row('current','2026-10-20T00:00:00Z','2026-10-25T00:00:00Z'),row('permanent','2026-10-20T00:00:00Z','2026-10-25T00:00:00Z',original=0),row('expired','2026-10-01T00:00:00Z','2026-10-02T00:00:00Z'),row('upcoming','2026-10-25T00:00:00Z','2026-11-01T00:00:00Z',price=100),row('paid','2026-10-20T00:00:00Z','2026-10-25T00:00:00Z',price=50)]}}}}
        rows=network.epic_items(data,datetime(2026,10,23,tzinfo=timezone.utc));self.assertEqual([r['title'] for r in rows],['current','upcoming']);self.assertTrue(rows[0]['current']);self.assertFalse(rows[1]['current'])

    def test_projects_auto_url_and_search_have_only_public_metadata(self):
        repo=dict(full_name='example/repo',description='示例项目',stargazers_count=17,license={'spdx_id':'MIT'},language='Python',pushed_at='2026-10-23T00:00:00Z',private=False)
        with patch.object(network,'get_json',return_value=repo) as request:
            answer=network.handle('d_projects','https://github.com/example/repo/issues/12',self.ctx);self.assertIn('17',answer['text']);self.assertEqual(answer['media'][0]['type'],'file');self.assertIn('/repos/example/repo',request.call_args.args[0])
        self.assertIsNone(network.handle('d_projects','https://github.com/login',self.ctx))
        with patch.object(network,'get_json',return_value={'items':[repo]}) as request:
            answer=network.handle('d_projects','/找项目 qq bot',self.ctx);self.assertIn('is%3Apublic',request.call_args.args[0]);self.assertIn('MIT',answer['text'])

    def test_dictionary_sends_source_audio_and_has_no_invented_pronunciation(self):
        entry=dict(word='hello',phonetic='/hello/',phonetics=[{'audio':'https://api.dictionaryapi.dev/media/pronunciations/en/hello-us.mp3'}],meanings=[{'partOfSpeech':'interjection','definitions':[{'definition':'A greeting.','example':'Hello, world.'}]}],license={'name':'CC BY-SA 3.0'})
        with patch.object(network,'get_json',return_value=[entry]),patch.object(network.public_net,'request',return_value=SimpleNamespace(body=b'ID3'+b'\x00'*30)) as request:
            answer=network.handle('d_dictionary','/查词 hello',self.ctx)
        self.assertEqual(request.call_count,1);self.assertLessEqual(request.call_args.kwargs['timeout'],3)
        self.assertEqual(answer['media'][0]['type'],'audio');self.assertIn('A greeting.',answer['text']);self.assertIn('CC BY-SA',answer['text'])
        self.ctx['settings']={'cache_seconds':0};entry['phonetics']=[]
        with patch.object(network,'get_json',return_value=[entry]):answer=network.handle('d_dictionary','/查词 hello',self.ctx)
        self.assertEqual(answer['media'],[]);self.assertIn('暂无',answer['text'])

    def test_dictionary_media_failure_uses_only_cited_commons_file_and_attributes_audio(self):
        entry=dict(word='hello',phonetics=[dict(audio='https://api.dictionaryapi.dev/media/pronunciations/en/hello-au.mp3',sourceUrl='https://commons.wikimedia.org/w/index.php?curid=75797336')],meanings=[{'partOfSpeech':'interjection','definitions':[{'definition':'A greeting.'}]}],license={'name':'CC BY-SA 3.0'})
        original='https://upload.wikimedia.org/wikipedia/commons/e/e9/En-au-hello.ogg?utm_source=commons.wikimedia.org'
        info={'url':original,'descriptionurl':'https://commons.wikimedia.org/wiki/File:En-au-hello.ogg','extmetadata':{'Artist':{'value':'<a href="//commons.wikimedia.org/wiki/User:Commander_Keane">Commander Keane</a>'},'LicenseShortName':{'value':'CC BY-SA 4.0'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0'}}}
        commons={'query':{'pages':{'75797336':{'imageinfo':[info]}}}}
        with patch.object(network,'get_json',side_effect=[[entry],commons]) as lookup,patch.object(network.public_net,'request',side_effect=network.public_net.NetworkError('HTTP 530')) as request:
            answer=validate_result(network.handle('d_dictionary','/查词 hello',self.ctx))
        self.assertEqual(lookup.call_count,2);self.assertEqual(request.call_count,1)
        self.assertEqual(answer['media'],[{'type':'audio','url':original,'name':'hello.ogg'}]);self.assertIn('A greeting.',answer['text'])
        self.assertIn('Commander Keane',answer['text']);self.assertIn('CC BY-SA 4.0',answer['text']);self.assertIn(info['descriptionurl'],answer['text']);self.assertIn('https://creativecommons.org/licenses/by-sa/4.0',answer['text'])
        self.assertIn('pageids=75797336',lookup.call_args.args[0]);self.assertIn('prop=imageinfo',lookup.call_args.args[0]);self.assertEqual(lookup.call_args.args[2],['commons.wikimedia.org'])

    def test_dictionary_rejects_unsafe_or_uncited_commons_media_and_keeps_definitions(self):
        entry=dict(word='hello',phonetics=[dict(audio='https://api.dictionaryapi.dev/media/pronunciations/en/hello.mp3',sourceUrl='https://commons.wikimedia.org/w/index.php?curid=12')],meanings=[{'definitions':[{'definition':'A greeting.'}]}])
        for url,metadata in [('https://evil.example/voice.mp3',{'Artist':{'value':'author'},'LicenseShortName':{'value':'CC BY 4.0'}}),('https://upload.wikimedia.org/wikipedia/commons/a/a1/voice.ogg',{'LicenseShortName':{'value':'CC BY 4.0'}})]:
            with self.subTest(url=url,metadata=metadata):
                commons={'query':{'pages':{'12':{'imageinfo':[{'url':url,'extmetadata':metadata}]}}}}
                self.ctx['settings']={'cache_seconds':0}
                with patch.object(network,'get_json',side_effect=[[entry],commons]),patch.object(network.public_net,'request',return_value=SimpleNamespace(body=b'<html>unavailable</html>')):
                    answer=network.handle('d_dictionary','/查词 hello',self.ctx)
                self.assertEqual(answer['media'],[]);self.assertIn('A greeting.',answer['text']);self.assertIn('发音来源暂时不可用',answer['text'])
        entry['phonetics'][0]['sourceUrl']='https://127.0.0.1/w/index.php?curid=12'
        with patch.object(network,'get_json',return_value=[entry]) as lookup,patch.object(network.public_net,'request',side_effect=network.public_net.NetworkError('HTTP 530')):
            answer=network.handle('d_dictionary','/查词 hello',self.ctx)
        self.assertEqual(lookup.call_count,1);self.assertEqual(answer['media'],[])

    def test_dictionary_commons_timeout_does_not_hide_text_or_try_more_endpoints(self):
        entry=dict(word='hello',phonetics=[dict(audio='https://api.dictionaryapi.dev/media/pronunciations/en/hello.mp3',sourceUrl='https://commons.wikimedia.org/wiki/File:En-uk-hello.ogg')],meanings=[{'definitions':[{'definition':'A greeting.'}]}])
        with patch.object(network,'get_json',side_effect=[[entry],network.public_net.NetworkError('deadline')]) as lookup,patch.object(network.public_net,'request',side_effect=network.public_net.NetworkError('HTTP 530')) as request:
            answer=network.handle('d_dictionary','/查词 hello',self.ctx)
        self.assertEqual(request.call_count,1);self.assertEqual(lookup.call_count,2);self.assertEqual(answer['media'],[]);self.assertIn('A greeting.',answer['text'])
        self.assertIn('titles=File%3AEn-uk-hello.ogg',lookup.call_args.args[0]);self.assertTrue(all('commons.wikimedia.org' in url for url in [lookup.call_args.args[0]]))

    def test_air_uses_explicit_location_and_labels_us_aqi_and_model_forecast(self):
        current=dict(time='2026-10-23T12:00',us_aqi=65,pm2_5=12,pm10=17);data=dict(current=current,current_units={'pm2_5':'μg/m³'},timezone='Asia/Shanghai',hourly={'time':['2026-10-23T12:00','2026-10-23T13:00','2026-10-23T14:00'],'pm2_5':[12,None,18],'pm10':[17,19,21]})
        def load(url,*args):return {'results':[dict(name='上海',admin1='上海',country='中国',country_code='CN',latitude=31.23,longitude=121.47)]} if '/v1/search' in url else data
        with patch.object(network,'get_json',side_effect=load) as request:answer=validate_result(network.handle('d_air','/空气质量 上海',self.ctx))
        self.assertIn('美标',answer['text']);self.assertIn('非中国监测站',answer['text']);self.assertEqual(len(answer['media']),1);self.assertIn('Shanghai',request.call_args_list[0].args[0]);self.assertNotIn('user_id',str(request.call_args_list))
        with patch.object(network,'get_json') as request:self.assertIsInstance(network.handle('d_air','/空气质量',self.ctx),str);request.assert_not_called()

if __name__=='__main__':unittest.main()
