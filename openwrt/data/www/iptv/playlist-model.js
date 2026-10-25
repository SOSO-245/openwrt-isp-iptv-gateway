/* Preserve unknown M3U tags and ordering; only edit the selected EXTINF/URL pair. */
(function(root){
  'use strict';
  function commaAt(text){let quoted=false;for(let i=0;i<text.length;i++){if(text[i]==='"')quoted=!quoted;if(text[i]===','&&!quoted)return i;}return -1;}
  function attribute(header,name){const m=header.match(new RegExp('(?:^|\\s)'+name+'="([^"]*)"'));return m?m[1]:'';}
  function parse(source){
    const lines=String(source).split('\n'),rows=[];let pending=null;
    lines.forEach((line,index)=>{
      const clean=line.trim();
      if(clean.startsWith('#EXTINF:')){const comma=commaAt(line);pending={info:index,header:comma<0?line:line.slice(0,comma),name:comma<0?'未命名':line.slice(comma+1).trim()};}
      else if(clean&&!clean.startsWith('#')&&pending){rows.push({...pending,url:clean,urlLine:index,group:attribute(pending.header,'group-title'),logo:attribute(pending.header,'tvg-logo')});pending=null;}
    });
    return {lines,rows};
  }
  function setAttribute(header,name,value){
    const regex=new RegExp('(\\s'+name+'=")[^"]*(")');
    if(regex.test(header))return header.replace(regex,(_m,a,b)=>a+value+b);
    return value?header+' '+name+'="'+value+'"':header;
  }
  function validate(fields){
    if(!fields.name.trim()||/[\r\n]/.test(fields.name))throw Error('请填写有效的频道名称');
    if(!/^(https?|rtp|udp|rtsp):\/\//i.test(fields.url)||/[\s"<>]/.test(fields.url))throw Error('播放地址须为 HTTP、HTTPS、RTP、UDP 或 RTSP 地址');
    if(/[\r\n"]/.test(fields.group+fields.logo))throw Error('分组和台标地址不能包含换行或双引号');
    if(fields.logo&&!/^(https?:\/\/|\/[^/])/i.test(fields.logo))throw Error('台标须为 HTTP(S) 或本地绝对路径');
  }
  function edit(source,index,fields){
    validate(fields);const {lines,rows}=parse(source),row=rows[index];
    let header=row?.header||'#EXTINF:-1';
    header=setAttribute(header,'group-title',fields.group);
    header=setAttribute(header,'tvg-logo',fields.logo);
    const info=header+','+fields.name.trim();
    if(row){lines[row.info]=info;lines[row.urlLine]=fields.url.trim();}
    else {if(index!==rows.length)throw Error('频道已变化，请重新读取');if(lines[lines.length-1]==='')lines.pop();lines.push(info,fields.url.trim(),'');}
    return lines.join('\n');
  }
  const api={parse,edit,validate};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.IPTVPlaylist=api;
})(typeof window==='undefined'?globalThis:window);
