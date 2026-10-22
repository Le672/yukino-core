"""Small Pillow result cards with CJK fonts; no browser or generated facts."""
from io import BytesIO
from pathlib import Path
import os
import re
import unicodedata
from PIL import Image, ImageDraw, ImageFont


def font(size, mono=False):
    candidates = [os.environ.get('YUKINO_CARD_FONT',''),
        str(Path(__file__).with_name('static')/'NotoSansSC-Regular.ttf'),
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        r'C:\Windows\Fonts\msyh.ttc',r'C:\Windows\Fonts\simhei.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path,size)
    return ImageFont.load_default(size=size)


def wrap(draw, text, face, width):
    lines=[]
    for paragraph in text.splitlines() or ['']:
        line=''
        for character in paragraph:
            if draw.textlength(line+character,font=face)>width and line:
                lines.append(line);line=''
            line+=character
        lines.append(line)
    return lines


def render(title, text, *, board=False, accent='#447868'):
    width=1040; face=font(25); heading=font(36); small=font(20)
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    if board:
        lines=[]
        for paragraph in text[:12000].splitlines():
            line='';used=0
            for char in paragraph:
                span=32 if unicodedata.east_asian_width(char) in ('W','F') else 16
                if used+span>width-112 and line:lines.append(line);line='';used=0
                line+=char;used+=span
            lines.append(line)
    else:lines=wrap(probe,text[:12000],face,width-112)
    pages=[]
    for start in range(0,min(len(lines),210),70):
        section=lines[start:start+70]
        height=max(240,166+len(section)*39+62)
        image=Image.new('RGB',(width,height),'#f5f5ef');draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((24,24,width-24,height-24),radius=25,fill='white',outline='#dde5df',width=2)
        draw.rounded_rectangle((48,48,58,95),radius=4,fill=accent)
        draw.text((76,45),title[:40],font=heading,fill='#203d36')
        draw.line((56,114,width-56,114),fill='#e1e9e2',width=2)
        for index,line in enumerate(section):
            y=138+index*39
            # Board glyphs retain exact row ordering from the real game state.
            if board and re.search(r'[●○■□▓█│┼┌┐└┘]',line):
                draw.rounded_rectangle((48,y-2,width-48,y+37),radius=5,fill='#f0f5ed')
            if board:
                x=56
                for char in line:
                    span=32 if unicodedata.east_asian_width(char) in ('W','F') else 16
                    if char in '🟩🟨⬛⬜':
                        color={'🟩':'#58a57a','🟨':'#d8b849','⬛':'#47514d','⬜':'#dce3dc'}[char]
                        draw.rounded_rectangle((x+2,y+4,x+span-2,y+33),radius=4,fill=color)
                    else:draw.text((x,y),char,font=face,fill='#263d38')
                    x+=span
            else:draw.text((56,y),line,font=face,fill='#263d38')
        page_number=start//70+1
        draw.text((56,height-65),f'YUKINO CORE  ·  {page_number} / {(min(len(lines),210)+69)//70}',font=small,fill='#779187')
        data=BytesIO();image.save(data,'PNG',optimize=True);pages.append(data.getvalue())
    return pages
