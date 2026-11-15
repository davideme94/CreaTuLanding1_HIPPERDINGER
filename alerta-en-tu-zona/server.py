"""Alerta en tu zona: servidor PWA y proxy de alertas CAP del SMN."""
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode, urljoin
from html.parser import HTMLParser
from urllib.request import Request, urlopen
import json, math, os, re, threading, time
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
FEED = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo.xml'
OFFICIAL_CAP_PAGE = 'https://ssl.smn.gob.ar/CAP/AR.php'
GEOREF = 'https://apis.datos.gob.ar/georef/api/v2.0'
PORT = int(os.environ.get('PORT', '8080'))
CACHE_TTL = 300
CACHE = {'ts':0, 'alerts':[], 'complete':False, 'checked_at':None, 'error':'Aún no consultado'}
LOCK = threading.Lock()
PLACES = {}

def clean(x, n=1500):
    return re.sub(r'\s+', ' ', str(x or '')).strip()[:n]

def tag(e):
    return e.tag.rsplit('}',1)[-1].lower()

def child(e, name):
    return next((x for x in e if tag(x)==name.lower()),None) if e is not None else None

def children(e, name):
    return [x for x in e if tag(x)==name.lower()] if e is not None else []

def val(e, name):
    x=child(e,name)
    return ''.join(x.itertext()).strip() if x is not None else ''

def dt(s):
    if not s:return None
    try:return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:
        try:return parsedate_to_datetime(s).astimezone(timezone.utc)
        except Exception:return None

def iso(t):
    return t.isoformat().replace('+00:00','Z') if t else None

def fetch(url, cap=False):
    p=urlsplit(url)
    if p.scheme != 'https' or (cap and p.hostname!='ssl.smn.gob.ar'):
        raise ValueError('Fuente no autorizada')
    req=Request(url,headers={'User-Agent':'AlertaEnTuZona/1.0','Accept':'application/xml,application/json,*/*'})
    with urlopen(req, timeout=14 if cap else 10) as r:
        result=r.read(2500001 if cap else 1500001)
    if len(result)>(2500000 if cap else 1500000):
        raise ValueError('Respuesta excede el tamaño máximo')
    return result

def feed_links(raw):
    root=ET.fromstring(raw)
    links=[]
    for item in root.iter():
        if tag(item) not in ('item','entry'):continue
        entry=child(item,'link')
        url=(entry.get('href','') if entry is not None else '') or (entry.text if entry is not None else '') or val(item,'guid')
        url=url.strip()
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!='ssl.smn.gob.ar' or not p.path.endswith('.xml'):
            raise ValueError('Referencia CAP inválida')
        links.append(url)
    return list(dict.fromkeys(links))

class _CapLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]
    def handle_starttag(self,tag_name,attrs):
        if tag_name.lower()!='a':return
        attrs=dict(attrs)
        href=attrs.get('href','')
        link=urljoin(OFFICIAL_CAP_PAGE,href)
        p=urlsplit(link)
        if p.scheme=='https' and p.hostname=='ssl.smn.gob.ar' and p.path.startswith('/feeds/CAP/') and p.path.endswith('.xml'):
            self.links.append(link)

def official_page_links(raw):
    parser=_CapLinkParser()
    parser.feed(raw.decode('utf-8','replace'))
    return list(dict.fromkeys(parser.links))

def polygon(raw):
    result=[]
    for pair in clean(raw,25000).split():
        m=re.fullmatch(r'(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)', pair)
        if m:
            a,b=map(float,m.groups())
            if -90<=a<=90 and -180<=b<=180:result.append((a,b))
    return result if len(result)>=3 else []

def circle(raw):
    m=re.fullmatch(r'\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*',raw or '')
    return tuple(map(float,m.groups())) if m else None

def get_level(info):
    params=' '.join((val(p,'valueName')+' '+val(p,'value')).lower() for p in children(info,'parameter'))
    for code,span in [('red','rojo'),('orange','naranja'),('yellow','amarillo')]:
        if re.search(r'\b'+span+r'\b',params):
            return code,'nivel explícito informado en CAP'
    severe=val(info,'severity').lower()
    return {'extreme':'red','severe':'orange','moderate':'yellow'}.get(severe,'unknown'),'equivalencia orientativa según severidad CAP'

def parse_cap(raw, link=''):
    root=ET.fromstring(raw)
    if tag(root)!='alert':raise ValueError('Documento no CAP')
    if val(root,'status').lower() not in ('actual',''):return []
    if val(root,'msgType').lower() in ('cancel','ack','error'):return []
    sent=dt(val(root,'sent'))
    items=[]
    for info in children(root,'info'):
        lang=val(info,'language').lower()
        if lang and not lang.startswith('es'):continue
        expires=dt(val(info,'expires'))
        if not expires:raise ValueError('CAP sin vencimiento')
        level,source=get_level(info)
        areas=[]
        for area in children(info,'area'):
            polys=[p for x in children(area,'polygon') if (p:=polygon(x.text))]
            circles=[c for x in children(area,'circle') if (c:=circle(x.text))]
            areas.append({'name':clean(val(area,'areaDesc'),400),'polygons':polys,'circles':circles})
        if not areas or not any(a['polygons'] or a['circles'] for a in areas):
            raise ValueError('CAP sin georreferenciación comprobable')
        items.append({'id':clean(val(root,'identifier'),120),'event':clean(val(info,'event'),120) or 'Fenómeno meteorológico',
            'headline':clean(val(info,'headline'),240),'description':clean(val(info,'description'),1800),
            'instructions':clean(val(info,'instruction'),1800),'severity':val(info,'severity'),
            'level':level,'level_source':source,'issued':iso(sent),
            'starts':iso(dt(val(info,'onset')) or dt(val(info,'effective')) or sent),
            'ends':iso(expires),'areas':areas,'url':link})
    if not items:raise ValueError('CAP sin bloques informativos en español')
    return items

def point_in_polygon(lat,lon,points):
    inside=False
    for i in range(len(points)):
        ya,xa=points[i];yb,xb=points[(i+1)%len(points)]
        if (ya>lat)!=(yb>lat):
            if lon<(xb-xa)*(lat-ya)/(yb-ya)+xa:inside=not inside
    return inside

def km(a,b,c,d):
    dlat=math.radians(c-a);dlon=math.radians(d-b)
    h=math.sin(dlat/2)**2+math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(dlon/2)**2
    return 12742*math.asin(min(1,math.sqrt(h)))

def matching(alert,lat,lon):
    for area in alert['areas']:
        if any(point_in_polygon(lat,lon,p) for p in area['polygons']):return True
        if any(km(lat,lon,c[0],c[1])<=c[2] for c in area['circles']):return True
    return False

def refresh():
    now=time.time()
    with LOCK:
        if now-CACHE['ts']<CACHE_TTL:return CACHE.copy()
        CACHE['ts']=now
    alerts=[];fail=0
    try:
        # El índice RSS puede contener URLs antiguas que el SMN ya no conserva.
        # Priorizamos el índice web oficial registrado en la OMM, con enlaces
        # actualizados a los XML CAP, y usamos el RSS solo como respaldo.
        try:
            raw=fetch(OFFICIAL_CAP_PAGE,True)
            print('DIAGNOSTICO CAP indice OMM:',repr(raw[:145]),flush=True)
            rss_root=ET.fromstring(raw)
            first_item=next((x for x in rss_root.iter() if tag(x)=='item'),None)
            if first_item is not None:
                print('DIAGNOSTICO CAP primer item XML:',ET.tostring(first_item,encoding='unicode')[:3800],flush=True)
            try:links=feed_links(raw)
            except ET.ParseError:links=official_page_links(raw)
            if not links:raise ValueError('El indice oficial CAP no contiene enlaces XML')
            print('DIAGNOSTICO CAP fuente: indice OMM',len(links),flush=True)
        except Exception as exc:
            print('DIAGNOSTICO CAP pagina oficial error:',type(exc).__name__,str(exc)[:200],flush=True)
            links=feed_links(fetch(FEED,True))
            print('DIAGNOSTICO CAP fuente: RSS',len(links),flush=True)
        if len(links)>260:raise ValueError('Demasiados documentos CAP')
        # Evitamos provocar cientos de solicitudes durante el diagnóstico.
        print('DIAGNOSTICO CAP omitiendo descarga masiva hasta validar feed',flush=True)
        with LOCK:
            CACHE.update({'ts':time.time(),'alerts':[],'complete':False,
                'checked_at':iso(datetime.now(timezone.utc)),
                'error':'El SMN limita las consultas de documentos individuales.'})
            return CACHE.copy()
        with concurrent.futures.ThreadPoolExecutor(max_workers=14) as pool:
            fs=[pool.submit(lambda url:parse_cap(fetch(url,True),url),url) for url in links]
            for f in concurrent.futures.as_completed(fs):
                try:alerts.extend(f.result())
                except Exception as exc:
                    fail+=1
                    if fail<=4: print('DIAGNOSTICO CAP documento no disponible:',type(exc).__name__,str(exc)[:220],flush=True)
        print('DIAGNOSTICO CAP feed documentos:',len(links),'alertas leidas:',len(alerts),'fallas:',fail,flush=True)
        result={'ts':time.time(),'alerts':alerts,'complete':fail==0,
                'checked_at':iso(datetime.now(timezone.utc)),
                'error':None if fail==0 else 'No pudieron verificarse todas las alertas CAP.'}
    except Exception as exc:
        print('DIAGNOSTICO CAP feed error:',type(exc).__name__,str(exc)[:260],flush=True)
        result={'ts':time.time(),'alerts':[],'complete':False,
                'checked_at':iso(datetime.now(timezone.utc)),
                'error':'La fuente oficial no se encuentra disponible desde este servidor.'}
    with LOCK:
        CACHE.update(result)
        return CACHE.copy()

def alerts_for(lat,lon):
    snap=refresh();now=datetime.now(timezone.utc);matches=[]
    for a in snap['alerts']:
        expiry=dt(a['ends']);issued=dt(a['issued'])
        if expiry and expiry<now:continue
        if issued and issued>now:continue
        if matching(a,lat,lon):
            matches.append({k:v for k,v in a.items() if k!='areas'}|
                {'area':', '.join(x['name'] for x in a['areas'] if x['name'])[:400]})
    rank={'red':4,'orange':3,'yellow':2,'unknown':1}
    matches.sort(key=lambda x:rank.get(x['level'],1),reverse=True)
    status=matches[0]['level'] if matches else ('green' if snap['complete'] else 'unknown')
    return {'ok':snap['complete'],'status':status,'alerts':matches[:35],'checked_at':snap['checked_at'],
            'source':'Servicio Meteorológico Nacional · CAP','source_url':'https://www.smn.gob.ar/alertas',
            'error':snap['error'],'scope':'Cobertura de alertas CAP. No incluye necesariamente avisos a corto plazo ni temperaturas extremas.'}

def get_place(key, url):
    v=PLACES.get(key)
    if v and time.time()-v[0]<86400:return v[1]
    res=json.loads(fetch(url))
    if len(PLACES)>200:PLACES.clear()
    PLACES[key]=(time.time(),res)
    return res

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(PUBLIC),**kw)
    def log_message(self,*a):pass # No guardar coordenadas precisas en logs
    def send_json(self,data,status=200):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        parsed=urlsplit(self.path);path=parsed.path;q=parse_qs(parsed.query)
        if path=='/api/health':return self.send_json({'ok':True,'service':'Alerta en tu zona'})
        if path.startswith('/api/'):
            try:
                if path in ('/api/alerts','/api/reverse'):
                    lat=float(q.get('lat',[''])[0]);lon=float(q.get('lon',[''])[0])
                    if not(-56<=lat<=-21 and -75<=lon<=-52):
                        return self.send_json({'error':'Seleccioná un punto de Argentina.'},400)
                    if path=='/api/alerts':return self.send_json(alerts_for(lat,lon))
                    url=GEOREF+'/ubicacion?'+urlencode({'lat':round(lat,6),'lon':round(lon,6)})
                    d=get_place(('reverse',round(lat,3),round(lon,3)),url)
                    x=d.get('ubicacion') or {}
                    m=(x.get('municipio') or {}).get('nombre')
                    dep=(x.get('departamento') or {}).get('nombre')
                    prov=(x.get('provincia') or {}).get('nombre')
                    return self.send_json({'name':m or dep or 'Tu ubicación actual','province':prov or 'Argentina'})
                if path=='/api/places':
                    name=clean(q.get('q',[''])[0],90)
                    if len(name)<3:return self.send_json({'places':[]})
                    url=GEOREF+'/localidades?'+urlencode({'nombre':name,'max':12,'campos':'nombre,provincia,centroide'})
                    d=get_place(('places',name.lower()),url)
                    found=[]
                    for p in d.get('localidades',[]):
                        c=p.get('centroide') or {}
                        if 'lat' in c and 'lon' in c:
                            found.append({'name':p.get('nombre'),'province':(p.get('provincia') or {}).get('nombre'),
                                'lat':c['lat'],'lon':c['lon']})
                    return self.send_json({'places':found})
                return self.send_json({'error':'Ruta no encontrada'},404)
            except (ValueError,TypeError,IndexError):
                return self.send_json({'error':'Consulta inválida'},400)
            except Exception:
                return self.send_json({'error':'Servicio externo no disponible'},503)
        self.path='/index.html' if path=='/' else path
        return super().do_GET()
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        if self.path=='/sw.js':
            self.send_header('Service-Worker-Allowed','/')
            self.send_header('Cache-Control','no-cache')
        return super().end_headers()

if __name__=='__main__':
    print('Alerta en tu zona escuchando en puerto',PORT,flush=True)
    threading.Thread(target=refresh,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
