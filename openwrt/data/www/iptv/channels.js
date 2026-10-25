(() => {
  'use strict';
  const el=id=>document.getElementById(id),model=window.IPTVPlaylist;
  const state={loaded:false,original:'',file:'new.m3u',page:1,busy:false,request:0,editing:null};
  window.iptvChannels=state;
  const message=(text,error=false)=>window.iptvMessage?.(text,error);
  const icon=name=>window.iptvIcon?.(name).outerHTML||'';
  const dirty=()=>state.loaded&&el('playlistEditor').value!==state.original;
  const readOnly=()=>state.file==='vsp.m3u';
  const mark=()=>{
    el('playlistDirty').hidden=!dirty();el('savePlaylistButton').disabled=state.busy||readOnly();
    el('addChannel').disabled=state.busy||readOnly()||!state.loaded;
    el('playlistEditor').readOnly=state.busy||readOnly();
    el('restorePlaylist').disabled=state.busy||readOnly();
    el('playlistFile').disabled=state.busy;
  };
  function render(){
    const rows=model.parse(el('playlistEditor').value).rows;
    const selected=el('channelGroup').value,groups=[...new Set(rows.map(r=>r.group).filter(Boolean))];
    el('channelGroup').innerHTML='<option value="">全部分组</option>'+groups.map(g=>'<option>'+esc(g)+'</option>').join('');
    if(groups.includes(selected))el('channelGroup').value=selected;
    el('channelGroupOptions').innerHTML=groups.map(g=>'<option value="'+esc(g)+'"></option>').join('');
    const query=el('channelSearch').value.trim().toLowerCase(),group=el('channelGroup').value;
    const filtered=rows.map((r,index)=>({...r,index})).filter(r=>(!group||r.group===group)&&(!query||[r.name,r.url,r.group].join(' ').toLowerCase().includes(query)));
    const pages=Math.max(1,Math.ceil(filtered.length/12));state.page=Math.min(state.page,pages);
    el('channelRows').innerHTML=filtered.length?filtered.slice((state.page-1)*12,state.page*12).map(r=>{
      const logo=/^(https?:\/\/|\/[^/])/.test(r.logo)?'<img src="'+esc(r.logo)+'" alt="" loading="lazy">':icon('monitor-play');
      return '<tr><td class="muted">'+(r.index+1)+'</td><td><div class="channel-name"><span class="channel-logo">'+logo+'</span><strong title="'+esc(r.name)+'">'+esc(r.name)+'</strong></div></td><td>'+esc(r.group||'未分组')+'</td><td class="channel-url" title="'+esc(r.url)+'">'+esc(r.url)+'</td><td><div class="row-tools"><button class="icon-button" data-edit="'+r.index+'" aria-label="编辑 '+esc(r.name)+'" title="编辑频道" '+(readOnly()||state.busy?'disabled':'')+'>'+icon('pencil')+'</button><button class="icon-button" data-copy="'+r.index+'" aria-label="复制 '+esc(r.name)+' 播放地址" title="复制播放地址">'+icon('copy')+'</button></div></td></tr>';
    }).join(''):'<tr><td colspan="5" class="empty-state">'+(state.loaded?'没有匹配的频道':'正在读取节目单')+'</td></tr>';
    el('channelRows').querySelectorAll('img').forEach(img=>img.onerror=()=>img.replaceWith(window.iptvIcon('monitor-play')));
    el('channelCount').textContent='共 '+rows.length+' 个频道'+(filtered.length!==rows.length?' · 筛选 '+filtered.length+' 个':'');
    el('channelPage').textContent=state.page+' / '+pages;
    el('channelPrev').disabled=state.page<=1;el('channelNext').disabled=state.page>=pages;
    el('playlistCount').textContent=state.file+' · '+rows.length+' 个频道';
    mark();
  }
  async function fetchPlaylist(file){
    const response=await fetch('/cgi-bin/iptv-action?action=get_playlist&file='+encodeURIComponent(file),{method:'POST',cache:'no-store'});
    if(!response.ok)throw Error('节目单读取失败');
    const text=await response.text();
    if(!text.trimStart().startsWith('#EXTM3U'))throw Error('返回内容不是 M3U，未替换当前编辑内容');
    return text;
  }
  window.loadPlaylist=async(skipDirtyPrompt=false)=>{
    const file=el('playlistFile').value;
    if(dirty()&&!skipDirtyPrompt&&!await window.iptvConfirm('有未保存的修改，丢弃修改并重新读取吗？')){el('playlistFile').value=state.file;return;}
    const serial=++state.request;state.busy=true;mark();
    try{const source=await fetchPlaylist(file);if(serial!==state.request)return;state.file=file;state.original=source;state.loaded=true;state.page=1;el('playlistEditor').value=source;render();await loadHistory();}
    catch(e){el('playlistFile').value=state.file;message(e.message,true);throw e;}
    finally{if(serial===state.request){state.busy=false;render();}}
  };
  window.updatePlaylistCount=render;
  window.savePlaylist=async()=>{
    if(state.busy||readOnly()||!dirty())return;
    const source=el('playlistEditor').value;
    if(!source.trimStart().startsWith('#EXTM3U')||!model.parse(source).rows.length){message('节目单为空或格式无效，未保存',true);return;}
    if(!await window.iptvConfirm('保存 '+state.file+'？保存前会生成历史备份，频道顺序按当前列表保留。'))return;
    state.busy=true;mark();
    try{
      // Recheck the baseline so automatic discovery changes are not silently overwritten.
      if(await fetchPlaylist(state.file)!==state.original)throw Error('远端节目单已变化，未覆盖。请先下载当前修改，再重新读取并合并');
      const response=await fetch('/cgi-bin/iptv-action?action=save_playlist&file='+encodeURIComponent(state.file)+authParams(),{method:'POST',body:source});
      const result=await response.text();
      if(!response.ok||/失败|错误|未授权|认证|拒绝/.test(result))throw Error(result||'保存失败');
      const actual=await fetchPlaylist(state.file);
      if(actual!==source)throw Error('保存后的内容与提交不一致，请下载当前修改并重新核对');
      state.original=actual;message(result||'节目单已保存');await loadHistory();await loadStatus();
    }catch(e){message(e.message,true);}
    finally{state.busy=false;render();}
  };
  function openEdit(index){
    if(state.busy||readOnly()||!state.loaded)return;
    const rows=model.parse(el('playlistEditor').value).rows,r=rows[index]||{name:'',group:'',url:'',logo:''};state.editing=index;
    el('channelDialogTitle').textContent=index===rows.length?'新增频道':'编辑频道';
    for(const [id,key] of [['editName','name'],['editGroup','group'],['editUrl','url'],['editLogo','logo']])el(id).value=r[key];
    el('channelError').hidden=true;el('channelDialog').showModal();
  }
  el('channelForm').onsubmit=e=>{
    e.preventDefault();const fields={name:el('editName').value,group:el('editGroup').value.trim(),url:el('editUrl').value.trim(),logo:el('editLogo').value.trim()};
    try{el('playlistEditor').value=model.edit(el('playlistEditor').value,state.editing,fields);el('channelDialog').close();render();}
    catch(error){el('channelError').textContent=error.message;el('channelError').hidden=false;}
  };
  for(const id of ['closeChannel','cancelChannel'])el(id).onclick=()=>el('channelDialog').close();
  el('addChannel').onclick=()=>openEdit(model.parse(el('playlistEditor').value).rows.length);
  el('channelRows').onclick=e=>{const edit=e.target.closest('[data-edit]'),copy=e.target.closest('[data-copy]');if(edit)openEdit(Number(edit.dataset.edit));if(copy)window.iptvCopy(model.parse(el('playlistEditor').value).rows[Number(copy.dataset.copy)].url).catch(error=>message(error.message,true));};
  for(const id of ['channelSearch','channelGroup'])el(id).oninput=()=>{state.page=1;render();};
  el('channelPrev').onclick=()=>{state.page--;render();};el('channelNext').onclick=()=>{state.page++;render();};
  el('playlistEditor').oninput=render;el('playlistFile').onchange=()=>loadPlaylist().catch(()=>{});
  el('savePlaylistButton').onclick=savePlaylist;
  el('discardPlaylist').onclick=async()=>{if(await window.iptvConfirm('撤销尚未保存的修改？')){el('playlistEditor').value=state.original;render();}};
  el('restorePlaylist').onclick=async()=>{
    const bak=el('historyList').value;
    if(state.busy||readOnly()||!bak)return;
    if(!await window.iptvConfirm('恢复 '+bak+'？当前远端版本会先备份。'+(dirty()?'未保存修改将被替换。':'')))return;
    state.busy=true;mark();
    try{
      const response=await fetch('/cgi-bin/iptv-action?action=restore_history&file='+encodeURIComponent(state.file)+'&bak='+encodeURIComponent(bak)+authParams(),{method:'POST'});
      const result=await response.text();
      if(!response.ok||/失败|错误|非法|不存在|不允许|未授权|认证|拒绝/.test(result))throw Error(result||'恢复失败');
      await loadPlaylist(true);message(result||'已恢复历史版本');
    }catch(error){message(error.message,true);}
    finally{state.busy=false;render();}
  };
  el('playlistDownload').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([el('playlistEditor').value],{type:'audio/x-mpegurl'}));a.download=state.file;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
  window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
})();
