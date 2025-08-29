(function(){
  let timer=null;
  let intervalMs = parseInt(localStorage.getItem('ls.refreshMs')||'1000',10);
  let paused = localStorage.getItem('ls.paused')==='1';
  let theme = localStorage.getItem('ls.theme')||'light';
  function setTheme(t){ theme=t; document.documentElement.setAttribute('data-theme', t==='dark'?'dark':'light'); localStorage.setItem('ls.theme', theme); const b=document.getElementById('themeBtn'); if(b) b.textContent=(theme==='dark'?'Light':'Dark'); }
  function setIntervalMs(ms){ intervalMs=ms; localStorage.setItem('ls.refreshMs', String(ms)); const lab=document.getElementById('refreshLabel'); if(lab) lab.textContent=(ms/1000).toFixed(1)+'s'; const rng=document.getElementById('refreshMs'); if(rng) rng.value=String(ms); restartTimer(); }
  function setPaused(p){ paused=!!p; localStorage.setItem('ls.paused', paused?'1':'0'); const btn=document.getElementById('pauseBtn'); if(btn) btn.textContent = paused ? 'Resume' : 'Pause'; restartTimer(); }
  function restartTimer(){ if(timer){ clearInterval(timer); timer=null; } if(!paused){ timer=setInterval(update, intervalMs); } }
  function esc(s){
    return (s==null?"":String(s))
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
  }
  async function update(){
    try{
      const res = await fetch('/status.json', {cache:'no-store'});
      if(!res.ok) return;
      const d = await res.json();
      const set=(id,txt)=>{const el=document.getElementById(id); if(el) el.textContent=(txt==null?'' : String(txt));};
      set('updatedAt', d.now_local);
      set('hostname', d.hostname);
      set('ipList', (d.ip_list||[]).join(', '));
      const tbody=document.getElementById('pingBody');
      if(tbody){
        let html='';
        const pings=d.pings||{};
        const order=d.pings_order||Object.keys(pings);
        for(const t of order){
          const p=pings[t]||{}; const ok=!!p.ok; const color= ok?'#22aa22':'#cc2222';
          const lat=(p.latency_ms!=null)?(p.latency_ms.toFixed(2)+' ms'):'—';
          const misses=p.misses||0; const when=p.ts_fmt||'—';
          html += '<tr><td>'+esc(t)+'</td><td style="color:'+color+';font-weight:bold">'+(ok?'OK':'FAIL')+'</td><td>'+lat+'</td><td>'+misses+'</td><td>'+esc(when)+'</td><td><button class="rm" data-t="'+esc(t)+'">Delete</button></td></tr>';
        }
        tbody.innerHTML=html;
      }
      // services
      const svcb=document.getElementById('svcBody');
      if(svcb){
        let html='';
        const sv=d.services||{}; const order=d.services_order||Object.keys(sv);
        for(const t of order){
          const p=sv[t]||{}; const ok=!!p.ok; const color= ok?'#22aa22':'#cc2222';
          const lat=(p.latency_ms!=null)?(p.latency_ms.toFixed(2)+' ms'):'—';
          const misses=p.misses||0; const when=p.ts_fmt||'—';
          html += '<tr><td>'+esc(t)+'</td><td style="color:'+color+';font-weight:bold">'+(ok?'UP':'DOWN')+'</td><td>'+lat+'</td><td>'+misses+'</td><td>'+esc(when)+'</td><td><button class="rmsvc" data-t="'+esc(t)+'">Delete</button></td></tr>';
        }
        svcb.innerHTML=html;
      }
      set('hbFile', d.heartbeat && d.heartbeat.path);
      set('hbWhen', d.heartbeat && d.heartbeat.last_write_fmt);
      set('hbBytes', d.heartbeat && d.heartbeat.bytes_written);
      const hbErr=document.getElementById('hbErr');
      if(hbErr){
        const err=d.heartbeat && d.heartbeat.last_error;
        hbErr.style.display = err ? 'block' : 'none';
        hbErr.textContent = err || '';
      }
      set('diskUsage', 'Disk usage ('+d.disk.path+'): total '+d.disk.total_h+', used '+d.disk.used_h+', free '+d.disk.free_h);
      set('eventsPath', d.events && d.events.path);
      const pre=document.getElementById('eventsPre'); if(pre) pre.textContent=(d.events && d.events.tail) || '';
      const sys=document.getElementById('sysLine');
      if(sys && d.system){
        const up = (d.system.proc_uptime_s||0); const mins = Math.floor(up/60); const hrs=Math.floor(mins/60); const days=Math.floor(hrs/24);
        document.getElementById('procUptime').textContent = 'Up '+(days>0?(days+'d '):'')+((hrs%24)+'h ')+((mins%60)+'m');
        const l1=d.system.load1!=null?d.system.load1.toFixed(2):'—';
        const l5=d.system.load5!=null?d.system.load5.toFixed(2):'—';
        const l15=d.system.load15!=null?d.system.load15.toFixed(2):'—';
        sys.textContent = 'Load avg: '+l1+', '+l5+', '+l15;
      }
    }catch(e){/* ignore */}
  }
  function delTarget(t){
    fetch('/api/pings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'delete', target:t})})
      .then(()=>update());
  }
  function delSvc(t){
    fetch('/api/services', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'delete', target:t})})
      .then(()=>update());
  }
  window.addEventListener('DOMContentLoaded', function(){
    setTheme(theme);
    const rng=document.getElementById('refreshMs'); if(rng){ rng.value=String(intervalMs); }
    const lab=document.getElementById('refreshLabel'); if(lab){ lab.textContent=(intervalMs/1000).toFixed(1)+'s'; }
    const pb=document.getElementById('pauseBtn'); if(pb){ pb.textContent= paused ? 'Resume' : 'Pause'; pb.addEventListener('click', function(){ setPaused(!paused); }); }
    const tb=document.getElementById('themeBtn'); if(tb){ tb.textContent= (theme==='dark'?'Light':'Dark'); tb.addEventListener('click', function(){ setTheme(theme==='dark'?'light':'dark'); }); }
    if(rng){ rng.addEventListener('input', function(){ setIntervalMs(parseInt(rng.value,10)||1000); }); }
    update();
    restartTimer();
    const body=document.getElementById('pingBody');
    if(body){
      body.addEventListener('click', function(ev){
        const el=ev.target;
        if(el && el.matches('button.rm')){ ev.preventDefault(); delTarget(el.dataset.t); }
      });
    }
    const svcb=document.getElementById('svcBody');
    if(svcb){ svcb.addEventListener('click', function(ev){ const el=ev.target; if(el && el.matches('button.rmsvc')){ ev.preventDefault(); delSvc(el.dataset.t); } }); }
    const addSvc=document.getElementById('addSvcForm');
    if(addSvc){ addSvc.addEventListener('submit', function(ev){ ev.preventDefault(); const t=document.getElementById('newSvc').value.trim(); if(!t) return; fetch('/api/services',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({target:t})}).then(()=>{ document.getElementById('newSvc').value=''; update(); }); }); }
  });
})();
