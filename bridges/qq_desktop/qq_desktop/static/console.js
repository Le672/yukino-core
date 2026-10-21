'use strict';
let auth='',rev='',nodes=[];
const $=id=>document.getElementById(id);
function notice(text,bad=false){$('notice').textContent=text;$('notice').classList.toggle('bad',bad);}
const reasons={disabled_until_calibrated:'已暂停，等待身份及会话校准',ready:'本人数字身份已核验',login_or_calibration_required:'请检查登录状态和身份控件'};
function explain(text){
 const messages=[
 ['Calibrate and verify all enabled bindings first','请先保存并核验每一个会话绑定，再启用收发。'],
 ['Configuration changed','配置已被其他操作更新，请重新读取后再保存。'],
 ['visible self QQ identity','当前界面未找到匹配的本人 QQ 号，请检查登录和身份选择器。'],
 ['QQ identity mismatch','当前界面与配置中的本人 QQ 号不一致。'],
 ['UI selector is missing or ambiguous','控件选择器未匹配或匹配了多个控件，请重新校准。'],
 ['Expected one exact QQ application','没有找到唯一的 QQ 窗口，请检查应用是否运行。'],
 ['existing draft','当前输入框有草稿，为保护草稿已停止操作。'],
 ['Expected an MP4','视频需要有效的 MP4 文件头。'],
 ['Media must be 1..8 MiB','附件必须大于零且不能超过 8 MiB。'],
 ['Media quota reached','附件空间已满，请先归档旧附件。'],
 ['QQ identifiers must be numeric','QQ 身份必须填写真实的数字 QQ 号。'],
 ['No writable config path','此实例没有配置可写的配置文件路径。']
 ];
 for(const [key,value] of messages)if(text.includes(key))return value;
 return text;
}
async function api(name,method='GET',value){
 const headers={Authorization:'Bearer '+auth,'X-Desktop-Console':'1','X-Config-Revision':rev};
 if(value!==undefined)headers['Content-Type']='application/json';
 const r=await fetch('/console/api/'+name,{method,headers,body:value===undefined?undefined:JSON.stringify(value),credentials:'omit',cache:'no-store'});
 if(!r.ok){const detail=(await r.text()).slice(0,250);throw new Error(r.status===401?'接口令牌无效，请重新连接。':explain(detail));}return r.json();
}
function showConfig(data){rev=data.revision;$('config').value=JSON.stringify(data.config,null,2);$('binding').replaceChildren();data.config.bindings.forEach((b,i)=>{const o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+(b.kind==='private'?'私聊':'群聊')+' / '+b.id+(b.verified?' · 已核验':' · 待核验');$('binding').append(o);});$('reviewed').checked=false;$('notices-reviewed').checked=false;}
async function status(){const d=await api('status'),c=d.capabilities;$('online').textContent=c.status.online?'已核验在线':'未就绪';$('reason').textContent=reasons[c.status.reason]||'请运行连接诊断';$('features').textContent=['text','image','file','video'].map(k=>c[k].ready?'可用':c[k].implemented?'待校准':'未支持').join(' / ');$('sends').textContent=c.metrics.sends_confirmed+' / '+c.metrics.sends_uncertain;$('spool').textContent=(d.media.bytes/1048576).toFixed(1)+' MiB';$('limitations').textContent=c.backend==='qqnt_private'?'官方 QQ 私聊模式：仅一个已核验私聊、纯文字，以及标准“对方戳了戳你”通知。读取/发送前会打开资料卡核验数字 QQ 号；核验时请勿手工操作桌面。图片、文件、群聊和管理动作未支持。实例收发验收请查看独立测试记录。':'通用选择器模式：文本、图片、文件、MP4 小视频、戳一戳通知读取须逐项校准。尚未支持：语音、合并转发、撤回、主动戳人、群管理。实例收发验收请查看独立测试记录。';$('error-state').textContent=d.last_error?'最近界面错误：'+(d.last_error==='UiError'?'界面或数字身份校验失败，请运行连接诊断。':d.last_error):'';}
async function diagnose(){const d=await api('diagnostics');$('diagnostics').replaceChildren();for(const c of d.checks){const item=document.createElement('li');item.className=c.ok?'passed':'pending';const title=document.createElement('strong');title.textContent=(c.ok?'已通过 · ':'待处理 · ')+c.message;item.append(title);if(c.next_step){const p=document.createElement('p');p.textContent=c.next_step;item.append(p);}$('diagnostics').append(item);}const when=t=>t?new Date(t*1000).toLocaleString('zh-CN'):'尚无记录';$('diagnostic-time').textContent='最近读取：'+when(d.last_poll_at)+'；最近事件：'+when(d.last_event_at)+'。诊断只读取当前窗口，不切换会话或发送消息。';}
async function load(){showConfig(await api('config'));await status();await diagnose();}
function action(id,fn){$(id).addEventListener('click',async()=>{const b=$(id);b.disabled=true;try{await fn();}catch(e){notice(e instanceof SyntaxError?'配置 JSON 格式错误，请检查逗号、引号和括号。':explain(e.message),true);}finally{b.disabled=false;}});}
action('connect',async()=>{auth=$('token').value.trim();await load();$('token').value='';$('workspace').hidden=false;notice('已连接。配置保存与界面核验均不会发送 QQ 消息。');});
action('disconnect',async()=>{auth='';rev='';nodes=[];$('token').value='';$('workspace').hidden=true;$('tree').replaceChildren();$('config').value='';$('selector').textContent='';$('selection').textContent='';$('diagnostics').replaceChildren();$('diagnostic-time').textContent='';$('media-result').textContent='';notice('已清除当前页面令牌和私人界面数据。');});
action('refresh',async()=>{await status();await diagnose();notice('状态已刷新。');});
action('diagnose',async()=>{await diagnose();notice('连接诊断完成，没有执行登录或发送动作。');});
action('reload',async()=>{await load();notice('已重新读取配置。');});
action('save',async()=>{showConfig(await api('config','PUT',JSON.parse($('config').value)));await status();notice('已保存禁用配置；请重新核验会话。');});
action('pause',async()=>{showConfig(await api('state','POST',{enabled:false}));await status();notice('已暂停自动收发。');});
action('enable',async()=>{showConfig(await api('state','POST',{enabled:true}));await status();notice('已核验本人身份并启用；各会话仍需数字身份字段匹配。');});
action('verify',async()=>{if(!$('binding').value)throw new Error('先保存至少一个会话绑定。');showConfig(await api('verify','POST',{index:Number($('binding').value),attachments_reviewed:$('reviewed').checked,notices_reviewed:$('notices-reviewed').checked}));await status();await diagnose();notice('当前会话身份核验通过；没有发送 QQ 消息。附件及通知流程仍需真实 QQ 验收。');});
function render(){const f=$('filter').value.toLocaleLowerCase();$('tree').replaceChildren();let count=0;for(const n of nodes){if(!JSON.stringify(n).toLocaleLowerCase().includes(f))continue;if(++count>250)break;const b=document.createElement('button');b.textContent=JSON.stringify(n.path)+' · '+n.role+' · '+(n.name||n.text||'（无名称）').slice(0,120);b.addEventListener('click',()=>{$('selection').textContent='控件文本：'+n.text;$('selector').textContent=JSON.stringify({path:n.path,...(n.name?{name:n.name}:{}),role:n.role},null,2);});$('tree').append(b);}if(count>250)notice('匹配超过 250 个控件，请输入筛选条件。');}
$('filter').addEventListener('input',render);
action('inspect',async()=>{nodes=(await api('inspect')).nodes;render();notice('已读取 '+nodes.length+' 个可见控件，未切换会话或发送消息。');});
action('upload',async()=>{const f=$('media-file').files[0];if(!f)throw new Error('请选择文件。');if(f.size>8388608)throw new Error('单文件不能超过 8 MiB。');const q=new URLSearchParams({kind:$('media-kind').value,name:f.name});const r=await fetch('/console/api/upload?'+q,{method:'POST',headers:{Authorization:'Bearer '+auth,'X-Desktop-Console':'1','Content-Type':'application/octet-stream'},body:f,credentials:'omit'});if(!r.ok)throw new Error(await r.text());$('media-result').textContent=JSON.stringify(await r.json(),null,2);await status();notice('附件已暂存，没有发送 QQ。');});
