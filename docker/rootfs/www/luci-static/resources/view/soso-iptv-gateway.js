'use strict';
'require view';

return view.extend({
  render: function () {
    return E('iframe', {
      src: '/iptv/?v=1.1.5-' + Date.now(),
      style: 'width:100%;min-height:calc(100vh - 120px);border:0;border-radius:8px;background:#101821;'
    });
  },
  handleSaveApply: null,
  handleSave: null,
  handleReset: null
});
