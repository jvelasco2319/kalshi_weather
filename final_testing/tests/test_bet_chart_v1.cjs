// Synthetic positions test payout semantics, never production wallets or forecasts.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const file=process.argv[2];const src=fs.readFileSync(file,'utf8').split('function probabilities(')[0];
const box=vm.createContext({Date,Number,Math,Object,String,Intl});vm.runInContext(src,box);
const day='2026-10-13',at=Date.parse(day+'T18:00:00Z')*1e6;
const wallet=(city,id,positions={},orders={})=>({city,profile_id:id,wallet_id:city+':'+id,name:id,positions,orders});
const position=(ticker,side,extra={})=>({ticker,side,day,quantity:10,first_fill_ns:at,...extra});
const city={city_id:'la',name:'Los Angeles',day,zone:'America/Los_Angeles',
 contracts:[{ticker:'mid',lower:70,upper:72},{ticker:'low',lower:null,upper:70},{ticker:'high',lower:72,upper:null}],
 observed:[{timestamp:day+'T16:00:00Z',temperature_f:67},{timestamp:day+'T20:00:00Z',temperature_f:73}],wallets:[]};
box.sample=city;
function call(code){return vm.runInContext(code,box);}
city.wallets=[wallet('la','a',{a:position('mid','YES')})];
assert.deepEqual(JSON.parse(JSON.stringify(call('payoutBands(paperBets(sample).bets[0])'))),[[69.5,71.5]]);
let html=call('chart(sample,"a",Date.parse("2026-10-13T21:00:00Z")*1e6)');
assert.equal((html.match(/class="paper-bet-band"/g)||[]).length,1);assert(html.includes('data-side="YES"'));
assert(Number(html.match(/data-side="YES" x="([^"]+)/)[1])>48,'Band must start at fill time');
city.wallets[0].positions.a.side='NO';
assert.deepEqual(JSON.parse(JSON.stringify(call('payoutBands(paperBets(sample).bets[0])'))),[[null,69.5],[71.5,null]]);
html=call('chart(sample,"a")');assert.equal((html.match(/class="paper-bet-band"/g)||[]).length,2);
assert(html.includes('NO outside 70–71°F'));assert(html.includes('stroke-dasharray="4 3"'));
city.wallets[0].positions.a=position('low','NO');
assert.deepEqual(JSON.parse(JSON.stringify(call('payoutBands(paperBets(sample).bets[0])'))),[[69.5,null]]);
city.wallets[0].positions.a=position('high','NO');
assert.deepEqual(JSON.parse(JSON.stringify(call('payoutBands(paperBets(sample).bets[0])'))),[[null,71.5]]);
city.wallets=[wallet('la','a',{}, {pending:{day,status:'UNKNOWN',action:'BUY'}})];
assert.equal(call('paperBets(sample).bets.length'),0);assert.equal(call('paperBets(sample).pending'),1);
assert(!call('chart(sample,"a")').includes('class="paper-bet-band"'));assert(call('betLegend(sample,"a")').includes('not shaded'));
city.wallets=[wallet('la','a',{old:position('mid','YES',{day:'2026-10-12'}),empty:position('mid','YES',{quantity:0}),missing:position('missing','NO')})];
assert.equal(call('paperBets(sample).bets.length'),0);assert.equal(call('paperBets(sample).missing.length'),1);
city.wallets=[wallet('la','a',{a:position('mid','YES')}),wallet('la','b',{b:position('high','NO')})];
assert.notEqual(call('paperBets(sample).bets[0].color'),call('paperBets(sample).bets[1].color'));
assert(call('chart(sample,"b")').includes('fill-opacity="0.2"'));
for(const name of ['la','ny','miami','phoenix']){
 city.city_id=name;city.wallets=[wallet(name,'a',{a:position('mid','NO')})];
 assert.equal(call('paperBets(sample).bets.length'),1);assert(call('chart(sample,"a")').includes('paper-bet-band'));
}
console.log('PASS: YES/NO integer outcomes, open tails, fill timing, pending/stale/missing exclusions, model colors and all four cities.');
