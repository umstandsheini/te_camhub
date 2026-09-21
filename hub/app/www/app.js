"use strict";
const $=s=>document.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const api=async(path,opts)=>{const r=await fetch(path,Object.assign({credentials:"same-origin"},opts||{}));
  if(r.status===401){boot();throw new Error("auth");}return r;};
const jget=async p=>(await api(p)).json();
const jpost=async(p,b)=>(await api(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})})).json();
function toast(t){const el=$("#toast");el.textContent=t;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2200);}

/* ---------------- auth ---------------- */
let SETUP=false;
async function boot(){
  const st=await (await fetch("api/vault/status",{credentials:"same-origin"})).json();
  if(st.session){startApp();return;}
  showAuth(st);
}
function showAuth(st){
  $("#app").classList.add("hidden");$("#auth").classList.remove("hidden");
  st=st||{};
  SETUP=!st.has_vault;
  $("#auth_title").textContent=SETUP?"Set up the vault":"TeslaCam Hub";
  $("#auth_sub").textContent=SETUP?"Choose a password. Without it, the recordings can't be decrypted.":"Please sign in.";
  $("#auth_pass2").classList.toggle("hidden",!SETUP);
  $("#auth_import_l").classList.toggle("hidden",!SETUP);
  $("#auth_go").textContent=SETUP?"Set up":"Sign in";
  $("#auth_pass").value="";$("#auth_pass2").value="";$("#auth_msg").textContent="";
  $("#auth_forgot").classList.toggle("hidden",SETUP);
  $("#auth_reset_panel").classList.add("hidden");
  $("#auth_reset_confirm").value="";$("#auth_reset_msg").textContent="";
  $("#auth_pass").focus();
}
$("#auth_forgot").onclick=(e)=>{e.preventDefault();$("#auth_reset_panel").classList.toggle("hidden");};
$("#auth_reset_go").onclick=async()=>{
  const m=$("#auth_reset_msg");m.className="msg";
  if($("#auth_reset_confirm").value!=="RESET"){m.className="msg err";m.textContent="Please type RESET exactly";return;}
  m.textContent="resetting…";
  try{
    const r=await jpost("api/vault/factory_reset",{confirm:$("#auth_reset_confirm").value});
    if(r.ok){toast("Vault reset");boot();}
    else{m.className="msg err";m.textContent=r.error||"Error";}
  }catch(e){m.className="msg err";m.textContent="Connection error";}
};
async function doAuth(){
  const m=$("#auth_msg");m.className="msg";
  const p=$("#auth_pass").value;if(!p){m.textContent="Password required";return;}
  try{
    if(SETUP){
      if(p!==$("#auth_pass2").value){m.className="msg err";m.textContent="Passwords don't match";return;}
      m.textContent="Setting up…";
      const r=await jpost("api/setup",{pass:p,import:$("#auth_import").checked});
      if(r.ok){startApp();toast("Vault set up ("+(r.imported||0)+" keys)");}
      else{m.className="msg err";m.textContent=r.error||"Error";}
    }else{
      m.textContent="Signing in…";
      const r=await jpost("api/login",{pass:p});
      if(r.ok)startApp();else{m.className="msg err";m.textContent=r.error||"wrong password";}
    }
  }catch(e){m.className="msg err";m.textContent="Connection error";}
}

/* ---------------- shell ---------------- */
function startApp(){
  $("#auth").classList.add("hidden");$("#app").classList.remove("hidden");
  render("clips");
  checkHubUpdateHint();
  startHeadline();
}
document.querySelectorAll("nav .nav[data-view]").forEach(a=>a.onclick=()=>{
  document.querySelectorAll("nav .nav").forEach(n=>n.classList.remove("active"));
  a.classList.add("active");render(a.dataset.view);
});
$("#lockbtn").onclick=async()=>{try{await jpost("api/logout",{});}catch(e){}if(headlineTimer)clearInterval(headlineTimer);const h=$("#headline");if(h)h.remove();boot();};
$("#auth_go").onclick=doAuth;
["auth_pass","auth_pass2"].forEach(id=>$("#"+id).addEventListener("keydown",e=>{if(e.key==="Enter")doAuth();}));

/* ---------------- Header: at the car? NAS up? ---------------- */
let headlineTimer=null;
function headlineBar(){
  let b=$("#headline");
  if(!b){
    b=el("div",null,"");b.id="headline";b.className="headline";
    b.innerHTML=`<span class="hchip hcar" title="loading…"><span class="hic">🚗</span><span class="htxt">…</span></span>
      <span class="hchip hnas" title="loading…"><span class="hic">🗄️</span><span class="htxt">…</span></span>`;
    document.body.append(b);
    b.querySelector(".hcar").onclick=()=>gotoView("ble");
    b.querySelector(".hnas").onclick=()=>gotoView("clips");
  }
  return b;
}
function gotoView(v){
  const a=document.querySelector(`nav .nav[data-view="${v}"]`);
  if(a)a.click();
}
async function refreshHeadline(){
  const b=headlineBar();
  const ago=t=>{if(!t)return "never";const m=Math.round((Date.now()/1000-t)/60);
    return m<1?"just now":m<60?`${m} min ago`:`${Math.floor(m/60)} h ago`;};
  let s;
  try{s=await jget("api/headline");}
  catch(e){return;}
  const car=b.querySelector(".hcar"),nas=b.querySelector(".hnas");
  if(!s.car_configured){
    car.className="hchip hcar off";car.querySelector(".htxt").textContent="Car?";
    car.title="No VIN set or no BLE key paired – the Hub can't check whether it is at the car.";
  }else if(s.in_car===true){
    car.className="hchip hcar ok";car.querySelector(".htxt").textContent="at car";
    car.title=`Paired vehicle confirmed over Bluetooth (${ago(s.car_last_seen)}).`
      +(s.usb_host?" USB drives are mounted by a host.":"")+" Click for details.";
  }else if(s.in_car===false){
    car.className="hchip hcar warn";car.querySelector(".htxt").textContent="Car?";
    car.title=`Vehicle not answering over Bluetooth (last confirmed ${ago(s.car_last_seen)}). `
      +"That doesn't necessarily mean the Hub is gone – a sleeping or distant car doesn't answer either.";
  }else{
    car.className="hchip hcar off";car.querySelector(".htxt").textContent="Car?";
    car.title="Not checked yet.";
  }
  if(!s.nas_configured){
    nas.className="hchip hnas off";nas.querySelector(".htxt").textContent="no NAS";
    nas.title="No archive server configured (Settings → NAS).";
  }else if(s.nas_ok){
    nas.className="hchip hnas ok";nas.querySelector(".htxt").textContent="NAS up";
    nas.title=`Trusted NAS reachable (checked ${ago(s.nas_checked)})`
      +(s.nas_paired?", paired":"")+`. Archived: ${s.nas_percent}%. Click for the recordings.`;
  }else if(s.nas_ok===false){
    nas.className="hchip hnas bad";nas.querySelector(".htxt").textContent="NAS down";
    nas.title=`NAS not reachable: ${s.nas_error||"unknown error"} (checked ${ago(s.nas_checked)}).`;
  }else{
    nas.className="hchip hnas off";nas.querySelector(".htxt").textContent="NAS?";
    nas.title="Not checked yet.";
  }
}
function startHeadline(){
  refreshHeadline();
  if(headlineTimer)clearInterval(headlineTimer);
  headlineTimer=setInterval(refreshHeadline,60000);
}
function render(view){
  const m=$("#main");m.innerHTML="";
  if(view==="clips")return viewClips(m);
  if(view==="files")return viewFiles(m,"");
  if(view==="assistant")return viewAssistant(m);
  if(view==="videos")return viewVideos(m);
  if(view==="diag")return viewDiag(m);
  if(view==="ble")return viewBle(m);
  if(view==="canbus")return viewCanbus(m);
  if(view==="trips")return viewTrips(m);
  if(view==="settings")return viewSettings(m);
}

/* ---------------- Recordings ---------------- */
async function viewClips(m){
  m.innerHTML="";
  m.append(el("h2","title","Recordings"));
  const info=el("div","sub","loading…");m.append(info);
  const nasrow=el("div","sub nasrow","NAS archive: loading…");m.append(nasrow);
  refreshNasStatus(nasrow);
  const bar=el("div","saverow");
  const bulkbtn=el("button","btn sm","🔓 Decrypt all + build metadata");
  const bulkmsg=el("span","note","");
  const syncbtn=el("button","btn sm ghost","🔄 Sync now");
  const syncmsg=el("span","note","");
  bar.append(bulkbtn,bulkmsg,syncbtn,syncmsg);m.append(bar);
  const grid=el("div","clipgrid");m.append(grid);
  let clips;try{clips=await jget("api/clips");}catch(e){return;}
  const enc=clips.filter(c=>c.encrypted).length;
  info.textContent=`${clips.length} clips · ${enc} encrypted`;
  let nasClips={};try{nasClips=(await jget("api/nas/sync_status")).clips||{};}catch(e){}
  if(!clips.length){info.textContent="No recordings found.";return;}
  syncbtn.onclick=async()=>{
    syncbtn.disabled=true;syncmsg.textContent="starting archiving…";
    try{await jpost("api/sync",{});}catch(e){}
    syncmsg.textContent="Archiving running (the car briefly drops the USB connection)…";
    setTimeout(async()=>{
      await jpost("api/nas/sync_status/refresh",{});
      setTimeout(()=>{syncbtn.disabled=false;syncmsg.textContent="✓ triggered";toast("Sync triggered, status updates shortly");viewClips(m);},15000);
    },20000);
  };
  bulkbtn.onclick=async()=>{
    bulkbtn.disabled=true;bulkmsg.textContent="starting…";
    let st;try{st=await jpost("api/bulk_prepare",{});}catch(e){bulkbtn.disabled=false;bulkmsg.textContent="✗ error";return;}
    const poll=async()=>{
      try{st=await jget("api/bulk_prepare");}catch(e){return;}
      if(st.running){
        bulkmsg.textContent=st.total?`${st.done} / ${st.total}…`:"searching clips…";
        setTimeout(poll,1500);
      }
      else{
        bulkbtn.disabled=false;
        bulkmsg.textContent=st.errors&&st.errors.length?`done, ${st.errors.length} errors`:(st.total?"✓ done":"nothing to do");
        toast("Decryption finished");
        viewClips(m);
      }
    };
    setTimeout(poll,1200);
  };
  clips.forEach(c=>{
    const card=el("div","clip"+(c.is_trigger?" trigger":""));
    const th=el("div","thumb");th.append(el("div","ph","🎞️"));card.append(th);
    // A thumbnail only needs the front camera decrypted -- attempt it
    // whenever a key is available (state "key"), not just once the whole
    // clip has been fully prepared/cached ("ready"/"plain"). make_thumb()
    // on the backend already knows how to decrypt just that one camera
    // on demand; only a truly key-less front camera can't produce one.
    const frontState=c.cameras&&c.cameras.front&&c.cameras.front.state;
    const src=(!frontState||frontState==="locked")?null:"api/thumb?id="+encodeURIComponent(c.id);
    if(src){const img=new Image();img.onload=()=>{th.style.backgroundImage=`url(${src})`;th.querySelector(".ph").remove();};img.src=src;}
    const meta=el("div","meta");
    meta.append(el("div","t",c.timestamp.replace("_"," ").replace(/-/g,(x,i)=>i<7?"-":":")));
    const b=el("div","badges");
    b.append(el("span","badge "+(c.encrypted?"enc":"plain"),c.encrypted?"🔒 encrypted":"plain"));
    // Every segment of an event folder carries has_event, but only one of
    // them (is_trigger) is the segment the trigger actually happened in --
    // give that one a distinct 🎯 badge instead of the same "Event" label
    // on every segment (ported from Te_FITI's trigger-segment marking).
    if(c.is_trigger){
      const at=c.event_at!=null?` at ${Math.floor(c.event_at/60)}:${String(Math.round(c.event_at%60)).padStart(2,"0")}`:"";
      b.append(el("span","badge trigger",(c.reason?c.reason+" ":"")+"🎯 trigger"+at));
    }else if(c.has_event){
      b.append(el("span","badge event",(c.reason||"Event")+" (other segment)"));
    }
    if(c.encrypted){
      const kk=c.cams_keyed||0,kt=c.cams_encrypted||0;
      const keyCls=kt===0?"locked":kk===0?"locked":kk<kt?"keypartial":"keyok";
      const keyTxt=kk===0?"🔑 no key":kk<kt?`🔑 ${kk}/${kt} cameras`:"🔑 key available";
      b.append(el("span","badge "+keyCls,keyTxt));
    }
    const ns=nasClips[c.id];
    b.append(el("span","badge "+(ns==="deleted"?"nasdel":ns?"nasok":"nasno"),ns==="deleted"?"🗑 deleted on NAS":ns?"☁️ on NAS":"☁️ not yet"));
    meta.append(b);card.append(meta);
    card.onclick=()=>openClip(c);
    grid.append(card);
  });
}
async function refreshNasStatus(nasrow){
  let p={};try{p=await jget("api/nas/archive_progress");}catch(e){}
  // A live transfer (teslausb's own native archiver, not this Hub's sync
  // functions -- see nassync.py's archive_progress()) takes priority over
  // the periodic coverage check below: while it's actually moving files,
  // that's more useful and more honest than "nicht erreichbar" from a
  // coverage-check mount that's simply waiting its turn on a busy NAS
  // connection.
  if(p.running&&p.total){
    nasrow.innerHTML="";
    const pct=Math.round((p.done||0)*100/p.total);
    nasrow.append(el("span",null,`NAS archive: transferring… ${p.done||0} / ${p.total} files (${pct}%) `));
    const bar=el("div","progressbar");const fill=el("div","fill");fill.style.width=pct+"%";bar.append(fill);
    nasrow.append(bar);
    setTimeout(()=>refreshNasStatus(nasrow),4000);
    return;
  }
  // A run that ended with an error (connectionmonitor killed rsync after
  // the NAS stopped answering -- see archive_progress()'s docstring) still
  // made real progress up to that point; say so explicitly rather than
  // falling through to the generic coverage-check error below, which
  // reads as "nothing is happening" even though most files did transfer.
  if(p.error&&p.total){
    nasrow.innerHTML="";
    nasrow.append(el("span",null,`NAS archive: transfer interrupted (network) at ${p.done||0} / ${p.total} files -- will retry automatically `));
    const rl=el("a",null,"check now");
    rl.onclick=()=>refreshNasStatus(nasrow);
    nasrow.append(rl);
    return;
  }
  let s;try{s=await jget("api/nas/sync_status");}catch(e){return;}
  nasrow.innerHTML="";
  if(s.ok===null){nasrow.append(el("span",null,"NAS archive: not checked yet "));}
  else if(!s.ok){nasrow.append(el("span",null,"NAS archive: not reachable ("+(s.error||"error")+") "));}
  else{nasrow.append(el("span",null,`NAS archive: ${s.percent}% archived (${s.on_nas}/${s.total} clips${s.deleted?`, ${s.deleted} deleted on NAS`:""}${s.requeued?`, ${s.requeued} files being re-transferred`:""}) `));}
  const rl=el("a",null,"check now");
  rl.onclick=async()=>{nasrow.querySelector("span").textContent="NAS archive: checking…";await jpost("api/nas/sync_status/refresh",{});setTimeout(()=>refreshNasStatus(nasrow),20000);};
  nasrow.append(rl);
}
/* ---------------- Player: synced multi-cam, event-seek, GPS map, HUD ---------------- */
const CAMS=[["front","Front","a-front"],["back","Rear","a-back"],
  ["left_repeater","Left","a-left"],["right_repeater","Right","a-right"],
  ["left_pillar","Left (pillar)","a-lp"],["right_pillar","Right (pillar)","a-rp"]];
const REASON_LABELS={
  user_interaction_dashcam_icon_tapped:"Dashcam button",
  user_interaction_dashcam_panel_save:"saved manually",
  sentry_aware_object_detection:"Sentry: object detected",
  sentry_aware_accel_detection:"Sentry: impact",
  sentry_aware_alarm_state:"Sentry: alarm",
  honk:"Honk"};
let PLAYER={videos:[],master:null,raf:0,tele:null,gpsPts:[],lmap:null,lmark:null,ltrack:null,event:null,initialSeek:null,cid:null};

function pSlaves(fn){PLAYER.videos.forEach(v=>{if(v!==PLAYER.master)fn(v);});}
function pFmt(t){t=Math.max(0,t||0);const m=Math.floor(t/60),s=Math.floor(t%60);return m+":"+String(s).padStart(2,"0");}

function pClearStage(){
  cancelAnimationFrame(PLAYER.raf);
  PLAYER.videos=[];PLAYER.master=null;PLAYER.tele=null;PLAYER.gpsPts=[];
  PLAYER.lmark=null;PLAYER.ltrack=null;PLAYER.event=null;PLAYER.initialSeek=null;
  if(PLAYER.lmap){PLAYER.lmap.remove();PLAYER.lmap=null;}
}

function pEnsureMap(){
  if(PLAYER.lmap||!window.L)return;
  PLAYER.lmap=L.map($("#pmap"),{attributionControl:false}).setView([0,0],2);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19}).addTo(PLAYER.lmap);
}
function pDrawTrack(){
  if(!PLAYER.lmap)return;
  if(PLAYER.ltrack){PLAYER.lmap.removeLayer(PLAYER.ltrack);PLAYER.ltrack=null;}
  if(PLAYER.lmark){PLAYER.lmap.removeLayer(PLAYER.lmark);PLAYER.lmark=null;}
  if(!PLAYER.gpsPts.length)return;
  PLAYER.ltrack=L.polyline(PLAYER.gpsPts,{color:"#e63946",weight:3}).addTo(PLAYER.lmap);
  PLAYER.lmark=L.circleMarker(PLAYER.gpsPts[0],{radius:6,color:"#4aa3ff",fillColor:"#4aa3ff",fillOpacity:1}).addTo(PLAYER.lmap);
  PLAYER.lmap.fitBounds(PLAYER.ltrack.getBounds(),{padding:[20,20]});
}
function pShowMap(on){
  const box=$("#pmapbox");if(!box)return;
  box.classList.toggle("hidden",!on);
  if(on){pEnsureMap();pDrawTrack();setTimeout(()=>PLAYER.lmap&&PLAYER.lmap.invalidateSize(),150);}
}
function pMapMarker(f){if(PLAYER.lmark&&f&&f.lat&&f.lon)PLAYER.lmark.setLatLng([f.lat,f.lon]);}

function pHud(f){
  if(!f)return;
  $("#h-gear").textContent=f.gear||"–";
  $("#h-spd").textContent=Math.round(Math.abs(f.speed_kmh||0));
  $("#h-l").classList.toggle("on",!!f.blink_l);
  $("#h-r").classList.toggle("on",!!f.blink_r);
  $("#h-accel-fill").style.height=Math.max(0,Math.min(100,(f.accel||0)*10))+"%";
  $("#h-brake").classList.toggle("on",!!f.brake);
  $("#h-ap").style.display=(f.autopilot>0)?"flex":"none";
  const steer=f.steer||0;
  $("#h-wheel").style.transform="rotate("+steer+"deg)";
  $("#h-wheel").classList.toggle("on",Math.abs(steer)>3);
}
function pNerdLines(f){
  const l=[];
  if(f)l.push(`t=${f.t}s v=${(f.speed_kmh||0).toFixed(1)}km/h gear=${f.gear} steer=${(f.steer||0).toFixed(1)}° accel=${(f.accel||0).toFixed(1)} brake=${f.brake} blink=${f.blink_l?"L":""}${f.blink_r?"R":""} ap=${f.autopilot} gps=${f.lat||"–"},${f.lon||"–"} heading=${f.heading||"–"}`);
  if(PLAYER.event){
    const e=PLAYER.event;
    l.push(`Event: ${REASON_LABELS[e.reason]||e.reason||"–"}${e.city?" @ "+e.city+(e.street?" / "+e.street:""):""}${(e.seek!=null)?` (t=${e.seek.toFixed(1)}s)`:""}`);
  }
  return l.join("\n");
}

function pLoop(){
  const m=PLAYER.master;if(!m)return;
  const t=m.currentTime;
  pSlaves(v=>{if(Math.abs(v.currentTime-t)>0.12)v.currentTime=t;});
  const seekEl=$("#pseek"),timeEl=$("#ptime");
  if(seekEl&&!seekEl.matches(":active"))seekEl.value=Math.floor(t*1000);
  if(timeEl)timeEl.textContent=pFmt(t)+" / "+pFmt(m.duration||0);
  if(PLAYER.tele&&PLAYER.tele.frame_count){
    const i=Math.min(PLAYER.tele.frame_count-1,Math.max(0,Math.round(t*PLAYER.tele.fps)));
    const fr=PLAYER.tele.frames[i];
    if($("#t_hud")&&$("#t_hud").checked)pHud(fr);
    if($("#t_nerd")&&$("#t_nerd").checked){$("#pnerd").textContent=pNerdLines(fr);$("#pnerd").style.display="block";}
    else if($("#pnerd"))$("#pnerd").style.display="none";
    if($("#t_map")&&$("#t_map").checked)pMapMarker(fr);
  }
  PLAYER.raf=requestAnimationFrame(pLoop);
}

function pSetupMaster(){
  const m=PLAYER.master;if(!m)return;
  m.onloadedmetadata=()=>{
    $("#pseek").max=Math.floor((m.duration||0)*1000)||1000;
    if(PLAYER.initialSeek!=null){m.currentTime=PLAYER.initialSeek;pSlaves(v=>v.currentTime=PLAYER.initialSeek);}
  };
  m.onplay=()=>{pSlaves(v=>v.play().catch(()=>{}));$("#pplay").textContent="⏸";PLAYER.raf=requestAnimationFrame(pLoop);};
  m.onpause=()=>{pSlaves(v=>v.pause());$("#pplay").textContent="▶";cancelAnimationFrame(PLAYER.raf);};
}

function pUpdateTelControls(c){
  const hasT=!!(PLAYER.tele&&PLAYER.tele.frame_count);
  const hasGps=PLAYER.gpsPts.length>0;
  $("#tc_hud").classList.toggle("hidden",!hasT);
  $("#tc_nerd").classList.toggle("hidden",!hasT&&!PLAYER.event);
  $("#tc_map").classList.toggle("hidden",!hasGps);
  $("#hud").style.display=(hasT&&$("#t_hud").checked)?"flex":"none";
}

async function openClip(c){
  pClearStage();
  PLAYER.cid=c.id;
  const wrap=el("div","player");
  const bar=el("div","bar");
  bar.innerHTML=`<b>${c.timestamp.replace("_"," ")}</b>
    <span class="note" id="pstatus">loading…</span>
    <label class="tc hidden" id="tc_hud"><input type="checkbox" id="t_hud" checked> HUD</label>
    <label class="tc hidden" id="tc_map"><input type="checkbox" id="t_map"> Map</label>
    <label class="tc hidden" id="tc_nerd"><input type="checkbox" id="t_nerd"> Debug</label>
    <select id="prate" class="ratesel"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
    <button class="btn sm ghost" id="pfull">⛶</button>
    <button class="x" id="pclose">✕</button>`;
  wrap.append(bar);
  const stage=el("div","stage");
  const grid=el("div","grid");stage.append(grid);
  const mapbox=el("div","mapbox hidden");mapbox.id="pmapbox";
  mapbox.innerHTML=`<div id="pmap"></div>`;stage.append(mapbox);
  const hud=el("div","hud");hud.id="hud";
  hud.innerHTML=`
    <div class="h-item h-turn" id="h-l">◀</div>
    <div class="h-item h-gear" id="h-gear">–</div>
    <div class="h-item h-speed"><span id="h-spd">0</span><small>km/h</small></div>
    <div class="h-item h-wheel" id="h-wheel">🎡</div>
    <div class="h-item h-accel"><div class="h-accel-fill" id="h-accel-fill"></div></div>
    <div class="h-item h-brake" id="h-brake">🛑</div>
    <div class="h-item h-ap" id="h-ap" style="display:none">AP</div>
    <div class="h-item h-turn" id="h-r">▶</div>`;
  stage.append(hud);
  const nerd=el("pre","nerd");nerd.id="pnerd";stage.append(nerd);
  wrap.append(stage);
  const transport=el("div","transport");
  transport.innerHTML=`<button class="btn sm" id="pplay">▶</button>
    <input type="range" id="pseek" value="0" min="0" max="1000">
    <span class="note" id="ptime">0:00 / 0:00</span>`;
  wrap.append(transport);
  document.body.append(wrap);
  $("#pclose").onclick=()=>{pClearStage();wrap.remove();};
  document.addEventListener("keydown",function esc(e){
    if(!document.body.contains(wrap)){document.removeEventListener("keydown",esc);return;}
    if(e.key==="Escape"){pClearStage();wrap.remove();document.removeEventListener("keydown",esc);}
    else if(e.key===" "){e.preventDefault();PLAYER.master&&(PLAYER.master.paused?PLAYER.master.play():PLAYER.master.pause());}
    else if(e.key==="ArrowRight"&&PLAYER.master){PLAYER.master.currentTime+=5;pSlaves(v=>v.currentTime=PLAYER.master.currentTime);}
    else if(e.key==="ArrowLeft"&&PLAYER.master){PLAYER.master.currentTime-=5;pSlaves(v=>v.currentTime=PLAYER.master.currentTime);}
  });
  $("#pfull").onclick=()=>{document.fullscreenElement?document.exitFullscreen():wrap.requestFullscreen();};
  $("#pplay").onclick=()=>{if(!PLAYER.master)return;PLAYER.master.paused?PLAYER.master.play():PLAYER.master.pause();};
  $("#pseek").oninput=()=>{if(!PLAYER.master)return;const t=$("#pseek").value/1000;PLAYER.master.currentTime=t;pSlaves(v=>v.currentTime=t);};
  $("#prate").onchange=()=>{if(!PLAYER.master)return;const r=+$("#prate").value;PLAYER.master.playbackRate=r;pSlaves(v=>v.playbackRate=r);};
  $("#t_hud").onchange=()=>pUpdateTelControls(c);
  $("#t_map").onchange=()=>pShowMap($("#t_map").checked);

  // event: fetch event.json seek offset if this clip has one
  if(c.has_event){
    try{PLAYER.event=await jget("api/event?id="+encodeURIComponent(c.id));}catch(e){PLAYER.event=null;}
    if(PLAYER.event&&PLAYER.event.seek!=null)PLAYER.initialSeek=PLAYER.event.seek;
    if(PLAYER.event&&PLAYER.event.lat&&PLAYER.event.lon)PLAYER.gpsPts=[[PLAYER.event.lat,PLAYER.event.lon]];
  }

  const status=$("#pstatus");
  let res;
  try{res=await jpost("api/prepare",{id:c.id});}catch(e){status.textContent="✗ Connection error";return;}
  if(!res||!res.cameras){status.textContent="✗ "+(res&&res.error?res.error:"Error");return;}
  status.remove();

  let any=false;
  CAMS.forEach(([cam,label,area])=>{
    const info=res.cameras[cam];
    const tile=el("div","tile "+area);
    if(info&&(info.state==="ready"||info.state==="plain")){
      const v=document.createElement("video");v.controls=false;v.playsInline=true;v.muted=true;v.preload="auto";
      v.src=info.url;
      tile.append(v);
      const ctl=el("div","tilectl");
      const dl=el("a","iconbtn dlcam","⬇");dl.href=info.url;dl.download=cam+".mp4";dl.title="Download camera";
      const fs=el("button","iconbtn fscam","⛶");fs.title="Fullscreen";fs.onclick=(e)=>{e.stopPropagation();(v.requestFullscreen||v.webkitEnterFullscreen||function(){}).call(v);};
      ctl.append(dl,fs);tile.append(ctl);
      tile.append(el("div","tilelabel",label));
      grid.append(tile);
      PLAYER.videos.push(v);
      if(!PLAYER.master)PLAYER.master=v;
      any=true;
    }else{
      tile.classList.add("empty");
      if(info&&info.state==="locked")tile.innerHTML=`<div class="ic">🔒</div><div class="note">${label}</div>`;
      grid.append(tile);
    }
  });
  if(!any){grid.append(el("div","note","No video available – no key for this clip."));return;}
  if(res.errors&&res.errors.length)toast("Errors: "+res.errors.join(", "));

  pSetupMaster();

  // telemetry (HUD/map): prepare() may have just extracted it, so build the
  // URL directly rather than relying on the (pre-prepare) clip list snapshot
  const telUrl="media/"+encodeURI(c.folder+"/"+c.timestamp+"-front.telemetry.json");
  try{const t=await jget(telUrl);if(t&&t.frame_count)PLAYER.tele=t;}catch(e){}
  if(PLAYER.tele&&PLAYER.tele.frames){
    const pts=PLAYER.tele.frames.filter(f=>f.lat&&f.lon).map(f=>[f.lat,f.lon]);
    if(pts.length)PLAYER.gpsPts=pts;
  }
  pUpdateTelControls(c);
  if(PLAYER.gpsPts.length)$("#tc_map").classList.remove("hidden");
}

/* ---------------- Files ---------------- */
async function viewFiles(m,path){
  m.innerHTML="";
  m.append(el("h2","title","Files"));
  const crumbs=el("div","crumbs");m.append(crumbs);
  const bar=el("div","saverow");
  const up=el("label","btn sm","⬆️ Upload");const fin=el("input");fin.type="file";fin.className="hidden";fin.multiple=true;up.append(fin);
  const mk=el("button","btn sm ghost","➕ Folder");
  if(path)bar.append(up,mk);m.append(bar);
  const list=el("div","filelist");m.append(list);
  function crumbLinks(){
    crumbs.innerHTML="";
    const root=el("a",null,"🏠");root.onclick=()=>viewFiles($("#main"),"");crumbs.append(root);
    let acc="";path.split("/").filter(Boolean).forEach(seg=>{acc+=(acc?"/":"")+seg;const a=el("a",null,"/ "+seg);const cur=acc;a.onclick=()=>viewFiles($("#main"),cur);crumbs.append(a);});
  }
  crumbLinks();
  let data;try{data=await jget("api/files?path="+encodeURIComponent(path));}catch(e){return;}
  (data.entries||[]).sort((a,b)=>(b.dir-a.dir)||a.name.localeCompare(b.name)).forEach(ent=>{
    const it=el("div","fitem");
    const rel=(path?path+"/":"")+ent.name;
    it.append(el("div","ic",ent.dir?"📁":ent.image?"🖼️":ent.audio?"🎵":"📄"));
    const nm=el("div","nm",ent.name);it.append(nm);
    it.append(el("div","sz",ent.dir?"":human(ent.size)));
    const act=el("div","act");
    if(!ent.dir){const dl=el("button","iconbtn","⬇️");dl.title="Download";dl.onclick=e=>{e.stopPropagation();location.href="api/files/download?path="+encodeURIComponent(rel);};act.append(dl);}
    if(ent.audio&&rel.replace(/\\/g,"/").startsWith("Boombox/")&&ent.name.toLowerCase().endsWith(".wav")&&ent.size<=1048576){
      const lc=el("button","iconbtn","🔔");lc.title="Set as LockChime (overwrites Boombox/LockChime.wav)";
      lc.onclick=async e=>{e.stopPropagation();if(confirm("Set “"+ent.name+"” as LockChime? Overwrites Boombox/LockChime.wav.")){
        const r=await jpost("api/files/lockchime",{path:rel});
        if(r.ok){toast("LockChime set: "+ent.name);viewFiles($("#main"),path);}else{toast("✗ "+(r.error||"Error"));}
      }};
      act.append(lc);
    }
    const rn=el("button","iconbtn","✏️");rn.title="Rename";rn.onclick=async e=>{e.stopPropagation();const n=prompt("New name",ent.name);if(n){await jpost("api/files/rename",{path:rel,name:n});viewFiles($("#main"),path);}};
    const del=el("button","iconbtn","🗑️");del.title="Delete";del.onclick=async e=>{e.stopPropagation();if(confirm("Delete: "+ent.name+"?")){await jpost("api/files/delete",{path:rel});viewFiles($("#main"),path);}};
    act.append(rn,del);it.append(act);
    it.onclick=()=>{if(ent.dir)viewFiles($("#main"),rel);else if(ent.image)lightbox(rel,ent.name);else if(ent.audio)audioPlayer(rel,ent.name);};
    list.append(it);
  });
  mk.onclick=async()=>{const n=prompt("Folder name");if(n){await jpost("api/files/mkdir",{path:(path?path+"/":"")+n});viewFiles($("#main"),path);}};
  fin.onchange=async()=>{
    for(const f of fin.files){
      toast("Uploading "+f.name+"…");
      await api("api/files/upload?path="+encodeURIComponent(path)+"&name="+encodeURIComponent(f.name),{method:"PUT",body:f});
    }
    toast("Upload done");viewFiles($("#main"),path);
  };
}
function lightbox(rel,name){
  const lb=el("div","lightbox");
  lb.append(el("div","cap",name));
  const img=new Image();img.src="api/files/download?inline=1&path="+encodeURIComponent(rel);lb.append(img);
  lb.onclick=e=>{if(e.target===lb)lb.remove();};
  document.addEventListener("keydown",function esc(e){if(e.key==="Escape"){lb.remove();document.removeEventListener("keydown",esc);}});
  document.body.append(lb);
}
function audioPlayer(rel,name){
  document.querySelectorAll(".audiobar").forEach(x=>x.remove());
  const bar=el("div","audiobar");
  bar.innerHTML=`<span class="ic">🎵</span><span class="cap"></span>
    <audio controls autoplay></audio>
    <button class="x">✕</button>`;
  bar.querySelector(".cap").textContent=name;
  bar.querySelector("audio").src="api/files/download?inline=1&path="+encodeURIComponent(rel);
  bar.querySelector(".x").onclick=()=>bar.remove();
  document.body.append(bar);
}
function human(b){b=+b||0;const u=["B","KB","MB","GB"];let i=0;while(b>=1024&&i<3){b/=1024;i++;}return b.toFixed(i?1:0)+" "+u[i];}

/* ---------------- Assistant ---------------- */
let ASSIST_TIMER=null;
// Claude's replies can quote web pages: escape everything, then allow only
// http(s) links, **bold**, `code` and line breaks.
function mdLite(s){
  let h=String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  h=h.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  h=h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,'$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  h=h.replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>").replace(/`([^`]+)`/g,"<code>$1</code>");
  return h.replace(/\n/g,"<br>");
}
async function viewAssistant(m){
  if(ASSIST_TIMER){clearTimeout(ASSIST_TIMER);ASSIST_TIMER=null;}
  m.innerHTML="";
  m.append(el("h2","title","Assistant"));
  m.append(el("div","sub","Searches the web for light shows and Boombox sounds and writes them to the USB drives. Installing and deleting happen only after you confirm."));
  const keyBox=el("div","card");m.append(keyBox);
  const log=el("div","chatlog");m.append(log);
  const pend=el("div","card confirm hidden");m.append(pend);
  const busy=el("div","chatstep hidden","⏳ Claude is working…");m.append(busy);
  const inp=el("div","chatinput");
  inp.innerHTML=`<textarea rows="2" placeholder='e.g. "Find me a Christmas light show"'></textarea><button class="btn sm">Send</button>`;
  m.append(inp);
  const foot=el("div","saverow");
  foot.innerHTML=`<span class="note"></span><span style="flex:1"></span><button class="btn sm ghost">New conversation</button>`;
  m.append(foot);
  const ta=inp.querySelector("textarea"),sendBtn=inp.querySelector("button");
  const usage=foot.querySelector(".note"),resetBtn=foot.querySelector("button");
  let since=0,conv=null,keySet=null;

  function renderKey(set){
    if(set===keySet)return;keySet=set;
    if(set){
      keyBox.innerHTML=`<div class="saverow" style="margin:0"><span class="note">✓ Anthropic API key stored in the vault</span><span style="flex:1"></span><button class="btn sm ghost">Remove key</button></div>`;
      keyBox.querySelector("button").onclick=async()=>{
        if(!confirm("Remove the API key from the vault?"))return;
        await jpost("api/assistant/key",{key:""});keySet=null;poll();
      };
      return;
    }
    keyBox.innerHTML=`<h3>Anthropic API key</h3>
      <div class="note">The assistant uses the Claude API (billed per use, separate from a Claude subscription). Create a key at console.anthropic.com → API Keys and paste it here – it is stored encrypted in the vault.</div>
      ${fld("API key","as_key","password","","sk-ant-…")}
      <div class="saverow"><button class="btn sm">Save &amp; check</button><span class="note"></span></div>`;
    const b=keyBox.querySelector(".saverow button"),msg=keyBox.querySelector(".saverow .note");
    b.onclick=async()=>{
      msg.textContent="checking…";
      const r=await jpost("api/assistant/key",{key:$("#as_key").value});
      if(r.ok){toast(r.warning?"Key saved ("+r.warning+")":"Key saved");keySet=null;poll();}
      else msg.textContent="✗ "+(r.error||"Error");
    };
  }
  function addEvent(e){
    let d;
    if(e.t==="user"){d=el("div","chatmsg user");d.textContent=e.text;}
    else if(e.t==="assistant"){d=el("div","chatmsg",mdLite(e.text));}
    else{
      const pre={confirm:"❓ ",info:"ℹ️ ",error:"⚠ "}[e.t]||"";
      d=el("div","chatstep"+(e.err||e.t==="error"||(e.t==="confirm_result"&&!e.approved)?" err":""));
      d.textContent=pre+e.text;
    }
    log.append(d);
  }
  function renderPending(p){
    if(!p){pend.classList.add("hidden");pend.dataset.id="";return;}
    if(pend.dataset.id===p.id)return;
    pend.dataset.id=p.id;pend.classList.remove("hidden");
    pend.innerHTML=`<h3></h3><ul class="note"></ul><div class="saverow"><button class="btn sm">Run</button><button class="btn sm ghost">Cancel</button></div>`;
    pend.querySelector("h3").textContent=p.summary;
    const ul=pend.querySelector("ul");
    (p.details||[]).forEach(t=>{const li=el("li");li.textContent=t;ul.append(li);});
    const [ok,no]=pend.querySelectorAll("button");
    const answer=async a=>{ok.disabled=no.disabled=true;await jpost("api/assistant/confirm",{id:p.id,approve:a});poll();};
    ok.onclick=()=>answer(true);no.onclick=()=>answer(false);
    pend.scrollIntoView({behavior:"smooth",block:"nearest"});
  }
  async function poll(){
    if(ASSIST_TIMER){clearTimeout(ASSIST_TIMER);ASSIST_TIMER=null;}
    if(!document.body.contains(log))return;
    let s;try{s=await jget("api/assistant/state?since="+since);}catch(e){return;}
    if(conv!==null&&conv!==s.conv){conv=s.conv;since=0;log.innerHTML="";return poll();}
    conv=s.conv;
    s.events.forEach(e=>{addEvent(e);since=Math.max(since,e.seq);});
    if(s.events.length&&log.lastElementChild)log.lastElementChild.scrollIntoView({block:"nearest"});
    renderKey(s.key_set);
    renderPending(s.pending);
    busy.classList.toggle("hidden",!s.busy||!!s.pending);
    sendBtn.disabled=!!s.busy||!s.key_set;
    const u=s.usage||{};
    usage.textContent=(u.input||u.output)?`${s.model} · ${Math.round((u.input+u.cache_read+u.cache_write)/1000)}k input / ${(u.output/1000).toFixed(1)}k output tokens · ${u.web_searches||0} searches · approx. $${(u.usd||0).toFixed(2)}`:"";
    // Poll only while something is happening: polling keeps the session
    // active, which would otherwise hold off the vault's auto-lock forever.
    if(s.busy||s.pending)ASSIST_TIMER=setTimeout(poll,1500);
  }
  async function send(){
    const t=ta.value.trim();if(!t)return;
    sendBtn.disabled=true;
    const r=await jpost("api/assistant/send",{text:t});
    if(r.ok)ta.value="";else toast("✗ "+(r.error||"Error"));
    poll();
  }
  sendBtn.onclick=send;
  ta.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}});
  resetBtn.onclick=async()=>{if(!confirm("Clear the conversation and staging area?"))return;await jpost("api/assistant/reset",{});poll();};
  poll();
}

/* ---------------- Videos ---------------- */
async function viewVideos(m){
  m.append(el("h2","title","Videos"));
  m.append(el("div","note","Files land here via the SMB share \"Videos\" (\\\\"+location.hostname+"\\Videos). MKV & co. are converted to a browser-compatible format automatically on first playback -- that can take a moment the first time, but is instant afterwards."));
  const playerWrap=el("div","card");playerWrap.style.display="none";
  playerWrap.innerHTML=`<video id="videoplayer" controls style="width:100%;max-height:70vh;background:#000;border-radius:10px"></video>
    <div class="saverow" style="margin-top:10px"><span class="note" id="videoplayer_cap"></span></div>`;
  m.append(playerWrap);
  const list=el("div","filelist");m.append(list);

  async function refresh(){
    list.innerHTML="loading…";
    let data;try{data=await jget("api/videos");}catch(e){list.textContent="✗ Error loading";return;}
    const vids=data.videos||[];
    list.innerHTML="";
    if(!vids.length){list.append(el("div","note","No videos in the folder yet. Just copy them over via SMB."));return;}
    for(const v of vids){
      const it=el("div","fileitem");
      it.innerHTML=`<span class="ic">🎞️</span>`;
      const nm=el("div","nm",v.name);it.append(nm);
      it.append(el("div","sz",human(v.size)+(v.ready?"":" · prepared on playback")));
      const act=el("div","act");
      const play=el("button","iconbtn","▶️");play.title="Play";
      play.onclick=()=>playVideo(v.name);
      act.append(play);
      const del=el("button","iconbtn","🗑️");del.title="Delete the prepared copy (regenerated when needed)";
      del.onclick=async e=>{e.stopPropagation();await jpost("api/videos/delete_cache",{name:v.name});toast("Reset");refresh();};
      act.append(del);
      it.append(act);
      it.onclick=()=>playVideo(v.name);
      list.append(it);
    }
  }

  async function playVideo(name){
    playerWrap.style.display="";
    playerWrap.scrollIntoView({behavior:"smooth"});
    const cap=$("#videoplayer_cap"),vid=$("#videoplayer");
    vid.pause();vid.removeAttribute("src");vid.load();
    cap.textContent=name+" -- preparing…";
    try{
      const r=await jpost("api/videos/prepare",{name});
      if(!r.ok){cap.textContent="✗ "+(r.error||"Error");return;}
      let st=r;
      while(st.state==="working"){
        await new Promise(res=>setTimeout(res,2000));
        st=await jget("api/videos/status?name="+encodeURIComponent(name));
      }
      if(st.state!=="done"){cap.textContent="✗ "+(st.error||"Error during preparation");return;}
      cap.textContent=name;
      vid.src="media/videos/"+encodeURIComponent(name);
      vid.play().catch(()=>{});
    }catch(e){cap.textContent="✗ Connection error";}
  }

  refresh();
}

/* ---------------- Diagnostics ---------------- */
// Ported from marcone/teslausb#1044: two consecutive tx_bytes samples
// (summed across all real interfaces) give a throughput estimate. Stops
// polling once the tile it's updating is no longer in the DOM (page
// navigated away) instead of running forever in the background.
let _lastTxSample=null;
async function pollUploadThroughput(valEl){
  if(!document.body.contains(valEl))return;
  try{
    const s=await jget("api/net_tx_bytes");
    if(_lastTxSample){
      const dt=s.sample_ms-_lastTxSample.sample_ms, db=s.tx_bytes-_lastTxSample.tx_bytes;
      valEl.textContent=(dt>0&&db>=0)?human(db*1000/dt)+"/s":"–";
    }
    _lastTxSample=s;
  }catch(e){valEl.textContent="–";}
  setTimeout(()=>pollUploadThroughput(valEl),1000);
}
async function viewDiag(m){
  m.append(el("h2","title","Diagnostics"));
  const stats=el("div","stats");m.append(stats);
  const logcard=el("div","card");m.append(logcard);
  let s;try{s=await jget("api/status");}catch(e){return;}
  const d=s.diag||{};
  const items=[["Temperature",d.temp||"–"],["Uptime",d.uptime||"–"],["Wi-Fi",d.wifi_ssid||"–"],
    ["USB at car",d.gadget_active?"active":"—"],["Clips",s.clips],["encrypted",s.encrypted]];
  items.forEach(([k,v])=>{const c=el("div","stat");c.append(el("div","k",k));c.append(el("div","v",String(v)));stats.append(c);});
  const uploadTile=el("div","stat");uploadTile.append(el("div","k","Upload"));
  const uploadVal=el("div","v","…");uploadTile.append(uploadVal);stats.append(uploadTile);
  pollUploadThroughput(uploadVal);
  const actions=el("div","saverow");
  const rb=el("button","btn sm","♻️ Reboot");rb.onclick=async()=>{if(confirm("Reboot the Pi?")){await jpost("api/reboot",{});toast("Rebooting…");}};
  const td=el("button","btn sm ghost","🔀 Toggle drives");td.onclick=async()=>{await jpost("api/toggle_drives",{});toast("toggled");};
  actions.append(rb,td);m.insertBefore(actions,logcard);
  logcard.append(el("h3",null,"Logs"));
  const sel=el("select");["archiveloop","setup","sync","retention"].forEach(w=>{const o=el("option",null,w);o.value=w;sel.append(o);});
  const box=el("div","logbox","loading…");
  const load=async()=>{const r=await jget("api/log?which="+sel.value);box.textContent=r.text||"(empty)";box.scrollTop=box.scrollHeight;};
  sel.onchange=load;const f=el("div","field");f.append(sel);logcard.append(f,box);load();
  const hub=el("div","card");m.append(hub);hubUpdateCard(hub);
  const upd=el("div","card");m.append(upd);osUpdateCard(upd);
  const bt=el("div","card");m.append(bt);bootCard(bt);
}
async function bootCard(card){
  card.innerHTML=`<h3>Boot times</h3>
    <div class="note">Seconds from power-on. "Drives" = until the car mounted the USB drives (– if no car was attached as USB host). "End" = how that run ended: clean shutdown or power lost – the normal case in the car.</div>
    <div class="note bt_head">loading…</div><div class="bt_list"></div>`;
  const q=s=>card.querySelector(s);
  let r;try{r=await jget("api/boottimes");}catch(e){q(".bt_head").textContent="✗ Error loading";return;}
  const fmt=v=>v==null?"–":v.toLocaleString("en-US",{minimumFractionDigits:1,maximumFractionDigits:1})+" s";
  const when=t=>t?new Date(t*1000).toLocaleString("en-GB",{dateStyle:"short",timeStyle:"short"}):"?";
  const b=r.boots||[];
  if(!b.length){q(".bt_head").textContent="No measurements yet – they are captured about a minute after the Hub starts.";return;}
  const c=b[0];
  q(".bt_head").textContent=`${c.boot_id===r.current?"Current boot":"Last boot"} (${when(c.booted_at)}): drives for the car after ${fmt(c.drives_s)} · Wi-Fi ${fmt(c.wifi_s)} · Hub ${fmt(c.hub_s)} · boot done ${fmt(c.finished_s)}`;
  const t=el("table","tlist"),body=el("tbody");
  [["Boot","Drives","Wi-Fi","Hub","Done","End"]].concat(b.map(x=>[when(x.booted_at),fmt(x.drives_s),fmt(x.wifi_s),fmt(x.hub_s),fmt(x.finished_s),
    x.boot_id===r.current?"running":(x.clean_end?"clean":"power lost")]))
    .forEach(row=>{const tr=el("tr");row.forEach(v=>{const td=el("td");td.textContent=v;tr.append(td);});body.append(tr);});
  t.append(body);q(".bt_list").append(t);
}
function hubUpdateCard(card){
  card.innerHTML=`<h3>Hub software</h3>
    <div class="note hu_status">loading…</div>
    <div class="note warn hu_result hidden"></div>
    <details class="hu_noteswrap hidden"><summary class="note hu_notestitle">Changes</summary><div class="note hu_notes" style="white-space:pre-wrap"></div></details>
    <div class="note">Before every update the Hub backs up the program, settings, Wi-Fi profiles and vault to <code>/backingfiles/hub-backups</code> (the newest 3). If the install fails or the Hub doesn't answer afterwards, it restores the previous version itself. Installing works on the home Wi-Fi only; the Hub restarts, so sign in again afterwards.</div>
    <div class="saverow"><button class="btn sm ghost hu_check">Check for updates</button>
      <button class="btn sm hidden hu_go">Install update</button></div>
    <div class="logbox hidden hu_log"></div>
    <details class="hu_bkwrap hidden"><summary class="note">Backups</summary><div class="note hu_bk"></div></details>`;
  const q=s=>card.querySelector(s);
  const when=t=>t?new Date((typeof t==="number"?t*1000:t)).toLocaleString("en-GB",{dateStyle:"short",timeStyle:"short"}):"";
  let last=null;
  function render(s){
    last=s;const l=s.latest;
    let txt=`Installed: ${s.installed}`;
    if(s.running)txt+=` · ⏳ update to ${(s.run&&s.run.tag)||"?"}: ${(s.run&&s.run.phase)||"running"}… (don't disconnect the Pi's power)`;
    else if(s.available)txt+=` · 🆕 update available: ${l.tag}${l.published?" from "+when(l.published):""}`;
    else if(l&&l.tag)txt+=` · ✓ up to date (latest version ${l.tag})`;
    if(s.check_error)txt+=` · ✗ ${s.check_error}`;
    else if(!s.checked)txt+=" · not checked yet";
    if(s.checked)txt+=` · checked ${when(s.checked)}`;
    q(".hu_status").textContent=txt;
    const r=s.run;
    const showResult=!s.running&&r&&r.finished&&r.ok===false;
    q(".hu_result").classList.toggle("hidden",!showResult);
    if(showResult)q(".hu_result").textContent=`✗ update to ${r.tag} on ${when(r.finished)}: ${r.error||"failed"}`;
    else if(!s.running&&r&&r.ok===true&&r.finished){q(".hu_status").textContent+=` · last update to ${r.tag} on ${when(r.finished)} ✓`;}
    q(".hu_noteswrap").classList.toggle("hidden",!(l&&l.notes));
    if(l){q(".hu_notestitle").textContent=`Changes in ${l.tag}`;q(".hu_notes").textContent=l.notes||"";}
    q(".hu_go").classList.toggle("hidden",s.running||!s.available);
    q(".hu_go").textContent=s.available?`Install update ${l.tag}`:"Install update";
    q(".hu_check").disabled=!!s.running;
    const lg=q(".hu_log"),tail=s.tail||[];
    lg.classList.toggle("hidden",!(tail.length&&(s.running||showResult)));
    lg.textContent=tail.join("\n");lg.scrollTop=lg.scrollHeight;
    const bk=s.backups||[];
    q(".hu_bkwrap").classList.toggle("hidden",!bk.length);
    q(".hu_bk").textContent=bk.map(b=>`${b.name} · ${(b.size/1048576).toFixed(1)} MB · ${when(b.t)}`).join("\n");
    q(".hu_bk").style.whiteSpace="pre-wrap";
    updateNavBadge(s);
  }
  async function refresh(){
    if(!document.body.contains(card))return;
    let s;try{s=await jget("api/hub/update_status");}catch(e){if(last&&last.running)setTimeout(refresh,4000);return;}
    render(s);if(s.running)setTimeout(refresh,3000);
  }
  q(".hu_check").onclick=async()=>{
    q(".hu_check").disabled=true;q(".hu_status").textContent="asking GitHub…";
    try{render(await jpost("api/hub/update_check",{}));}catch(e){toast("✗ Connection error");}
    q(".hu_check").disabled=false;
  };
  q(".hu_go").onclick=async()=>{
    const l=last&&last.latest;if(!l)return;
    if(!confirm(`Install update ${l.tag}?\n\nFirst a backup is made, then the package is downloaded, verified and installed. The Hub restarts – sign in again afterwards. Don't disconnect the Pi's power during the update.`))return;
    const r=await jpost("api/hub/update_install",{});
    if(!r.ok){toast("✗ "+(r.error||"Error"));return;}
    refresh();
  };
  refresh();
}
function updateNavBadge(s){
  const nav=document.querySelector('nav .nav[data-view="diag"]');if(!nav)return;
  let b=nav.querySelector(".navbadge");
  if(s&&s.available){
    if(!b){b=el("span","navbadge","Update");nav.append(b);}
    b.title=`Hub update ${s.latest.tag} available`;
  }else if(b)b.remove();
}
async function checkHubUpdateHint(){
  try{
    const s=await jget("api/hub/update_status");updateNavBadge(s);
    if(s.available&&!sessionStorage.getItem("hubUpdateToast")){
      sessionStorage.setItem("hubUpdateToast","1");
      toast(`Hub update ${s.latest.tag} available – see Diagnostics`);
    }
  }catch(e){}
}
function osUpdateCard(card){
  card.innerHTML=`<h3>Operating-system updates</h3>
    <div class="note">Checking is safe at any time – the system partition stays read-only. Installing works on the home Wi-Fi only (no download over mobile data).</div>
    <div class="note warn">⚠ If power is lost mid-update, the system can be damaged. In the car that means: the car must stay awake the whole time (e.g. Sentry mode on), otherwise it cuts the USB power. Safest on the power supply.</div>
    <div class="note os_status">loading…</div>
    <div class="note warn os_audit hidden">⚠ An earlier install was interrupted – "Install now" completes it.</div>
    <details class="os_listwrap hidden"><summary class="note">Show packages</summary><div class="note os_list"></div></details>
    <div class="saverow"><button class="btn sm ghost os_check">Check for updates</button>
      <button class="btn sm hidden os_go">Install now</button>
      <button class="btn sm hidden os_reboot">♻️ Reboot</button></div>
    <div class="logbox hidden os_log"></div>`;
  const q=s=>card.querySelector(s);
  const when=t=>t?new Date(t*1000).toLocaleString("en-GB",{dateStyle:"short",timeStyle:"short"}):"";
  let last=null;
  function render(s){
    last=s;const n=(s.updates||[]).length;
    let txt;
    if(s.running)txt="⏳ "+(s.phase||"running")+"… (please don't disconnect the Pi's power)";
    else if(s.check_error)txt="✗ "+s.check_error;
    else if(!s.checked)txt="Not checked yet.";
    else txt=(n?`${n} update${n>1?"s":""} available`+(s.security?` (${s.security} security)`:""):"✓ system is up to date")+" · checked "+when(s.checked);
    if(!s.running&&s.finished)txt+=" · last install "+when(s.finished)+": "+(s.ok?"✓ succeeded":"✗ "+(s.error||"failed"));
    q(".os_status").textContent=txt;
    q(".os_audit").classList.toggle("hidden",!s.audit||s.running);
    q(".os_listwrap").classList.toggle("hidden",!n);
    q(".os_list").innerHTML="";
    (s.updates||[]).forEach(u=>{const d=el("div");d.textContent=`${u.security?"🔒 ":""}${u.pkg}  ${u.from||"new"} → ${u.to}`;q(".os_list").append(d);});
    q(".os_go").classList.toggle("hidden",s.running||!(n||s.audit));
    q(".os_check").disabled=!!s.running;
    q(".os_reboot").classList.toggle("hidden",s.running||!s.reboot_recommended||!s.finished);
    const lg=q(".os_log");lg.classList.toggle("hidden",!(s.tail||[]).length);
    lg.textContent=(s.tail||[]).join("\n");lg.scrollTop=lg.scrollHeight;
  }
  async function refresh(){
    if(!document.body.contains(card))return;
    let s;try{s=await jget("api/os/status");}catch(e){return;}
    render(s);if(s.running)setTimeout(refresh,2000);
  }
  q(".os_check").onclick=async()=>{
    q(".os_check").disabled=true;q(".os_status").textContent="checking… (may take 1–2 minutes)";
    const r=await jpost("api/os/check",{});
    if(r.status)render(r.status);
    if(!r.ok)toast("✗ "+(r.error||"Error"));
    q(".os_check").disabled=false;
  };
  q(".os_go").onclick=async()=>{
    const n=(last&&last.updates||[]).length;
    if(!confirm(`Install ${n?n+" packages":"the interrupted installation"} now?\n\nDon't disconnect the Pi's power during this. Takes a few minutes depending on size.`))return;
    const r=await jpost("api/os/upgrade",{});
    if(!r.ok){toast("✗ "+(r.error||"Error"));return;}
    refresh();
  };
  q(".os_reboot").onclick=async()=>{if(confirm("Reboot the Pi now?")){await jpost("api/reboot",{});toast("Rebooting…");}};
  refresh();
}

/* ---------------- Settings ---------------- */
function fld(label,id,type,val,ph){return `<div class="field"><label>${label}</label><input id="${id}" type="${type||'text'}" value="${val==null?'':String(val).replace(/"/g,'&quot;')}" ${ph?`placeholder="${ph}"`:''}></div>`;}
function chk(label,id,on){return `<label class="checkline"><input type="checkbox" id="${id}" ${on?'checked':''}> ${label}</label>`;}
function syncHoldText(h){
  if(!h)return "";
  if(!h.enabled)return "off";
  const dur=s=>{const m=Math.round((s||0)/60);return m>=60?`${Math.floor(m/60)}h ${m%60}min`:`${m} min`;};
  const open=(h.waiting_for||[]).join(" + ");
  if(h.phase==="holding")
    return `active for ${dur(h.elapsed_sec)} – waiting for: ${open||"completion"} (limit in ${dur(h.remaining_sec)})`
      +(h.failing?" – ⚠️ wake command is failing, still retrying":"");
  if(h.phase==="ended"){
    if(h.end_reason==="complete")return `done after ${dur(h.ended_after_sec)} – car may sleep`;
    if(h.end_reason==="timeout")return `limit of ${h.max_min} min reached, still open: ${open} – car may sleep`;
    return "ended for this visit – car may sleep";
  }
  return "ready – kicks in once home Wi-Fi and NAS are reachable";
}
function presenceCard(card){
  card.innerHTML=`<h3>At the car?</h3>
    <div class="note">Checked over Bluetooth: if the paired vehicle (the configured VIN) answers a short <code>ping</code>, the Hub is near it. This does not wake a sleeping car and only briefly uses the Bluetooth connection. The Hub checks every 10 minutes; during a drive the reads that run anyway are enough.</div>
    <div class="note warn">This proves proximity (a few meters), not that it's plugged in – and a sleeping or distant car simply doesn't answer. A missing answer is not a theft alarm.</div>
    <div class="note pres_status">loading…</div>
    <div class="saverow"><button class="btn sm ghost pres_check">Check now</button><span class="note pres_msg"></span></div>`;
  const q=s=>card.querySelector(s);
  const when=t=>t?new Date(t*1000).toLocaleString("en-GB",{dateStyle:"short",timeStyle:"short"}):"–";
  const ago=t=>{if(!t)return "";const m=Math.round((Date.now()/1000-t)/60);return m<1?"just now":m<60?`${m} min ago`:`${Math.floor(m/60)} h ${m%60} min ago`;};
  function render(s){
    let txt;
    if(!s.configured)txt="✗ No VIN set or no BLE key paired – can't check.";
    else if(s.in_car===true)txt=`✅ Vehicle confirmed (${ago(s.last_seen)}), since ${when(s.since)}`;
    else if(s.in_car===false)txt=`❔ Vehicle not answering${s.error?" ("+s.error+")":""} · last confirmed: ${when(s.last_seen)}`;
    else txt="Not checked yet.";
    if(s.checked)txt+=` · checked ${ago(s.checked)}`;
    if(s.usb_host)txt+=" · USB drives are currently mounted by a host";
    q(".pres_status").textContent=txt;
  }
  q(".pres_check").onclick=async()=>{
    q(".pres_check").disabled=true;q(".pres_msg").textContent="asking the car…";
    try{render(await jpost("api/presence/check",{}));q(".pres_msg").textContent="";}
    catch(e){q(".pres_msg").textContent="✗ Connection error";}
    q(".pres_check").disabled=false;
  };
  jget("api/presence").then(render).catch(()=>{q(".pres_status").textContent="✗ Error loading";});
}
/* ---------------- Vehicle (BLE) ---------------- */
async function viewBle(m){
  m.append(el("h2","title","Vehicle (BLE)"));
  let c;try{c=await jget("api/settings");}catch(e){c={};}
  const pres=el("div","card");m.append(pres);presenceCard(pres);
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Vehicle</h3>
      ${fld("Vehicle VIN","ble_vin","text",c.tesla_ble_vin)}
      <div class="saverow"><button class="btn sm ghost" id="blevinsave">Save VIN</button><span class="note" id="blevinmsg"></span></div>
    </div>
    <div class="card"><h3>Keep the car awake</h3>
      <div class="note">Sends a BLE <code>wake</code> command to the car every 5 minutes so it doesn't fall asleep -- e.g. during a longer cleanup. (<code>keep-accessory-power</code> alone isn't enough: per Tesla it doesn't apply to the data port used by dashcam/USB -- confirmed in practice, hence the periodic nudge instead of a one-off command.) Turns off automatically after the set time; "Stop now" ends it early at any point. Needs the paired <b>keep-awake</b> key below.</div>
      <div class="note" style="margin-top:8px">⚠️ <b>Measured 2026-09-13:</b> <code>wake</code> keeps the car awake only for 1–4 minutes, and another <code>wake</code> to the awake car doesn't extend that. Even once a minute the locked car slept in between. So this feature and "sync before sleep" do <b>not</b> reliably keep the car awake right now.</div>
      <div class="saverow"><span class="note" id="keepawake_status">loading…</span></div>
      <div class="saverow" style="flex-wrap:wrap">
        <input type="number" id="keepawake_hours" value="24" min="1" max="48" style="width:70px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <span class="note">hours</span>
        <button class="btn sm" id="keepawake_on">Turn on</button>
        <button class="btn sm ghost" id="keepawake_off" style="display:none">Stop now</button>
      </div>
      <div class="note" style="margin-top:14px"><b>Sync before sleep:</b> once the home Wi-Fi is connected and the NAS is reachable, the Hub tries to keep the car awake (a <code>wake</code> every 2 minutes, see the note above) until archiving and then a full NAS sync are done – at most ${c.sync_hold_max_min||120} minutes, after which it may sleep.</div>
      ${chk("Fully sync before the car sleeps","synchold_enabled",c.sync_hold_enabled!=='false')}
      <div class="saverow"><span class="note" id="synchold_status">loading…</span><span class="note" id="synchold_msg"></span></div>
    </div>
    <div class="card"><h3>BLE tools</h3>
      <div class="note">The official Tesla command-line tools (<code>tesla-control</code>, <code>tesla-keygen</code>) used to generate and pair BLE keys.</div>
      <div class="saverow" style="flex-wrap:wrap">
        <button class="btn sm ghost" id="bleinstall">Install BLE tools</button>
        <span class="note" id="bleinstallmsg"></span>
      </div>
    </div>
    <div class="card"><h3>Keys &amp; roles</h3>
      <div class="note">BLE keys are paired with a <b>role</b> that defines what the key may do. Instead of always pairing with full "owner" access as before, you can pair deliberately restricted roles for separate purposes here (each role = its own independent key).</div>
      <div class="ble-row">
        <div><b>Keep-awake</b> <span class="note">(role: charging_manager)</span></div>
        <div class="saverow" style="flex-wrap:wrap">
          <button class="btn sm ghost" id="blepair_awake">Pair</button>
          <span class="note" id="blemsg_awake">–</span>
        </div>
      </div>
      <div class="note">The role <code>vehicle_monitor</code> is no longer offered here. If it was paired before, the key stays enrolled on the car for now — our restricted keys can't remove themselves. Removal is only possible via the Tesla app (Security &amp; vehicle access → Keys) or on the touchscreen.</div>
      <div class="note warn">⚠ Security note: every paired private key sits unencrypted as a file on the stick (<code>/root/.ble/&lt;name&gt;/key_private.pem</code>); whoever gets physical access to the stick could copy it and (only within Bluetooth range of the car) misuse it within its role. Recommendation: enable <b>PIN-to-Drive</b> in the car and, if the stick is lost/stolen, remove all BLE keys immediately in the Tesla app.</div>
    </div>
    <div class="card" id="ble_reads_card" style="display:none"><h3>Sensors (read)</h3>
      <div class="note">Only commands tested and confirmed allowed for this key. Each value is only actually fetched from the car when you click "Read".</div>
      <div id="ble_reads_list"></div>
    </div>
    <div class="card" id="ble_actions_card" style="display:none"><h3>Commands (trigger)</h3>
      <div class="note">Sends real commands to the car. Commands the vehicle rejects with a permission error disappear from this list automatically.</div>
      <div class="note">No glovebox command here: Tesla's official command tool (<code>tesla-control</code>) has no "glovebox" command -- only trunk/frunk. The glovebox can only be triggered via a raw, unsigned CAN signal, not over this authenticated channel -- for that see the "⚠ Experimental: raw commands" section under <b>CAN bus</b>.</div>
      <div id="ble_actions_list"></div>
      <div class="saverow"><button class="btn sm ghost" id="ble_reset_unavailable">Retry hidden commands</button><span class="note" id="ble_reset_msg"></span></div>
    </div>`;
  m.append(box);
  $("#blevinsave").onclick=async()=>{
    const v=$("#ble_vin").value.trim();
    $("#blevinmsg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{tesla_ble_vin:v});
      $("#blevinmsg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
    }catch(e){$("#blevinmsg").textContent="✗ Connection error";}
  };
  $("#bleinstall").onclick=async()=>{
    $("#bleinstallmsg").textContent="installing… (may take a while)";
    try{
      const r=await jpost("api/ble/install",{});
      $("#bleinstallmsg").textContent=r.ok?(r.already?"✓ already installed":"✓ installed"):"✗ "+(r.error||"Error");
    }catch(e){
      $("#bleinstallmsg").textContent="✗ Connection error – please try again";
    }
  };
  function wireBlePair(id,name,role){
    $("#blepair_"+id).onclick=async()=>{
      $("#blemsg_"+id).textContent="pairing…";
      try{
        const r=await jpost("api/ble/pair",{name,role});
        if(!r.ok){$("#blemsg_"+id).textContent="✗ "+(r.error||"Error");return;}
        $("#blemsg_"+id).textContent="Request sent – now hold a key card to the console in the car and confirm on the screen";
        pollBlePaired(id,name,40,3000,role);
      }catch(e){
        $("#blemsg_"+id).textContent="✗ Connection error – please try again";
      }
    };
  }
  async function pollBlePaired(id,name,triesLeft,delayMs,role){
    if(triesLeft<=0)return;
    let r;
    try{r=await jget("api/ble/status?name="+name);}catch(e){return;}
    if(r.paired){$("#blemsg_"+id).textContent="✓ paired";loadBleCommands(id);return;}
    setTimeout(()=>pollBlePaired(id,name,triesLeft-1,delayMs,role),delayMs);
  }
  const bleValues={};
  const BLE_ACTION_STATUS={
    charging_start:{read:"charge",field:"chargingState",label:v=>v},
    charging_stop:{read:"charge",field:"chargingState",label:v=>v},
  };
  const BLE_ACTION_PREFILL={
    charging_set_limit:{read:"charge",field:"chargeLimitSoc"},
    charging_set_amps:{read:"charge",field:"chargingAmps"},
  };
  function applyActionStatus(actionId){
    const spec=BLE_ACTION_STATUS[actionId];
    const elMsg=$("#bleactstatus_"+actionId);
    if(elMsg&&spec){
      const vals=bleValues[spec.read];
      elMsg.textContent=(vals&&spec.field in vals)?("Status: "+spec.label(vals[spec.field])):"";
    }
    const prefill=BLE_ACTION_PREFILL[actionId];
    const inputEl=$("#bleval_"+actionId);
    if(inputEl&&prefill&&!inputEl.value){
      const vals=bleValues[prefill.read];
      if(vals&&prefill.field in vals)inputEl.value=vals[prefill.field];
    }
  }
  async function doBleRead(id,readId){
    $("#blereadmsg_"+readId).textContent="reading…";
    try{
      const r=await jpost("api/ble/read",{name:id,id:readId});
      if(!r.ok){$("#blereadmsg_"+readId).textContent="✗ "+(r.error||"Error");return;}
      $("#blereadmsg_"+readId).textContent="✓";
      bleValues[readId]=r.values||{};
      const entries=Object.entries(r.values||{});
      $("#blereadvals_"+readId).innerHTML=entries.length?`<table class="probe"><tbody>${entries.map(([k,v])=>
        `<tr><td>${k}</td><td class="note">${v}</td></tr>`).join("")}</tbody></table>`:"";
      Object.keys(BLE_ACTION_STATUS).concat(Object.keys(BLE_ACTION_PREFILL)).forEach(applyActionStatus);
    }catch(e){$("#blereadmsg_"+readId).textContent="✗ Connection error";}
  }
  async function loadBleCommands(id){
    let cmds;try{cmds=await jget("api/ble/commands");}catch(e){return;}
    const readsList=$("#ble_reads_list"),actionsList=$("#ble_actions_list");
    readsList.innerHTML=cmds.reads.map(c=>`
      <div class="ble-row">
        <div>${c.label}</div>
        <div class="saverow"><button class="btn sm ghost" id="bleread_${c.id}">Read</button><span class="note" id="blereadmsg_${c.id}">loading…</span></div>
        <div id="blereadvals_${c.id}" style="width:100%"></div>
      </div>`).join("");
    actionsList.innerHTML=cmds.actions.map(c=>{
      const hasStatus=c.id in BLE_ACTION_STATUS, noStatus=(c.id==="keep_accessory_power_on"||c.id==="keep_accessory_power_off");
      return `
      <div class="ble-row">
        <div>${c.label}${noStatus?' <span class="note">(no read command available for status)</span>':''}</div>
        <div class="saverow">
          ${(c.id==="charging_set_limit"||c.id==="charging_set_amps")?`<input type="number" id="bleval_${c.id}" style="width:80px" placeholder="${c.id==='charging_set_limit'?'%':'A'}">`:""}
          <button class="btn sm ghost" id="bleact_${c.id}">Run</button>
          <span class="note" id="bleactmsg_${c.id}"></span>
          ${hasStatus?`<span class="note" id="bleactstatus_${c.id}"></span>`:""}
        </div>
      </div>`;
    }).join("");
    cmds.reads.forEach(c=>{
      $("#bleread_"+c.id).onclick=()=>doBleRead(id,c.id);
    });
    cmds.actions.forEach(c=>{
      $("#bleact_"+c.id).onclick=async()=>{
        $("#bleactmsg_"+c.id).textContent="sending…";
        const body={name:id,id:c.id};
        const valEl=$("#bleval_"+c.id);
        if(valEl&&valEl.value)body.value=valEl.value;
        try{
          const r=await jpost("api/ble/exec",body);
          const msg=r.ok?"✓ "+(r.detail||"OK"):"✗ "+(r.error||r.detail||"Error");
          if(!r.ok&&/INSUFFICIENT_PRIVILEGES|UNAUTHORIZED/i.test(r.error||r.detail||"")){
            toast(c.label+": rejected by the car, hiding it");
            loadBleCommands(id);
            return;
          }
          $("#bleactmsg_"+c.id).textContent=msg;
        }catch(e){$("#bleactmsg_"+c.id).textContent="✗ Connection error";}
      };
    });
    $("#ble_reset_unavailable").onclick=async()=>{
      $("#ble_reset_msg").textContent="resetting…";
      try{await jpost("api/ble/reset_unavailable",{});$("#ble_reset_msg").textContent="✓";loadBleCommands(id);}
      catch(e){$("#ble_reset_msg").textContent="✗ Connection error";}
    };
    $("#ble_reads_card").style.display="";
    $("#ble_actions_card").style.display="";
    for(const c of cmds.reads){
      await doBleRead(id,c.id);
    }
  }
  wireBlePair("awake","awake","charging_manager");
  async function refreshBleStatus(id,name){
    try{
      const r=await jget("api/ble/status?name="+name);
      $("#blemsg_"+id).textContent=r.paired?"✓ paired":"not paired yet (status check unreliable -- try the commands below anyway)";
      loadBleCommands(id);
    }catch(e){}
  }
  refreshBleStatus("awake","awake");
  async function refreshKeepAwake(){
    const statusEl=$("#keepawake_status");
    if(!statusEl)return;
    let r;try{r=await jget("api/keepawake/status");}catch(e){setTimeout(refreshKeepAwake,30000);return;}
    const onBtn=$("#keepawake_on"),offBtn=$("#keepawake_off"),hoursInp=$("#keepawake_hours");
    if(r.active){
      const h=Math.floor(r.remaining_sec/3600),mn=Math.floor((r.remaining_sec%3600)/60);
      statusEl.textContent=r.failing
        ? `active – ${h}h ${mn}min left (⚠️ wake command is currently failing, still retrying)`
        : `active – ${h}h ${mn}min left`;
      onBtn.style.display="none";offBtn.style.display="";hoursInp.disabled=true;
    }else{
      statusEl.textContent="off";
      onBtn.style.display="";offBtn.style.display="none";hoursInp.disabled=false;
    }
    const holdEl=$("#synchold_status");
    if(holdEl)holdEl.textContent=syncHoldText(r.sync_hold);
    if(document.body.contains(statusEl))setTimeout(refreshKeepAwake,30000);
  }
  $("#keepawake_on").onclick=async()=>{
    $("#keepawake_status").textContent="turning on…";
    try{
      const r=await jpost("api/keepawake/start",{hours:Number($("#keepawake_hours").value)||24});
      if(!r.ok)toast("✗ "+(r.error||"Error"));
      else if(r.warning)toast("⚠️ Activated, but: "+r.warning);
      refreshKeepAwake();
    }catch(e){toast("✗ Connection error");}
  };
  $("#keepawake_off").onclick=async()=>{
    $("#keepawake_status").textContent="turning off…";
    try{
      const r=await jpost("api/keepawake/stop",{});
      if(!r.ok)toast("✗ "+(r.error||r.detail||"Error"));
      refreshKeepAwake();
    }catch(e){toast("✗ Connection error");}
  };
  $("#synchold_enabled").onchange=async e=>{
    $("#synchold_msg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{sync_hold_enabled:e.target.checked});
      $("#synchold_msg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
      refreshKeepAwake();
    }catch(err){$("#synchold_msg").textContent="✗ Connection error";}
  };
  refreshKeepAwake();
}

/* ---------------- CAN bus ---------------- */
async function viewCanbus(m){
  m.append(el("h2","title","CAN bus"));
  let c;try{c=await jget("api/settings");}catch(e){c={};}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>OBD dongle</h3>
      ${fld("Bluetooth address (MAC)","canbus_mac","text",c.canbus_mac,"e.g. 01:1D:A5:02:2C:CB")}
      <div class="saverow"><button class="btn sm ghost" id="canbusmacsave">Save address</button><span class="note" id="canbusmacmsg"></span></div>
      <div class="note">BLE OBD dongle (e.g. UniCarScan) on the car's OBD port. The Hub briefly connects over Bluetooth when needed, reads the CAN bus for a few seconds and disconnects again &mdash; no continuous polling, no pairing required.</div>
    </div>
    <div class="card"><h3>Live values</h3>
      <div class="saverow" style="flex-wrap:wrap">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="canbus_monitor"><span>Monitor continuously</span>
        </label>
        <span class="note" id="canbusmonitormsg"></span>
      </div>
      <div class="note">Re-reads every few seconds while active and shows the last known status of each address. This temporarily blocks other BLE actions (keep-awake, vehicle commands) too, since the Pi has only one Bluetooth adapter -- meant for longer observation, don't leave it running in the background permanently.</div>
      <div class="saverow">
        <input type="number" id="canbusdur" style="width:70px" value="5" min="2" max="15"> <span class="note">seconds</span>
        <button class="btn sm ghost" id="canbusread">Read now</button><span class="note" id="canbusmsg">–</span>
      </div>
      <div id="canbusvals"></div>
    </div>
    <div class="card"><h3>Unknown CAN IDs</h3>
      <div class="note">Everything the Hub doesn't recognize yet -- raw content of the last-seen message per ID. Useful for pattern hunting: e.g. open/close a door or flash the indicators while reading and watch which ID changes.</div>
      <div id="canbusunknown"></div>
    </div>
    <div class="card"><h3>⚠ Experimental: raw commands</h3>
      <div class="note warn">Writes unsigned raw data directly onto the vehicle CAN bus -- unlike the commands under "Vehicle (BLE)", the car checks neither role nor signature here, it just accepts the frame (if the gateway lets it through at all). Not verified against a real vehicle, no counter/checksum as real Tesla frames usually have -- may have no effect or trigger unexpected behavior. Only use this if you know what the bytes you send mean.</div>
      <div class="saverow"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="canbus_ack"><span>I know what I'm doing</span>
      </label></div>
      <div class="ble-row">
        <div>Open glovebox <span class="note">(0x3B3, UI_gloveboxRequest)</span></div>
        <div class="saverow"><button class="btn sm ghost" id="canbus_glovebox" disabled>Send</button><span class="note" id="canbus_glovebox_msg"></span></div>
      </div>
      <div class="ble-row">
        <div>Arbitrary frame</div>
        <div class="saverow" style="flex-wrap:wrap">
          <input type="text" id="canbus_raw_id" placeholder="CAN ID (hex, e.g. 3B3)" style="width:170px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
          <input type="text" id="canbus_raw_data" placeholder="Data (hex, max. 8 bytes, e.g. 01000000)" style="width:230px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
          <button class="btn sm ghost" id="canbus_raw_send" disabled>Send</button>
        </div>
        <span class="note" id="canbus_raw_msg"></span>
      </div>
    </div>`;
  m.append(box);
  $("#canbusmacsave").onclick=async()=>{
    const v=$("#canbus_mac").value.trim();
    $("#canbusmacmsg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{canbus_mac:v});
      $("#canbusmacmsg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
    }catch(e){$("#canbusmacmsg").textContent="✗ Connection error";}
  };
  function renderCanbusValues(values,unknown_frames,unknown_count){
    const entries=Object.entries(values||{});
    $("#canbusvals").innerHTML=entries.length?`<table class="probe"><tbody>${entries.map(([k,v])=>
      `<tr><td>${k}</td><td class="note">${v}</td></tr>`).join("")}</tbody></table>`:'<div class="note">no known signals decoded</div>';
    const uf=Object.entries(unknown_frames||{});
    const more=(unknown_count||0)-uf.length;
    $("#canbusunknown").innerHTML=uf.length?`<table class="probe"><tbody>${uf.map(([id,bytes])=>
      `<tr><td>0x${id}</td><td class="note">${bytes}</td></tr>`).join("")}</tbody></table>${more>0?`<div class="note">… and ${more} more</div>`:""}`:'<div class="note">none</div>';
  }
  $("#canbusread").onclick=async()=>{
    $("#canbusread").disabled=true;$("#canbusmsg").textContent="connecting & reading…";
    const dur=parseInt($("#canbusdur").value,10)||5;
    try{
      const r=await jpost("api/canbus/read",{duration:dur});
      if(!r.ok){$("#canbusmsg").textContent="✗ "+(r.error||"Error");$("#canbusread").disabled=false;return;}
      $("#canbusmsg").textContent=`✓ ${r.can_ids_seen} CAN IDs seen, ${Object.keys(r.values||{}).length} known values`;
      renderCanbusValues(r.values,r.unknown_frames,r.unknown_count);
    }catch(e){$("#canbusmsg").textContent="✗ Connection error";}
    $("#canbusread").disabled=false;
  };
  async function pollCanbusMonitor(){
    if(!document.body.contains($("#canbusvals"))){
      try{await jpost("api/canbus/monitor/stop",{});}catch(e){}
      return;
    }
    if(!$("#canbus_monitor").checked)return;
    let r;
    try{r=await jget("api/canbus/monitor/status");}catch(e){setTimeout(pollCanbusMonitor,3000);return;}
    renderCanbusValues(r.values,r.unknown_frames,r.unknown_count);
    $("#canbusmonitormsg").textContent=r.error?("⚠ "+r.error):`active – ${r.can_ids_seen} addresses seen, cycle #${r.cycles}`;
    setTimeout(pollCanbusMonitor,3000);
  }
  $("#canbus_monitor").onchange=async(e)=>{
    if(e.target.checked){
      $("#canbusread").disabled=true;
      $("#canbusmonitormsg").textContent="starting…";
      try{
        const r=await jpost("api/canbus/monitor/start",{});
        if(!r.ok){$("#canbusmonitormsg").textContent="✗ "+(r.error||"Error");e.target.checked=false;$("#canbusread").disabled=false;return;}
        $("#canbusmonitormsg").textContent="active…";
        pollCanbusMonitor();
      }catch(err){$("#canbusmonitormsg").textContent="✗ Connection error";e.target.checked=false;$("#canbusread").disabled=false;}
    }else{
      $("#canbusmonitormsg").textContent="";
      $("#canbusread").disabled=false;
      try{await jpost("api/canbus/monitor/stop",{});}catch(err){}
    }
  };
  (async()=>{
    let st;try{st=await jget("api/canbus/monitor/status");}catch(e){return;}
    if(st.active){
      $("#canbus_monitor").checked=true;
      $("#canbusread").disabled=true;
      $("#canbusmonitormsg").textContent="active…";
      pollCanbusMonitor();
    }
  })();
  $("#canbus_ack").onchange=(e)=>{
    const ok=e.target.checked;
    $("#canbus_glovebox").disabled=!ok;
    $("#canbus_raw_send").disabled=!ok;
  };
  $("#canbus_glovebox").onclick=async()=>{
    $("#canbus_glovebox").disabled=true;$("#canbus_glovebox_msg").textContent="sending…";
    try{
      const r=await jpost("api/canbus/write_action",{id:"glovebox_open",confirm:$("#canbus_ack").checked});
      $("#canbus_glovebox_msg").textContent=r.ok?`✓ sent (response: ${r.dongle_response||"–"})`:"✗ "+(r.error||"Error");
    }catch(e){$("#canbus_glovebox_msg").textContent="✗ Connection error";}
    $("#canbus_glovebox").disabled=!$("#canbus_ack").checked;
  };
  $("#canbus_raw_send").onclick=async()=>{
    $("#canbus_raw_send").disabled=true;$("#canbus_raw_msg").textContent="sending…";
    const can_id=$("#canbus_raw_id").value.trim();
    const data=$("#canbus_raw_data").value.trim();
    try{
      const r=await jpost("api/canbus/write_raw",{can_id,data,confirm:$("#canbus_ack").checked});
      $("#canbus_raw_msg").textContent=r.ok?`✓ sent (response: ${r.dongle_response||"–"})`:"✗ "+(r.error||"Error");
    }catch(e){$("#canbus_raw_msg").textContent="✗ Connection error";}
    $("#canbus_raw_send").disabled=!$("#canbus_ack").checked;
  };
}

/* ---------------- Trips & Log ---------------- */
const TRIP_EVENT_ICONS={wifi:"📶",usb:"🔌",temp:"🌡️",trip:"🚗",ble:"🔵",power:"⚡"};
async function viewTrips(m){
  m.append(el("h2","title","Trips & Log"));
  let c={};try{c=await jget("api/settings");}catch(e){}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Blackbox mode</h3>
      <div class="note">Automatically records position/route as soon as a drive is detected (gear ≠ Park) and stops recording once parked again. Requires a paired BLE key. Points are stored encrypted immediately; viewing, exporting and uploading to the NAS is only possible with the vault unlocked.</div>
      ${chk("Record drives automatically","trip_blackbox_enabled",c.blackbox_enabled==='true')}
      <div class="saverow"><span class="note" id="trip_bbmsg"></span><span class="note" id="trip_active_status">loading…</span></div>
    </div>
    <div class="card"><h3>Drives (GPX export)</h3>
      <div id="trips_list" class="note">loading…</div>
      ${chk("Upload to NAS automatically","trip_sync_enabled",c.sync_trips_enabled!=='false')}
      <div class="saverow"><span class="note" id="trip_syncenmsg"></span></div>
      <div class="saverow"><button class="btn sm ghost" id="tripsyncbtn">Upload to NAS now</button><span class="note" id="trips_syncstatus">loading…</span></div>
    </div>
    <div class="card"><h3>Event log</h3>
      <div class="note">Important events. More detailed with a paired BLE key (drive start/end, locking, charge state); without BLE only Wi-Fi/USB connection changes and temperature warnings.</div>
      <div id="events_list" class="note">loading…</div>
    </div>
    <div class="card"><h3>Pi temperature</h3>
      <div class="saverow" style="flex-wrap:wrap">
        <div class="seg" id="temp_range"><button data-h="6">6 h</button><button data-h="24" class="on">24 h</button><button data-h="168">7 days</button><button data-h="720">30 days</button></div>
        <span class="note" id="temp_current">loading…</span>
      </div>
      <div class="tchart" id="temp_chart" tabindex="0" aria-label="Pi temperature history; arrow keys select a data point"></div>
      <details class="note"><summary>Values as table</summary><div id="temp_table"></div></details>
      <div class="saverow"><a href="api/temperature/download" class="btn sm ghost" download>Download log</a></div>
    </div>`;
  m.append(box);

  $("#trip_blackbox_enabled").onchange=async(e)=>{
    $("#trip_bbmsg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{blackbox_enabled:e.target.checked});
      $("#trip_bbmsg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
    }catch(err){$("#trip_bbmsg").textContent="✗ Connection error";}
  };
  $("#trip_sync_enabled").onchange=async(e)=>{
    $("#trip_syncenmsg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{sync_trips_enabled:e.target.checked});
      $("#trip_syncenmsg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
    }catch(err){$("#trip_syncenmsg").textContent="✗ Connection error";}
  };

  try{
    const tr=await jget("api/blackbox/trips");
    $("#trip_active_status").textContent=tr.active?"🔴 Drive being recorded":"⚪ No active drive";
    const list=$("#trips_list");
    if(tr.locked){list.textContent="🔒 Vault locked – the drives are encrypted and visible after signing in.";}
    else if(!tr.trips||!tr.trips.length){list.textContent="No recorded drives yet.";}
    else{
      list.innerHTML=`<table class="probe"><tbody>${tr.trips.map(t=>`
        <tr>
          <td>${(t.start||"").replace("T"," ").slice(0,16)}</td>
          <td class="note">${t.distance_km!=null?t.distance_km+" km":"–"} · ${t.points} points</td>
          <td><a href="api/blackbox/export?trip=${encodeURIComponent(t.trip_id)}" class="btn sm ghost" download>GPX</a></td>
        </tr>`).join("")}</tbody></table>`;
    }
  }catch(e){$("#trips_list").textContent="✗ Error while loading";}

  const refreshTripSyncStatus=async()=>{
    let s;try{s=await jget("api/nas/trips_status");}catch(e){return;}
    const el2=$("#trips_syncstatus");
    if(!el2)return;
    if(!s.t){el2.textContent="not synced yet";}
    else if(!s.ok){el2.textContent="✗ "+(s.error||"Error");}
    else if(s.locked){el2.textContent="🔒 waiting for sign-in (drives are encrypted)";}
    else{el2.textContent="✓ uploaded ("+s.uploaded+" new, "+new Date(s.t*1000).toLocaleTimeString()+")";}
  };
  refreshTripSyncStatus();
  $("#tripsyncbtn").onclick=async()=>{
    $("#trips_syncstatus").textContent="uploading…";
    try{await jpost("api/nas/sync_trips",{});}catch(e){}
    setTimeout(refreshTripSyncStatus,4000);
  };

  try{
    const ev=await jget("api/events?limit=100");
    const list=$("#events_list");
    if(!ev.events||!ev.events.length){list.textContent="No events yet.";}
    else{
      list.innerHTML=`<table class="probe"><tbody>${ev.events.map(e=>`
        <tr><td>${(TRIP_EVENT_ICONS[e.category]||"•")}</td>
        <td class="note">${(e.ts||"").replace("T"," ")}</td>
        <td>${e.message}</td></tr>`).join("")}</tbody></table>`;
    }
  }catch(e){$("#events_list").textContent="✗ Error while loading";}

  tempChart();
}

/* Pi temperature chart: one series (per-bucket average) over time, a faint
   min–max band once a bucket spans more than one reading, gaps where the Pi
   was off (the car cut its USB power), crosshair + tooltip on hover/touch
   and via arrow keys. Every value is also in the table and the raw log. */
const TEMP_LINES=[[75,"75 °C Hub warning"],[80,"80 °C throttling"]];
function fmtC(v){return v.toLocaleString("de-DE",{minimumFractionDigits:1,maximumFractionDigits:1});}
async function tempChart(){
  const box=$("#temp_chart");if(!box)return;
  let hours=24,data=null,sel=-1,lastW=0,hover={show:()=>{},hide:()=>{},n:0};
  const tt=el("div","tt hidden");
  const xt=p=>p.t+data.bucket_sec/2;   // bucket start -> bucket middle
  const fmtT=(t,withDate)=>new Date(t*1000).toLocaleString("de-DE",withDate?
    {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}:{hour:"2-digit",minute:"2-digit"});
  async function load(){
    if(data)box.style.opacity=".5";   // refetch keeps the previous frame
    try{data=await jget("api/temperature/series?hours="+hours);}catch(e){box.style.opacity="";return;}
    box.style.opacity="";sel=-1;draw();header();table();
  }
  function header(){
    const pts=data.points,cur=$("#temp_current");
    if(!pts.length){cur.textContent="No readings in this period.";return;}
    const lo=Math.min(...pts.map(p=>p.min)),hi=Math.max(...pts.map(p=>p.max)),L=data.latest;
    const age=Date.now()/1000-L.t;
    cur.textContent=(age<300?"Now ":"Last ("+fmtT(L.t,true)+") ")+fmtC(L.temp)+" °C · Min "+fmtC(lo)+" · Max "+fmtC(hi)+" °C";
  }
  function draw(){
    box.innerHTML="";box.append(tt);tt.classList.add("hidden");
    const pts=data.points;
    if(!pts.length){box.append(el("div","empty","No readings in this period."));hover={show:()=>{},hide:()=>{},n:0};return;}
    const W=Math.max(280,box.clientWidth);lastW=box.clientWidth;
    const H=Math.round(Math.min(260,Math.max(180,W*0.4))),m={l:34,r:12,t:12,b:24};
    const t1=Date.now()/1000,t0=t1-hours*3600;
    let lo=Math.min(...pts.map(p=>p.min)),hi=Math.max(...pts.map(p=>p.max));
    lo=Math.floor((lo-2)/5)*5;hi=Math.ceil((hi+2)/5)*5;if(hi-lo<10)hi=lo+10;
    const X=t=>m.l+(t-t0)/(t1-t0)*(W-m.l-m.r),Y=v=>m.t+(hi-v)/(hi-lo)*(H-m.t-m.b);
    const NS="http://www.w3.org/2000/svg",svg=document.createElementNS(NS,"svg");
    svg.setAttribute("viewBox",`0 0 ${W} ${H}`);svg.setAttribute("aria-hidden","true");
    const add=(tag,attrs)=>{const n=document.createElementNS(NS,tag);for(const k in attrs)n.setAttribute(k,attrs[k]);svg.append(n);return n;};
    const label=(x,y,s,anchor)=>{const n=add("text",{x,y,"text-anchor":anchor,fill:"var(--axis)","font-size":11});n.textContent=s;};
    const step=hi-lo>25?10:5;
    for(let v=lo;v<=hi;v+=step){add("line",{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:"var(--grid)","stroke-width":1});label(m.l-6,Y(v)+4,String(v),"end");}
    // x ticks on local hour/day boundaries
    const daily=hours>24,every=hours<=6?1:hours<=24?4:hours<=168?1:5;
    const d=new Date(t0*1000);d.setMinutes(0,0,0);if(daily)d.setHours(0);
    for(let i=0;d.getTime()/1000<=t1;i++){
      const t=d.getTime()/1000,x=X(t);
      if(t>=t0&&(daily?i%every===0:d.getHours()%every===0)&&x>m.l+12&&x<W-m.r-16){
        add("line",{x1:x,x2:x,y1:H-m.b,y2:H-m.b+4,stroke:"var(--grid)","stroke-width":1});
        label(x,H-6,daily?d.toLocaleDateString("de-DE",{day:"2-digit",month:"2-digit"}):String(d.getHours()).padStart(2,"0")+":00","middle");
      }
      if(daily)d.setDate(d.getDate()+1);else d.setHours(d.getHours()+1);
    }
    TEMP_LINES.forEach(([v,s])=>{if(v>lo&&v<=hi){
      add("line",{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:"var(--axis)","stroke-width":1,"stroke-opacity":.45});
      label(W-m.r-2,Y(v)-4,s,"end");}});
    // split where readings are missing, so a power gap isn't drawn as a line
    const gap=Math.max(3*data.bucket_sec,180),segs=[];let cur=[];
    pts.forEach((p,k)=>{if(cur.length&&p.t-pts[k-1].t>gap){segs.push(cur);cur=[];}cur.push(p);});
    segs.push(cur);
    segs.forEach(s=>{
      if(data.bucket_sec>60&&s.length>1)
        add("polygon",{points:s.map(p=>`${X(xt(p))},${Y(p.max)}`).concat(s.slice().reverse().map(p=>`${X(xt(p))},${Y(p.min)}`)).join(" "),
          fill:"var(--series-1)","fill-opacity":.14});
      if(s.length===1)add("circle",{cx:X(xt(s[0])),cy:Y(s[0].avg),r:2,fill:"var(--series-1)"});
      else add("polyline",{points:s.map(p=>`${X(xt(p))},${Y(p.avg)}`).join(" "),fill:"none",stroke:"var(--series-1)",
        "stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"});
    });
    const last=pts[pts.length-1];
    add("circle",{cx:X(xt(last)),cy:Y(last.avg),r:4,fill:"var(--series-1)",stroke:"var(--card)","stroke-width":2});
    const cross=add("line",{y1:m.t,y2:H-m.b,stroke:"var(--axis)","stroke-width":1,visibility:"hidden"});
    const dot=add("circle",{r:4,fill:"var(--series-1)",stroke:"var(--card)","stroke-width":2,visibility:"hidden"});
    const hit=add("rect",{x:m.l,y:0,width:W-m.l-m.r,height:H,fill:"transparent"});
    const xs=pts.map(p=>X(xt(p)));
    const show=k=>{
      sel=k;const p=pts[k],x=xs[k];
      cross.setAttribute("x1",x);cross.setAttribute("x2",x);cross.setAttribute("visibility","visible");
      dot.setAttribute("cx",x);dot.setAttribute("cy",Y(p.avg));dot.setAttribute("visibility","visible");
      tt.innerHTML="";const b=el("b");b.textContent=fmtC(p.avg)+" °C";tt.append(b,document.createTextNode(fmtT(xt(p),daily)));
      if(data.bucket_sec>60){tt.append(el("br"),document.createTextNode("Ø "+Math.round(data.bucket_sec/60)+" min · "+fmtC(p.min)+"–"+fmtC(p.max)+" °C"));}
      tt.classList.remove("hidden");
      const bw=box.clientWidth,px=x/W*bw;
      tt.style.left=Math.max(0,Math.min(px+12,bw-tt.offsetWidth-4))+"px";
    };
    const hide=()=>{sel=-1;cross.setAttribute("visibility","hidden");dot.setAttribute("visibility","hidden");tt.classList.add("hidden");};
    const nearest=cx=>{const r=svg.getBoundingClientRect(),x=(cx-r.left)/r.width*W;
      let a=0,z=xs.length-1;while(z-a>1){const mid=(a+z)>>1;if(xs[mid]<x)a=mid;else z=mid;}
      return Math.abs(xs[a]-x)<=Math.abs(xs[z]-x)?a:z;};
    hit.addEventListener("pointermove",e=>show(nearest(e.clientX)));
    hit.addEventListener("pointerdown",e=>show(nearest(e.clientX)));
    hit.addEventListener("pointerleave",()=>{if(document.activeElement!==box)hide();});
    hover={show,hide,n:pts.length};
    box.append(svg);
  }
  function table(){
    const det=box.parentElement.querySelector("details"),tb=$("#temp_table");
    tb.innerHTML="";
    const fill=()=>{
      if(!det.open||tb.childElementCount||!data.points.length)return;
      const t=el("table","tlist"),body=el("tbody");
      [["Time","Ø °C","Min","Max"]].concat(data.points.slice().reverse().map(p=>[fmtT(xt(p),true),fmtC(p.avg),fmtC(p.min),fmtC(p.max)]))
        .forEach(r=>{const tr=el("tr");r.forEach(v=>{const td=el("td");td.textContent=v;tr.append(td);});body.append(tr);});
      t.append(body);tb.append(t);
    };
    det.ontoggle=fill;fill();
  }
  box.addEventListener("keydown",e=>{
    if(!hover.n)return;
    if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();
      hover.show(sel<0?hover.n-1:Math.max(0,Math.min(hover.n-1,sel+(e.key==="ArrowRight"?1:-1))));}
    else if(e.key==="Escape")hover.hide();
  });
  box.addEventListener("focus",()=>{if(sel<0&&hover.n)hover.show(hover.n-1);});
  box.addEventListener("blur",()=>hover.hide());
  document.querySelectorAll("#temp_range button").forEach(b=>b.onclick=()=>{
    document.querySelectorAll("#temp_range button").forEach(x=>x.classList.toggle("on",x===b));
    hours=+b.dataset.h;load();
  });
  new ResizeObserver(()=>{if(data&&document.body.contains(box)&&box.clientWidth!==lastW)draw();}).observe(box);
  load();
}

async function viewSettings(m){
  m.append(el("h2","title","Settings"));
  let c;try{c=await jget("api/settings");}catch(e){return;}
  let login={logged_in:false,has_refresh:false};
  try{login=(await jget("api/status")).login||login;}catch(e){}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Tesla account (for key retrieval)</h3>
      <div class="note">To decrypt encrypted recordings automatically, the Hub has to sign in to Tesla once and fetch a key-retrieval token. Without this login, encrypted clips stay locked permanently.</div>
      <div class="saverow"><span class="note" id="teslastatus">${login.logged_in?"✓ signed in"+(login.has_refresh?" (stays valid automatically)":""):"✗ not signed in"}</span></div>
      <div class="saverow"><button class="btn sm" id="teslaloginbtn">Sign in to Tesla</button></div>
      <div class="note">Opens the Tesla sign-in page in a new tab. After login the browser shows a blank/error page at <code>dashcam.tesla.com/callback?...</code> — paste the complete address from the address bar here:</div>
      ${fld("Callback URL after login","s_tesla_callback","text","","https://dashcam.tesla.com/callback?code=...")}
      <div class="saverow"><button class="btn sm ghost" id="teslaexchange">Confirm</button><span class="note" id="teslamsg"></span></div>
    </div>
    <div class="card"><h3>Connection / NAS</h3>
      ${fld("Archive server (NAS IP)","s_archive_server","text",c.archive_server)}
      ${fld("Share + path","s_share_name","text",c.share_name)}
      ${fld("Benutzer","s_share_user","text",c.share_user)}
      ${fld("Password","s_share_password","password","",c.share_password_set?"•••• unchanged":"")}
      <div class="saverow"><button class="btn sm" id="nastest">Test connection</button><span class="note" id="nasmsg"></span></div>
      ${chk("Archive RecentClips","s_archive_recentclips",c.archive_recentclips==='true')}
      ${chk("Archive SavedClips","s_archive_savedclips",c.archive_savedclips==='true')}
      ${chk("Archive SentryClips","s_archive_sentryclips",c.archive_sentryclips==='true')}
      ${chk("Don't re-upload clips deleted on the NAS","s_nas_skip_deleted",c.nas_skip_deleted!=='false')}
      <div class="note">If you delete clips on the NAS (e.g.&nbsp;to save space), they won't be uploaded again; in the overview they show as "🗑 deleted on NAS" and count as done. Turned off, the next archive run uploads them again as long as they still exist locally.</div>
      ${fld("SavedClips: archive only the last N minutes per event (0 = everything)","s_archive_savedclips_last_minutes","number",c.archive_savedclips_last_minutes||"0")}
      <div class="note">A "saved" Tesla event can span several minutes of footage around the trigger. With &gt;0, only the most recently recorded part (the last N minutes) of each event folder is uploaded to the NAS, to save time/space. <b>Important:</b> the older, non-uploaded part is marked "already done" and never archived later — it only stays on the stick until it eventually gets overwritten, not permanently backed up. 0 = previous behavior, the complete event is archived (default, safest setting).</div>
      ${fld("Limit upload speed (KB/s, 0 = unlimited)","s_archive_bwlimit_kbps","number",c.archive_bwlimit_kbps||"0")}
      <div class="note">Safety net against aborted transfers on weak Wi-Fi: the Hub now automatically applies a better queueing discipline (fq_codel) on the Wi-Fi interface so large transfers no longer starve the reachability check to the NAS and the transfer is wrongly aborted. If that alone isn't enough (still "transfer interrupted" on the Recordings page), set an upper limit here, e.g. 2000-4000 KB/s — deliberately throttles so there is always headroom for the check. 0 = no limit (default).</div>
    </div>
    <div class="card"><h3>Network</h3>
      ${fld("Wi-Fi SSID","s_ssid","text",c.ssid)}
      ${fld("Wi-Fi password","s_wifipass","password","",c.wifipass_set?"•••• unchanged":"")}
      ${fld("Access point SSID","s_ap_ssid","text",c.ap_ssid)}
      ${fld("Access point password","s_ap_pass","password","",c.ap_pass_set?"•••• unchanged":"")}
      <div class="note">Enter SSID/password above and save it with the big "Save" button below before enabling the fallback below.</div>
    </div>
    <div class="card"><h3>Access point fallback</h3>
      <div class="note">Off: the access point runs permanently alongside the home Wi-Fi as usual. On: only enabled when the home Wi-Fi is currently unreachable (checked every 30s), otherwise off.</div>
      <div class="saverow"><span class="note" id="apfallback_status">loading…</span></div>
      <div class="saverow">
        <button class="btn sm" id="apfallback_on">Turn on</button>
        <button class="btn sm ghost" id="apfallback_off" style="display:none">Turn off</button>
      </div>
      <div class="note warn">⚠ On this Pi, running AP+Wi-Fi at the same time can briefly disturb the Wi-Fi connection (chip limitation, observed while testing). Test it once in peace before leaving the house, don't rely on it blindly.</div>
      <div class="note" id="apusb_note" style="display:none">External USB Wi-Fi adapter detected (<code id="apusb_device"></code>) -- fully fixes the chip problem above: the access point then runs as its own radio, without sharing the antenna with the home Wi-Fi.</div>
      <div class="saverow" id="apusb_row" style="display:none">
        <span class="note" id="apusb_status">loading…</span>
        <button class="btn sm" id="apusb_on">Move permanently to USB adapter</button>
        <button class="btn sm ghost" id="apusb_off" style="display:none">Back to onboard chip</button>
      </div>
    </div>
    <div class="card"><h3>Wi-Fi networks (phone hotspots etc.)</h3>
      <div class="note">The home Wi-Fi (above under "Network") always takes priority. If it's not in range, the Pi connects to the first reachable network in this list – e.g. phone hotspots on the road. Change the order with ⬆️/⬇️; changes apply immediately, without "Save" below.</div>
      <div class="filelist" id="wifi_list">loading…</div>
      ${fld("SSID","wifi_new_ssid","text","","e.g. My iPhone")}
      ${fld("Password (empty = open network; for an existing network, empty = unchanged)","wifi_new_pass","password","")}
      <div class="saverow"><button class="btn sm" id="wifi_add">Add / change password</button><span class="note" id="wifi_msg"></span></div>
      <div class="note" style="margin-top:14px"><b>Automatic Wi-Fi selection:</b> NetworkManager does not switch back on its own – once connected to a phone hotspot, the Pi stays there even when the home Wi-Fi is back in range. And while its own access point is running, the client is stuck on that radio channel and barely finds the hotspot on the road. With this option the Pi checks every minute which known network is the best reachable one (home Wi-Fi first, then this list), takes the access point down if needed and switches. At home the learned home zone also counts, in case the home Wi-Fi is missing from the scan. If it finds nothing known three times in a row, the access point comes back on.</div>
      ${chk("Automatically switch to the best known Wi-Fi (recommended)","s_home_wifi_prefer",c.home_wifi_prefer!=='false')}
      <div class="note" id="homezone_note">${c.home_lat&&c.home_lon?`Home zone learned: ${(+c.home_lat).toFixed(4)}, ${(+c.home_lon).toFixed(4)} · radius ${c.home_radius_m||150} m`:"Home zone not learned yet – the Hub remembers it as soon as it's on the home Wi-Fi and knows a position from the car."}</div>
      <div class="saverow"><span class="note" id="home_wifi_msg"></span></div>
    </div>
    <div class="card"><h3>WireGuard VPN (to home)</h3>
      <div class="note">Builds an encrypted VPN connection on the road (e.g. via the phone hotspot above) to a WireGuard server at home -- for remote access to the Hub without having to open a port in your home network to the outside. Enter the public key below into the home server's peer configuration, then enter the peer data here, save and turn on.</div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input type="file" id="wg_qr_file" accept="image/*" style="flex:1;min-width:220px">
        <button class="btn sm ghost" id="wg_qr_import">Read QR code</button>
      </div>
      <div class="note" id="wg_qr_msg"></div>
      <div class="note">Upload and read a screenshot/photo of the WireGuard QR code -- fills in all fields below (incl. the private key) automatically. Then check below and save with the big "Save" button. Alternatively fill in by hand.</div>
      ${fld("Peer public key (home server)","s_wg_peer_pubkey","text",c.wg_peer_pubkey)}
      ${fld("Endpoint (Host:Port)","s_wg_endpoint","text",c.wg_endpoint,"vpn.example.com:51820")}
      ${fld("Allowed IPs (AllowedIPs)","s_wg_allowed_ips","text",c.wg_allowed_ips||"0.0.0.0/0")}
      ${fld("Hub tunnel address (CIDR)","s_wg_address","text",c.wg_address,"10.10.10.2/24")}
      ${fld("Keepalive (sec, 0=off)","s_wg_keepalive","number",c.wg_keepalive||25)}
      ${fld("Preshared key (optional)","s_wg_psk","password","",c.wg_psk_set?"•••• set":"")}
      ${fld("Private key (from QR code, usually not needed by hand)","s_wg_privkey","password","",c.wg_privkey_set?"•••• set":"")}
      ${fld("DNS (optional)","s_wg_dns","text",c.wg_dns)}
      <div class="note">Enter peer data here and save it with the big "Save" button below before enabling below.</div>
      <div class="saverow"><span class="note" id="wg_status">loading…</span></div>
      <div class="saverow">
        <button class="btn sm" id="wg_on">Turn on</button>
        <button class="btn sm ghost" id="wg_off" style="display:none">Turn off</button>
      </div>
      <div class="note">This Hub's public key (add it to the home server's peer config): <code id="wg_own_pubkey">–</code></div>
    </div>
    <div class="card"><h3>Keep the car awake</h3>
      ${fld("TeslaFi API token","s_teslafi_api_token","password","",c.teslafi_api_token_set?"•••• set":"")}
      ${fld("Tessie API token","s_tessie_api_token","password","",c.tessie_api_token_set?"•••• set":"")}
      ${fld("BLE vehicle VIN","s_tesla_ble_vin","text",c.tesla_ble_vin)}
      <div class="note">Pair BLE keys, test them and view the pairing status: menu item <b>"Vehicle (BLE)"</b> on the left.</div>
    </div>
    <div class="card"><h3>Notifications</h3>
      ${chk("Pushover enabled","s_pushover_enabled",c.pushover_enabled==='true')}
      ${fld("Pushover user key","s_pushover_user_key","password","",c.pushover_user_key_set?"•••• set":"")}
      ${fld("Pushover app key","s_pushover_app_key","password","",c.pushover_app_key_set?"•••• set":"")}
      ${chk("Telegram enabled","s_telegram_enabled",c.telegram_enabled==='true')}
      ${fld("Telegram chat ID","s_telegram_chat_id","text",c.telegram_chat_id)}
      ${fld("Telegram bot token","s_telegram_bot_token","password","",c.telegram_bot_token_set?"•••• set":"")}
    </div>
    <div class="card"><h3>Home Assistant (MQTT)</h3>
      ${chk("Register as a device in Home Assistant","s_mqtt_enabled",c.mqtt_enabled==='true')}
      ${fld("MQTT server (host)","s_mqtt_host","text",c.mqtt_host,"192.168.1.10")}
      ${fld("Port","s_mqtt_port","number",c.mqtt_port||1883)}
      ${fld("Benutzer","s_mqtt_user","text",c.mqtt_user)}
      ${fld("Password","s_mqtt_password","password","",c.mqtt_password_set?"•••• unchanged":"")}
      <div class="note">Registers the Hub via MQTT Discovery automatically as a device "TeslaCam Hub" in Home Assistant (sensors: recordings, encrypted recordings, NAS archiving %, Pi temperature, Wi-Fi, USB at the car, vault unlocked). No manual setup in HA needed, as long as the MQTT integration is already set up there.</div>
    </div>
    <div class="card"><h3>Retention & sync</h3>
      <div class="note"><b>Camera recordings:</b> go through the normal teslausb archiving (see "Connection / NAS" above) and are transferred <b>one way only</b>: from the stick to the NAS. Afterwards they are removed from the stick to free up space. Nothing comes back from the NAS.</div>
      <div class="note" style="margin-bottom:14px"><b>Music/LightShow/Boombox:</b> go through a separate mechanism and are synced <b>in both directions</b>: changes on the NAS (e.g. music added there) are copied to the stick, and changes on the stick to the NAS. Nothing is deleted automatically anywhere — a file removed on one side stays on the other side.</div>
      ${chk("Sync Music/LightShow/Boombox automatically over Wi-Fi (bidirectional)","s_sync_all_content",c.sync_all_content==='true')}
      ${fld("Sync path on the NAS","s_sync_media_path","text",c.sync_media_path,"Tesla_Video/Other")}
      <div class="note">The first part (before the first <code>/</code>) must be an existing share name on the NAS (e.g. <code>Tesla_Video</code>, same server/credentials as above under "Connection / NAS"). Everything after it (e.g. <code>Other</code>) as well as the folders <code>Music/</code>, <code>LightShow/</code>, <code>Boombox/</code> are created automatically if they don't exist yet. "Sync now" saves the path automatically too.</div>
      <div class="saverow"><button class="btn sm ghost" id="mediasync">Sync now (bidirectional)</button><span class="note" id="mediasyncmsg"></span></div>
      <div class="field"><label>Delete recordings on the stick</label>
        <select id="s_retention_mode">
          <option value="off"${c.retention_mode==='off'||!c.retention_mode?' selected':''}>Off</option>
          <option value="time"${c.retention_mode==='time'?' selected':''}>By time period</option>
          <option value="space"${c.retention_mode==='space'?' selected':''}>Rolling by storage</option>
        </select></div>
      ${fld("Retention (days)","s_retention_days","number",c.retention_days)}
      ${fld("Keep at least this much free space (GB)","s_retention_free_gb","number",c.retention_free_gb)}
      <div class="note">Only files already backed up on the NAS are deleted.</div>
    </div>
    <div class="card"><h3>Backup & restore</h3>
      <div class="note">Exports/imports the complete configuration file (<code>teslausb_setup_variables.conf</code>) incl. all passwords/tokens in plain text -- deliberately separate from the normal Save function above, which never shows passwords again for security reasons. Meant as a safety net before larger interventions (e.g. re-flashing the stick with a bigger root partition): export first, then import instead of retyping every field by hand.</div>
      <div class="note warn">⚠ The exported file contains all credentials in plain text (Wi-Fi, NAS, MQTT, access point, ...). Keep it safe, never upload/share it unencrypted.</div>
      <div class="saverow"><a href="api/backup/export" class="btn sm ghost" download>Export settings</a></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input type="file" id="backup_import_file" accept=".conf,text/plain" style="flex:1;min-width:220px">
        <button class="btn sm ghost" id="backup_import_btn">Import settings</button>
      </div>
      <div class="note" id="backup_import_msg"></div>
    </div>
    <div class="card"><h3>Raw keys for an external instance</h3>
      <div class="note warn">⚠ Security trade-off: by default the key sidecar files on the NAS (<code>*.key.json</code>) are useless without the vault password. This option additionally writes <b>unencrypted</b> keys (<code>*.rawkey.json</code>) next to the videos so a separate system can read the clips directly without knowing the vault password. Only enable this if the NAS itself is trustworthy/secured.</div>
      <div class="note">Protection against raw keys accidentally being written to a wrong/swapped NAS: the first time, a random pairing token is stored both here and in a file on the NAS (<code>HUB-NAS-KOPPLUNG.json</code>). If the tokens don't match on a later run (e.g. because a different NAS is reachable under the same name), <b>no</b> raw keys are written.</div>
      ${chk("Store unencrypted keys for an external instance on the NAS","s_nas_raw_keys",c.nas_raw_keys==='true')}
      <div class="saverow" style="flex-wrap:wrap">
        <span class="note" id="pairingstatus">Pairing status: loading…</span>
        <button class="btn sm ghost" id="rawkeypush">Upload now</button>
        <button class="btn sm ghost" id="pairingreset">Reset pairing</button>
      </div>
      <div class="note" id="rawkeymsg"></div>
    </div>
    <div class="card"><h3>SMB share</h3>
      <div class="note">Provides <code>TeslaCam</code> (RecentClips/SavedClips/SentryClips, read-only) as a network share, e.g. for browsing/copying from a PC. On by default, password-protected (user <code>pi</code>).</div>
      <div class="note">Next to every video whose key is known in the vault, a <code>*.mp4.key.json</code> file is placed automatically -- encrypted with the vault password, useless without it. This way video + key can be copied together over SMB. Appears at most 60s after unlocking the vault, and is not added retroactively for videos already deleted from the device.</div>
      ${chk("SMB share enabled","s_samba_enabled",c.samba_enabled!=='false')}
      <div class="saverow"><span class="note" id="sambastatus">loading…</span></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_samba_pw_new" type="password" placeholder="new SMB password (min. 8 characters)" style="flex:1;min-width:220px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="sambapwchange">Set SMB password</button>
      </div>
      <div class="note">Independent of the vault and SSH password. On the very first activation (via <code>hub/install.sh</code>) a random password is assigned automatically and printed once in the install log -- set your own directly here.</div>
      <div class="note" id="sambapwmsg"></div>
    </div>
    <div class="card"><h3>Security & system</h3>
      <div class="note">Core principle: the stick should be worthless if stolen. Decryption keys and the Tesla token are never stored unencrypted on the stick, only encrypted in the vault; the actual video is only briefly decrypted in RAM while viewing, never stored permanently. The two settings below secure the two remaining attack surfaces: remote access (SSH) and the "vault currently unlocked" state.</div>
      ${chk("Disable SSH password login","s_ssh_disable_password",c.ssh_disable_password==='true')}
      <div class="note">Why: without this, SSH is reachable by password from the whole (W)LAN and thus vulnerable to automated password guessing. With the box checked, only SSH-key login is possible. <b>Caution:</b> be sure to install your own SSH key on the Pi first (<code>~/.ssh/authorized_keys</code>) — otherwise you lock yourself out of SSH and can only get back in with a screen+keyboard directly at the Pi.</div>
      ${fld("Auto-lock vault after (min, 0=off)","s_vault_autolock_min","number",c.vault_autolock_min)}
      <div class="note">Why: the vault keeps keys/tokens decrypted in RAM only while it is open. The longer it stays open (e.g. because you left the browser tab open), the longer someone with access to the running device could grab these plaintext keys from memory. Auto-locking after inactivity limits this window.</div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_pw_old" type="password" placeholder="current password" style="flex:1;min-width:160px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <input id="s_pw_new" type="password" placeholder="new password" style="flex:1;min-width:160px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="pwchange">Change vault password</button>
      </div>
      <div class="note" id="pwmsg"></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_ssh_pw_new" type="password" placeholder="new SSH password (min. 8 characters)" style="flex:1;min-width:220px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="sshpwchange">Set SSH password</button>
      </div>
      <div class="note">Resets the Linux login password of user <code>pi</code> for SSH access — independent of the vault password (deliberately separate: the vault password is never stored in plain text anywhere and could change independently, coupling them would be risky). Takes effect immediately, without a reboot.</div>
      <div class="note" id="sshpwmsg"></div>
      ${fld("Time zone","s_time_zone","text",c.time_zone,"Europe/Berlin")}
      ${fld("Hostname","s_teslausb_hostname","text",c.teslausb_hostname)}
    </div>
    <div class="saverow"><button class="btn primary" style="width:auto" id="savebtn">Save</button><span class="note" id="savemsg"></span></div>`;
  m.append(box);
  $("#teslaloginbtn").onclick=async()=>{
    $("#teslamsg").textContent="fetching login link…";
    try{const r=await jget("api/tesla/login_url");window.open(r.url,"_blank");$("#teslamsg").textContent="Tab opened – after login paste the address here.";}
    catch(e){$("#teslamsg").textContent="✗ Error fetching the login link";}
  };
  $("#teslaexchange").onclick=async()=>{
    const cb=$("#s_tesla_callback").value.trim();
    if(!cb){$("#teslamsg").textContent="Please paste the callback URL first";return;}
    $("#teslamsg").textContent="checking…";
    try{
      const r=await jpost("api/tesla/exchange",{callback:cb});
      if(r.ok){$("#teslamsg").textContent="✓ signed in";$("#teslastatus").textContent="✓ signed in"+(r.refresh?" (stays valid automatically)":"");toast("Tesla login successful");}
      else{$("#teslamsg").textContent="✗ "+(r.error||"Error");}
    }catch(e){$("#teslamsg").textContent="✗ Connection error";}
  };
  $("#nastest").onclick=async()=>{$("#nasmsg").textContent="Testing…";
    const r=await jget("api/nas/test");$("#nasmsg").textContent=r.ok?("✓ OK"+(r.writable?" (writable)":" (read-only)")):("✗ "+(r.error||"Error"));};
  $("#pwchange").onclick=async()=>{
    const oldp=$("#s_pw_old").value,newp=$("#s_pw_new").value;
    if(!oldp||!newp){$("#pwmsg").textContent="✗ please fill in both fields";return;}
    if(newp.length<8){$("#pwmsg").textContent="✗ new password should be at least 8 characters";return;}
    $("#pwmsg").textContent="changing…";
    const r=await jpost("api/vault/change_pass",{old:oldp,new:newp});
    if(r.ok){$("#pwmsg").textContent="✓ password changed";$("#s_pw_old").value="";$("#s_pw_new").value="";toast("Vault password changed");}
    else{$("#pwmsg").textContent="✗ "+(r.error||"Error");}
  };
  $("#sshpwchange").onclick=async()=>{
    const newp=$("#s_ssh_pw_new").value;
    if(!newp||newp.length<8){$("#sshpwmsg").textContent="✗ password should be at least 8 characters";return;}
    $("#sshpwmsg").textContent="setting…";
    try{
      const r=await jpost("api/system/ssh_password",{password:newp});
      if(r.ok){$("#sshpwmsg").textContent="✓ SSH password set";$("#s_ssh_pw_new").value="";toast("SSH password changed");}
      else{$("#sshpwmsg").textContent="✗ "+(r.error||"Error");}
    }catch(e){$("#sshpwmsg").textContent="✗ Connection error";}
  };
  $("#sambapwchange").onclick=async()=>{
    const newp=$("#s_samba_pw_new").value;
    if(!newp||newp.length<8){$("#sambapwmsg").textContent="✗ password should be at least 8 characters";return;}
    $("#sambapwmsg").textContent="setting…";
    try{
      const r=await jpost("api/system/samba_password",{password:newp});
      if(r.ok){$("#sambapwmsg").textContent="✓ SMB password set";$("#s_samba_pw_new").value="";toast("SMB password changed");}
      else{$("#sambapwmsg").textContent="✗ "+(r.error||"Error");}
    }catch(e){$("#sambapwmsg").textContent="✗ Connection error";}
  };
  $("#backup_import_btn").onclick=async()=>{
    const file=$("#backup_import_file").files[0];
    if(!file){$("#backup_import_msg").textContent="✗ please select a file first";return;}
    if(!confirm("Overwrite all current settings with this file?"))return;
    $("#backup_import_msg").textContent="importing…";
    try{
      const text=await file.text();
      const r=await jpost("api/backup/import",{content:text});
      if(r.ok){$("#backup_import_msg").textContent="✓ imported -- reload the page, then restart the Hub/Pi so everything takes effect";toast("Settings imported");}
      else{$("#backup_import_msg").textContent="✗ "+(r.error||"Error");}
    }catch(e){$("#backup_import_msg").textContent="✗ Connection error";}
  };
  async function refreshSambaStatus(){
    const el=$("#sambastatus");
    if(!el)return;
    let r;try{r=await jget("api/samba/status");}catch(e){setTimeout(refreshSambaStatus,15000);return;}
    if(!r.installed)el.textContent="not installed -- run hub/install.sh again";
    else el.textContent=(r.active?"✓ active":"off")+" -- "+r.share;
    if(document.body.contains(el))setTimeout(refreshSambaStatus,15000);
  }
  refreshSambaStatus();
  async function refreshPairingStatus(){
    try{
      const r=await jget("api/nas/raw_keys/pairing");
      $("#pairingstatus").textContent=r.paired?("Pairing status: ✓ paired (token "+r.token_prefix+"…)"):"Pairing status: not paired yet (created on the first upload)";
    }catch(e){$("#pairingstatus").textContent="Pairing status: unknown";}
  }
  refreshPairingStatus();
  $("#rawkeypush").onclick=async()=>{
    $("#rawkeymsg").textContent="uploading…";
    const r=await jpost("api/nas/raw_keys/push",{});
    $("#rawkeymsg").textContent=r.ok?`✓ ${r.written||0} raw keys written`:"✗ "+(r.error||(r.errors&&r.errors[0])||"Error");
    refreshPairingStatus();
  };
  $("#pairingreset").onclick=async()=>{
    if(!confirm("Really reset the pairing? On the next upload a new pairing with the currently reachable NAS will be created."))return;
    $("#rawkeymsg").textContent="resetting…";
    const r=await jpost("api/nas/raw_keys/reset_pairing",{});
    $("#rawkeymsg").textContent=r.ok?"✓ pairing reset":"✗ "+(r.error||"Error");
    refreshPairingStatus();
  };
  $("#mediasync").onclick=async()=>{
    const p=$("#s_sync_media_path").value.trim();
    if(!p){$("#mediasyncmsg").textContent="✗ please enter a sync path first";return;}
    $("#mediasyncmsg").textContent="saving path…";
    const sr=await jpost("api/settings",{sync_media_path:p});
    if(!sr.ok){$("#mediasyncmsg").textContent="✗ "+(sr.error||"Path could not be saved");return;}
    $("#mediasyncmsg").textContent="syncing…";
    let before=0;try{before=(await jget("api/nas/media_status")).t||0;}catch(e){}
    await jpost("api/nas/sync_media",{});
    const poll=async()=>{
      let st;try{st=await jget("api/nas/media_status");}catch(e){return;}
      if(!st.t||st.t<=before){setTimeout(poll,1500);return;}
      $("#mediasyncmsg").textContent=st.ok?`✓ done (${st.copied}/3 folders)`:"✗ "+(st.error||"Error");
    };
    setTimeout(poll,2000);
  };
  $("#savebtn").onclick=async()=>{
    const fields=["archive_server","share_name","share_user","ssid","ap_ssid","tesla_ble_vin",
      "telegram_chat_id","retention_days","retention_free_gb","vault_autolock_min","time_zone","teslausb_hostname","sync_media_path",
      "mqtt_host","mqtt_port","mqtt_user","archive_savedclips_last_minutes","archive_bwlimit_kbps",
      "wg_peer_pubkey","wg_endpoint","wg_allowed_ips","wg_address","wg_keepalive","wg_dns"];
    const secrets=["share_password","wifipass","ap_pass","teslafi_api_token","tessie_api_token",
      "pushover_user_key","pushover_app_key","telegram_bot_token","mqtt_password","wg_psk","wg_privkey"];
    const bools=["archive_recentclips","archive_savedclips","archive_sentryclips","sync_all_content",
      "ssh_disable_password","pushover_enabled","telegram_enabled","mqtt_enabled","nas_raw_keys","samba_enabled",
      "nas_skip_deleted"];
    const body={};
    fields.forEach(f=>body[f]=($("#s_"+f)||{}).value||"");
    secrets.forEach(f=>{const v=($("#s_"+f)||{}).value;if(v)body[f]=v;});
    bools.forEach(f=>body[f]=($("#s_"+f)||{}).checked||false);
    body.retention_mode=$("#s_retention_mode").value;
    $("#savemsg").textContent="Saving…";
    const r=await jpost("api/settings",body);
    $("#savemsg").textContent=r.ok?"✓ saved (takes effect after archive restart/reboot)":"✗ "+(r.error||"Error");
    if(r.ok)toast("Saved");
  };
  async function refreshApFallback(){
    const statusEl=$("#apfallback_status");
    if(!statusEl)return;
    let r;try{r=await jget("api/ap_fallback/status");}catch(e){setTimeout(refreshApFallback,15000);return;}
    const onBtn=$("#apfallback_on"),offBtn=$("#apfallback_off");
    if(r.enabled){
      onBtn.style.display="none";offBtn.style.display="";
      statusEl.textContent=r.ap_broadcasting?"active -- AP broadcasting now (home Wi-Fi unreachable)":
        r.home_wifi_connected?"ready (home Wi-Fi connected)":"ready (home Wi-Fi status unclear)";
    }else{
      onBtn.style.display="";offBtn.style.display="none";
      statusEl.textContent="off";
    }
    if(document.body.contains(statusEl))setTimeout(refreshApFallback,15000);
  }
  $("#apfallback_on").onclick=async()=>{
    $("#apfallback_on").disabled=true;
    $("#apfallback_status").textContent="turning on…";
    try{
      const r=await jpost("api/settings",{ap_fallback_only:true});
      if(!r.ok)$("#apfallback_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#apfallback_status").textContent="✗ Connection error";}
    $("#apfallback_on").disabled=false;
    refreshApFallback();
  };
  $("#apfallback_off").onclick=async()=>{
    $("#apfallback_off").disabled=true;
    $("#apfallback_status").textContent="turning off…";
    try{
      const r=await jpost("api/settings",{ap_fallback_only:false});
      if(!r.ok)$("#apfallback_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#apfallback_status").textContent="✗ Connection error";}
    $("#apfallback_off").disabled=false;
    refreshApFallback();
  };
  refreshApFallback();
  async function refreshApUsb(){
    const row=$("#apusb_row"),note=$("#apusb_note"),statusEl=$("#apusb_status");
    if(!row)return;
    let r;try{r=await jget("api/ap_usb/status");}catch(e){setTimeout(refreshApUsb,15000);return;}
    if(!r.usb_available){
      row.style.display="none";note.style.display="none";
    }else{
      row.style.display="";note.style.display="";
      $("#apusb_device").textContent=r.usb_device;
      const onBtn=$("#apusb_on"),offBtn=$("#apusb_off");
      if(r.ap_on_usb){
        onBtn.style.display="none";offBtn.style.display="";
        statusEl.textContent="✓ active on "+r.usb_device;
      }else{
        onBtn.style.display="";offBtn.style.display="none";
        statusEl.textContent="still on onboard chip";
      }
    }
    if(document.body.contains(row))setTimeout(refreshApUsb,15000);
  }
  $("#apusb_on").onclick=async()=>{
    $("#apusb_on").disabled=true;
    $("#apusb_status").textContent="moving…";
    try{
      const r=await jpost("api/settings",{ap_on_usb:true});
      if(!r.ok)$("#apusb_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#apusb_status").textContent="✗ Connection error";}
    $("#apusb_on").disabled=false;
    refreshApUsb();
  };
  $("#apusb_off").onclick=async()=>{
    $("#apusb_off").disabled=true;
    $("#apusb_status").textContent="turning off…";
    try{
      const r=await jpost("api/settings",{ap_on_usb:false});
      if(!r.ok)$("#apusb_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#apusb_status").textContent="✗ Connection error";}
    $("#apusb_off").disabled=false;
    refreshApUsb();
  };
  refreshApUsb();
  function renderWifi(r){
    const box=$("#wifi_list");if(!box)return;
    box.innerHTML="";
    const badge=n=>n.connected?"✓ connected":(n.in_range?"in range":"");
    const row=(ic,name,info,acts)=>{
      const it=el("div","fitem");it.append(el("div","ic",ic));
      const nm=el("div","nm");nm.textContent=name;it.append(nm);
      const sz=el("div","sz");sz.textContent=info;it.append(sz);
      const a=el("div","act");a.style.opacity=1;acts.forEach(b=>a.append(b));it.append(a);
      box.append(it);
    };
    if(r.home&&r.home.ssid)row("🏠",r.home.ssid+" (Home)",badge(r.home),[]);
    const nets=r.networks||[];
    nets.forEach((n,i)=>{
      const up=el("button","iconbtn","⬆️");up.title="Raise priority";up.disabled=i===0;
      up.onclick=()=>wifiOp("move",{ssid:n.ssid,delta:-1});
      const dn=el("button","iconbtn","⬇️");dn.title="Lower priority";dn.disabled=i===nets.length-1;
      dn.onclick=()=>wifiOp("move",{ssid:n.ssid,delta:1});
      const del=el("button","iconbtn","🗑️");del.title="Remove";
      del.onclick=()=>{if(confirm("Remove Wi-Fi \""+n.ssid+"\"?"+(n.connected?"\n\nThe Pi is currently connected through it and will lose the connection.":"")))wifiOp("remove",{ssid:n.ssid});};
      row("📶",(i+1)+". "+n.ssid+(n.has_password?"":" (open)"),badge(n),[up,dn,del]);
    });
    if(!nets.length)box.append(el("div","note","No additional Wi-Fi networks yet."));
  }
  async function wifiOp(op,body){
    const m=$("#wifi_msg");m.textContent="saving…";
    try{
      const r=await jpost("api/wifi/"+op,body);
      if(r.ok){m.textContent="✓ saved";renderWifi(r);}else m.textContent="✗ "+(r.error||"Error");
      return !!r.ok;
    }catch(e){m.textContent="✗ Connection error";return false;}
  }
  $("#wifi_add").onclick=async()=>{
    const s=$("#wifi_new_ssid").value.trim();
    if(!s){$("#wifi_msg").textContent="✗ SSID missing";return;}
    if(await wifiOp("add",{ssid:s,password:$("#wifi_new_pass").value})){$("#wifi_new_ssid").value="";$("#wifi_new_pass").value="";}
  };
  jget("api/wifi/networks").then(renderWifi).catch(()=>{});
  $("#s_home_wifi_prefer").onchange=async e=>{
    $("#home_wifi_msg").textContent="saving…";
    try{
      const r=await jpost("api/settings",{home_wifi_prefer:e.target.checked});
      $("#home_wifi_msg").textContent=r.ok?"✓ saved":"✗ "+(r.error||"Error");
    }catch(err){$("#home_wifi_msg").textContent="✗ Connection error";}
  };
  $("#wg_qr_import").onclick=async()=>{
    const file=($("#wg_qr_file").files||[])[0];
    if(!file){$("#wg_qr_msg").textContent="✗ please select an image first";return;}
    $("#wg_qr_msg").textContent="reading QR code…";
    try{
      const dataUrl=await new Promise((resolve,reject)=>{
        const fr=new FileReader();
        fr.onload=()=>resolve(String(fr.result||""));
        fr.onerror=()=>reject(new Error("read failed"));
        fr.readAsDataURL(file);
      });
      const r=await jpost("api/wireguard/import_qr",{image:dataUrl});
      if(!r.ok){$("#wg_qr_msg").textContent="✗ "+(r.error||"Error while reading");return;}
      const cfg=r.config||{};
      if(cfg.peer_pubkey)$("#s_wg_peer_pubkey").value=cfg.peer_pubkey;
      if(cfg.endpoint)$("#s_wg_endpoint").value=cfg.endpoint;
      if(cfg.allowed_ips)$("#s_wg_allowed_ips").value=cfg.allowed_ips;
      if(cfg.address)$("#s_wg_address").value=cfg.address;
      if(cfg.keepalive)$("#s_wg_keepalive").value=cfg.keepalive;
      if(cfg.psk)$("#s_wg_psk").value=cfg.psk;
      if(cfg.privkey)$("#s_wg_privkey").value=cfg.privkey;
      if(cfg.dns)$("#s_wg_dns").value=cfg.dns;
      $("#wg_qr_msg").textContent="✓ Fields filled in -- check below and save with \"Save\"";
      toast("QR code read");
    }catch(e){$("#wg_qr_msg").textContent="✗ Error reading the file";}
  };
  async function refreshWg(){
    const statusEl=$("#wg_status");
    if(!statusEl)return;
    let r;try{r=await jget("api/wireguard/status");}catch(e){setTimeout(refreshWg,15000);return;}
    const pkEl=$("#wg_own_pubkey");
    if(pkEl)pkEl.textContent=r.own_pubkey||"–";
    const onBtn=$("#wg_on"),offBtn=$("#wg_off");
    if(r.enabled){
      onBtn.style.display="none";offBtn.style.display="";
      statusEl.textContent=r.active?
        ("✓ active"+(r.handshake?" -- last handshake "+r.handshake:"")+(r.transfer?" ("+r.transfer+")":"")):
        "starting…";
    }else{
      onBtn.style.display="";offBtn.style.display="none";
      statusEl.textContent="off";
    }
    if(document.body.contains(statusEl))setTimeout(refreshWg,15000);
  }
  $("#wg_on").onclick=async()=>{
    $("#wg_on").disabled=true;
    $("#wg_status").textContent="turning on…";
    try{
      const r=await jpost("api/settings",{wg_enabled:true});
      if(!r.ok)$("#wg_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#wg_status").textContent="✗ Connection error";}
    $("#wg_on").disabled=false;
    refreshWg();
  };
  $("#wg_off").onclick=async()=>{
    $("#wg_off").disabled=true;
    $("#wg_status").textContent="turning off…";
    try{
      const r=await jpost("api/settings",{wg_enabled:false});
      if(!r.ok)$("#wg_status").textContent="✗ "+(r.error||"Error");
    }catch(e){$("#wg_status").textContent="✗ Connection error";}
    $("#wg_off").disabled=false;
    refreshWg();
  };
  refreshWg();
}

boot();
