'use strict';
const root='/api/studio/v1/servo-workbench/reports', $=id=>document.getElementById(id);
const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
async function api(path,settings={}){const r=await fetch(path,settings),v=await r.json();if(!r.ok)throw new Error(v.error||'读取失败');return v;}
function fail(e){$('notice').textContent=e.message+'；需要先在Studio完成配对。';}
async function openReport(id){try{const r=await api(root+'?id='+id),s=r.session,box=$('detail');box.replaceChildren(node('h2',s.kind+' / '+s.layout+' / '+s.version),node('p','历史报告 · '+(r.simulated?'模拟数据':'来源声明未核实')+' · 不代表实机验收'));for(const x of s.slots)box.append(node('p','ID '+x.id+' · '+x.name+' · '+x.state+' · '+x.persistence));const details=document.createElement('details');details.append(node('summary','原始证据与任务步骤'),node('pre',JSON.stringify(r,null,2)));box.append(details);}catch(e){fail(e);}}
async function reload(){try{const rows=await api(root);$('list').replaceChildren();for(const row of rows){const b=node('button',row.id.slice(0,16));b.onclick=()=>openReport(row.id);$('list').append(b);}}catch(e){fail(e);}}
$('reload').onclick=reload;
$('import').onclick=async()=>{try{const f=$('file').files[0];if(!f||f.size>2*1024*1024)throw new Error('请选择≤2MiB的报告');const value=JSON.parse(await f.text()),auth=await api('/api/studio/v1/status');const out=await api(root,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-CSRF':auth.csrf},body:JSON.stringify(value)});$('notice').textContent='已导入历史报告，未改变实时配置。';await reload();await openReport(out.id);}catch(e){fail(e);}};
reload();
