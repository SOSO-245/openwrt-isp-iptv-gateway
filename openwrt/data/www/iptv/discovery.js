(() => {
  'use strict';
  const askConfirm=message=>window.iptvConfirm?window.iptvConfirm(message):Promise.resolve(window.confirm(message));
  const pane = document.querySelector('.pane[data-tab="capture"]');
  if (!pane || document.getElementById('discoveryPanel')) return;
  const panel = document.createElement('div'); panel.id = 'discoveryPanel';
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => window.iptvIcon?.(name).outerHTML || '';
  const command = (action, label, symbol, primary = false, compact = false) => `<button type="button" class="btn ${primary ? '' : 'secondary'} ${compact ? 'console-icon-button' : ''}" data-discovery="${action}" title="${label}" aria-label="${label}">${icon(symbol)}${compact ? '' : label}</button>`;
  panel.innerHTML = `
    <div class="discovery-metrics">
      <div><span>正式节目</span><strong id="discoveryBaseline">--</strong><small id="discoveryObserved">等待读取</small></div>
      <div><span>待确认差异</span><strong id="discoveryCount">--</strong><small id="discoveryChanges">等待读取</small></div>
      <div><span>最近采样</span><strong id="discoveryLatest">--:--</strong><small id="discoveryDate">尚未采样</small></div>
    </div>
    <div class="discovery-actions">
      <div class="discovery-command-group">${command('sample','立即采样','play',true)}${command('scan','小范围扫描','scan-line')}${command('verify','验证待确认项','shield-check')}${command('retry','复查无响应','refresh-cw')}${command('audit','全量检查','list-video')}${command('pause','暂停检查','pause')}</div>
      <div class="discovery-command-group">${command('refresh','刷新结果','refresh-cw',false,true)}
        <details class="discovery-export"><summary title="导出结果" aria-label="导出结果">${icon('download')}</summary><div>${command('export','差异 JSON','download')}${command('playlist','候选 M3U','list-video')}</div></details>
      </div>
    </div>
    <div id="discoveryProgress" hidden><span id="discoveryProgressText"></span><progress id="discoveryProgressBar" max="8" value="0"></progress></div>
    <div id="discoveryNotice" role="status" aria-live="polite" hidden></div>
    <div class="discovery-result-bar">
      <div class="discovery-tabs" role="tablist" aria-label="发现结果">
        <button type="button" id="discoveryDiffTab" role="tab" aria-selected="true" aria-controls="discoveryDiffView" data-view="diff">差异结果<span id="discoveryTabCount">0</span></button>
        <button type="button" id="discoveryHistoryTab" role="tab" aria-selected="false" aria-controls="discoveryHistory" data-view="history">扫描记录</button>
      </div>
      <div class="discovery-filters" id="discoveryFilters">
        <label class="discovery-search">${icon('search')}<input id="discoverySearch" type="search" aria-label="搜索频道或地址" placeholder="搜索频道或地址"></label>
        <select id="discoveryFilter" aria-label="差异类型"><option value="all">全部类型</option><option value="failed">无响应 / 待复核</option><option value="unnamed">名称待确认</option><option value="changes">仅差异</option><option value="new_media">新增可播源</option><option value="possible_move">疑似换源</option><option value="unverified">待确认</option><option value="existing">已存在</option></select>
      </div>
    </div>
    <div id="discoveryDiffView" role="tabpanel" aria-labelledby="discoveryDiffTab">
      <div class="discovery-catalog"><span id="discoveryCatalogState">本地频道表未同步</span>${command('catalog','匹配频道表','book-open')}</div>
      <div class="discovery-table"><table><thead><tr><th>差异</th><th>频道</th><th>原地址</th><th>发现地址</th><th>验证状态</th><th>最近记录</th><th>操作</th></tr></thead><tbody id="discoveryRows"></tbody></table></div>
      <div class="discovery-pagination"><span id="discoveryPageCount">0 条结果</span><div>${command('prev','上一页','chevron-left',false,true)}<span id="discoveryPage">1 / 1</span>${command('next','下一页','chevron-right',false,true)}</div></div>
    </div>
    <div id="discoveryHistory" role="tabpanel" aria-labelledby="discoveryHistoryTab" hidden></div>
    <section class="discovery-settings-section">
      <div class="discovery-settings-main"><h3>采样设置</h3>
        <label class="discovery-toggle"><input type="checkbox" id="discoveryEnabled">每日采样</label>
        <input type="time" id="discoveryTime" value="09:30" aria-label="每日采样时间（北京时间）" title="北京时间">
        <label class="discovery-toggle"><input type="checkbox" id="discoveryWeekdays">仅工作日</label>
        <label class="discovery-toggle"><input type="checkbox" id="discoveryActive">每日附加扫描</label>
        ${command('settings','保存设置','check')}
      </div>
      <div class="discovery-settings-main discovery-schedule">
        <label class="discovery-toggle"><input type="checkbox" id="discoveryWeekly">每周全量检查</label>
        <select id="discoveryWeekday" aria-label="每周检查日期">${['周一','周二','周三','周四','周五','周六','周日'].map((day,i)=>`<option value="${i}" ${i===4?'selected':''}>${day}</option>`).join('')}</select>
        <input type="time" id="discoveryWeeklyTime" value="09:30" aria-label="每周全量检查时间（北京时间）" title="北京时间">
        <span id="discoveryAuditState" class="discovery-setting-status">尚未全量检查</span>
      </div>
      <div class="discovery-settings-main discovery-schedule">
        <label class="discovery-toggle"><input type="checkbox" id="discoveryAutoUpdate">自动更新节目单</label>
        <span id="discoveryUpdateState" class="discovery-setting-status">未启用</span>
        <span id="discoveryLinkState" class="discovery-setting-status"></span>
      </div>
      <div class="discovery-settings-main discovery-schedule" id="discoveryNextRuns"><span class="discovery-setting-status" id="discoveryNextSample"></span><span class="discovery-setting-status" id="discoveryNextAudit"></span></div>
      <div class="discovery-settings-main discovery-schedule" id="discoveryLastRuns" hidden><span class="discovery-setting-status" id="discoveryLastSample"></span><span class="discovery-setting-status" id="discoveryLastAudit"></span></div>
      <details class="discovery-scope"><summary>${icon('sliders-horizontal')}扫描范围<span id="discoveryScopeSummary">未设置</span>${icon('chevron-down')}</summary>
        <div class="discovery-settings-fields"><label>扫描网段<input id="discoveryRanges" placeholder="239.x.x.0/24" autocomplete="off" list="discoveryRangeOptions"></label><datalist id="discoveryRangeOptions"></datalist>
          <label>节目端口<input id="discoveryPorts" placeholder="8000" autocomplete="off"></label>
          <div class="discovery-available" id="discoveryScope"></div>
        </div>
      </details>
    </section>
    <dialog id="discoveryRetryDialog" aria-labelledby="discoveryRetryTitle">
      <h3 id="discoveryRetryTitle">复查无响应</h3>
      <p>复查最早检查的最多4个无响应或待复核地址，有播放时暂停。夜间无响应不能证明下线，建议白天复查。</p>
      <div class="discovery-name-buttons"><button type="button" class="btn secondary" id="discoveryRetryCancel">取消</button>${command('retry-confirm','开始复核','shield-check',true)}</div>
    </dialog>
    <dialog id="discoveryNameDialog" aria-labelledby="discoveryNameTitle">
      <form id="discoveryNameForm"><h3 id="discoveryNameTitle">确认频道名称</h3>
        <p id="discoveryNameEndpoint"></p><p id="discoveryNameEvidence"></p>
        <label>频道名称<input id="discoveryNameInput" maxlength="120" required autocomplete="off" list="discoveryNameOptions"></label>
        <datalist id="discoveryNameOptions"></datalist>
        <label class="discovery-name-reviewed"><input id="discoveryNameReviewed" type="checkbox" required>已核对播放画面或可靠频道表</label>
        <p>自动更新开启时，确认名称且至少两次验证通过的源会自动入库。若已满足验证条件，保存后即可进入自动合并。</p>
        <p id="discoveryNameError" role="alert" hidden></p>
        <div class="discovery-name-buttons"><button type="button" class="btn secondary" id="discoveryNameRevoke">撤回确认</button><button type="button" class="btn secondary" id="discoveryNameCancel">取消</button><button type="submit" class="btn">${icon('check')}确认名称</button></div>
      </form>
    </dialog>`;
  pane.prepend(panel);
  const automation=document.getElementById('automationDiscovery');
  if(automation)automation.append(panel.querySelector('.discovery-settings-section'));
  const el = id => document.getElementById(id);
  const when = (value, short = false) => value ? new Date(value * 1000).toLocaleString('zh-CN', short ? {hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}) : '--';
  const labels = {existing:'已存在',possible_move:'疑似换源',new_media:'新增可播源',unverified:'待确认',pending:'未验证',media:'已识别音视频',no_media:'未识别音视频',no_response:'本次无响应',inconclusive:'待复核',sample:'被动采样',scan:'小范围扫描',verify:'媒体验证',audit:'全量检查',idle:'空闲',running:'运行中',paused:'已暂停',complete:'完成',error:'失败',skipped:'已跳过'};
  Object.assign(labels,{identify:'深度识别',name:'名称确认',submitted:'已提交',unconfirmed:'未确认'});
  const nameLabels={playlist:'原节目单',stream:'节目流名称',manual:'人工已确认',catalog:'频道表候选 · 待确认',conflict:'名称存在歧义',unknown:'名称待识别'};
  let naming=null;
  let loaded=false, dirty=false, inflight=false, submitting=false, busy=false, unavailable=false, nextPoll=0, page=1, revision=0, current={rows:[],history:[]};
  const text = (id,value) => {el(id).textContent=value;};
  const notice = (message='',error=false) => {
    const active=document.querySelector('.pane.on')?.dataset.tab||'capture';
    for(const [id,view] of [['discoveryNotice','capture'],['automationNotice','automation']]){
      const target=el(id);if(!target)continue;
      const visible=!!message&&(error||active===view);
      target.textContent=visible?message:'';target.hidden=!visible;target.classList.toggle('is-error',visible&&error);
    }
  };
  document.querySelectorAll('.discovery-settings-section input,.discovery-settings-section select').forEach(input=>input.addEventListener('input',()=>{dirty=true;}));
  async function api(action, fields={}) {
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),12000);
    try {
      const response=await fetch('/cgi-bin/iptv-discovery',{method:'POST',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-IPTV-Discovery':'1'},body:JSON.stringify({action,user:sessionStorage.iptvUser||'',password:sessionStorage.iptvPass||'',...fields})});
      const data=await response.json();
      if(!response.ok||data.error)throw new Error(data.error||'请求失败');
      return data;
    } finally {clearTimeout(timer);}
  }
  function mark(code) {
    const positive=['media','new_media','complete'].includes(code), warning=['possible_move','unverified','pending','paused','inconclusive'].includes(code);
    return `<span class="discovery-state ${positive?'positive':warning?'warning':''}">${icon(positive?'check':warning?'clock':'activity')}${safe(labels[code]||code)}</span>`;
  }
  function renderRows() {
    const query=el('discoverySearch').value.trim().toLowerCase(), filter=el('discoveryFilter').value;
    const rows=(current.rows||[]).filter(r=>(filter==='all'||(filter==='failed'?needsRecheck(r):filter==='unnamed'?['unknown','catalog','conflict'].includes(r.name_source):(filter==='changes'?r.change!=='existing':r.change===filter)))&&(!query||[r.name,r.endpoint,...r.previous||[]].join(' ').toLowerCase().includes(query)));
    const pages=Math.max(1,Math.ceil(rows.length/20));page=Math.min(page,pages);
    const selected=rows.slice((page-1)*20,page*20);
    el('discoveryRows').innerHTML=selected.length?selected.map(rowMarkup).join(''):`<tr><td colspan="7" class="discovery-empty">${icon('radar')}<strong>${query||filter!=='all'?'没有匹配结果':loaded?'暂未发现组播地址':'正在连接采样服务'}</strong><span>${query||filter!=='all'?'':'最近采样未观察到流量，不代表频道失效'}</span></td></tr>`;
    text('discoveryPageCount',`${rows.length} 条结果`);text('discoveryPage',`${page} / ${pages}`);
    document.querySelector('[data-discovery=prev]').disabled=page<=1;document.querySelector('[data-discovery=next]').disabled=page>=pages;
  }
  function needsRecheck(row){return ['no_response','no_media','inconclusive'].includes(row.last_check||row.validation);}
  function rowMarkup(r){
    const button=(action,label,symbol,disabled=false)=>`<button type="button" class="btn secondary console-icon-button" data-row-action="${action}" data-endpoint="${safe(r.endpoint)}" title="${label}" aria-label="${label}" ${busy||submitting||unavailable||disabled?'disabled':''}>${icon(symbol)}</button>`;
    const canName=r.change!=='existing'&&r.validation==='media'&&!r.last_check&&(r.programs||[]).length<=1;
    const validation=mark(r.last_check||r.validation)+(r.last_check&&r.validation==='media'?'<small>此前识别过音视频</small>':'')+(r.change!=='existing'&&r.validation==='media'?`<small>有效复核 ${safe(r.verified_count||0)} / 2</small>`:'');
    return `<tr><td>${mark(r.change)}</td><td><strong>${safe(r.name||'名称待识别')}</strong><small class="discovery-name-source">${safe(nameLabels[r.name_source]||'名称待识别')}${r.confirmation_stale?' · 原确认已失效':''}</small>${r.streams?.length?'<small>'+safe(r.streams.map(s=>[s.codec,s.width&&s.height?`${s.width}×${s.height}`:''].filter(Boolean).join(' ')).join(' · '))+'</small>':''}</td><td class="discovery-address">${safe(r.previous?.join('\n')||(r.change==='existing'?r.endpoint:'--'))}</td><td class="discovery-address">${safe(r.endpoint)}</td><td>${validation}</td><td class="discovery-time">${safe(when(r.checked_at||r.last_seen))}</td><td><div class="discovery-row-actions">${button('identify','深度识别此地址','scan-line')}${r.change!=='existing'?button('name','确认频道名称','pencil',!canName):''}</div></td></tr>`;
  }
  function renderHistory() {
    el('discoveryHistory').innerHTML=(current.history||[]).map(h=>{
      const changes=(h.changes||[]).map(c=>`<li><code>${safe(c.endpoint)}</code><span>${safe(c.before?(labels[c.before.change]||c.before.change)+' / '+(c.before.name||'名称待识别'):'未记录')} → ${safe(labels[c.after.change]||c.after.change)} / ${safe(c.after.name||'名称待识别')}</span></li>`).join('');
      const results=(h.results||[]).map(r=>`<li><code>${safe(r.endpoint)}</code>${mark(r.validation)}</li>`).join('');
      return `<details class="discovery-record"><summary>${icon('chevron-right')}<span>${safe(labels[h.kind]||h.kind)}</span><time>${safe(when(h.at))}</time><span class="discovery-record-count">${h.changes?.length||0} 项变化</span>${mark(h.state||'complete')}</summary><div>${h.message?'<p>'+safe(h.message)+'</p>':''}${h.kind==='sample'?'<p>观察到 '+safe(h.observed||0)+' 个组播地址</p>':''}${changes?'<ul>'+changes+'</ul>':''}${results?'<ul>'+results+'</ul>':''}${!changes&&!results?'<p>没有新增或变更证据</p>':''}</div></details>`;
    }).join('')||'<div class="discovery-empty">'+icon('clock')+'<strong>暂无扫描记录</strong></div>';
  }
  function renderSchedule(data) {
    text('discoveryNextSample',data.next_runs?.sample?`下次短采样：${when(data.next_runs.sample)} · 北京时间`:'');
    text('discoveryNextAudit',data.next_runs?.audit?`下次全量：${when(data.next_runs.audit)} · 北京时间`:'');
    el('discoveryNextRuns').hidden=!data.next_runs?.sample&&!data.next_runs?.audit;
    const runs=data.scheduled_runs||{};
    for(const [kind,id,label] of [['sample','discoveryLastSample','上次短采样计划'],['audit','discoveryLastAudit','上次全量计划']]){
      const run=runs[kind];
      text(id,run?`${label}：${when(run.at)} · ${labels[run.state]||'未确认'}${run.message?' · '+run.message:''}`:'');
    }
    el('discoveryLastRuns').hidden=!runs.sample&&!runs.audit;
    if(!loaded||!dirty){
      el('discoveryEnabled').checked=!!data.collector?.enabled;el('discoveryTime').value=data.collector?.daily_time||'09:30';
      el('discoveryWeekdays').checked=!!data.collector?.weekdays_only;
      el('discoveryWeekly').checked=!!data.collector?.weekly_enabled;el('discoveryWeekday').value=String(data.collector?.weekly_day??4);el('discoveryWeeklyTime').value=data.collector?.weekly_time||'09:30';el('discoveryAutoUpdate').checked=!!data.collector?.auto_update;
    }
  }
  function setUnavailable(message) {
    unavailable=true;notice(message,true);
    document.dispatchEvent(new CustomEvent('iptv:discovery',{detail:{service_error:message}}));
    text('discoveryLinkState','NAS未连接 · 当前结果未更新');
    text('discoveryUpdateState','无法读取自动更新状态');
    el('discoveryProgress').hidden=true;
    document.querySelectorAll('[data-discovery]').forEach(button=>{
      if(!['refresh','prev','next'].includes(button.dataset.discovery))button.disabled=true;
    });
    document.querySelectorAll('.discovery-settings-section input,.discovery-settings-section select').forEach(input=>{input.disabled=true;});
    el('discoveryNameDialog').querySelectorAll('button:not(#discoveryNameCancel),input').forEach(input=>{input.disabled=true;});
    renderRows();
  }
  function renderUpdate(update={},collector={}) {
    const applied=update.last_applied;
    const summary=applied?`${when(applied.at)} · 已更新 ${applied.changes.length} 项`:'';
    let value=update.state==='error'?update.message:summary|| (collector.auto_update?'等待重复验证通过的变更':'未启用');
    if(update.sync_pending)value=`${summary||'节目单已更新'} · NAS待同步${collector.auto_update?'（仅补同步，不重复更新）':'（自动更新已停用）'}`;
    text('discoveryUpdateState',value);
    el('discoveryUpdateState').title=update.sync_message||'';
  }
  function render(data) {
    document.dispatchEvent(new CustomEvent('iptv:discovery',{detail:data}));
    renderSchedule(data);
    if(data.service_error){setUnavailable(data.service_error);if(data.update?.state||data.update?.last_applied)renderUpdate(data.update,data.collector);return;}
    if(unavailable){
      document.querySelectorAll('[data-discovery],.discovery-settings-section input,.discovery-settings-section select,#discoveryNameDialog button,#discoveryNameDialog input').forEach(input=>{input.disabled=false;});
    }
    unavailable=false;
    current=data;const rows=data.rows||[],job=data.job||{},capture=data.capture||{};
    busy=job.state==='running'||capture.state==='sampling';
    text('discoveryBaseline',data.baseline_count||0);text('discoveryObserved',`已观察 ${rows.length} 个组播地址`);
    const changes=rows.filter(r=>r.change!=='existing');text('discoveryCount',changes.length);text('discoveryTabCount',rows.length);
    text('discoveryChanges',`新增 ${changes.filter(r=>r.change==='new_media').length} · 疑似换源 ${changes.filter(r=>r.change==='possible_move').length}`);
    const lastSample=(data.history||[]).find(h=>h.kind==='sample');
    text('discoveryLatest',lastSample?when(lastSample.at,true):'--:--');text('discoveryDate',lastSample?when(lastSample.at)+' · 北京时间':'尚未完成采样');
    el('discoveryProgress').hidden=!busy;
    text('discoveryProgressText',capture.state==='sampling'?'正在采样 IPTV 入口':`${labels[job.kind]||'扫描'} ${job.done||0} / ${job.total||0}`);
    if(capture.state==='sampling')el('discoveryProgressBar').removeAttribute('value');else {el('discoveryProgressBar').max=job.total||1;el('discoveryProgressBar').value=job.done||0;}
    if(data.storage_error)notice(data.storage_error,true);else if(capture.state==='error')notice(capture.error,true);else if(job.state==='error'||job.state==='paused')notice(job.message,true);else if(el('discoveryNotice').classList.contains('is-error'))notice();
    document.querySelectorAll('[data-discovery=scan],[data-discovery=verify],[data-discovery=retry],[data-discovery=sample],[data-discovery=audit],[data-discovery=settings]').forEach(b=>{b.disabled=busy||submitting;});
    const failedCount=rows.filter(needsRecheck).length, retryButton=document.querySelector('[data-discovery=retry]');
    retryButton.disabled=busy||submitting||!failedCount;retryButton.title=`复查无响应：${failedCount} 项待复核，每次最多4项`;
    document.querySelector('[data-discovery=pause]').hidden=job.state!=='running';
    const audit=data.audit||{}, update=data.update||{};
    text('discoveryAuditState',audit.total?`${labels[audit.state]||audit.state} · ${audit.done||0} / ${audit.total}`:'尚未全量检查');
    const auditButton=document.querySelector('[data-discovery=audit]');
    const resume=['paused','error'].includes(audit.state)&&audit.done<audit.total;
    auditButton.innerHTML=icon('list-video')+(resume?'继续全量检查':'全量检查');auditButton.title=auditButton.ariaLabel=resume?'继续全量检查':'全量检查';
    renderUpdate(update,data.collector);
    text('discoveryLinkState',data.link?.message||'等待入口检查');
    text('discoveryCatalogState',data.catalog?.count?`本地频道表 ${data.catalog.count} 条 · 缓存 ${when(data.catalog.at)}`:'没有本地频道表缓存');
    if(!loaded||!dirty){
      el('discoveryActive').checked=!!data.settings?.active_enabled;el('discoveryRanges').value=(data.settings?.ranges||[]).join(', ');el('discoveryPorts').value=(data.settings?.ports||[]).join(', ');
    }
    loaded=true;
    text('discoveryScopeSummary',(data.settings?.ranges||[]).join(' · ')||'未设置');
    text('discoveryScope',`现有节目网段：${(data.allowed_ranges||[]).join('、')||'待采样'}；端口：${(data.allowed_ports||[]).join('、')||'--'}`);
    el('discoveryRangeOptions').innerHTML=(data.allowed_ranges||[]).map(r=>`<option value="${safe(r)}"></option>`).join('');
    renderRows();renderHistory();
  }
  async function refresh() {
    if(inflight||sessionStorage.iptvAuthed!=='1')return;
    inflight=true;
    const snapshot=revision;
    try{const data=await api('status');if(snapshot===revision&&!submitting)render(data);}catch(error){if(snapshot===revision&&!submitting)setUnavailable(error.name==='AbortError'?'采样服务响应超时；当前结果未更新':error.message);}
    finally{inflight=false;nextPoll=Date.now()+(busy?3000:30000);}
  }
  function download(name,body,type){const url=URL.createObjectURL(new Blob([body],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function selectView(view){
    const history=view==='history';el('discoveryDiffView').hidden=history;el('discoveryHistory').hidden=!history;el('discoveryFilters').hidden=history;
    document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.view===(history?'history':'diff'))));
  }
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>selectView(button.dataset.view)));
  document.addEventListener('iptv:discovery-view',event=>selectView(event.detail));
  for(const id of ['discoverySearch','discoveryFilter'])el(id).addEventListener('input',()=>{page=1;renderRows();});
  el('discoveryNameCancel').addEventListener('click',()=>el('discoveryNameDialog').close());
  el('discoveryRetryCancel').addEventListener('click',()=>el('discoveryRetryDialog').close());
  async function saveName(revoke=false){
    if(submitting||unavailable||!naming)return;
    submitting=true;revision++;
    el('discoveryNameForm').querySelectorAll('button').forEach(b=>{b.disabled=true;});
    el('discoveryNameError').hidden=true;
    try{
      const result=await api('name',{endpoint:naming.endpoint,fingerprint:naming.fingerprint,name:el('discoveryNameInput').value,reviewed:el('discoveryNameReviewed').checked,revoke});
      el('discoveryNameDialog').close();notice(result.message);
      submitting=false;await refresh();
    }catch(error){text('discoveryNameError',error.name==='AbortError'?'请求超时，请刷新核对保存结果':error.message);el('discoveryNameError').hidden=false;}
    finally{submitting=false;el('discoveryNameForm').querySelectorAll('button').forEach(b=>{b.disabled=unavailable&&b.id!=='discoveryNameCancel';});}
  }
  el('discoveryNameForm').addEventListener('submit',event=>{event.preventDefault();saveName();});
  el('discoveryNameRevoke').addEventListener('click',()=>saveName(true));
  panel.addEventListener('click',async event=>{
    const button=event.target.closest('[data-row-action]');if(!button||button.disabled||submitting||busy||unavailable)return;
    const row=current.rows.find(r=>r.endpoint===button.dataset.endpoint);if(!row)return;
    if(button.dataset.rowAction==='name'){
      naming={...row};el('discoveryNameInput').value=row.name||'';el('discoveryNameReviewed').checked=false;
      text('discoveryNameEndpoint',row.endpoint);
      text('discoveryNameEvidence',`${nameLabels[row.name_source]||'名称待识别'}${row.name_candidates?.length?' · 缓存候选：'+row.name_candidates.join(' / ')+'（'+when(row.catalog_at)+'）':''}`);
      el('discoveryNameOptions').innerHTML=(row.name_candidates||[]).map(n=>`<option value="${safe(n)}"></option>`).join('');
      el('discoveryNameRevoke').hidden=!row.confirmation;el('discoveryNameError').hidden=true;el('discoveryNameDialog').showModal();return;
    }
    if(!await askConfirm(`深度识别 ${row.endpoint}：最多读取约10秒或8MiB，分析另限5秒。有播放时暂停；仍可能无法读到名称。继续吗？`))return;
    submitting=true;revision++;button.disabled=true;
    try{const result=await api('identify',{endpoints:[row.endpoint]});notice(result.message);submitting=false;await refresh();nextPoll=Date.now()+2000;}
    catch(error){notice(error.name==='AbortError'?'请求超时，请刷新核对任务状态':error.message,true);}
    finally{submitting=false;renderRows();}
  });
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-discovery]');if(!button||button.disabled)return;const action=button.dataset.discovery==='retry-confirm'?'retry':button.dataset.discovery;
    if(action==='prev'||action==='next'){page+=action==='prev'?-1:1;renderRows();return;}
    if(action==='refresh'){notice();await refresh();return;}
    if(unavailable){notice('NAS扫描服务暂不可用，请刷新确认恢复后再操作',true);return;}
    if(submitting)return;
    if(!loaded){notice('请等待当前设置读取完成',true);return;}
    if(action==='scan'&&!await askConfirm('按已保存范围逐个验证最多8个地址。有播放时暂停；开启自动更新后，重复验证通过的新源会自动入库。开始吗？'))return;
    if(action==='verify'&&!await askConfirm('验证最多4个已记录地址，有播放时停止。继续吗？'))return;
    if(action==='retry'){
      if(busy)return;
      if(button.dataset.discovery==='retry'){el('discoveryRetryDialog').showModal();return;}
      el('discoveryRetryDialog').close();
    }
    if(action==='audit'&&!await askConfirm('逐个检查正式节目单、已发现地址及已设扫描范围，有播放或入口异常时暂停并保留进度。不会因单次失败删除频道。开始或继续吗？'))return;
    let fields=action==='retry'?{retry_failed:true}:{};
    if(action==='settings'){
      fields.settings={enabled:el('discoveryEnabled').checked,daily_time:el('discoveryTime').value,weekdays_only:el('discoveryWeekdays').checked,weekly_enabled:el('discoveryWeekly').checked,weekly_day:Number(el('discoveryWeekday').value),weekly_time:el('discoveryWeeklyTime').value,auto_update:el('discoveryAutoUpdate').checked,active_enabled:el('discoveryActive').checked,ranges:el('discoveryRanges').value.split(/[,，\s]+/).filter(Boolean),ports:el('discoveryPorts').value.split(/[,，\s]+/).filter(Boolean).map(Number)};
      if(fields.settings.active_enabled&&!current.settings?.active_enabled&&!await askConfirm('每日采样后附加最多8个地址的主动验证。有播放时停止。启用吗？'))return;
      if(fields.settings.auto_update&&!current.collector?.auto_update&&!await askConfirm('启用后会备份并自动更新基础/台标节目单，保留排序。新源至少两次验证；疑似换源还须旧源持续失效。不自动删频道。启用吗？'))return;
    }
    submitting=true;revision++;button.disabled=true;notice('正在提交…');
    try{
      const result=await api(action==='playlist'?'export':action==='retry'?'verify':action,fields);
      if(action==='export'||action==='playlist'){
        download(action==='playlist'?'iptv-candidates.m3u':'iptv-discovery-diff.json',action==='playlist'?result.playlist:JSON.stringify(result,null,2),action==='playlist'?'audio/x-mpegurl':'application/json');
        document.querySelector('.discovery-export').open=false;notice('已导出；正式节目单未修改');
      }else{notice(result.message||'已提交');if(action==='settings')dirty=false;}
      submitting=false;await refresh();nextPoll=Date.now()+2000;
    }catch(error){notice(error.name==='AbortError'?'请求超时，请刷新查看任务状态':error.message,true);}
    finally{submitting=false;button.disabled=unavailable||(busy&&['sample','scan','verify','retry','audit','settings'].includes(action))||(action==='retry'&&!(current.rows||[]).some(needsRecheck));}
  });
  document.addEventListener('iptv:refresh',refresh);
  document.addEventListener('iptv:view',event=>{if(['overview','capture','automation'].includes(event.detail)&&Date.now()>=nextPoll)refresh();});
  setInterval(()=>{if(!document.hidden&&(pane.classList.contains('on')||document.querySelector('.pane.on[data-tab="automation"],.pane.on[data-tab="overview"]'))&&Date.now()>=nextPoll)refresh();},2000);
  renderRows();refresh();
})();
