"""Genera los iconos PWA desde el nuevo diseño aprobado.

El logo institucional ORIGINAL permanece separado dentro de la interfaz.
El archivo fuente de este favicon se incluye en assets/ como WebP base64
para facilitar la publicación desde GitHub en un entorno de solo texto.
"""
from pathlib import Path
from io import BytesIO
from PIL import Image
import re, base64

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
ASSETS = PUBLIC / "assets"
ASSETS.mkdir(parents=True,exist_ok=True)

# Conservamos el logo original que se sigue usando en el encabezado.
html=(PUBLIC / "index.html").read_text(encoding="utf-8")
match=re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)",html)
if match:
    (ASSETS / "logo-institucional.png").write_bytes(base64.b64decode(match.group(1),validate=True))

# Nuevo icono original de Alerta en tu zona.
source=(ROOT / "assets" / "nuevo-icono-source.webp.b64").read_text(encoding="utf-8").strip()
image=Image.open(BytesIO(base64.b64decode(source,validate=True))).convert("RGB")
assert image.width == 512 and image.height == 512, "El diseño fuente debe ser cuadrado."

def export(size, name, inset=1.0, background="#0C4174"):
    canvas=Image.new("RGB",(size,size),background)
    side=round(size*inset)
    scaled=image.resize((side,side),Image.Resampling.LANCZOS)
    canvas.paste(scaled,((size-side)//2,(size-side)//2))
    canvas.save(ASSETS/name,"PNG",optimize=True)

export(192,"icon-192-v2.png")
export(512,"icon-512-v2.png")
export(512,"maskable-512-v2.png",inset=.8)
export(180,"apple-touch-icon-v2.png")
export(64,"favicon-v2.png")
image.save(PUBLIC/"favicon.ico",format="ICO",sizes=[(16,16),(32,32),(48,48)])
print("Nuevo ícono instalable y favicon generados correctamente",flush=True)
