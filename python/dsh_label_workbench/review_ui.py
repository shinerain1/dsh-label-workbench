"""Small loopback reviewer UI; token is local access, not multi-user identity proof."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from .service import Fault

HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DSH 标注复核</title>
<style>body{font:16px system-ui,sans-serif;background:#f5f7fa;color:#202b38;max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:28px}header{display:flex;justify-content:space-between;align-items:center}.badge{background:#e2edff;color:#184b91;border-radius:20px;padding:7px 16px}section{background:white;border:1px solid #dce2eb;border-radius:12px;padding:24px;margin:18px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.7}button,select,input,textarea{font:inherit;padding:10px;border:1px solid #c7d1df;border-radius:7px}button{cursor:pointer;background:#215fba;color:white;border:0}label{display:block;margin:16px 0 6px}textarea{box-sizing:border-box;width:100%;min-height:70px}.muted{color:#607084;font-size:14px}#message{white-space:pre-wrap;color:#a52a28}nav{display:flex;gap:14px;align-items:center}#text{font-size:20px}#history{font-size:14px}button:disabled{opacity:.5}details{margin-top:16px}</style>
<header><h1>DSH 标注复核</h1><span id="version" class="badge"></span></header>
<p class="muted">规则改变后重新确认；历史审核始终保留。此页面由本机审核者操作。</p>
<section><strong id="project"></strong><pre id="rule"></pre><div id="counts" class="muted"></div></section>
<section id="card"><nav><strong id="record"></strong><span id="position" class="muted"></span></nav><pre id="text"></pre><pre id="history" class="muted"></pre>
<label for="reviewer">审核者</label><input id="reviewer" autocomplete="name" placeholder="填写你的名字">
<label for="label">新规则下的标签</label><select id="label"></select><label for="note">审核说明（可选）</label><textarea id="note"></textarea>
<p><button id="save">确认此标签并继续</button> <button id="refresh">刷新队列</button></p></section><p id="message" role="status"></p>
<script>
const token=new URLSearchParams(location.search).get('token'); let state,item;
async function api(path,body){const r=await fetch(path+'?token='+encodeURIComponent(token),{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok)throw Error(j.error);return j}
function text(id,value){document.getElementById(id).textContent=value}
async function load(){try{
state=await api('/state');item=state.queue.items[0];text('project',state.status.name);text('version','规则 v'+state.status.rule_version);
const rule=state.status.rule;let explanation=rule.guidelines;
if(rule.mode==='decision_list')explanation+='\n\n按顺序检查，首条命中即采用：\n'+rule.rules.map((r,i)=>(i+1)+'. 包含 '+r.contains_any.join(' / ')+' → '+r.label).join('\n')+'\n均未命中 → '+rule.default;
text('rule',explanation);text('counts','共 '+state.status.total+' 条 · 待处理 '+state.status.pending+' 条');document.getElementById('card').hidden=!item;
if(!item){text('message','当前没有待复核记录。导出前仍会检查后台任务和规则版本。');return}
text('record',item.id);text('position','修订 '+item.revision);text('text',item.text);
const origins={human:'人工审核',machine:'自动建议',carry:'已核对沿用'};
text('history',item.decision?'历史标签：'+item.decision.label+' · 规则 v'+item.decision.version+' · '+origins[item.decision.origin]+(item.suggestions.length?'\n待确认建议：'+item.suggestions.map(s=>s.label+'（v'+s.version+'）').join('、'):''):'尚无历史标签');
const select=document.getElementById('label'),placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择新规则下的标签';placeholder.disabled=true;placeholder.selected=true;
select.replaceChildren(placeholder,...rule.labels.map(label=>{const o=document.createElement('option');o.value=label;o.textContent=label;return o}));document.getElementById('note').value='';text('message','');
}catch(e){text('message',e.message)}}
document.getElementById('refresh').onclick=load;
document.getElementById('save').onclick=async()=>{const b=document.getElementById('save');b.disabled=true;try{await api('/review',{record_id:item.id,expected_version:state.status.rule_version,expected_revision:item.revision,label:document.getElementById('label').value,reviewer:document.getElementById('reviewer').value,note:document.getElementById('note').value});await load()}catch(e){text('message',e.message+'。请刷新后重新确认。')}finally{b.disabled=false}};load();
</script></html>'''


def make_server(wb,project,port=0):
    wb.call("status",{"project_id":project})
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def send(self,status,value,html=False):
            data=(value if html else json.dumps(value,ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type","text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            given=parse_qs(urlparse(self.path).query).get("token",[""])[0]
            return secrets.compare_digest(given,token) and self.headers.get("Host")==f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.authorized(): return self.send(403,{"error":"Local reviewer token required"})
            path=urlparse(self.path).path
            if path=="/": return self.send(200,HTML,True)
            if path=="/state":
                return self.send(200,{"status":wb.call("status",{"project_id":project}),"queue":wb.call("queue",{"project_id":project,"limit":1})})
            self.send(404,{"error":"Not found"})

        def do_POST(self):
            if not self.authorized() or self.headers.get("Origin") not in {None,f"http://127.0.0.1:{self.server.server_port}"}:
                return self.send(403,{"error":"Local reviewer token/origin required"})
            if urlparse(self.path).path!="/review": return self.send(404,{"error":"Not found"})
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=20000: raise ValueError("Invalid request size")
                args=json.loads(self.rfile.read(length))
                args["project_id"]=project
                self.send(200,wb.call("review",args,human=True))
            except (Fault,ValueError,TypeError,KeyError) as exc:
                self.send(409,{"error":str(exc)})
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    return server,f"http://127.0.0.1:{server.server_port}/?token={token}"


def serve(wb,project,port=0):
    server,url=make_server(wb,project,port)
    print(json.dumps({"review_url":url,"notice":"Local single-user reviewer; Ctrl+C stops server"}),flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
