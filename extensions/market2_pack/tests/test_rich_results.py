from contextlib import closing
from datetime import date
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    s=importlib.util.spec_from_file_location('rich_'+name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

class RichResults(unittest.TestCase):
    def test_rss_includes_real_source_summary_with_html_removed(self):
        m=module('network_engine')
        rows=m.feed_entries(b'<rss><channel><item><title>Release</title><description>&lt;b&gt;Actual update details&lt;/b&gt;</description><link>https://example.com/news</link></item></channel></rss>','https://example.com/feed')
        self.assertEqual(rows[0]['summary'],'Actual update details')
        self.assertNotIn('<b>',rows[0]['summary'])
    def test_wallpaper_returns_actual_image_source(self):
        m=module('network_engine')
        payload={'images':[{'title':'山谷','copyright':'作者','startdate':'20261022','urlbase':'/th?id=OHR.Scenery'}]}
        with patch.object(m,'api',return_value=payload):r=m.handle('m_bing','/每日壁纸',{'rich_result_version':1})
        self.assertEqual(r['media'][0]['type'],'image');self.assertIn('_UHD.jpg',r['media'][0]['url'])
        self.assertNotIn('未下载',r['text'])
    def test_ics_export_roundtrip_preserves_local_midnight_weekday(self):
        m=module('ics_engine')
        raw='\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','BEGIN:VEVENT','UID:course',
            'SUMMARY:'+'中文课程'*20,'DTSTART;TZID=Asia/Shanghai:20261019T010000',
            'DTEND;TZID=Asia/Shanghai:20261019T020000','RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=MO','END:VEVENT','END:VCALENDAR'])
        parsed=m.parse_ics(raw);exported=m.export_ics(parsed);again=m.parse_ics(exported)
        self.assertEqual(parsed,again)
        self.assertTrue(all(len(line.encode())<=75 for line in exported.split('\r\n')))
        self.assertEqual(len(m.occurrences_on(again,date(2026,10,26))),1)
    def test_personal_calendar_export_cannot_access_other_users(self):
        m=module('ics_engine')
        with tempfile.TemporaryDirectory() as tmp:
            ctx={'data_dir':tmp,'session_id':'p:42','user_id':'42','rich_result_version':1}
            m.handle('/课表 导入\n'+m.EXAMPLE,ctx)
            result=m.handle('/课表 导出',ctx)
            self.assertEqual(result['media'][0]['name'],'我的课表.ics')
            self.assertIn('BEGIN:VCALENDAR',result['media'][0]['text'])
            self.assertIn('尚未导入',m.handle('/课表 导出',{**ctx,'session_id':'p:99','user_id':'99'}))

if __name__=='__main__':unittest.main()
