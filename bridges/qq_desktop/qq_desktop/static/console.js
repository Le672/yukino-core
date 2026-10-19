'use strict';
let auth='',rev='',nodes=[];
const $=id=>document.getElementById(id);
function notice(text,bad=false){$('notice').textContent=text;$('notice').classList.toggle('bad',bad);}
async function api(name,method='GET',value){
 const headers={Authorization:'Bearer '+auth,'X-Desktop-Console':'1','X-Config-Revision':rev};
 if(value!==undefined)headers['Content-Type']='application/json';
 const r=await fetch('/console/api/'+name,{method,headers,body:value===undefined?undefined:JSON.stringify(value),credentials:'omit',cache:'no-store'});
 if(!r.ok)throw new Error((await r.text()).slice(0,250));return r.json();
}
function showConfig(data){rev=data.revision;$('config').value=JSON.stringify(data.config,null,2);$('binding').replaceChildren();data.config.bindings.forEach((b,i)=>{const o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+b.kind+' / '+b.id+(b.verified?' · 已核验':' · 待核验');$('binding').append(o);});$('reviewed').checked=false;}
async function status(){const d=await api('status'),c=d.capabilities;$('online').textContent=c.status.online?'已核验在线':'未就绪';$('reason').textContent=c.status.reason;$('features').textContent=['text','image','file'].map(k=>c[k].ready?'可用':'待校准').join(' / ');$('sends').textContent=c.metrics.sends_confirmed+' / '+c.metrics.sends_uncertain;$('spool').textContent=(d.media.bytes/1048576).toFixed(1)+' MiB';$('limitations').textContent='尚未支持：视频、语音、合并转发、撤回、戳一戳、群管理。真实 QQ 收发未完成验收。';$('error-state').textContent=d.last_error?'最近界面错误：'+d.last_error:'';}
async function load(){showConfig(await api('config'));await status();}
function action(id,fn){$(id).addEventListener('click',async()=>{const b=$(id);b.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{b.disabled=false;}});}
action('connect',async()=>{auth=$('token').value.trim();await load();$('token').value='';$('workspace').hidden=false;notice('已连接。配置保存与界面核验均不会发送 QQ 消息。');});
action('disconnect',async()=>{auth='';rev='';nodes=[];$('token').value='';$('workspace').hidden=true;$('tree').replaceChildren();$('config').value='';$('selector').textContent='';$('media-result').textContent='';notice('已清除当前页面令牌和私人界面数据。');});
action('refresh',async()=>{await status();notice('状态已刷新。');});
action('reload',async()=>{await load();notice('已重新读取配置。');});
action('save',async()=>{showConfig(await api('config','PUT',JSON.parse($('config').value)));await status();notice('已保存禁用配置；请重新核验会话。');});
action('pause',async()=>{showConfig(await api('state','POST',{enabled:false}));await status();notice('已暂停自动收发。');});
action('enable',async()=>{showConfig(await api('state','POST',{enabled:true}));await status();notice('已核验本人身份并启用；各会话仍需数字身份字段匹配。');});
action('verify',async()=>{if(!$('binding').value)throw new Error('先保存至少一个会话绑定。');showConfig(await api('verify','POST',{index:Number($('binding').value),attachments_reviewed:$('reviewed').checked}));await status();notice('当前会话身份核验通过；没有发送 QQ 消息。');});
function render(){const f=$('filter').value.toLocaleLowerCase();$('tree').replaceChildren();let count=0;for(const n of nodes){if(!JSON.stringify(n).toLocaleLowerCase().includes(f))continue;if(++count>250)break;const b=document.createElement('button');b.textContent=JSON.stringify(n.path)+' · '+n.role+' · '+(n.name||n.text||'（无名称）').slice(0,120);b.addEventListener('click',()=>{$('selection').textContent='控件文本：'+n.text;$('selector').textContent=JSON.stringify({path:n.path,...(n.name?{name:n.name}:{}),role:n.role},null,2);});$('tree').append(b);}if(count>250)notice('匹配超过 250 个控件，请输入筛选条件。');}
$('filter').addEventListener('input',render);
action('inspect',async()=>{nodes=(await api('inspect')).nodes;render();notice('已读取 '+nodes.length+' 个可见控件，未切换会话或发送消息。');});
action('upload',async()=>{const f=$('media-file').files[0];if(!f)throw new Error('请选择文件。');if(f.size>8388608)throw new Error('单文件不能超过 8 MiB。');const q=new URLSearchParams({kind:$('media-kind').value,name:f.name});const r=await fetch('/console/api/upload?'+q,{method:'POST',headers:{Authorization:'Bearer '+auth,'X-Desktop-Console':'1','Content-Type':'application/octet-stream'},body:f,credentials:'omit'});if(!r.ok)throw new Error(await r.text());$('media-result').textContent=JSON.stringify(await r.json(),null,2);await status();notice('附件已暂存，没有发送 QQ。');});
