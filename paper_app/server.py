"""Kronos historical simulation dashboard with durable, single-job execution."""
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


class Jobs:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory/'jobs.sqlite'
        self.lock = threading.RLock()
        self.process = None
        self.active = None
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT, status TEXT, created TEXT, updated TEXT, message TEXT, settings TEXT)')
            c.execute("UPDATE jobs SET status='interrupted',message='Servidor reiniciado; la prueba no se reanuda automáticamente.',updated=? WHERE status IN ('running','stopping')",(now(),))

    def connect(self):
        return sqlite3.connect(self.db)

    def update(self, ident, status, message=''):
        with self.connect() as c:
            c.execute('UPDATE jobs SET status=?,message=?,updated=? WHERE id=?',(status,message,now(),ident))

    def list(self):
        with self.connect() as c:
            c.row_factory = sqlite3.Row
            rows=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]
        for r in rows:
            r['settings']=json.loads(r['settings'])
        return rows

    def start(self, csv_text, settings):
        import math
        if not isinstance(csv_text,str) or not csv_text.strip():
            raise ValueError('Carga un CSV antes de iniciar.')
        mode=settings.get('mode','forecast')
        if mode not in ('forecast','import'):
            raise ValueError('Modo inválido.')
        clean={k:float(settings.get(k,d)) for k,d in [('balance',10000),('commission',1.25),('slippage',1)]}
        if not all(math.isfinite(v) for v in clean.values()) or clean['balance'] <= 0 or clean['commission'] < 0 or clean['slippage'] < 0 or not clean['slippage'].is_integer():
            raise ValueError('Saldo o costes inválidos.')
        clean['slippage']=int(clean['slippage']); clean['mode']=mode
        if len(csv_text.encode()) > 20*1024*1024:
            raise ValueError('Máximo 20 MB por CSV.')
        fingerprint=hashlib.sha256((csv_text+json.dumps(clean,sort_keys=True)).encode()).hexdigest()
        with self.lock:
            if self.active:
                raise ValueError('Ya hay una prueba activa; espera o detenla.')
            with self.connect() as c:
                duplicate=c.execute("SELECT id FROM jobs WHERE fingerprint=? AND status='completed'",(fingerprint,)).fetchone()
                if duplicate:
                    return {'id':duplicate[0],'reused':True}
            ident=uuid.uuid4().hex
            folder=self.directory/ident; folder.mkdir()
            source=folder/'input.csv'; source.write_text(csv_text,encoding='utf-8')
            from futures_lab.backtest import load_bars
            bars=load_bars(source)
            if len(bars) < 53:
                raise ValueError('Se necesitan al menos 53 velas para indicadores y una salida de 15 minutos.')
            if mode=='forecast' and len(bars) < 403:
                raise ValueError('Kronos necesita 400 velas de contexto y datos posteriores: mínimo 403.')
            if mode=='import' and not any(b['forecast_close'] is not None for b in bars):
                raise ValueError('El modo importar requiere la columna forecast_close con predicciones causales.')
            with self.connect() as c:
                c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(ident,fingerprint,'running',now(),now(),'',json.dumps(clean)))
            self.active=ident
            threading.Thread(target=self.run,args=(ident,clean),daemon=True).start()
            return {'id':ident,'reused':False}

    def run(self, ident, settings):
        folder=self.directory/ident
        source=folder/'input.csv'
        forecast=folder/'forecasts.csv'
        commands=[]
        if settings['mode']=='forecast':
            commands.append([sys.executable,'-m','futures_lab.forecast',str(source),'--output',str(forecast),'--device','cpu'])
        else:
            forecast=source
        commands.append([sys.executable,'-m','futures_lab.backtest',str(forecast),'--output',str(folder/'results'),
                         '--balance',str(settings['balance']),'--commission',str(settings['commission']),
                         '--slippage-ticks',str(settings['slippage'])])
        try:
            with (folder/'execution.log').open('wb') as log:
                for command in commands:
                    with self.lock:
                        if self.status(ident)=='stopping':
                            self.update(ident,'stopped'); return
                        self.process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                        proc=self.process
                    code=proc.wait()
                    with self.lock:
                        self.process=None
                        if self.status(ident)=='stopping':
                            self.update(ident,'stopped'); return
                    if code:
                        self.update(ident,'failed','La ejecución falló. Consulta el registro; los resultados parciales no son finales.')
                        return
                # Completion and stop are serialized to prevent contradictory outcomes.
                with self.lock:
                    self.update(ident,'stopped' if self.status(ident)=='stopping' else 'completed')
        except Exception as exc:
            self.update(ident,'failed',str(exc))
        finally:
            with self.lock:
                self.process=None; self.active=None

    def status(self, ident):
        with self.connect() as c:
            row=c.execute('SELECT status FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('Prueba desconocida.')
        return row[0]

    def stop(self):
        with self.lock:
            if not self.active:
                return {'status':'idle'}
            self.update(self.active,'stopping','Detención solicitada.')
            if self.process and self.process.poll() is None:
                # One subprocess, no broker/order execution. kill guarantees prompt cancellation.
                self.process.kill()
            return {'status':'stopping'}

    def detail(self, ident):
        if len(ident)!=32 or any(ch not in '0123456789abcdef' for ch in ident):
            raise ValueError('Identificador inválido.')
        status=self.status(ident)
        folder=self.directory/ident
        result={'status':status,'results':{}}
        if status=='completed':
            for variant in ('fixed','trailing'):
                result['results'][variant]=json.loads((folder/'results'/(variant+'.json')).read_text())
        log=folder/'execution.log'
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0,log.stat().st_size-16000))
                result['log']=f.read().decode('utf-8',errors='replace')
        else: result['log']=''
        return result


def make_handler(jobs, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self, code, value, content_type='application/json'):
            body=value.encode() if isinstance(value,str) else json.dumps(value,allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type',content_type+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers(); self.wfile.write(body)
        def authorized(self):
            return not token or hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            if self.path=='/':
                self.send(200,(ROOT/'paper_app'/'index.html').read_text(),'text/html'); return
            if not self.authorized(): self.send(401,{'error':'Introduce la clave de acceso del panel.'}); return
            try:
                if self.path=='/api/jobs': self.send(200,jobs.list())
                elif self.path.startswith('/api/jobs/'):
                    self.send(200,jobs.detail(self.path.rsplit('/',1)[-1]))
                else: self.send(404,{'error':'Ruta desconocida.'})
            except ValueError as exc: self.send(400,{'error':str(exc)})
        def do_POST(self):
            # Reject browser cross-origin requests even on localhost.
            origin=self.headers.get('Origin')
            if origin and origin not in ('http://'+self.headers.get('Host',''),'https://'+self.headers.get('Host','')):
                self.send(403,{'error':'Origen no permitido.'}); return
            if not self.authorized(): self.send(401,{'error':'Acceso denegado.'}); return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 22*1024*1024: raise ValueError('Solicitud vacía o demasiado grande.')
                if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('Se requiere JSON.')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError('Solicitud inválida.')
                if self.path=='/api/start': self.send(200,jobs.start(body.get('csv',''),body.get('settings',{})))
                elif self.path=='/api/stop': self.send(200,jobs.stop())
                else: self.send(404,{'error':'Ruta desconocida.'})
            except (ValueError,KeyError,TypeError,OSError) as exc: self.send(400,{'error':str(exc)})
    return Handler


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8080)
    p.add_argument('--state',default='paper-state')
    args=p.parse_args()
    token=os.environ.get('KRONOS_PANEL_TOKEN','')
    if args.host not in ('127.0.0.1','localhost','::1') and len(token)<24:
        p.error('Para acceso externo configura KRONOS_PANEL_TOKEN de al menos 24 caracteres y HTTPS.')
    jobs=Jobs(args.state)
    server=ThreadingHTTPServer((args.host,args.port),make_handler(jobs,token))
    print(f'Panel histórico: http://{args.host}:{args.port} — solo simulación',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: jobs.stop(); server.server_close()

if __name__=='__main__': main()
