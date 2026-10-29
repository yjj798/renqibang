const fs=require('fs');
const s=fs.readFileSync('index.html','utf8');
const m=s.match(/function marketNote\(d\)\{[\s\S]*?\n\}/);
if(!m){ console.log('!! 未找到 marketNote'); process.exit(1); }
const src=m[0];
const marketNote=new Function('return '+src.replace('function marketNote','function')+';')();

function at(str, fn){
  const [y,mo,d,h,mi]=str.split(/[-: ]/).map(Number);
  const Real=Date;
  class Fake extends Real{ constructor(...a){ a.length? super(...a): super(y,mo-1,d,h,mi); } }
  global.Date=Fake;
  try{ return fn(); } finally { global.Date=Real; }
}
const cases=[
  ['周四盘中·数据是今天',    '2026-10-29 14:00', {quote_date:'2026-10-29'}, ''],
  ['周六·数据周五',          '2026-10-31 10:00', {quote_date:'2026-10-30'}, '　·　今日休市，数据截至 2026-10-30'],
  ['周日·数据周五',          '2026-11-01 20:00', {quote_date:'2026-10-30'}, '　·　今日休市，数据截至 2026-10-30'],
  ['周一 09:00 未开盘',      '2026-11-02 09:00', {quote_date:'2026-10-30'}, '　·　今日未开盘，数据截至 2026-10-30'],
  ['周一 10:00 数据仍周五',  '2026-11-02 10:00', {quote_date:'2026-10-30'}, '　·　数据截至 2026-10-30'],
  ['元旦 10:00·法定休市',    '2027-01-01 10:00', {quote_date:'2026-12-31', holidays:['2027-01-01']}, '　·　今日休市，数据截至 2026-12-31'],
  ['元旦 10:00·表里没这天',  '2027-01-01 10:00', {quote_date:'2026-12-31'}, '　·　数据截至 2026-12-31'],
  ['周四·数据却缺 quote_date','2026-10-29 14:00', {}, ''],
  ['周六·数据缺 quote_date',  '2026-10-31 10:00', {}, '　·　今日休市'],
];
let bad=0;
for(const [name, now, d, want] of cases){
  const got=at(now, ()=>marketNote(Object.assign({updated_at:'x'}, d)));
  const ok = got===want;
  if(!ok) bad++;
  console.log((ok?'  ok  ':'  !!  ')+name.padEnd(24)+' => '+JSON.stringify(got)+(ok?'':'  期望 '+JSON.stringify(want)));
}
console.log(bad? `FAIL ${bad}/${cases.length}` : `PASS ${cases.length}/${cases.length}`);
process.exit(bad?1:0);
