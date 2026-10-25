// Extract the existing Lucide node data; never copy a complete application bundle.
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const [source, target] = process.argv.slice(2);
if (!source || !target) throw new Error('Usage: node vendor-icons.mjs LUCIDE_PACKAGE OUTPUT_DIRECTORY');
const names = ['monitor-play','house','list-video','calendar-days','radar','network','settings-2',
  'refresh-cw','download','play','scan-line','shield-check','search','sliders-horizontal','clock',
  'circle-alert','check','chevron-down','chevron-left','chevron-right','user-round','palette','book-open','activity','moon','sun','pause','pencil'];
const icons = {};
for (const name of names) {
  const mod = await import(pathToFileURL(path.join(source,'dist/esm/icons',name+'.js')).href);
  const nodes = mod.__iconNode || mod.default;
  if (!Array.isArray(nodes)) throw new Error('Lucide icon node array missing: '+name);
  icons[name] = nodes.map(([tag,attrs]) => [tag,Object.fromEntries(Object.entries(attrs).filter(([k])=>k!=='key'))]);
}
const version = JSON.parse(await readFile(path.join(source,'package.json'),'utf8')).version;
await writeFile(path.join(target,'icons.js'), '/* Lucide '+version+', ISC. See lucide-LICENSE. */\nwindow.IPTV_ICONS='+JSON.stringify(icons)+';\n');
await writeFile(path.join(target,'lucide-LICENSE'), await readFile(path.join(source,'LICENSE')));
