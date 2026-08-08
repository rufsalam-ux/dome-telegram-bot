from datetime import date
import json

from app.services import course_scheduler


def test_windows_cross_year():
    assert course_scheduler._in_window(date(2026,12,20),'12-01','02-28')
    assert course_scheduler._in_window(date(2027,2,28),'12-01','02-28')
    assert not course_scheduler._in_window(date(2026,6,1),'12-01','02-28')


def test_choose_season_then_fallback(monkeypatch):
    course = {
        'lesson_ids':['l1','summer1','l2','summer2','l3'],
        'seasonal':{'periods':[{'id':'summer','start':'06-01','end':'08-31','priority':20,'enabled':True,'lesson_ids':['summer1','summer2']}]}
    }
    monkeypatch.setattr(course_scheduler, '_load_course', lambda _: course)
    assert course_scheduler.choose_next_lesson('c', [], date(2026,8,8)) == 'summer1'
    assert course_scheduler.choose_next_lesson('c', ['summer1'], date(2026,8,8)) == 'summer2'
    assert course_scheduler.choose_next_lesson('c', ['summer1','summer2'], date(2026,8,8)) == 'l1'
    assert course_scheduler.choose_next_lesson('c', [], date(2026,9,1)) == 'l1'


def test_halloween_dates(monkeypatch):
    course={'lesson_ids':['l1','h1'],'seasonal':{'periods':[{'id':'halloween','start':'10-17','end':'11-03','priority':10,'lesson_ids':['h1']}]}}
    monkeypatch.setattr(course_scheduler, '_load_course', lambda _: course)
    assert course_scheduler.choose_next_lesson('c', [], date(2026,10,17)) == 'h1'
    assert course_scheduler.choose_next_lesson('c', [], date(2026,11,3)) == 'h1'
    assert course_scheduler.choose_next_lesson('c', [], date(2026,11,4)) == 'l1'
