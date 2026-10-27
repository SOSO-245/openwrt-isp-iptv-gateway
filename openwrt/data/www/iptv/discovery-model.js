/* Shared, side-effect-free view rules. Never delete observations or playlists. */
(function(root) {
  'use strict';
  const lastCheck = row => row.last_check || row.validation;
  const needsRecheck = row => ['no_response', 'no_media', 'inconclusive'].includes(lastCheck(row));
  const candidateSilent = row => row.change !== 'existing' && lastCheck(row) === 'no_response'
    && row.validation !== 'media' && !row.last_success;
  function workflowStage(row) {
    if(row.change === 'existing') return needsRecheck(row) ? 'existing_failed' : 'existing';
    if(candidateSilent(row)) return 'silent';
    if(row.validation !== 'media' || row.last_check) return 'needs_verify';
    if(!row.name || !['stream','manual'].includes(row.name_source)) return 'needs_name';
    return (row.verified_count || 0) < 2 ? 'needs_verify' : 'await_update';
  }
  function matches(row, filter) {
    switch(filter) {
      case 'needs_name': case 'needs_verify': case 'await_update': return workflowStage(row) === filter;
      case 'active': return !candidateSilent(row);
      case 'silent': return candidateSilent(row);
      case 'media': return lastCheck(row) === 'media';
      case 'failed': return needsRecheck(row);
      case 'existing_failed': return row.change === 'existing' && needsRecheck(row);
      case 'unnamed': return workflowStage(row) === 'needs_name';
      case 'changes': return ['new_media','possible_move'].includes(row.change);
      case 'all': return true;
      default: return row.change === filter;
    }
  }
  function scanState(data, mode) {
    const audit = data.audit || {}, job = data.job || {}, capture = data.capture || {};
    const resumable = ['paused','error'].includes(audit.state) && audit.done < audit.total;
    const batch=data.batch||{}, batchResumable=['paused','error'].includes(batch.state)&&batch.done<batch.total;
    let reason = data.service_error ? 'NAS未连接' : data.storage_error ? '存储异常' :
      job.state === 'running' || capture.state === 'sampling' ? '当前任务进行中' : '';
    if(mode.startsWith('audit-')) {
      const value = mode.slice(6);
      if(!reason && !data.capabilities?.audit_modes?.includes(value)) reason = 'NAS发现服务需要更新';
      if(!reason && value === 'resume' && !resumable) reason = '没有可继续的全量进度';
    }
    if(!reason && mode === 'retry' && !(data.rows || []).some(needsRecheck)) reason = '没有待复核地址';
    if(!reason && mode === 'batch-resume' && (!data.capabilities?.batch || !batchResumable)) reason = '没有可继续的批量任务';
    if(!reason && mode === 'scan' && !(data.settings?.ranges || []).length) reason = '尚未配置扫描范围';
    const labels = {sample:'开始短采样',scan:'开始小范围扫描',verify:'验证待确认项',retry:'复查无响应',
      'audit-new':'重新全量扫描','audit-resume':'继续上次全量','batch-resume':'继续批量处理'};
    return {disabled:!!reason, reason, label:labels[mode] || '开始检查', resumable, batchResumable};
  }
  function appliedUpdates(data) {
    const records = Array.isArray(data.recent_updates) && data.recent_updates.length
      ? data.recent_updates : data.update?.last_applied ? [data.update.last_applied] : [];
    return records.flatMap(record => (Array.isArray(record.changes) ? record.changes : [])
      .filter(change => ['add','replace','archive','restore'].includes(change.kind) && typeof change.name === 'string')
      .map(change => ({at:record.at, kind:change.kind, name:change.name}))).slice(0, 16);
  }
  const api = {lastCheck, needsRecheck, candidateSilent, workflowStage, matches, scanState, appliedUpdates};
  if(typeof module === 'object' && module.exports) module.exports = api;
  else root.IPTVDiscoveryModel = api;
})(typeof window === 'object' ? window : globalThis);
