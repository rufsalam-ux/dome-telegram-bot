from __future__ import annotations
import subprocess,re
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from app.core.config import settings

_CREDIT={
'ru':('Мультфильм озвучил','Мультфильм озвучила','Мультфильм озвучивает'),
'en':('Voiced by','Voiced by','Voiced by'),'es':('Voz de','Voz de','Voz de'),'de':('Gesprochen von','Gesprochen von','Gesprochen von'),
'fr':('Doublage :','Doublage :','Doublage :'),'it':('Voce di','Voce di','Voce di'),'pt':('Voz de','Voz de','Voz de'),
}
def credit_text(name:str,gender:str|None,target_language:str='ru')->tuple[str,str]:
    male,female,neutral=_CREDIT.get(target_language,_CREDIT['en']); line= female if gender=='female' else male if gender=='male' else neutral
    return line,(name or 'Child').strip()
def _font(size:int):
    for fp in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'):
        try:return ImageFont.truetype(fp,size)
        except Exception:pass
    return ImageFont.load_default()
def _fit(d,t,m,s=44,n=24):
    while s>n:
        f=_font(s); b=d.textbbox((0,0),t,font=f)
        if b[2]-b[0]<=m:return f
        s-=2
    return _font(n)
def add_cartoon_credit(video:Path,output:Path,name:str,gender:str|None,seconds:float=3.0,target_language:str='ru')->Path:
    """Append a real final title card to the MP4. v54: credit is never merely hidden over the last scene."""
    if not video.exists(): return video
    work=output.parent/(output.stem+'_credit'); work.mkdir(parents=True,exist_ok=True)
    png=work/'credit.png'
    im=Image.new('RGB',(1280,720),(30,44,70)); d=ImageDraw.Draw(im)
    x0,y0,x1,y1=190,230,1090,500
    d.rounded_rectangle((x0,y0,x1,y1),radius=42,fill=(246,249,255),outline=(210,224,245),width=4)
    line1,line2=credit_text(name,gender,target_language)
    f1=_fit(d,line1,820,42,26); f2=_fit(d,line2,820,62,34)
    for text,font,y,fill in ((line1,f1,280,(40,60,95)),(line2,f2,360,(20,38,70))):
        b=d.textbbox((0,0),text,font=font); x=640-(b[2]-b[0])//2; d.text((x,y),text,font=font,fill=fill)
    im.save(png)
    tmp=work/'with_credit.mp4'
    # Re-encode once and append a silent title card. Base cartoons always contain audio,
    # but the '?' map keeps this robust if an unusual lesson does not.
    cmd=[settings.ffmpeg_bin,'-y','-i',str(video),'-loop','1','-t',str(seconds),'-i',str(png),
         '-f','lavfi','-t',str(seconds),'-i','anullsrc=channel_layout=stereo:sample_rate=44100',
         '-filter_complex',
         '[0:v]scale=1280:720,setsar=1,fps=30[v0];[1:v]scale=1280:720,setsar=1,fps=30,format=yuv420p[v1];'
         '[v0][0:a][v1][2:a]concat=n=2:v=1:a=1[v][a]',
         '-map','[v]','-map','[a]','-c:v','libx264','-preset','ultrafast','-crf','23','-c:a','aac','-b:a','160k','-movflags','+faststart',str(tmp)]
    try:
        subprocess.run(cmd,check=True,capture_output=True,timeout=240)
        tmp.replace(output)
        return output
    except Exception:
        # Fallback: keep the old video rather than breaking delivery.
        return video
