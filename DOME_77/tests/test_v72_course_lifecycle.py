from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def text(rel): return (ROOT/rel).read_text('utf-8')

def test_transition_defaults_and_editable_routes():
    s=text('app/services/course_transitions.py')
    assert '"notify_remaining_lessons": [4, 1]' in s
    assert 'def set_route' in s
    assert 'def save_choice' in s
    assert 'async def apply_transition' in s

def test_midcourse_switch_preserves_progress():
    s=text('app/services/course_transitions.py')
    assert 'async def apply_course_switch' in s
    assert 'release_baseline_count=existing_count' in s
    assert 'clear_course_switch(child_id)' in s
    assert 'delete' not in s[s.index('async def apply_course_switch'):]

def test_parent_can_switch_now_or_after_current():
    k=text('app/bot/keyboards.py'); h=text('app/bot/handlers.py')
    assert 'course_switch:mode:after:' in k
    assert 'course_switch:mode:now:' in k
    assert 'save_course_switch' in h
    assert '_apply_pending_course_switch_after_lesson' in h

def test_course_specific_prices_are_admin_editable():
    h=text('app/bot/handlers.py'); p=text('app/services/pricing_engine.py')
    assert 'Command("dome_course_price")' in h
    assert 'set_course_plan_price' in h
    assert 'subscription_plans_for_course(course_id)' in h
    assert 'course_prices' in p

def test_price_change_does_not_create_second_subscription_on_course_switch():
    h=text('app/bot/handlers.py')
    assert "switch_from=str(data.get('course_switch_from') or '')" in h
    assert 'Subscription.course_id==(switch_from or course_id)' in h
    assert 'Новый режим выдачи уроков применится только после подтверждения платёжным webhook.' in h

def test_provider_event_can_move_existing_subscription_course():
    p=text('app/services/payment_lifecycle.py')
    assert "ev.course_id != sub.course_id" in p
    assert 'sub.course_id=ev.course_id' in p
    assert "await _baseline(db,sub.child_id,sub.course_id)" in p

def test_course_end_notifications_and_email():
    h=text('app/bot/handlers.py'); e=text('app/services/email_reports.py')
    assert '_maybe_notify_course_progress' in h
    assert 'build_course_ending_email' in h
    assert 'build_course_completed_email' in h
    assert 'осталось занятий' in e
    assert 'Вы уже можете выбрать' in e

def test_family_discount_is_per_lesson_not_per_month():
    f=text('app/services/family_pricing.py')
    assert 'additional_child_discount_per_lesson_eur' in f
    assert 'lessons_per_week' in f
