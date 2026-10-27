(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  if(!el('aiDiagnostics'))return;
  const dialog=document.createElement('dialog');
  dialog.id='aiPreviewDialog';dialog.setAttribute('aria-labelledby','aiPreviewTitle');
  dialog.innerHTML='<form method="dialog"><div class="section-heading"><h2 id="aiPreviewTitle">发送前确认</h2><button class="icon-button" value="cancel" title="关闭" aria-label="关闭诊断预览" data-icon="x"></button></div><p id="aiDestination" class="muted"></p><dl id="aiSummary" class="data-list"></dl><p class="warning-note">以下摘要将发送到 DeepSeek，可能产生 API 费用。不发送地址、频道名、账号、订阅或抓包。建议仅供人工核对，不会自动修改设备。</p><label class="check-label"><input id="aiConsent" type="checkbox">同意发送本次摘要</label><p id="aiPreviewNotice" class="error-text" role="status"></p><div class="dialog-actions"><button class="btn secondary" value="cancel">取消</button><button type="button" class="btn" id="aiSend" disabled data-icon="activity">发送并分析</button></div></form>';
  document.body.append(dialog);
  const resultDialog=document.createElement('dialog');resultDialog.id='aiResultDialog';
  resultDialog.setAttribute('aria-labelledby','aiResultTitle');
  resultDialog.innerHTML='<form method="dialog"><div class="section-heading"><h2 id="aiResultTitle">AI 分析</h2><button class="icon-button" value="close" aria-label="关闭分析结果" title="关闭分析结果" data-icon="x"></button></div><p id="aiResultDialogMeta" class="muted"></p><pre id="aiResultDialogAdvice" class="ai-advice" role="status"></pre><div class="dialog-actions"><button class="btn secondary" value="close">关闭</button></div></form>';
  document.body.append(resultDialog);
  const settingsDialog=document.createElement('dialog');settingsDialog.id='aiSettingsDialog';
  settingsDialog.setAttribute('aria-labelledby','aiSettingsTitle');
  settingsDialog.innerHTML='<form id="aiSettingsForm" autocomplete="off"><div class="section-heading"><h2 id="aiSettingsTitle">AI 接口设置</h2><button type="button" class="icon-button" id="aiSettingsClose" title="关闭" aria-label="关闭接口设置" data-icon="x"></button></div><div class="ai-settings-fields"><label>接口地址（Base URL）<input id="aiBaseUrl" type="url" required maxlength="100" spellcheck="false" autocomplete="off"></label><p class="muted">仅支持 DeepSeek 官方接口，可填写根地址或 /v1 地址。</p><label>模型<select id="aiModel"><option value="deepseek-v4-flash">deepseek-v4-flash</option><option value="deepseek-flash">deepseek-flash</option></select></label><label>API Key<input id="aiApiKey" type="password" maxlength="512" autocomplete="new-password" spellcheck="false" autocapitalize="none" aria-describedby="aiKeyStatus"></label><p id="aiKeyStatus" class="muted"></p></div><div id="aiHttpWarning"><p class="warning-note">此页面未加密，已禁止输入或提交密钥。请使用设备的 HTTPS 管理页；证书身份须由你核对，也可使用设备端安全终端配置。</p><a id="aiHttpsLink" class="btn secondary" target="_blank" rel="noopener noreferrer">打开 HTTPS 管理页</a></div><p class="muted">连接测试仅向 DeepSeek 校验密钥和模型列表，不发送家庭数据，不生成诊断。</p><p id="aiSettingsNotice" role="status"></p><div class="dialog-actions"><button type="button" class="btn secondary" id="aiSettingsCancel">取消</button><button type="button" class="btn secondary" id="aiSettingsTest" data-icon="activity">连接测试</button><button type="submit" class="btn" id="aiSettingsSave" data-icon="check">保存设置</button></div></form>';
  document.body.append(settingsDialog);
  el('aiSettingsSave').dataset.icon='check';
  const setupButton=document.createElement('button');setupButton.type='button';setupButton.className='btn secondary';
  setupButton.id='aiConfigureFromPreview';setupButton.textContent='配置 API';setupButton.dataset.icon='settings-2';
  dialog.querySelector('.dialog-actions').prepend(setupButton);
  let configuration=null,settingsPending=false;
  let preview=null,pending=false,deadline=0,statusPending=false;
  const notice=(text,error=false)=>{el('aiNotice').textContent=text;el('aiNotice').classList.toggle('error-text',error);document.dispatchEvent(new CustomEvent('iptv:ai-notice',{detail:{text,error}}));};
  async function api(action,fields={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
    try{
      const response=await fetch('/cgi-bin/iptv-discovery',{method:'POST',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({action,user:sessionStorage.iptvUser||'',password:sessionStorage.iptvPass||'',...fields})});
      const result=await response.json();
      if(!response.ok||result.error)throw Error(result.error||'AI 诊断读取失败');
      return result;
    }catch(error){
      if(error.name==='AbortError')throw Error(action==='ai_settings'?'保存结果未确认，请关闭后重新打开 API 设置核对，勿重复提交密钥':action==='ai_test'?'连接测试超时；未保存设置，未自动重试':'请求未确认，请勿重复发送；一分钟后可重新预览');
      throw error;
    }finally{clearTimeout(timer);}
  }
  function configure(info){configuration={...configuration,...info};el('aiConfiguration').textContent='DeepSeek · '+info.model+' · '+info.message;}
  function updateSave(){
    const disabled=settingsPending||configuration?.secure_transport!==true;
    ['aiBaseUrl','aiModel','aiApiKey','aiSettingsSave','aiSettingsTest'].forEach(id=>{el(id).disabled=disabled;});
    el('aiSettingsClose').disabled=settingsPending;el('aiSettingsCancel').disabled=settingsPending;
  }
  const settingsNotice=(text,error=false)=>{el('aiSettingsNotice').textContent=text;el('aiSettingsNotice').classList.toggle('error-text',error);};
  ['aiBaseUrl','aiModel','aiApiKey'].forEach(id=>el(id).addEventListener('input',()=>settingsNotice('')));
  const closeSettings=()=>{if(!settingsPending)settingsDialog.close();};
  el('aiSettingsClose').onclick=closeSettings;el('aiSettingsCancel').onclick=closeSettings;
  settingsDialog.addEventListener('cancel',event=>{if(settingsPending)event.preventDefault();});
  settingsDialog.addEventListener('close',()=>{el('aiApiKey').value='';});
  async function openSettings(){
    if(pending||settingsPending)return;
    el('aiSettings').disabled=true;
    try{
      const info=await api('ai_status');configure(info);
      el('aiBaseUrl').value=info.base_url;el('aiModel').value=info.model;el('aiApiKey').value='';
      el('aiApiKey').required=!info.configured;el('aiApiKey').placeholder=info.configured?'留空保留已保存的密钥':'填写 DeepSeek API Key';
      el('aiKeyStatus').textContent=info.configured?'密钥已保存，不会回显；留空保留，填写则替换。':'尚未配置密钥；仅设备服务账号可读写，不在浏览器中持久保存。';
      el('aiHttpWarning').hidden=info.secure_transport===true;
      const secureUrl=new URL('/iptv/#maintenance',location.href);secureUrl.protocol='https:';secureUrl.port='';el('aiHttpsLink').href=secureUrl.href;
      settingsNotice('');updateSave();settingsDialog.showModal();
    }catch(error){notice(error.message,true);}finally{el('aiSettings').disabled=false;}
  }
  el('aiSettings').onclick=openSettings;
  setupButton.onclick=()=>{dialog.close();openSettings();};
  const draftSettings=()=>({base_url:el('aiBaseUrl').value,model:el('aiModel').value,api_key:el('aiApiKey').value});
  el('aiSettingsTest').onclick=async()=>{
    if(settingsPending||el('aiSettingsTest').disabled||!el('aiSettingsForm').reportValidity())return;
    const draft=draftSettings();settingsPending=true;updateSave();settingsNotice('正在测试连接…');
    try{
      const result=await api('ai_test',{...draft,test_consent:true});
      const available=!result.ok&&result.available_models?.length?'；可选模型：'+result.available_models.join('、'):'';
      settingsNotice(result.message+available+(result.latency_ms!==undefined?' · '+result.latency_ms+' ms':'')+'。未保存设置。',!result.ok);
    }catch(error){settingsNotice(error.message,true);}finally{draft.api_key='';settingsPending=false;updateSave();}
  };
  el('aiSettingsForm').onsubmit=async event=>{
    event.preventDefault();if(settingsPending||el('aiSettingsSave').disabled)return;
    const draft=draftSettings();settingsPending=true;updateSave();settingsNotice('正在保存…');
    try{
      const info=await api('ai_settings',{...draft,save_consent:true});
      configure(info);preview=null;settingsDialog.close();notice('API 设置已保存，未调用云端；可预览摘要后手动发送分析');
    }catch(error){settingsNotice(error.message,true);}
    finally{draft.api_key='';el('aiApiKey').value='';settingsPending=false;updateSave();}
  };
  async function refresh(){
    if(statusPending||pending||sessionStorage.iptvAuthed!=='1')return;
    statusPending=true;
    try{configure(await api('ai_status'));}catch(_){el('aiConfiguration').textContent='AI 服务尚未部署或暂不可用';}finally{statusPending=false;}
  }
  function updateSend(){el('aiSend').disabled=pending||!preview?.configured||!el('aiConsent').checked||Date.now()>deadline;}
  el('aiConsent').onchange=updateSend;
  dialog.addEventListener('close',()=>{preview=null;el('aiConsent').checked=false;});
  async function openPreview(){
    if(pending)return;
    if(dialog.open){dialog.focus();return;}
    pending=true;el('aiPreview').disabled=true;notice('正在读取本地状态…');
    try{
      preview=await api('ai_preview',{issue:el('aiIssue').value});configure(preview);
      deadline=Date.now()+preview.expires_in*1000;
      el('aiSummary').replaceChildren(...Object.entries(preview.summary).map(([key,value])=>{
        const row=document.createElement('div'),name=document.createElement('dt'),content=document.createElement('dd');
        name.textContent=key;content.textContent=value===null?'未知':value===true?'是':value===false?'否':String(value);
        row.append(name,content);return row;
      }));
      el('aiDestination').textContent=preview.base_url+' · '+preview.model;
      el('aiConsent').checked=false;
      el('aiPreviewNotice').textContent=preview.configured?'':preview.message+'；点击“配置 API”填写密钥';
      setupButton.hidden=preview.configured;
      notice('摘要已生成，尚未发送');dialog.showModal();
    }catch(error){notice(error.message,true);}finally{pending=false;el('aiPreview').disabled=false;updateSend();}
  }
  el('aiPreview').onclick=openPreview;
  document.addEventListener('iptv:ai-preview',event=>{if(pending)return;el('aiIssue').value=event.detail;openPreview();});
  el('aiSend').onclick=async()=>{
    if(pending||!preview?.configured||!el('aiConsent').checked)return;
    if(Date.now()>deadline){el('aiPreviewNotice').textContent='预览已过期，请关闭后重新生成';updateSend();return;}
    const ticket=preview.ticket;dialog.close();pending=true;el('aiPreview').disabled=true;notice('DeepSeek 分析中…');
    el('aiResultDialogMeta').textContent='正在分析已确认的脱敏摘要';el('aiResultDialogAdvice').textContent='等待 DeepSeek 回复…';resultDialog.showModal();
    el('aiResult').hidden=true;el('aiAdvice').textContent='';
    try{
      const result=await api('ai_analyze',{ticket,consent:true});
      el('aiAdvice').textContent=result.advice;
      el('aiResultMeta').textContent=result.model+' · '+new Date(result.at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' · 建议待核对';
      el('aiResultDialogMeta').textContent=el('aiResultMeta').textContent;el('aiResultDialogAdvice').textContent=result.advice;
      el('aiResult').hidden=false;notice(result.truncated?'回复已达到长度限制，建议不完整；未自动续费重试':'分析完成，未执行任何设备变更',result.truncated);
    }catch(error){notice(error.message,true);el('aiResultDialogAdvice').textContent=error.message;}finally{pending=false;el('aiPreview').disabled=false;}
  };
  el('aiCopy').onclick=()=>window.iptvCopy(el('aiAdvice').textContent).catch(error=>notice(error.message,true));
  document.addEventListener('iptv:view',event=>{if(event.detail==='maintenance')refresh();});
})();
