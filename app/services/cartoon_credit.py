from __future__ import annotations
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from app.core.config import settings

def credit_text(name:str, gender:str|None)->str:
    if gender=='female': return f"Мультфильм озвучила {name}"
    if gender=='male': return f"Мультфильм озвучил {name}"
    return f"Мультфильм озвучивает {name}"

def add_cartoon_credit(video:Path, output:Path, name:str, gender:str|None, seconds:float=4.0)->Path:
    if not video.exists(): return video
    work=output.parent/(output.stem+'_credit'); work.mkdir(parents=True,exist_ok=True)
    png=work/'credit.png'
    im=Image.new('RGBA',(1280,720),(20,34,54,235)); d=ImageDraw.Draw(im)
    try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',52)
    except Exception: font=ImageFont.load_default()
    text=credit_text(name,gender); box=d.textbbox((0,0),text,font=font); x=(1280-(box[2]-box[0]))//2; y=325
    d.text((x,y),text,font=font,fill='white')
    im.save(png)
    tmp=work/'with_credit.mp4'
    # Overlay credit card during the last seconds; keeps existing soundtrack and duration.
    probe=subprocess.run([settings.ffmpeg_bin,'-i',str(video)],capture_output=True,text=True)
    import re
    m=re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)',probe.stderr or '')
    dur=75.0
    if m: dur=int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    start=max(0,dur-seconds)
    cmd=[settings.ffmpeg_bin,'-y','-i',str(video),'-loop','1','-i',str(png),'-filter_complex',f'[1:v]format=rgba[cr];[0:v][cr]overlay=0:0:enable=\'gte(t,{start:.3f})\'[v]','-map','[v]','-map','0:a?','-c:v','libx264','-preset','ultrafast','-crf','23','-c:a','copy','-t',f'{dur:.3f}',str(tmp)]
    subprocess.run(cmd,check=True,capture_output=True,timeout=180)
    tmp.replace(output)
    return output
