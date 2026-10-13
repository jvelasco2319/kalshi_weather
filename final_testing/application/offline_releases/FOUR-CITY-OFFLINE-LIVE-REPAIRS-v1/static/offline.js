let offline=null,offlineCity='all',offlineWallet='',offlineTimer=null;
const cityNames={la:'Los Angeles',ny:'New York',miami:'Miami',phoenix:'Phoenix'};
const dayLabel=d=>new Date(d+'T12:00:00').toLocaleDateString('en-US',{month:'short',day:'numeric'});
const number=x=>x==null?'—':Number(x).toLocaleString('en-US');
const amount=x=>x==null?'—':money(x);
// Completed trading profit is distinct from cash leaving the wallet to buy.
// Entry-day attribution does not advance the date cash is reusable.
function tradeRows(rows){return rows.map(r=>{
 let value=Object.hasOwn(r,'known_eventual_origin_net')?r.known_eventual_origin_net:r.eventual_origin_net;
 if(r.status==='COMPLETE'&&!Number(r.contracts))value=0;
 if((r.status!=='COMPLETE'&&!Number(r.contracts))||Number(r.reserved)>0||/PAYOUT_PENDING|SETTLEMENT_PENDING|UNKNOWN.*EXECUTION/.test(r.reason||''))value=null;
 return {...r,supported_trade_pnl:value??null};
});}
const offlineProfiles=()=> (offline?.profiles||[]).filter(p=>offlineCity==='all'||p.city===offlineCity);
function plainReason(value){
 const s=String(value||'');
 if(/CONNECTION_REQUIRED|GENUINE_SOURCE_ACQUISITION_REQUIRED/.test(s))return 'Connect the read-only archive to download missing historical books.';
 if(/CHECKPOINT|NEW_MONTH_FIT/.test(s))return 'This month needs an explicitly saved model checkpoint.';
 if(/OFFICIAL.*PENDING|PAYOUT_PENDING|SETTLEMENT_PENDING/.test(s))return 'Waiting for the official market result and payout time.';
 if(/UNKNOWN.*EXECUTION|UNRESOLVED.*EXECUTION|ARRIVAL.*UNKNOWN/.test(s))return 'The archived arrival book cannot confirm this simulated fill.';
 if(/EARLIER.*GAP|EARLIER_UNRESOLVED/.test(s))return 'An earlier incomplete day prevents a complete compounded balance.';
 if(/WEATHER|FORECAST/.test(s))return 'Historical weather inputs are incomplete or unavailable at the required time.';
 if(/OPPORTUNIT|UNSUPPORTED_NO_TRADE|UNAVAILABLE_NATIVE/.test(s))return 'Some historical books are missing or too old to judge every possible trade.';
 if(/AWAITING_ANALYSIS/.test(s))return 'Waiting for this day’s analysis.';
 if(/REPLAY_FAILED/.test(s))return 'The latest analysis failed; the previous saved history is retained.';
 return s.replaceAll('_',' ').replace(/\b([A-Z]{3,})\b/g,x=>x.toLowerCase());
}
function offlinePlot(rows,field,kind,label){
 const valid=rows.filter(r=>r[field]!=null&&Number.isFinite(Number(r[field])));
 if(!valid.length)return `<div class="empty">${esc(label)} is unavailable while the required evidence is missing.<br>Pending days are not counted as $0 days.</div>`;
 const width=Math.max(180,Math.min(600,innerWidth<620?innerWidth-66:600)),height=240,left=54,right=width-22,top=18,bottom=195;
 let low=Math.min(...valid.map(r=>Number(r[field]))),high=Math.max(...valid.map(r=>Number(r[field])));
 if(kind==='bars'){low=Math.min(0,low);high=Math.max(0,high);}else{low=Math.min(100,low);high=Math.max(100,high);}
 const spread=Math.max(1,high-low);low-=spread*.1;high+=spread*.1;
 const X=i=>left+(i+.5)/Math.max(1,rows.length)*(right-left),Y=y=>bottom-(y-low)/(high-low)*(bottom-top);
 let marks='';for(let i=0;i<4;i++){const y=low+(high-low)*i/3;marks+=`<line x1="${left}" y1="${Y(y)}" x2="${right}" y2="${Y(y)}" stroke="#e5eaf1"/><text x="4" y="${Y(y)+3}">$${y.toFixed(high-low<5?2:0)}</text>`;}
 let part=[];const segments=[];
 rows.forEach((r,i)=>{const v=r[field];if(v==null||!Number.isFinite(Number(v))){if(part.length)segments.push(part);part=[];marks+=`<rect x="${X(i)-2}" y="${bottom-5}" width="4" height="5" fill="#b6bfcc"><title>${esc(dayLabel(r.day))}: incomplete evidence</title></rect>`;return;}
  const uncertain=r.status!=='COMPLETE',title=esc(dayLabel(r.day)+': '+money(v)+(uncertain?' · supported subset; other evidence incomplete':''));
  if(uncertain)marks+=`<rect x="${X(i)-2}" y="${bottom-5}" width="4" height="5" fill="#aab4c4"><title>${esc(dayLabel(r.day)+': incomplete evidence')}</title></rect>`;
  if(kind==='bars'){const y0=Y(0),y=Y(Number(v));marks+=`<rect tabindex="0" x="${X(i)-Math.max(2,(right-left)/rows.length*.29)}" y="${Math.min(y,y0)}" width="${Math.max(4,(right-left)/rows.length*.58)}" height="${Math.max(1,Math.abs(y-y0))}" fill="${Number(v)<0?'#bc4650':'#13806b'}"><title>${title}</title></rect>`;}
  else{part.push([X(i),Y(Number(v))]);marks+=`<circle tabindex="0" cx="${X(i)}" cy="${Y(Number(v))}" r="3" fill="${uncertain?'#aab4c4':'#4263d5'}"><title>${title}</title></circle>`;}
 });if(part.length)segments.push(part);
 if(kind!=='bars')marks=segments.map(points=>`<polyline points="${points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#4263d5" stroke-width="2" ${field==='known_cash'?'stroke-dasharray="5 4"':''}/>`).join('')+marks;
 const ticks=[0,Math.floor((rows.length-1)/2),rows.length-1];[...new Set(ticks)].forEach(i=>{marks+=`<text x="${X(i)}" y="222" text-anchor="middle">${esc(dayLabel(rows[i].day))}</text>`;});
 return `<svg class="chart offline-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">${marks}</svg>`;
}
function renderOffline(){
 $('#refresh').hidden=true;$('#freeze').textContent='Saved models · offline';$('#freeze').className='pill blue';
 $('#release').textContent=offline?.offline_release||'Offline history';
 document.querySelector('#cities').hidden=true;document.querySelector('.topline').hidden=true;
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('selected',b.dataset.view==='offline'));
 $('#notice').textContent='Offline historical simulation · separate from online wallets. Archived timing, fills and settlement-target assumptions remain conditional.';
 if(!offline){$('#content').innerHTML='<section class="panel"><div class="empty">Loading saved offline history…</div></section>';return;}
 const job=offline.job||{},busy=['RUNNING','QUEUED'].includes(job.status),profiles=offlineProfiles();
 if(!profiles.some(p=>p.wallet_id===offlineWallet))offlineWallet=profiles[0]?.wallet_id||'';
 const p=profiles.find(p=>p.wallet_id===offlineWallet),rows=p?.days||[],allRows=profiles.flatMap(p=>p.days||[]);
 const pending=allRows.filter(r=>r.status!=='COMPLETE'),complete=allRows.filter(r=>r.status==='COMPLETE');
 const known=profiles.reduce((s,p)=>s+Number(p.known_cash??100),0);
 const full=profiles.length&&profiles.every(p=>p.ending_cash!=null)?profiles.reduce((s,p)=>s+Number(p.ending_cash),0):null;
 const assumptions=[...new Set([...(offline.city_results?.[p?.city]?.assumptions||[]),...(p?.assumptions||[])])];
 const pendingRows=profiles.flatMap(p=>(p.days||[]).filter(r=>r.status!=='COMPLETE').map(r=>({...r,name:p.name,city:p.city})));
 $('#content').innerHTML=`<section class="panel"><div class="section-title"><div><h2>Offline history</h2><p class="muted">October 1, 2026 – ${esc(dayLabel(offline.end_day||offline.today))} · each strategy starts with $100 once</p></div><button id="analyze" class="primary" ${busy?'disabled':''}>${busy?'Analyzing missing days…':'Download & analyze missing days'}</button></div><div class="status-line" role="status">${esc(job.message||'Click the button to check every missing city, strategy and day.')} ${busy?Object.entries(job.cities||{}).map(([c,s])=>esc(cityNames[c]+': '+s)).join(' · '):''}</div><div class="offline-actions"><label>City <select id="offline-city"><option value="all">All four cities</option>${Object.entries(cityNames).map(([c,n])=>`<option value="${c}" ${offlineCity===c?'selected':''}>${n}</option>`).join('')}</select></label><a href="/api/offline/export" download>Download saved results</a><button id="archive-login">${offline.archive_connection==='CONNECTED'?'Archive connected':'Connect read-only archive'}</button></div><div class="offline-metrics"><div class="metric"><span>Complete history balance</span><strong>${amount(full)}</strong><span>${full==null?'Full calendar evidence is incomplete':'Includes this view’s complete wallets'}</span></div><div class="metric"><span>Known supported cash</span><strong>${money(known)}</strong><span>Separate simulations; does not prove shared fills or fill missing opportunities</span></div><div class="metric"><span>Analyzed strategy-days</span><strong>${complete.length}</strong><span>${allRows.length} calendar strategy-days checked</span></div><div class="metric"><span>Pending / uncertain days</span><strong>${pending.length}</strong><span>Retried on the next button click</span></div></div>${offline.updated_ns?`<p class="muted">Saved ${esc(new Date(offline.updated_ns/1e6).toLocaleString())}. The server keeps working if this tab closes.</p>`:''}</section><section class="wallet-grid">${profiles.map(w=>`<article class="wallet-card"><span class="eyebrow">${esc(cityNames[w.city])}</span><h3>${esc(w.name)}</h3><div class="wallet-balance">${amount(w.ending_cash)}</div><p class="muted">Complete-history cash · started at $100</p><div class="wallet-metrics"><div class="metric"><span>Known cash</span><strong>${amount(w.known_cash)}</strong></div><div class="metric"><span>Wins / losses</span><strong>${number(w.wins)} / ${number(w.losses)}</strong></div><div class="metric"><span>Contracts bought</span><strong>${number(w.contracts)}</strong></div><div class="metric"><span>Fees paid</span><strong>${amount(w.fees)}</strong></div></div><p class="muted">${(w.days||[]).filter(r=>r.status==='COMPLETE').length} complete · ${(w.days||[]).filter(r=>r.status!=='COMPLETE').length} pending / uncertain. Native sizing rules remain fixed. ${w.latest_attempt_failed?`Latest refresh failed; known cash is saved through ${esc(dayLabel(w.known_cash_asof_day))}.`:""}</p><button data-offline-wallet="${esc(w.wallet_id)}">View daily results</button></article>`).join('')}</section><section class="panel"><div class="model-bar"><label for="offline-method">Strategy</label><select id="offline-method">${profiles.map(w=>`<option value="${esc(w.wallet_id)}" ${w.wallet_id===offlineWallet?'selected':''}>${esc(cityNames[w.city]+' · '+w.name)}</option>`).join('')}</select></div><p class="model-caption">${esc(p?.rule_summary||'Waiting for the first saved replay')}</p></section><section class="charts"><article class="panel"><div class="chart-head"><div><h3>Known cash through time</h3><p>Simulated cash follows the official settlement-time proxy. Dashed line: supported conditional path, not complete opportunity coverage.</p></div></div>${offlinePlot(rows,'known_cash','line','Known supported daily cash')}<p class="muted">No monthly reset. Missing days are marked in gray; cash is never filled in from a guessed trade.</p></article><article class="panel"><div class="chart-head"><div><h3>Trade gains & losses by entry day</h3><p>Supported closed trades after fees; uncertain opportunities remain marked in gray. Buying a contract is not a trading loss.</p></div></div>${offlinePlot(tradeRows(rows),'supported_trade_pnl','bars','Supported closed trade profit and loss by entry day')}</article></section><section class="panel"><details ${pendingRows.length?'open':''}><summary>${pendingRows.length} pending or uncertain strategy-days</summary><div class="table-scroll"><table><thead><tr><th>Day</th><th>City / strategy</th><th>Status</th><th>What is missing?</th></tr></thead><tbody>${pendingRows.map(r=>`<tr><td>${esc(dayLabel(r.day))}</td><td>${esc(cityNames[r.city]+' · '+r.name)}</td><td>${esc(r.status)}</td><td class="reason-cell">${esc(plainReason(r.reason)||'More source evidence is needed')}</td></tr>`).join('')||'<tr><td colspan="4">No pending days in this view.</td></tr>'}</tbody></table></div></details></section><section class="panel"><details><summary>Selected strategy: daily record & sources</summary><div class="table-scroll"><table><thead><tr><th>Day</th><th>Complete cash</th><th>Known cash</th><th>Closed trade P/L</th><th>Wins / losses</th><th>Contracts</th><th>Fees</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(dayLabel(r.day))}</td><td>${amount(r.ending_cash)}</td><td>${amount(r.known_cash)}</td><td>${amount(tradeRows([r])[0].supported_trade_pnl)}</td><td>${number(r.wins)} / ${number(r.losses)}</td><td>${number(r.contracts)}</td><td>${amount(r.fees)}</td></tr>`).join('')}</tbody></table></div><p>${esc(assumptions.join(' · ')||'All archive source hashes and assumptions are retained with the saved results.')}</p><p>A win is a completed profitable trade or daily basket after fees, using each strategy’s original counting rule. Cash follows each strategy’s recorded reporting and payout times; trade profit is assigned to the entry day after its exit or settlement is known. NWS physical forecasts are not yet verified as the same target as The Weather Company settlement.</p></details></section>`;
 $('#analyze').onclick=async()=>{const b=$('#analyze');b.disabled=true;try{const r=await fetch('/api/offline/start',{method:'POST'});const value=await r.json();if(!r.ok)throw Error(value.error);offline=value;renderOffline();scheduleOffline(1200);}catch(e){b.disabled=false;$('#notice').textContent=e.message;}};
 $('#offline-city').onchange=e=>{offlineCity=e.target.value;offlineWallet='';renderOffline();};
 $('#offline-method').onchange=e=>{offlineWallet=e.target.value;renderOffline();};
 document.querySelectorAll('[data-offline-wallet]').forEach(b=>b.onclick=()=>{offlineWallet=b.dataset.offlineWallet;renderOffline();$('#offline-method').scrollIntoView({block:'center',behavior:'smooth'});});
 $('#archive-login').onclick=showArchiveLogin;
}
function showArchiveLogin(){
 const dialog=document.createElement('dialog');dialog.className='archive-dialog';dialog.innerHTML=`<form id="archive-form"><h2>Read-only archive connection</h2><p class="muted">Use your Probalytics read-only database login. It stays in this server’s memory and is released when the server stops.</p><label>Username<input name="username" required autocomplete="username"></label><label>Password<input name="password" type="password" required autocomplete="current-password"></label><p id="login-status" role="status"></p><div class="offline-actions"><button type="button" id="close-login">Cancel</button><button type="submit" class="primary">Accept & connect read-only</button></div></form>`;
 document.body.append(dialog);dialog.showModal();dialog.querySelector('#close-login').onclick=()=>{dialog.close();dialog.remove();};
 dialog.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=e.target,button=form.querySelector('[type=submit]');button.disabled=true;
  try{const r=await fetch('/api/offline/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.elements.username.value,password:form.elements.password.value})});const result=await r.json();if(!r.ok)throw Error(result.error);form.reset();dialog.close();dialog.remove();await updateOffline();}catch(error){dialog.querySelector('#login-status').textContent=error.message;button.disabled=false;}};
}
async function updateOffline(){try{const r=await fetch('/api/offline',{cache:'no-store'});if(!r.ok)throw Error('Offline history server unavailable');offline=await r.json();if(view==='offline')renderOffline();}catch(e){if(view==='offline')$('#notice').textContent='Offline history server unavailable. Previously saved results remain on disk.';}}
function scheduleOffline(delay=5000){clearTimeout(offlineTimer);offlineTimer=setTimeout(async()=>{await updateOffline();scheduleOffline(['RUNNING','QUEUED'].includes(offline?.job?.status)?2500:15000);},delay);}
document.querySelector('[data-view=offline]').onclick=async()=>{view='offline';renderOffline();await updateOffline();};
updateOffline();scheduleOffline();
if(new URLSearchParams(location.search).get('tab')==='offline'){view='offline';renderOffline();}
