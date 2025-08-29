(function(){
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
    }catch(e){/* ignore */}
  }
  function delTarget(t){
    fetch('/api/pings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'delete', target:t})})
      .then(()=>update());
  }
  window.addEventListener('DOMContentLoaded', function(){
    update();
    setInterval(update, 1000);
    const body=document.getElementById('pingBody');
    if(body){
      body.addEventListener('click', function(ev){
        const el=ev.target;
        if(el && el.matches('button.rm')){ ev.preventDefault(); delTarget(el.dataset.t); }
      });
    }
  });
})();
