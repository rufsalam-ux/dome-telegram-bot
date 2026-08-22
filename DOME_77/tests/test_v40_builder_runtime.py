from tools.lesson_builder import compile_runtime_lesson


def test_builder_compiles_cartoon_phrase_and_timeline():
    d={
      'lesson_id':'lesson_002','course_id':'demo_english','title':'Test','target_language':'en',
      'activities':[{'type':'speak','image':'lesson-images/slide-001.png','prompt':'Say hello','native_hint':'Скажи привет','cartoon_phrase':'Hello!','required':True,'allow_skip':False}],
      'cartoon':{'base_file':'cartoon-base.mp4','first_child_scene_seconds':8,'timeline':[{'visible_start':0,'talk_start':1,'end':8,'x':100,'y':200,'height':220}]}
    }
    out=compile_runtime_lesson(d)
    assert out['slides'][0]['required_phrase_id']=='phrase_01'
    assert out['slides'][0]['allow_skip'] is False
    assert out['required_phrases'][0]['target_text']=='Hello!'
    assert out['timeline'][0]['phrase_id']=='phrase_01'
