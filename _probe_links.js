// 探针：把页面里真实的 <script> 拿出来，用真实 data.json 跑一遍，检查链接与表头排序是否真的生效
// 不是复刻逻辑，是执行产物本身（改完页面必须这么验，否则只证明模板里有字符串）
const fs = require('fs');
const path = 'D:/ai/renqibang/';
const html = fs.readFileSync(path + 'index.html', 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (!blocks.length) { console.error('FAIL 找不到 script 块'); process.exit(1); }
const code = blocks[blocks.length - 1][1];

const els = {};
const mk = id => (els[id] || (els[id] = { id, innerHTML: '', textContent: '', value: '' }));
global.document = { getElementById: mk, querySelectorAll: () => [] };
global.window = global;
global.setInterval = () => 0;
const data = JSON.parse(fs.readFileSync(path + 'data.json', 'utf8'));
global.fetch = () => Promise.resolve({ json: () => Promise.resolve(data) });

try { eval(code); } catch (e) { console.error('FAIL 执行抛错:', e.message); process.exit(1); }

const mono = (a, dir) => a.length > 1 && a.every((v, i) => i === 0 || (dir < 0 ? a[i - 1] >= v : a[i - 1] <= v));
const nums = (h, re) => [...h.matchAll(re)].map(m => parseFloat(m[1]));
const scoresOf = h => nums(h, /<td class="score num"[^>]*>([\d.]+)<\/td>/g);
const ranksOf = h => nums(h, /<td class="num">第(\d+)名<\/td>/g);
const pricesOf = h => nums(h, /<td class="dim num">¥([\d.]+)<\/td>/g);
// 取每行第 idx 列的纯文本（用于概念表/板块表）
const colOf = (h, idx) => [...h.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(m => {
  const tds = [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(x => x[1].replace(/<[^>]+>/g, '').trim());
  return tds[idx];
});

setTimeout(() => {
  const lb = els['lb_rows'] ? els['lb_rows'].innerHTML : '';
  const cpt = els['cpt_rows'] ? els['cpt_rows'].innerHTML : '';
  const all = lb + cpt;
  const links = [...all.matchAll(/<a class="sk"[^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>/g)];
  // 裸名只查「榜单表」：概念名不挂个股链接是正常口径，不能算残留
  const bare = [...lb.matchAll(/<td><b>([^<]{1,12})<\/b><\/td>/g)].map(m => m[1]);

  console.log('rows: lb=' + (lb.match(/<tr/g) || []).length + ' cpt=' + (cpt.match(/<tr/g) || []).length);
  console.log('链接数=' + links.length + '  裸名残留=' + bare.length + (bare.length ? ' → ' + JSON.stringify(bare.slice(0, 8)) : ''));

  // 校验 ①链接格式 ②后缀与数据一致 ③没有 href 缺 6 位
  const name2code = window.NAME2CODE || {};
  let bad = 0, sample = [];
  const codeIdx = {};
  Object.keys(name2code).forEach(n => { codeIdx[n] = name2code[n]; });
  // 数据源后缀是通达信风格(.SH)，选股通规范写法是 .SS —— 两者等价，比较前先映射
  const SUFMAP = { SH: 'SS', SS: 'SS', SZ: 'SZ', BJ: 'BJ' };
  links.forEach(([, href, name]) => {
    const m = href.match(/^https:\/\/xuangutong\.com\.cn\/stock\/(\d{6})\.(SS|SZ|BJ)$/);
    if (!m) { bad++; console.log('  BAD-FMT', name, href); return; }
    const src = codeIdx[name];
    if (src) {
      const [c, s] = String(src).split('.');
      if (c !== m[1] || SUFMAP[(s || '').toUpperCase()] !== m[2]) { bad++; console.log('  BAD-SUF', name, src, '->', href); }
    }
    if (sample.length < 3) sample.push(name + ' → ' + href);
  });
  console.log('格式/后缀错误=' + bad);
  sample.forEach(s => console.log('  ' + s));

  // 概念表龙头列：能解析到代码的必须带链接，解析不到的应为纯文本（不编链接）
  const leaderCells = [...cpt.matchAll(/<td class="lead">([\s\S]*?)<\/td>/g)].map(m => m[1]);
  const withLink = leaderCells.filter(x => x.includes('<a class="sk"')).length;
  console.log('概念表龙头单元格=' + leaderCells.length + ' 其中带链接=' + withLink + ' 纯文本=' + (leaderCells.length - withLink));

  // 搜索框结果块：股票名也必须带链接
  let searchOk = false;
  try {
    if (typeof doSearch === 'function') {
      mk('q').value = '大洋电机';
      doSearch();
      const sr = els['sr'] ? els['sr'].innerHTML : '';
      searchOk = sr.includes('<a class="sk"') && sr.includes('002249.SZ');
      console.log('查分块链接=' + (searchOk ? 'OK' : 'MISSING') + ' 长度=' + sr.length);
    } else { console.log('查分块 跳过(doSearch 不在全局)'); }
  } catch (e) { console.log('查分块执行抛错: ' + e.message); }

  // ---------- 表头排序 ----------
  let sortOk = true;
  const chk = (label, cond, extra) => {
    console.log('  ' + (cond ? 'OK  ' : 'FAIL') + ' ' + label + (extra ? ' ' + extra : ''));
    if (!cond) sortOk = false;
  };
  if (typeof sortLB !== 'function' || typeof sortCPT !== 'function') {
    console.log('排序 FAIL：sortLB/sortCPT 未定义');
    sortOk = false;
  } else {
    console.log('排序（三态：首点 ▼ → 再点 ▲ → 三点回默认）');
    const lb0 = els['lb_rows'].innerHTML;
    sortLB('score');
    chk('评分 首点降序', mono(scoresOf(els['lb_rows'].innerHTML), -1), JSON.stringify(scoresOf(els['lb_rows'].innerHTML)));
    sortLB('score');
    chk('评分 再点升序', mono(scoresOf(els['lb_rows'].innerHTML), 1), JSON.stringify(scoresOf(els['lb_rows'].innerHTML)));
    sortLB('score');
    chk('评分 三点回默认', els['lb_rows'].innerHTML === lb0);

    sortLB('rank');
    const rk = ranksOf(els['lb_rows'].innerHTML);
    chk('人气排名 首点升序(第1名在前)', mono(rk, 1), JSON.stringify(rk));

    sortLB('price');
    const pr = pricesOf(els['lb_rows'].innerHTML);
    chk('股价 首点降序(贵→便宜)', mono(pr, -1), JSON.stringify(pr));
    sortLB('price'); sortLB('price');   // 复位

    // 连板列（第 8 列）：首点降序，无连板的 -- 沉底
    const b0 = els['lb_rows'].innerHTML;
    sortLB('board');
    const bd = colOf(els['lb_rows'].innerHTML, 7);
    const bdNum = bd.map(v => {
      const m = v.match(/(\d+)板/);
      return m ? parseInt(m[1], 10) : (v.indexOf('首板') >= 0 ? 1 : null);
    });
    const bdOk = bdNum.filter(v => v !== null);
    chk('连板 首点降序', mono(bdOk, -1), JSON.stringify(bd));
    chk('连板 缺值(--)沉底', bd.slice(bdOk.length).every(v => v === '--'));
    sortLB('board'); sortLB('board');
    chk('连板 三点回默认', els['lb_rows'].innerHTML === b0);

    // 概念总榜：热度今日列（第 2 列）降序，缺值沉底
    const c0 = els['cpt_rows'].innerHTML;
    sortCPT('hn');
    const hn = colOf(els['cpt_rows'].innerHTML, 1);
    const hnNum = hn.filter(v => /^\d+$/.test(v)).map(Number);
    const tailOk = hn.slice(hnNum.length).every(v => v === '--');
    chk('概念 热度今日降序', mono(hnNum, -1), JSON.stringify(hn));
    chk('概念 缺值(--)沉底', tailOk);
    sortCPT('hn'); sortCPT('hn');
    chk('概念 三点回默认', els['cpt_rows'].innerHTML === c0);
  }

  // 表头是否都挂了 data-sort（回归：新加列别忘了）
  const headHtml = html.match(/<thead>[\s\S]*?<\/thead>/g) || [];
  const thTotal = headHtml.join('').match(/<th[ >]/g) || [];
  const thSort = headHtml.join('').match(/<th data-sort=/g) || [];
  console.log('表头 th=' + thTotal.length + ' 其中可排序=' + thSort.length + '（# 与轨迹列不参与排序属正常）');

  const ok = links.length > 0 && bad === 0 && bare.length === 0 && searchOk && sortOk;
  console.log(ok ? 'PASS' : 'FAIL');
  process.exit(ok ? 0 : 1);
}, 300);
