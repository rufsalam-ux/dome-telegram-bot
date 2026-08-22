from pathlib import Path
from PIL import Image, ImageDraw
from app.services.character_processor import process_character

def test_white_background_removed(tmp_path: Path):
    src=tmp_path/'source.jpg'; out=tmp_path/'out.png'
    im=Image.new('RGB',(400,400),'white'); d=ImageDraw.Draw(im); d.ellipse((100,80,300,330),fill='red'); im.save(src)
    process_character(src,out)
    result=Image.open(out).convert('RGBA')
    assert result.getbbox() is not None
    assert min(result.getchannel('A').getextrema()) == 0
