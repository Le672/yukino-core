"""Bounded recurrence, atomic import, privacy, and real worker tests."""
from contextlib import closing
from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ics_under_test',ROOT/'ics_engine.py')
ics=importlib.util.module_from_spec(spec); spec.loader.exec_module(ics)


def calendar(properties='',title='原课程',uid='course-1',start='DTSTART;TZID=Asia/Shanghai:20261019T090000',end='DTEND;TZID=Asia/Shanghai:20261019T100000'):
    return '\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','BEGIN:VEVENT','UID:'+uid,'SUMMARY:'+title,start,end,*([properties] if properties else []),'END:VEVENT','END:VCALENDAR'])


def second_event(raw,uid='course-2',title='第二门'):
    event='\n'.join(['BEGIN:VEVENT','UID:'+uid,'SUMMARY:'+title,'DTSTART;TZID=Asia/Shanghai:20261019T110000','DTEND;TZID=Asia/Shanghai:20261019T120000','END:VEVENT'])
    return raw.replace('END:VCALENDAR',event+'\nEND:VCALENDAR')


def on(raw,day): return ics.occurrences_on(ics.parse_ics(raw),date.fromisoformat(day))


class RecurrenceTests(unittest.TestCase):
    def test_weekly_interval_count_and_multiple_weekdays(self):
        raw=calendar('RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=4;BYDAY=MO,WE')
        for day in ('2026-10-19','2026-10-21','2026-11-02','2026-11-04'): self.assertEqual(len(on(raw,day)),1,day)
        for day in ('2026-10-18','2026-10-26','2026-10-28','2026-11-09','2026-11-16'): self.assertEqual(on(raw,day),[],day)

    def test_partial_first_week_count_is_not_a_full_week(self):
        raw=calendar('RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=MO,WE,FR',start='DTSTART;TZID=Asia/Shanghai:20261021T090000',end='DTEND;TZID=Asia/Shanghai:20261021T100000')
        for day in ('2026-10-21','2026-10-23','2026-10-26'): self.assertEqual(len(on(raw,day)),1)
        self.assertEqual(on(raw,'2026-10-19'),[]); self.assertEqual(on(raw,'2026-10-28'),[])

    def test_until_is_inclusive_and_utc(self):
        raw=calendar('RRULE:FREQ=WEEKLY;UNTIL=20261102T010000Z;BYDAY=MO')
        self.assertEqual(len(on(raw,'2026-11-02')),1); self.assertEqual(on(raw,'2026-11-09'),[])

    def test_floating_time_and_floating_until_use_explicit_fixed_plus8(self):
        raw=calendar('RRULE:FREQ=WEEKLY;UNTIL=20261102T090000',start='DTSTART:20261019T090000',end='DTEND:20261019T100000')
        result=on(raw,'2026-11-02'); self.assertEqual(result[0][0].hour,9); self.assertEqual(result[0][0].utcoffset().total_seconds(),28800)
        self.assertEqual(on(raw,'2026-11-09'),[])

    def test_exdate_does_not_extend_count_and_matches_utc_instants(self):
        raw=calendar('RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=MO\nEXDATE:20261026T010000Z')
        self.assertEqual(len(on(raw,'2026-10-19')),1); self.assertEqual(on(raw,'2026-10-26'),[])
        self.assertEqual(len(on(raw,'2026-11-02')),1); self.assertEqual(on(raw,'2026-11-09'),[])

    def test_multiple_exdates_with_timezone_and_list(self):
        raw=calendar('RRULE:FREQ=WEEKLY;COUNT=4\nEXDATE;TZID=Asia/Shanghai:20261026T090000,20261102T090000')
        self.assertEqual(on(raw,'2026-10-26'),[]); self.assertEqual(on(raw,'2026-11-02'),[]); self.assertEqual(len(on(raw,'2026-11-09')),1)

    def test_utc_recurrence_preserves_source_weekday_but_displays_local_day(self):
        raw=calendar('RRULE:FREQ=WEEKLY;COUNT=2;BYDAY=SU',start='DTSTART:20261018T230000Z',end='DTEND:20261019T000000Z')
        event=on(raw,'2026-10-19')[0]; self.assertEqual(event[0].hour,7); self.assertEqual(event[1].hour,8)
        self.assertEqual(len(on(raw,'2026-10-26')),1); self.assertEqual(on(raw,'2026-11-02'),[])

    def test_cross_midnight_events_appear_on_both_overlapping_days(self):
        raw=calendar(start='DTSTART:20261019T233000',end='DTEND:20261020T013000')
        self.assertEqual(len(on(raw,'2026-10-19')),1); self.assertEqual(len(on(raw,'2026-10-20')),1); self.assertEqual(on(raw,'2026-10-21'),[])

    def test_nonrecurring_exdate_can_remove_the_single_occurrence(self):
        raw=calendar('EXDATE:20261019T010000Z'); self.assertEqual(on(raw,'2026-10-19'),[])

    def test_folded_lines_and_text_escapes(self):
        raw=calendar('LOCATION:第一行\\n第二行\\;A\\,B\\\\C',title='数据结\n 构')
        event=ics.parse_ics(raw.replace('\n','\r\n'))[0]
        self.assertEqual(event['title'],'数据结构'); self.assertEqual(event['location'],'第一行\n第二行;A,B\\C')

    def test_quoted_timezone_parameter_with_colon(self):
        raw=calendar(start='DTSTART;TZID="UTC+08:00":20261019T090000',end='DTEND;TZID="UTC+08:00":20261019T100000')
        self.assertEqual(on(raw,'2026-10-19')[0][0].hour,9)

    def test_unsupported_recurrence_and_conflicting_rules_are_rejected(self):
        rules=('FREQ=DAILY','FREQ=WEEKLY;BYMONTHDAY=19','FREQ=WEEKLY;COUNT=2;UNTIL=20261201T000000Z','FREQ=WEEKLY;INTERVAL=0','FREQ=WEEKLY;BYDAY=1MO','FREQ=WEEKLY;BYDAY=TU','FREQ=WEEKLY;BYDAY=MO,MO','FREQ=WEEKLY;WKST=SU','FREQ=WEEKLY;UNTIL=20261101T090000')
        for rule in rules:
            with self.subTest(rule=rule),self.assertRaises(ValueError): ics.parse_ics(calendar('RRULE:'+rule))

    def test_unsupported_components_and_overrides_reject_whole_calendar(self):
        cases=[calendar('RDATE:20261021T090000'),calendar('RECURRENCE-ID:20261019T090000'),calendar('DURATION:PT1H'),calendar('STATUS:CANCELLED'),calendar('BEGIN:VALARM\nACTION:DISPLAY\nEND:VALARM'),calendar().replace('BEGIN:VEVENT','BEGIN:VTIMEZONE\nEND:VTIMEZONE\nBEGIN:VEVENT',1)]
        for raw in cases:
            with self.subTest(raw=raw[-80:]),self.assertRaises(ValueError): ics.parse_ics(raw)

    def test_unknown_timezone_allday_invalid_duration_and_duplicate_uid(self):
        cases=[calendar(start='DTSTART;TZID=America/New_York:20261019T090000'),calendar(start='DTSTART;VALUE=DATE:20261019'),calendar(end='DTEND:20261019T080000'),calendar(start='DTSTART:19911019T090000'),second_event(calendar(),uid='course-1')]
        for raw in cases:
            with self.subTest(raw=raw[:80]),self.assertRaises(ValueError): ics.parse_ics(raw)

    def test_recurrence_query_far_in_future_is_bounded_and_preserves_interval(self):
        raw=calendar('RRULE:FREQ=WEEKLY;INTERVAL=3;BYDAY=MO,TH')
        # Query does not expand or retain tens of years of individual events.
        for day in ('1992-01-01','2026-10-19','2099-12-31'):
            self.assertLessEqual(len(on(raw,day)),1)


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='.ics-test-',dir=ROOT/'tests'); self.root=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def ctx(self,user='2',scope='p:2',settings=None):
        return {'user_id':user,'session_id':scope,'data_dir':str(self.root/'data'),'settings':settings or {}}

    def send(self,arg,user='2',scope='p:2',settings=None): return ics.handle('/课表 '+arg,self.ctx(user,scope,settings))

    def rows(self):
        with closing(sqlite3.connect(self.root/'data/courses.sqlite3')) as db: return db.execute('SELECT id,scope,user,body,active FROM calendars ORDER BY id').fetchall()

    def test_real_import_query_and_tentative_status(self):
        self.assertIn('课表已导入',self.send('导入\n'+calendar('STATUS:TENTATIVE')))
        result=self.send('2026-10-19'); self.assertIn('09:00–10:00',result); self.assertIn('暂定',result); self.assertIn('原课程',result)

    def test_today_and_tomorrow_use_fixed_local_date_instead_of_machine_timezone(self):
        self.send('导入\n'+calendar())
        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                instant=datetime(2026,10,18,16,30,tzinfo=timezone.utc)
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
        with patch.object(ics,'datetime',FixedDateTime):
            today=self.send('今天'); tomorrow=self.send('明天')
        self.assertIn('2026-10-19 课表',today); self.assertIn('原课程',today)
        self.assertIn('2026-10-20 课表',tomorrow); self.assertIn('当天没有课程',tomorrow)

    def test_invalid_second_event_never_partially_replaces_existing_import(self):
        self.send('导入\n'+calendar())
        invalid=second_event(calendar(title='不应保存')).replace('UID:course-2','UID:course-2\nRDATE:20261020T110000')
        self.assertIn('未完成',self.send('导入\n'+invalid))
        self.assertEqual(len(self.rows()),1); self.assertEqual(self.rows()[0][4],1)
        self.assertIn('原课程',self.send('2026-10-19')); self.assertNotIn('不应保存',self.send('2026-10-19'))

    def test_user_and_session_isolation_including_version_restore(self):
        self.send('导入\n'+calendar())
        self.assertIn('尚未导入',self.send('2026-10-19',user='3'))
        self.assertIn('尚未导入',self.send('2026-10-19',scope='p:3'))
        self.assertIn('不属于',self.send('恢复 1',user='3'))
        self.assertIn('不属于',self.send('恢复 1',scope='p:3'))
        self.assertIn('暂无历史',self.send('历史',user='3'))
        self.assertIn('未完成',self.send('历史 2'))

    def test_soft_clear_and_restore_keep_historical_data(self):
        self.send('导入\n'+calendar()); self.send('导入\n'+calendar(title='新课程'))
        self.assertEqual([r[4] for r in self.rows()],[0,1])
        self.send('清空'); self.assertEqual([r[4] for r in self.rows()],[0,0]); self.assertIn('尚未导入',self.send('今天'))
        self.assertIn('已恢复',self.send('恢复 1')); self.assertIn('原课程',self.send('2026-10-19')); self.assertEqual(len(self.rows()),2)

    def test_version_limit_is_effective_and_identical_import_does_not_consume_slot(self):
        options={'max_versions':1}
        self.assertIn('已导入',self.send('导入\n'+calendar(),settings=options))
        self.assertIn('内容相同',self.send('导入\n'+calendar(),settings=options))
        self.assertIn('上限',self.send('导入\n'+calendar(title='第二版'),settings=options)); self.assertEqual(len(self.rows()),1)

    def test_event_limit_rejects_import_and_result_limit_is_visible(self):
        raw=calendar()
        for i in range(2,7): raw=second_event(raw,uid=f'course-{i}',title=f'课程{i}')
        self.assertIn('超过',self.send('导入\n'+raw,settings={'max_events':5}))
        self.assertFalse((self.root/'data/courses.sqlite3').exists())
        self.send('导入\n'+raw)
        result=self.send('2026-10-19',settings={'result_limit':1})
        self.assertIn('共6条，仅显示前1条',result); self.assertNotIn('课程6',result)

    def test_private_only_and_no_url_or_file_import(self):
        self.assertIn('仅支持私聊',self.send('导入\n'+calendar(),scope='g:1'))
        self.assertFalse((self.root/'data/courses.sqlite3').exists())
        for raw in ('https://example.com/course.ics','C:\\course.ics'):
            self.assertIn('未完成',self.send('导入 '+raw))
        self.assertFalse((self.root/'data/courses.sqlite3').exists())

    def test_help_unknown_messages_relative_data_and_length_bounds(self):
        self.assertIsNone(ics.handle('普通聊天',self.ctx()))
        self.assertIn('BEGIN:VCALENDAR',self.send('示例')); self.assertIn('不主动提醒',self.send('帮助'))
        self.assertIn('最多4000',self.send('导入 '+'x'*4000))
        ctx=self.ctx(); ctx['data_dir']='relative'
        self.assertIn('绝对',ics.handle('/课表 今天',ctx))
        self.assertIn('未完成',self.send('2026-10-19 3'))

    def test_public_wrapper_and_catalog_real_smoke(self):
        catalog=json.loads((ROOT/'local_catalog.json').read_text(encoding='utf-8'))
        item=next(p for p in catalog if p['id']=='m_ics')
        worker_spec=importlib.util.spec_from_file_location('ics_worker',ROOT/'plugins/m_ics/worker.py'); worker=importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker)
        self.assertIsNone(worker.handle('/other',self.ctx()))
        self.assertIn(item['expected_contains'],worker.handle(item['smoke_command'],self.ctx()))


if __name__=='__main__': unittest.main()
