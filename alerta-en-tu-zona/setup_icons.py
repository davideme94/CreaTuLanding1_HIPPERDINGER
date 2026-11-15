"""Genera los íconos PWA desde el logo institucional incorporado al HTML."""
from pathlib import Path
from io import BytesIO
from PIL import Image
import re, base64

P = Path(__file__).resolve().parent / 'public'
A = P / 'assets'
A.mkdir(parents=True,exist_ok=True)
html=(P/'index.html').read_text(encoding='utf-8')
m=re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)',html)
if not m:raise SystemExit('No se encontró el logo institucional incrustado en index.html')
original=base64.b64decode(m.group(1),validate=True)
(A/'logo-institucional.png').write_bytes(original)
logo=Image.open(BytesIO(original)).convert('RGBA')
pix=logo.load()
points=[(x,y) for y in range(logo.height) for x in range(logo.width)
        if min(pix[x,y][:3])<225 and not all(c>242 for c in pix[x,y][:3])]
if points:
    xs,ys=zip(*points)
    logo=logo.crop((max(0,min(xs)-5),max(0,min(ys)-5),min(logo.width,max(xs)+6),min(logo.height,max(ys)+6)))

def icon(size,scale,name):
    canvas=Image.new('RGBA',(size,size),'white')
    w=round(size*scale);h=round(w*logo.height/logo.width)
    if h>size*scale:
        h=round(size*scale);w=round(h*logo.width/logo.height)
    body=logo.resize((w,h),Image.Resampling.LANCZOS)
    canvas.alpha_composite(body,((size-w)//2,(size-h)//2))
    if size>=192:
        canvas=canvas.convert('RGB').quantize(colors=96)
    canvas.save(A/name,'PNG',optimize=True)
    return canvas

icon(192,.91,'icon-192.png')
icon(512,.85,'icon-512.png')
icon(512,.64,'maskable-512.png')
icon(180,.9,'apple-touch-icon.png')
icon(64,.96,'favicon.png')
Image.open(A/'favicon.png').convert('RGBA').save(P/'favicon.ico',format='ICO',sizes=[(16,16),(32,32),(48,48)])
print('Íconos PWA generados correctamente')
