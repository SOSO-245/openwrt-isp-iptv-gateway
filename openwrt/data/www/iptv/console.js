(() => {
  'use strict';
  function icon(name) {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [k,v] of Object.entries({viewBox:'0 0 24 24',width:20,height:20,fill:'none',stroke:'currentColor','stroke-width':1.75,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(k,v);
    svg.classList.add('ui-icon');
    for(const [tag,attrs] of window.IPTV_ICONS?.[name]||[]){const part=document.createElementNS(svg.namespaceURI,tag);for(const [k,v] of Object.entries(attrs))part.setAttribute(k,v);svg.append(part);}
    return svg;
  }
  window.iptvIcon=icon;
  if(!document.querySelector('.app-shell'))return;
  const el=id=>document.getElementById(id),safe=esc;
  const confirmation=document.createElement('dialog');
  confirmation.className='modal';
  confirmation.setAttribute('aria-labelledby','confirmationTitle');
  confirmation.innerHTML='<form method="dialog"><h2 id="confirmationTitle">确认操作</h2><p id="confirmationMessage" style="white-space:pre-wrap;line-height:1.7"></p><div class="dialog-actions"><button class="btn secondary" value="cancel" autofocus>取消</button><button class="btn" value="confirm">确认继续</button></div></form>';
  document.body.append(confirmation);
  let confirmationPending=false;
  window.iptvConfirm=message=>new Promise(resolve=>{
    if(confirmationPending){resolve(false);return;}
    confirmationPending=true;confirmation.returnValue='cancel';
    el('confirmationMessage').textContent=String(message).replace(/\\n/g,'\n');
    confirmation.addEventListener('close',()=>{confirmationPending=false;resolve(confirmation.returnValue==='confirm');},{once:true});
    confirmation.showModal();
  });
  document.querySelectorAll('[data-icon]').forEach(node=>{node.prepend(icon(node.dataset.icon));delete node.dataset.icon;});
  const labels={overview:'总览',playlist:'频道管理',capture:'源发现',epg:'节目资料',automation:'自动任务',maintenance:'系统维护'};
  const date=value=>value?new Date(value*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}):'--';
  let status={},discovery=null,statusPending=false,view='overview';
  window.iptvMessage=(message,error=false)=>{el('appMessage').textContent=message;el('appMessage').hidden=!message;el('appMessage').classList.toggle('is-error',error);};
  window.iptvCopy=async value=>{
    if(navigator.clipboard&&isSecureContext)await navigator.clipboard.writeText(value);
    else {const area=document.createElement('textarea');area.value=value;area.style.cssText='position:fixed;left:-9999px';document.body.append(area);area.select();const copied=document.execCommand('copy');area.remove();if(!copied)throw Error('复制失败');}
    window.iptvMessage('已复制');
  };
  window.showTab=(name)=>{
    name=({network:'maintenance',advanced:'maintenance'})[name]||name;
    if(!labels[name])name='overview';
    if(view!==name)window.iptvMessage('');
    view=name;
    document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.dataset.tab===name));
    document.querySelectorAll('#tabBar .tab').forEach(b=>{b.classList.toggle('on',b.dataset.tab===name);b.setAttribute('aria-current',b.dataset.tab===name?'page':'false');});
    el('consoleTitle').textContent=labels[name];
    try{localStorage.iptvTab=name;history.replaceState(null,'','#'+name);}catch(_){}
    if(name==='playlist'&&window.iptvChannels&&!window.iptvChannels.loaded)loadPlaylist().catch(report);
    if(name==='maintenance'){loadSharedLog();loadCaptureSharedLog();}
    document.dispatchEvent(new CustomEvent('iptv:view',{detail:name}));
  };
  document.addEventListener('click',e=>{const nav=e.target.closest('#tabBar [data-tab],[data-nav]');if(nav){showTab(nav.dataset.nav||nav.dataset.tab);if(nav.dataset.discoveryView)document.dispatchEvent(new CustomEvent('iptv:discovery-view',{detail:nav.dataset.discoveryView}));}});
  document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();showTab('overview');});
  window.addEventListener('hashchange',()=>showTab(location.hash.slice(1)));
  window.toggleAccount=show=>{const d=el('accountModal');if(!show){d.close();return;}el('oldUser').value=sessionStorage.iptvUser||'';el('oldPass').value='';el('newUser').value=sessionStorage.iptvUser||'';el('newPass').value='';el('newPass2').value='';d.showModal();};
  function report(error){window.iptvMessage(error?.message||String(error),true);}
  window.addEventListener('unhandledrejection',event=>report(event.reason));
  const theme=window.matchMedia('(prefers-color-scheme: dark)');
  function colorMode(dark,save=false){document.body.classList.toggle('iptv-dark',dark);const b=el('consoleColorToggle');b.replaceChildren(icon(dark?'sun':'moon'));b.title=b.ariaLabel=dark?'切换至浅色模式':'切换至深色模式';b.setAttribute('aria-pressed',String(dark));if(save)localStorage.iptvColorMode=dark?'dark':'light';}
  colorMode(localStorage.iptvColorMode?localStorage.iptvColorMode==='dark':theme.matches);
  el('consoleColorToggle').onclick=()=>colorMode(!document.body.classList.contains('iptv-dark'),true);
  theme.addEventListener('change',e=>{if(!localStorage.iptvColorMode)colorMode(e.matches);});
  window.renderConfig=cfg=>{
    const links=(cfg.links||[]).filter(link=>{try{return ['http:','https:'].includes(new URL(link.url,location.origin).protocol);}catch(_){return false;}});
    window.iptvLinks=links;
    el('subscriptionCount').textContent=links.length?links.length+' 个入口':'';
    el('linkGrid').innerHTML=links.length?links.map((link,i)=>'<div class="subscription-row"><b>'+safe(link.title||'订阅入口')+'</b><a class="url" href="'+safe(link.url)+'" target="_blank" rel="noopener" title="'+safe(link.url)+'">'+safe(link.url)+'</a><div class="row-tools"><button class="icon-button" data-link-copy="'+i+'" title="复制地址" aria-label="复制'+safe(link.title||'地址')+'">'+icon('copy').outerHTML+'</button><a class="icon-button" href="'+safe(link.url)+'" target="_blank" rel="noopener" title="打开地址" aria-label="打开'+safe(link.title||'地址')+'">'+icon('external-link').outerHTML+'</a></div></div>').join(''):'<div class="empty-state">尚未配置订阅入口</div>';
  };
  el('linkGrid').addEventListener('click',e=>{const b=e.target.closest('[data-link-copy]');if(b)window.iptvCopy(window.iptvLinks[Number(b.dataset.linkCopy)].url).catch(report);});
  const list=rows=>rows.map(([name,value])=>'<div><dt>'+safe(name)+'</dt><dd>'+safe(value??'--')+'</dd></div>').join('');
  function renderStatus(){
    const running=String(status.proxy_status||'').startsWith('running'),connected=status.iptv_link==='UP';
    el('summaryService').textContent=Object.keys(status).length?(running&&connected?'正常':'待检查'):'--';
    el('summaryService').classList.toggle('bad',!!status.proxy_status&&(!running||!connected));
    el('summaryChannels').textContent=status.new_count??'--';
    el('connectionState').innerHTML=list([['IPTV 接口',status.iptv_iface?status.iptv_iface+' · '+(status.iptv_link==='UP'?'已连接':'未连接'):'--'],['NAS 分析',discovery?(discovery.service_error?'未连接':'在线'):'等待读取'],['组播代理',status.proxy_status?(running?'运行中':'未运行'):'--'],['当前播放',status.play_active_clients??'--']]);
    el('diag').innerHTML=list([['LAN 地址',status.lan_ip],['IPTV 地址',status.iptv_ip],['IPTV 接口',status.iptv_iface],['组播路由',status.multicast_route],['组播直连',status.nikki_state?status.nikki_state==='direct'?'正常':'待检查':'--'],['EPG',status.epg_status==='ok'?'可访问':status.epg_status],['机顶盒接口（可选）',status.capture_iface?(status.capture_iface+' · '+(status.capture_link==='UP'?'已连接':'未连接')):'--'],['防火墙 IPTV 命中',status.iptv_fw_packets]]);
    const sec=Number(status.play_total_seconds||0),mb=Number(status.play_total_mb||0);
    el('playStats').innerHTML=[['累计观看',Math.floor(sec/3600)+' 小时 '+Math.floor(sec%3600/60)+' 分钟'],['当前连接',status.play_active_clients??'--'],['估算流量',mb>1024?(mb/1024).toFixed(1)+' GB':mb+' MB'],['最高同时连接',status.play_max_clients??'--']].map(([k,v])=>'<div><small>'+safe(k)+'</small><b>'+safe(v)+'</b></div>').join('');
    el('playStatsList').innerHTML=String(status.play_top_channels||'').split('|').filter(x=>x.includes('=')).map(x=>{const i=x.lastIndexOf('=');return '<div><span>'+safe(x.slice(0,i))+'</span><span>'+Math.round(Number(x.slice(i+1))/60)+' 分钟</span></div>';}).join('')||'<div class="empty-state">暂无频道观看记录</div>';
    el('footerStatus').textContent=status.lan_ip?status.lan_ip+' · '+(status.iptv_iface||'--'):'等待连接';
  }
  window.loadStatus=async()=>{
    if(statusPending)return;
    statusPending=true;
    try{const response=await fetch('/cgi-bin/iptv-status',{cache:'no-store'});if(!response.ok)throw Error('设备状态读取失败');status=await response.json();window._iptvD=status;
      if(!el('networkTools').dataset.loaded){for(const [id,key] of [['mac','mac'],['hostname','hostname'],['vendorid','vendorid'],['clientid','clientid'],['proto','proto'],['iptvIf','iptv_iface'],['boxIf','capture_iface']])if(status[key])el(id).value=status[key];el('networkTools').dataset.loaded='1';}
      renderStatus();el('lastRefreshed').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
    }catch(e){el('footerStatus').textContent='状态读取失败';throw e;}finally{statusPending=false;}
  };
  document.addEventListener('iptv:discovery',event=>{
    discovery=event.detail;renderStatus();
    if(discovery.service_error){el('summaryChanges').textContent='--';el('summarySample').textContent='--:--';el('overviewAttention').hidden=false;el('overviewAttentionText').textContent='NAS 未连接，发现结果未更新';return;}
    const rows=discovery.rows||[],changes=rows.filter(r=>r.change!=='existing'),history=discovery.history||[];
    el('summaryChanges').textContent=changes.length;
    const sample=history.find(h=>h.kind==='sample');el('summarySample').textContent=sample?new Date(sample.at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}):'--:--';
    const names={sample:'入口采样',scan:'小范围扫描',audit:'全量检查',verify:'媒体验证',identify:'名称识别',name:'名称确认'};
    const states={complete:'完成',paused:'已暂停',error:'失败',running:'运行中',skipped:'已跳过'};
    el('recentTasks').innerHTML=history.slice(0,4).map(h=>'<div class="task-row"><span class="dot '+safe(h.state||'')+'"></span><div>'+safe(names[h.kind]||h.kind)+'<time>'+safe(date(h.at))+' · 北京时间</time></div><span class="task-state">'+safe(states[h.state]||h.state||'已记录')+'</span></div>').join('')||'<div class="empty-state">暂无任务记录</div>';
    const unnamed=changes.filter(r=>['unknown','catalog','conflict'].includes(r.name_source)).length;
    el('overviewAttention').hidden=!unnamed&&!discovery.storage_error;
    el('overviewAttentionText').textContent=discovery.storage_error||'待确认名称 '+unnamed;
  });
  document.querySelectorAll('[data-metadata]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-metadata]').forEach(x=>{const active=x===b;x.setAttribute('aria-selected',String(active));el(x.dataset.metadata).hidden=!active;});if(b.dataset.metadata==='logoLibrary')loadLogoManager().catch(report);});
  el('logoSearch').oninput=()=>document.querySelectorAll('.logo-item').forEach(row=>{row.hidden=!row.querySelector('.nm').textContent.toLowerCase().includes(el('logoSearch').value.toLowerCase());});
  new MutationObserver(()=>{if(el('log').textContent.trim()&&el('log').textContent!=='等待系统操作'&&view!=='maintenance')window.iptvMessage(el('log').textContent);}).observe(el('log'),{childList:true,subtree:true,characterData:true});
  new MutationObserver(()=>{el('epgScheduleStatus').textContent=el('epgConfigStatus').textContent;}).observe(el('epgConfigStatus'),{childList:true,subtree:true});
  el('pageRefresh').onclick=async()=>{const b=el('pageRefresh');b.disabled=true;try{await loadStatus();if(view==='playlist')await loadPlaylist();if(view==='capture'||view==='automation')document.dispatchEvent(new Event('iptv:refresh'));if(view==='maintenance')await loadSharedLog();}catch(e){report(e);}finally{b.disabled=false;}};
  window.iptvStart=()=>{requireLogin();if(sessionStorage.iptvAuthed!=='1')return;loadConfig().catch(report);loadEpgConfig().catch(report);loadStatus().catch(report);showTab(location.hash.slice(1)||localStorage.iptvTab||'overview');document.dispatchEvent(new Event('iptv:refresh'));};
  window.iptvStart();
  setInterval(()=>{if(!document.hidden&&sessionStorage.iptvAuthed==='1'&&view==='overview')loadStatus().catch(report);},30000);
})();
