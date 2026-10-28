/* Independent presentation for the optional read-only observer. Existing calibration handlers stay unchanged. */
(() => {
  const el=id=>document.getElementById(id);
  const request=(path,body)=>fetchJson('/api/servo-health/'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-Calibration-CSRF':CSRF},body:JSON.stringify(body)});
  const labels={observing:'未发现已配置规则异常',insufficient:'数据或参考不足',stale:'数据过期 / 未连接',warning:'需要注意',alarm:'需要人工核查'};
  let selected=20,filled=false,busy=false,last=null,seriesCache={id:null,samples:[]},maintenanceVersion=undefined;
  const rowNodes=new Map();
  const format=(n,d=1)=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(d):'未知';
  function text(id,value){el(id).textContent=value;}
  function draw(id,samples,keys,colors,unit){
    const canvas=el(id),ctx=canvas.getContext('2d'),w=720,h=210;
    canvas.width=w;canvas.height=h;ctx.fillStyle='#090b0c';ctx.fillRect(0,0,w,h);
    const values=samples.flatMap(s=>keys.map(k=>s[k])).filter(Number.isFinite);
    if(!values.length){ctx.fillStyle='#aaa';ctx.font='14px sans-serif';ctx.fillText('没有可用测量数据',24,104);return;}
    let lo=Math.min(0,...values),hi=Math.max(...values);if(hi-lo<.01)hi=lo+1;
    const start=samples[0].t,end=samples[samples.length-1].t;
    ctx.font='12px monospace';ctx.fillStyle='#aaa';ctx.fillText(hi.toFixed(2)+' '+unit,8,18);ctx.fillText(lo.toFixed(2),8,h-8);
    ctx.strokeStyle='#303332';ctx.lineWidth=1;
    for(let i=1;i<4;i++){ctx.beginPath();ctx.moveTo(65,i*h/4);ctx.lineTo(w-10,i*h/4);ctx.stroke();}
    keys.forEach((key,j)=>{
      ctx.strokeStyle=colors[j];ctx.lineWidth=2;ctx.beginPath();let connected=false,previous=null;
      samples.forEach(s=>{
        const value=s[key];if(!Number.isFinite(value)){connected=false;return;}
        if(previous!==null&&s.t-previous>.5)connected=false;previous=s.t;
        const x=65+(s.t-start)/Math.max(.001,end-start)*(w-80),y=25+(hi-value)/(hi-lo)*(h-50);
        if(connected)ctx.lineTo(x,y);else ctx.moveTo(x,y);connected=true;
      });ctx.stroke();
    });
    ctx.fillStyle='#999';ctx.fillText('最近 '+Math.max(0,end-start).toFixed(1)+' 秒 · 间断值不补零',75,h-7);
  }
  async function history(){
    if(!last?.enabled)return;
    const joint=selected;const cursor=seriesCache.id===joint&&seriesCache.cursor?`&after_t_ns=${seriesCache.cursor}&epoch=${encodeURIComponent(seriesCache.epoch)}`:'';
    const data=await request('history?id='+joint+cursor);if(selected!==joint)return;
    const samples=data.reset||seriesCache.id!==joint?data.samples:seriesCache.samples.concat(data.samples);
    if(samples.length){const end=samples[samples.length-1].t;while(samples.length&&end-samples[0].t>300)samples.shift();}
    seriesCache={id:joint,samples,cursor:data.cursor,epoch:data.epoch};
    text('servoSelected',data.id+' / '+data.name);
    draw('servoCurrentPlot',samples,['currents_ma'],['#ff702a'],'mA');
    draw('servoPositionPlot',samples.map(s=>({...s,actual:s.joints==null?null:s.joints*180/Math.PI,target:s.targets==null?null:s.targets*180/Math.PI})),['actual','target'],['#ece9df','#ff702a'],'°');
    draw('servoErrorPlot',samples.map(s=>({...s,error:s.joints==null||s.targets==null?null:Math.abs(s.targets-s.joints)*180/Math.PI})),['error'],['#ffad79'],'°');
    draw('servoVelocityPlot',samples.map(s=>({...s,speed:s.velocities==null?null:s.velocities*180/Math.PI})),['speed'],['#86c8ad'],'°/s');
  }
  function table(rows){
    const body=el('servoRows');
    for(const row of rows){
      let tr=rowNodes.get(row.id);if(!tr){tr=document.createElement('tr');const td=document.createElement('td'),button=document.createElement('button');button.className='btn ghost';button.type='button';button.textContent=row.id+' / '+row.name;button.onclick=()=>{selected=row.id;table(last.rows);history().catch(showError);};td.append(button);tr.append(td);for(let i=0;i<7;i++)tr.append(document.createElement('td'));rowNodes.set(row.id,tr);body.append(tr)}tr.className=row.id===selected?'selected':'';let cellIndex=1;
      for(const value of [format(row.current_ma),format(row.threshold_ma),format(row.position_deg),format(row.target_deg),format(row.error_deg),format(row.velocity_deg_s),labels[row.state]+(row.stall_paused?' · 卡滞判断暂停':'')]){
        tr.children[cellIndex++].textContent=value;
      }
    }
  }
  function showError(error){text('servoMessage','插件：'+error.message);}
  function list(id,values){
    const box=el(id);box.replaceChildren();
    if(!values.length){box.textContent='暂无记录';return;}
    for(const item of values){const line=document.createElement('pre');line.className='code';line.textContent=JSON.stringify(item,null,2);box.append(line);}
  }
  function maintenance(report){
    const box=el('servoMaintenance');box.replaceChildren();
    if(!report){box.textContent='未导入维护报告。实时接口缺少的字段不补零，不推算。';return;}
    const title=document.createElement('p');title.textContent='历史维护值 · '+report.robot_label+' · '+report.collected_at+' · 设备身份为报告自述，尚未核验';box.append(title);
    for(const row of report.servos){
      const d=row.decoded,line=document.createElement('div');line.className='servo-maint-row';
      line.textContent=row.id+' / '+row.name+' · '+(d.supported?
        `型号 ${d.model} / 固件 ${d.firmware??'未知'} / 模式 ${d.operating_mode??'未知'} / 温度 ${format(d.temp_c)}°C（限制 ${format(d.temp_limit_c)}°C） / 电压 ${format(d.voltage_v)}V（保护范围 ${format(d.min_voltage_v)}–${format(d.max_voltage_v)}V；XL330规格 3.7–6.0V） / 电流限制 ${format(d.current_limit_ma,0)}mA / PWM限制 ${d.pwm_limit??'未知'} / 历史反馈电流 ${format(d.current_ma)}mA / 历史扭矩开关 ${d.torque_enable??'未知'} / Shutdown ${d.shutdown??'未知'} / 故障 ${d.hardware_error??'未知'} ${(d.errors||[]).join(', ')} / 电压故障触发关闭扭矩 ${d.input_voltage_shutdown_enabled===null?'未知':d.input_voltage_shutdown_enabled?'启用':'未启用'}`:
        '型号不支持，保留原始值，不应用 XL330 解码')+(row.read_errors.length?' · 读取错误：'+row.read_errors.join('; '):'');box.append(line);
      const details=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='原始寄存器 / 采集时间';raw.className='code';raw.textContent=JSON.stringify({sampled_at:row.sampled_at,raw:row.raw},null,2);details.append(summary,raw);box.append(details);
    }
  }
  async function refresh(){
    if(busy||!el('tab-servo').classList.contains('active'))return;
    busy=true;
    try{
      const s=await request('status?compact=1');last=s;
      el('servoContent').hidden=!s.enabled;el('servoDisabled').hidden=s.enabled;
      if(!s.enabled){text('servoDisabled',s.message);return;}
      text('servoConnection',s.live_transport?(s.stale?'传感器数据过期':'只读遥测已连接'):'离线 / 回放，无真实连接');
      text('servoOverall',labels[s.overall]);
      text('servoCoverage',Object.values(s.coverage).filter(Boolean).length+'/4 项 · '+(s.baseline_valid?'当前 policy 已有可比参考':'当前 policy 无可比参考'));
      text('servoAge',format(s.sensor_age_ms,0)+' ms · '+(s.policy||'未知 policy')+' · 已收 '+s.frames+' 帧');
      text('servoMessage',`告警 ${s.alerts.length} · 重复/乱序 ${s.repeated_or_reordered_frames} · 丢弃样本 ${s.dropped_frames} / 日志 ${s.dropped_logs}。${s.errors.join('；')} ${s.compatibility?.reason||''}`);
      const h=s.health||{},m=h.motors||{},b=h.battery||{};
      text('servoSlow',`最近报告：最高 ${format(m.max_c)}°C（${m.hottest||'未知关节'}），平均 ${format(m.mean_c)}°C，汇总平滑电压 ${format(b.volts,2)}V（XL330厂商规格3.7–6.0V；监测参考${s.rules.voltage_min_v}–${s.rules.voltage_max_v}V）。RPC 距今 ${format(s.health_rpc_age_ms,0)} ms；原始温度/电压采样新鲜度不可验证。`);
      text('servoBaselineState',s.baseline_capture?`正在采集 ${s.baseline_capture.policy} · ${format(s.baseline_capture.elapsed_s)} / 60 s · ${s.baseline_capture.samples} 个样本`:s.baseline_result||'人工确认当前工况正常后，才能采集参考。没有参考时不进行基于基线的电流告警。');
      el('servoBaselineStart').disabled=s.stale||!!s.baseline_capture||s.live_transport&&!s.identity_verified;el('servoBaselineCancel').disabled=!s.baseline_capture;
      if(!filled){
        const fields=el('servoRuleFields');fields.replaceChildren();
        const names={current_multiplier:'电流 P95 倍数',current_margin_ma:'电流增量 (mA)',duration_s:'触发持续 (s)',error_deg:'卡滞误差 (°)',speed_deg_s:'卡滞速度上限 (°/s)',target_span_deg:'1秒目标变化上限 (°)',temp_warn_c:'温度提醒 (°C)',temp_alarm_c:'温度高等级 (°C)',voltage_min_v:'电压参考下限 (V)',voltage_max_v:'电压参考上限 (V)'};
        for(const [key,value] of Object.entries(s.rules)){
          const wrap=document.createElement('div');wrap.className='control';const label=document.createElement('label');label.textContent=names[key];label.htmlFor='servo-rule-'+key;
          const input=document.createElement('input');input.type='number';input.step='any';input.id='servo-rule-'+key;input.dataset.rule=key;input.value=value;wrap.append(label,input);fields.append(wrap);
        }filled=true;
      }
      table(s.rows);list('servoAlerts',s.alerts);list('servoEvents',s.events.slice(-15).reverse());if(s.maintenance_version!==maintenanceVersion){const report=await request('maintenance');maintenance(report.report);maintenanceVersion=s.maintenance_version;}await history();
    }catch(e){showError(e);}finally{busy=false;}
  }
  el('servoRefresh').onclick=refresh;
  el('servoRulesSave').onclick=async()=>{try{
    const values={};el('servoRuleFields').querySelectorAll('input').forEach(input=>values[input.dataset.rule]=Number(input.value));
    await request('config',{rules:values});notify('监测规则已保存；未修改机器人配置。');await refresh();
  }catch(e){showError(e);}};
  el('servoBaselineStart').onclick=async()=>{try{
    await request('baseline',{action:'start',confirm_normal:el('servoNormalConfirmed').checked});await refresh();
  }catch(e){showError(e);}};
  el('servoBaselineCancel').onclick=async()=>{try{await request('baseline',{action:'cancel'});await refresh();}catch(e){showError(e);}};
  el('servoImport').onclick=async()=>{try{
    const file=el('servoReportFile').files[0];if(!file)throw Error('请选择维护 JSON 报告');if(file.size>1_000_000)throw Error('报告过大');
    const report=JSON.parse(await file.text());await request('maintenance',{report});await refresh();
  }catch(e){showError(e);}};
  document.querySelector('[data-tab="servo"]').addEventListener('click',refresh);
  setInterval(refresh,1000);
})();
