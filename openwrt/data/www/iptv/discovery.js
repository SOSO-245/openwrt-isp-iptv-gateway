(() => {
  'use strict';
  const askConfirm=message=>window.iptvConfirm?window.iptvConfirm(message):Promise.resolve(window.confirm(message));
  const pane = document.querySelector('.pane[data-tab="capture"]');
  if (!pane || document.getElementById('discoveryPanel')) return;
  const model = window.IPTVDiscoveryModel;
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
      <div class="discovery-command-group discovery-scan-controls"><select id="discoveryMode" aria-label="扫描方式"><option value="audit-new">重新全量扫描</option><option value="audit-resume">继续上次全量</option><option value="batch-resume">继续上次批量处理</option><option value="sample">短时被动采样</option><option value="scan">小范围扫描 · 最多8项</option><option value="verify">验证待确认项 · 最多4项</option><option value="retry">复查无响应 · 最多4项</option></select>${command('run','开始检查','play',true)}${command('pause','暂停检查','pause')}</div>
      <div class="discovery-command-group">${command('ai','AI 分析','activity')}${command('refresh','刷新结果','refresh-cw',false,true)}
        <details class="discovery-export"><summary title="导出结果" aria-label="导出结果">${icon('download')}</summary><div>${command('export','差异 JSON','download')}${command('playlist','候选 M3U','list-video')}</div></details>
      </div>
    </div>
    <div class="discovery-task-status"><span id="discoveryTaskState">等待读取扫描状态</span><span id="discoveryTaskScope"></span></div>
    <div id="discoveryProgress" hidden><span id="discoveryProgressText"></span><progress id="discoveryProgressBar" max="8" value="0"></progress></div>
    <div id="discoveryNotice" role="status" aria-live="polite" hidden></div>
    <section id="discoveryApplied" class="discovery-applied" hidden><h3>最近节目单变更</h3><ul id="discoveryAppliedRows"></ul></section>
    <div class="discovery-result-bar">
      <div class="discovery-tabs" role="tablist" aria-label="发现结果">
        <button type="button" id="discoveryDiffTab" role="tab" aria-selected="true" aria-controls="discoveryDiffView" data-view="diff">差异结果<span id="discoveryTabCount">0</span></button>
        <button type="button" id="discoveryHistoryTab" role="tab" aria-selected="false" aria-controls="discoveryHistory" data-view="history">扫描记录</button>
      </div>
      <div class="discovery-filters" id="discoveryFilters">
        <label class="discovery-search">${icon('search')}<input id="discoverySearch" type="search" aria-label="搜索频道或地址" placeholder="搜索频道或地址"></label>
        <select id="discoveryFilter" aria-label="差异类型"><option value="active">有效与待复核</option><option value="all">全部结果</option><option value="media">本次有效源</option><option value="existing_failed">正式节目异常</option><option value="failed">全部待复核</option><option value="silent">无响应候选</option><option value="unnamed">名称待确认</option><option value="changes">新增与换源</option><option value="new_media">新增可播源</option><option value="possible_move">疑似换源</option><option value="existing">已存在</option></select>
      </div>
    </div>
    <div id="discoveryDiffView" role="tabpanel" aria-labelledby="discoveryDiffTab">
      <div class="discovery-stage-tabs" role="group" aria-label="处理阶段">${[['needs_name','待识别'],['needs_verify','待复核'],['await_update','待更新核验'],['existing_failed','正式节目异常']].map(([stage,label])=>`<button type="button" data-stage-filter="${stage}" aria-pressed="false">${label}<b>--</b></button>`).join('')}</div>
      <div class="discovery-catalog"><span id="discoveryCatalogState">本地频道表未同步</span>${command('catalog','匹配频道表','book-open')}</div>
      <div id="discoverySilentSummary" class="discovery-silent-summary" hidden><span id="discoverySilentCount"></span><button type="button" class="text-button" data-discovery="show-silent">查看候选${icon('chevron-right')}</button></div>
      <div class="discovery-bulk-bar"><div><strong id="discoverySelected">已选 0 项</strong><button type="button" class="text-button" data-discovery="select-filtered">选择筛选结果（最多32项）</button><button type="button" class="text-button" data-discovery="clear-selection">清空</button></div><div><select id="discoveryBatchMode" aria-label="批量操作"><option value="smart">自动识别与二次验证</option><option value="verify">批量验证</option><option value="identify">批量深度识别 · 最多8项</option></select>${command('batch','批量处理','play')}</div></div>
      <div class="discovery-flow-status"><span id="discoveryFlowSummary"></span><span id="discoveryFlowUpdate"></span></div>
      <div class="discovery-table"><table><thead><tr><th><input type="checkbox" id="discoverySelectPage" aria-label="选择本页地址"></th><th>差异</th><th>频道</th><th>原地址</th><th>发现地址</th><th>验证与入库</th><th>最近记录</th><th>操作</th></tr></thead><tbody id="discoveryRows"></tbody></table></div>
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
        <label class="discovery-toggle"><input type="checkbox" id="discoveryAutoProcess">全量后自动识别与复核</label>
        <label class="discovery-toggle"><input type="checkbox" id="discoveryAutoArchive">长期失效自动归档</label>
        <span id="discoveryUpdateState" class="discovery-setting-status">未启用</span>
        <span id="discoveryLinkState" class="discovery-setting-status"></span>
        <span id="discoveryArchiveState" class="discovery-setting-status"></span>
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
      <p>复查最早检查的最多4个无响应或待复核地址，有播放时暂停。单次无响应不能证明下线，请在节目正常播出时复查。</p>
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
  for(const [value,label] of [['needs_name','待识别'],['needs_verify','待复核'],['await_update','待更新核验']])el('discoveryFilter').add(new Option(label,value));
  const when = (value, short = false) => value ? new Date(value * 1000).toLocaleString('zh-CN', short ? {hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}) : '--';
  const labels = {existing:'已存在',possible_move:'疑似换源',new_media:'新增可播源',unverified:'待确认',pending:'未验证',media:'已识别音视频',no_media:'未识别音视频',no_response:'本次无响应',inconclusive:'待复核',sample:'被动采样',scan:'小范围扫描',verify:'媒体验证',audit:'全量检查',idle:'空闲',running:'运行中',paused:'已暂停',complete:'完成',error:'失败',skipped:'已跳过'};
  Object.assign(labels,{identify:'深度识别',batch:'批量处理',name:'名称确认',submitted:'已提交',unconfirmed:'未确认'});
  const nameLabels={playlist:'原节目单',stream:'节目流名称',manual:'人工已确认',catalog:'频道表候选 · 待确认',conflict:'名称存在歧义',unknown:'名称待识别'};
  let naming=null;
  const selectedEndpoints=new Set();
  let pageRows=[];
  let loaded=false, dirty=false, inflight=false, submitting=false, busy=false, unavailable=false, modeChosen=false, nextPoll=0, page=1, revision=0, current={rows:[],history:[]};
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
  function filteredRows(){
    const query=el('discoverySearch').value.trim().toLowerCase(), filter=el('discoveryFilter').value;
    return (current.rows||[]).filter(r=>model.matches(r,filter)&&(!query||[r.name,r.endpoint,...r.previous||[]].join(' ').toLowerCase().includes(query)));
  }
  function renderBulk(){
    text('discoverySelected',`已选 ${selectedEndpoints.size} 项`);
    const check=el('discoverySelectPage'), count=pageRows.filter(r=>selectedEndpoints.has(r.endpoint)).length;
    check.checked=!!pageRows.length&&count===pageRows.length;check.indeterminate=count>0&&count<pageRows.length;
    check.disabled=!pageRows.length;
    const button=document.querySelector('[data-discovery=batch]');
    button.disabled=!selectedEndpoints.size||busy||submitting||unavailable||!!current.storage_error||!current.capabilities?.batch;
    button.title=current.capabilities?.batch?'处理所选地址':'NAS发现服务需要更新';
  }
  function renderRows() {
    panel.querySelectorAll('[data-stage-filter]').forEach(button=>{
      button.setAttribute('aria-pressed',String(el('discoveryFilter').value===button.dataset.stageFilter));
      button.querySelector('b').textContent=loaded?(current.rows||[]).filter(r=>model.matches(r,button.dataset.stageFilter)).length:'--';
    });
    const query=el('discoverySearch').value.trim(), filter=el('discoveryFilter').value, rows=filteredRows();
    const pages=Math.max(1,Math.ceil(rows.length/20));page=Math.min(page,pages);
    pageRows=rows.slice((page-1)*20,page*20);
    el('discoveryRows').innerHTML=pageRows.length?pageRows.map(rowMarkup).join(''):`<tr><td colspan="8" class="discovery-empty">${icon('radar')}<strong>${!loaded?'正在连接采样服务':query||filter!=='all'?'没有匹配结果':'暂未发现组播地址'}</strong><span>${!loaded||query||filter!=='all'?'':'最近采样未观察到流量，不代表频道失效'}</span></td></tr>`;
    text('discoveryPageCount',`${rows.length} 条结果`);text('discoveryPage',`${page} / ${pages}`);
    document.querySelector('[data-discovery=prev]').disabled=page<=1;document.querySelector('[data-discovery=next]').disabled=page>=pages;
    renderBulk();
  }
  const needsRecheck = model.needsRecheck;
  function renderScanControls(){
    const mode=el('discoveryMode').value, state=model.scanState(current,mode), button=document.querySelector('[data-discovery=run]');
    button.innerHTML=icon('play')+state.label;button.title=state.reason||state.label;button.ariaLabel=state.label;
    button.disabled=!loaded||unavailable||submitting||state.disabled;
    for(const option of el('discoveryMode').options)option.disabled=option.value==='audit-resume'&&!state.resumable||option.value==='batch-resume'&&!model.scanState(current,'batch-resume').batchResumable;
    el('discoveryMode').disabled=submitting;
  }
  function rowMarkup(r){
    const button=(action,label,symbol,disabled=false)=>`<button type="button" class="btn secondary console-icon-button" data-row-action="${action}" data-endpoint="${safe(r.endpoint)}" title="${label}" aria-label="${label}" ${busy||submitting||unavailable||disabled?'disabled':''}>${icon(symbol)}</button>`;
    const canName=r.change!=='existing'&&r.validation==='media'&&!r.last_check&&(r.programs||[]).length<=1;
    const stage={existing:'已在节目单',existing_failed:'正式节目异常，需复核',silent:'未取得信号，不能入库',needs_name:'等待核对名称',needs_verify:'等待有效复核',await_update:r.change==='possible_move'?'等待旧源失效证据':'验证已达标，等待更新核验'}[model.workflowStage(r)];
    const validation=mark(r.last_check||r.validation)+`<small>${safe(stage)}</small>`+(r.last_check&&r.validation==='media'?'<small>此前识别过音视频</small>':'')+(r.change!=='existing'&&r.validation==='media'?`<small>有效复核 ${safe(r.verified_count||0)} / 2</small>`:'');
    return `<tr><td><input type="checkbox" data-select-endpoint="${safe(r.endpoint)}" aria-label="选择 ${safe(r.name||r.endpoint)}" ${selectedEndpoints.has(r.endpoint)?'checked':''}></td><td>${mark(r.change)}</td><td><strong>${safe(r.name||'名称待识别')}</strong><small class="discovery-name-source">${safe(nameLabels[r.name_source]||'名称待识别')}${r.confirmation_stale?' · 原确认已失效':''}</small>${r.streams?.length?'<small>'+safe(r.streams.map(s=>[s.codec,s.width&&s.height?`${s.width}×${s.height}`:''].filter(Boolean).join(' ')).join(' · '))+'</small>':''}</td><td class="discovery-address">${safe(r.previous?.join('\n')||(r.change==='existing'?r.endpoint:'--'))}</td><td class="discovery-address">${safe(r.endpoint)}</td><td>${validation}</td><td class="discovery-time">${safe(when(r.checked_at||r.last_seen))}</td><td><div class="discovery-row-actions">${button('identify','深度识别此地址','scan-line')}${r.change!=='existing'?button('name','确认频道名称','pencil',!canName):''}</div></td></tr>`;
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
      el('discoveryAutoArchive').checked=!!data.collector?.auto_archive;
    }
  }
  function setUnavailable(message) {
    unavailable=true;notice(message,true);
    document.dispatchEvent(new CustomEvent('iptv:discovery',{detail:{service_error:message}}));
    text('discoveryLinkState','NAS未连接 · 当前结果未更新');
    text('discoveryUpdateState','无法读取自动更新状态');
    text('discoveryTaskState','NAS未连接 · 进度未更新');text('discoveryTaskScope','');
    el('discoveryProgress').hidden=true;
    document.querySelectorAll('[data-discovery]').forEach(button=>{
      if(!['refresh','prev','next','show-silent','select-filtered','clear-selection','ai'].includes(button.dataset.discovery))button.disabled=true;
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
    const changes=rows.filter(r=>['new_media','possible_move'].includes(r.change));text('discoveryCount',changes.length);text('discoveryTabCount',rows.length);
    text('discoveryChanges',`新增 ${changes.filter(r=>r.change==='new_media').length} · 疑似换源 ${changes.filter(r=>r.change==='possible_move').length}`);
    const lastSample=(data.history||[]).find(h=>h.kind==='sample');
    text('discoveryLatest',lastSample?when(lastSample.at,true):'--:--');text('discoveryDate',lastSample?when(lastSample.at)+' · 北京时间':'尚未完成采样');
    const audit=data.audit||{}, update=data.update||{};
    const applied=model.appliedUpdates(data);
    el('discoveryApplied').hidden=!applied.length;
    el('discoveryAppliedRows').innerHTML=applied.map(item=>`<li><time>${safe(when(item.at))}</time><span>${{add:'已新增',replace:'已换源',archive:'已归档',restore:'已恢复'}[item.kind]}</span><strong>${safe(item.name)}</strong></li>`).join('');
    text('discoveryArchiveState',data.collector?.auto_archive?(!data.collector?.auto_update?'归档暂停：自动更新未开启':`归档：${data.archive?.reason||'等待检查'}`):'');
    const resumable=['paused','error'].includes(audit.state)&&audit.done<audit.total;
    const batch=data.batch||{}, batchResumable=['paused','error'].includes(batch.state)&&batch.done<batch.total;
    const progressJob=busy?job:batchResumable?{...batch,kind:'batch'}:audit.total?{...audit,kind:'audit'}:job;
    el('discoveryProgress').hidden=!busy&&!audit.total&&!batch.total;
    text('discoveryProgressText',capture.state==='sampling'?'正在采样 IPTV 入口':`${labels[progressJob.kind]||'扫描'} ${progressJob.done||0} / ${progressJob.total||0}${progressJob.kind==='batch'?' 步':''} · ${labels[progressJob.state]||'未知'}`);
    if(capture.state==='sampling')el('discoveryProgressBar').removeAttribute('value');else {el('discoveryProgressBar').max=progressJob.total||1;el('discoveryProgressBar').value=progressJob.done||0;}
    text('discoveryTaskState',job.state==='running'?`${labels[job.kind]||'检查'}进行中${job.message?' · '+job.message:''}`:batchResumable?'上次批量处理进度已保留':resumable?'上次全量进度已保留':audit.state==='complete'?'上次全量已完成':'当前没有运行中的检查');
    text('discoveryTaskScope',audit.total?`全量目标 ${audit.total} · 音视频 ${audit.counts?.media||0} · 无响应 ${audit.counts?.no_response||0}`:'范围：正式节目单、发现记录与已设网段');
    if(data.storage_error)notice(data.storage_error,true);else if(capture.state==='error')notice(capture.error,true);else if(job.state==='error'||job.state==='paused')notice(job.message,true);else if(el('discoveryNotice').classList.contains('is-error'))notice();
    document.querySelectorAll('[data-discovery=settings],[data-discovery=catalog]').forEach(b=>{b.disabled=busy||submitting||!!data.storage_error;});
    document.querySelector('[data-discovery=pause]').hidden=job.state!=='running';
    text('discoveryAuditState',audit.total?`${labels[audit.state]||audit.state} · ${audit.done||0} / ${audit.total}`:'尚未全量检查');
    if(!modeChosen)el('discoveryMode').value=batchResumable?'batch-resume':resumable?'audit-resume':'audit-new';
    const silent=rows.filter(model.candidateSilent).length;
    el('discoverySilentSummary').hidden=!silent;
    text('discoverySilentCount',`${silent} 个无响应候选单独归类 · 未删除记录或正式频道`);
    const candidates=rows.filter(r=>r.change!=='existing'), media=candidates.filter(r=>r.validation==='media'&&!r.last_check), unnamed=media.filter(r=>['unknown','catalog','conflict'].includes(r.name_source));
    text('discoveryFlowSummary',`候选 ${candidates.length} → 本次音视频 ${media.length} → 待定名 ${unnamed.length} · 待二次验证 ${media.filter(r=>(r.verified_count||0)<2).length}`);
    text('discoveryFlowUpdate',`${data.settings?.auto_process?'自动识别与复核已开启':'自动识别与复核未开启'} · ${data.collector?.auto_update?'自动入库已开启':'自动入库未开启'}${update.sync_pending?' · 节目单待同步':''}${update.state==='error'?' · 更新受阻：'+(update.message||'需检查'):(update.last_applied?.changes?.length?' · 最近更新 '+update.last_applied.changes.length+' 项':'')}${data.auto_followup?.state==='blocked'?' · 自动处理未启动：'+data.auto_followup.message:''}`);
    for(const key of selectedEndpoints)if(!rows.some(r=>r.endpoint===key))selectedEndpoints.delete(key);
    renderUpdate(update,data.collector);
    text('discoveryLinkState',data.link?.message||'等待入口检查');
    text('discoveryCatalogState',data.catalog?.count?`本地频道表 ${data.catalog.count} 条 · 缓存 ${when(data.catalog.at)}`:'没有本地频道表缓存');
    if(!loaded||!dirty){
      el('discoveryActive').checked=!!data.settings?.active_enabled;el('discoveryRanges').value=(data.settings?.ranges||[]).join(', ');el('discoveryPorts').value=(data.settings?.ports||[]).join(', ');
      el('discoveryAutoProcess').checked=!!data.settings?.auto_process;
    }
    loaded=true;
    renderScanControls();
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
  panel.querySelectorAll('[data-stage-filter]').forEach(button=>button.addEventListener('click',()=>{
    el('discoveryFilter').value=button.dataset.stageFilter;el('discoverySearch').value='';selectedEndpoints.clear();page=1;renderRows();
  }));
  el('discoveryMode').addEventListener('change',()=>{modeChosen=true;renderScanControls();});
  el('discoverySelectPage').addEventListener('change',event=>{for(const row of pageRows){if(event.target.checked&&selectedEndpoints.size<32)selectedEndpoints.add(row.endpoint);else if(!event.target.checked)selectedEndpoints.delete(row.endpoint);}renderRows();});
  el('discoveryRows').addEventListener('change',event=>{const box=event.target.closest('[data-select-endpoint]');if(!box)return;if(box.checked&&selectedEndpoints.size>=32){box.checked=false;notice('每批最多32项，请先处理当前选择',true);}else if(box.checked)selectedEndpoints.add(box.dataset.selectEndpoint);else selectedEndpoints.delete(box.dataset.selectEndpoint);renderBulk();});
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
    const button=event.target.closest('[data-discovery]');if(!button||button.disabled)return;
    let action=button.dataset.discovery==='retry-confirm'?'retry':button.dataset.discovery;
    if(action==='ai'){document.dispatchEvent(new CustomEvent('iptv:ai-preview',{detail:'discovery'}));return;}
    if(action==='show-silent'){el('discoveryFilter').value='silent';page=1;selectView('diff');renderRows();return;}
    if(action==='select-filtered'){selectedEndpoints.clear();filteredRows().slice(0,32).forEach(r=>selectedEndpoints.add(r.endpoint));renderRows();return;}
    if(action==='clear-selection'){selectedEndpoints.clear();renderRows();return;}
    if(action==='prev'||action==='next'){page+=action==='prev'?-1:1;renderRows();return;}
    if(action==='refresh'){notice();await refresh();return;}
    if(unavailable){notice('NAS扫描服务暂不可用，请刷新确认恢复后再操作',true);return;}
    if(submitting)return;
    if(!loaded){notice('请等待当前设置读取完成',true);return;}
    let fields={};
    if(action==='run'){
      const mode=el('discoveryMode').value, state=model.scanState(current,mode);
      if(state.disabled){notice(state.reason,true);return;}
      action=mode.startsWith('audit-')?'audit':mode==='batch-resume'?'batch':mode;
      if(action==='audit')fields.mode=mode.slice(6);
      if(mode==='batch-resume')fields.mode='resume';
    }
    if(action==='batch'){
      if(fields.mode==='resume'){
        if(!await askConfirm('继续上次批量处理，从保存的步骤恢复；有播放时暂停。继续吗？'))return;
      }else{
        fields={mode:'new',workflow:el('discoveryBatchMode').value,endpoints:[...selectedEndpoints]};
        const limit=fields.workflow==='identify'?8:32;
        if(!fields.endpoints.length||fields.endpoints.length>limit){notice(`本次最多处理${limit}项，请调整选择`,true);return;}
        const unfinished=['paused','error'].includes(current.batch?.state)&&current.batch.done<current.batch.total;
        if(!await askConfirm(`${unfinished?'这会替换上次未完成的批量进度，保留发现证据和历史摘要。':''}批量处理 ${fields.endpoints.length} 个地址：${fields.workflow==='smart'?'自动验证、必要时深度识别，并间隔至少60秒再次验证':fields.workflow==='identify'?'逐个深度识别':'逐个验证'}。有播放时暂停；自动更新启用时，可靠名称及验证达标的新源可自动入库。无信号或名称不明的保留待复核，不删除频道。开始吗？`))return;
      }
    }
    if(action==='scan'&&!await askConfirm('按已保存范围逐个验证最多8个地址。有播放时暂停；开启自动更新后，重复验证通过的新源会自动入库。开始吗？'))return;
    if(action==='verify'&&!await askConfirm('验证最多4个已记录地址，有播放时停止。继续吗？'))return;
    if(action==='retry'){
      if(busy)return;
      if(button.dataset.discovery!=='retry-confirm'){el('discoveryRetryDialog').showModal();return;}
      el('discoveryRetryDialog').close();
    }
    if(action==='audit'){
      const retention=current.collector?.auto_archive&&current.collector?.auto_update?'已启用长期失效归档，达标条目会保留备份后移出正式节目单。':'不自动移除频道。';
      if(!await askConfirm((fields.mode==='new'?'从第一项重新全量扫描，替换上次全量进度并保留历史摘要与发现证据。范围限正式节目单、发现记录及已设网段。':`继续上次全量，保留已完成的 ${current.audit?.done||0} 项，仅检查剩余项。`)+`有播放时暂停；${retention}继续吗？`))return;
    }
    if(action==='sample'&&!await askConfirm('被动采样最多10秒，不主动加入新组播；零观察不代表没有频道。现在采样吗？'))return;
    if(action==='retry')fields.retry_failed=true;
    if(action==='settings'){
      fields.settings={enabled:el('discoveryEnabled').checked,daily_time:el('discoveryTime').value,weekdays_only:el('discoveryWeekdays').checked,weekly_enabled:el('discoveryWeekly').checked,weekly_day:Number(el('discoveryWeekday').value),weekly_time:el('discoveryWeeklyTime').value,auto_update:el('discoveryAutoUpdate').checked,auto_process:el('discoveryAutoProcess').checked,auto_archive:el('discoveryAutoArchive').checked,active_enabled:el('discoveryActive').checked,ranges:el('discoveryRanges').value.split(/[,，\s]+/).filter(Boolean),ports:el('discoveryPorts').value.split(/[,，\s]+/).filter(Boolean).map(Number)};
      if(fields.settings.auto_archive&&!current.collector?.auto_archive){
        fields.archive_consent=await askConfirm('启用长期失效自动归档：跨至少7天、3个不同日期在北京时间09:00（含）至22:00（不含）的完整检查均无响应，且全量覆盖和正常对照达标后移出正式节目单。异常超过20%或单轮超过5条/5%时阻断。保留原条目、台标、排序和备份；恢复信号并通过复核后可自动恢复。启用吗？');
        if(!fields.archive_consent)return;
      }
      if(fields.settings.auto_process&&!current.settings?.auto_process&&!await askConfirm('每轮全量完成后，对最多32个已取到音视频的新候选自动识别并二次验证。名称不明不猜名，有播放时暂停；不增加定时任务。启用吗？'))return;
      if(fields.settings.active_enabled&&!current.settings?.active_enabled&&!await askConfirm('每日采样后附加最多8个地址的主动验证。有播放时停止。启用吗？'))return;
      if(fields.settings.auto_update&&!current.collector?.auto_update&&!await askConfirm('启用后会备份并自动更新基础/台标节目单，保留排序。新源至少两次验证；疑似换源还须旧源持续失效。长期失效归档由独立开关控制，不永久删除记录。启用吗？'))return;
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
    finally{submitting=false;button.disabled=unavailable||(busy&&['sample','scan','verify','retry','audit','batch','settings'].includes(action))||(action==='retry'&&!(current.rows||[]).some(needsRecheck));renderScanControls();renderBulk();}
  });
  document.addEventListener('iptv:refresh',refresh);
  document.addEventListener('iptv:ai-notice',event=>{if(pane.classList.contains('on'))notice(event.detail.text,event.detail.error);});
  document.addEventListener('iptv:view',event=>{if(['overview','capture','automation'].includes(event.detail)&&Date.now()>=nextPoll)refresh();});
  setInterval(()=>{if(!document.hidden&&(pane.classList.contains('on')||document.querySelector('.pane.on[data-tab="automation"],.pane.on[data-tab="overview"]'))&&Date.now()>=nextPoll)refresh();},2000);
  renderRows();refresh();
})();
