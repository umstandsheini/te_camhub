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
  $("#auth_title").textContent=SETUP?"Tresor einrichten":"TeslaCam Hub";
  $("#auth_sub").textContent=SETUP?"Lege ein Passwort fest. Ohne dieses Passwort sind die Aufnahmen nicht entschlüsselbar.":"Bitte anmelden.";
  $("#auth_pass2").classList.toggle("hidden",!SETUP);
  $("#auth_import_l").classList.toggle("hidden",!SETUP);
  $("#auth_go").textContent=SETUP?"Einrichten":"Anmelden";
  $("#auth_pass").value="";$("#auth_pass2").value="";$("#auth_msg").textContent="";
  $("#auth_forgot").classList.toggle("hidden",SETUP);
  $("#auth_reset_panel").classList.add("hidden");
  $("#auth_reset_confirm").value="";$("#auth_reset_msg").textContent="";
  $("#auth_pass").focus();
}
$("#auth_forgot").onclick=(e)=>{e.preventDefault();$("#auth_reset_panel").classList.toggle("hidden");};
$("#auth_reset_go").onclick=async()=>{
  const m=$("#auth_reset_msg");m.className="msg";
  if($("#auth_reset_confirm").value!=="ZURUECKSETZEN"){m.className="msg err";m.textContent="Bitte ZURUECKSETZEN genau eintippen";return;}
  m.textContent="setze zurück…";
  try{
    const r=await jpost("api/vault/factory_reset",{confirm:$("#auth_reset_confirm").value});
    if(r.ok){toast("Tresor zurückgesetzt");boot();}
    else{m.className="msg err";m.textContent=r.error||"Fehler";}
  }catch(e){m.className="msg err";m.textContent="Verbindungsfehler";}
};
async function doAuth(){
  const m=$("#auth_msg");m.className="msg";
  const p=$("#auth_pass").value;if(!p){m.textContent="Passwort erforderlich";return;}
  try{
    if(SETUP){
      if(p!==$("#auth_pass2").value){m.className="msg err";m.textContent="Passwörter stimmen nicht überein";return;}
      m.textContent="Richte ein…";
      const r=await jpost("api/setup",{pass:p,import:$("#auth_import").checked});
      if(r.ok){startApp();toast("Tresor eingerichtet ("+(r.imported||0)+" Schlüssel)");}
      else{m.className="msg err";m.textContent=r.error||"Fehler";}
    }else{
      m.textContent="Anmelden…";
      const r=await jpost("api/login",{pass:p});
      if(r.ok)startApp();else{m.className="msg err";m.textContent=r.error||"falsches Passwort";}
    }
  }catch(e){m.className="msg err";m.textContent="Verbindungsfehler";}
}

/* ---------------- shell ---------------- */
function startApp(){
  $("#auth").classList.add("hidden");$("#app").classList.remove("hidden");
  render("clips");
}
document.querySelectorAll("nav .nav[data-view]").forEach(a=>a.onclick=()=>{
  document.querySelectorAll("nav .nav").forEach(n=>n.classList.remove("active"));
  a.classList.add("active");render(a.dataset.view);
});
$("#lockbtn").onclick=async()=>{try{await jpost("api/logout",{});}catch(e){}boot();};
$("#auth_go").onclick=doAuth;
["auth_pass","auth_pass2"].forEach(id=>$("#"+id).addEventListener("keydown",e=>{if(e.key==="Enter")doAuth();}));

function render(view){
  const m=$("#main");m.innerHTML="";
  if(view==="clips")return viewClips(m);
  if(view==="files")return viewFiles(m,"");
  if(view==="videos")return viewVideos(m);
  if(view==="diag")return viewDiag(m);
  if(view==="ble")return viewBle(m);
  if(view==="canbus")return viewCanbus(m);
  if(view==="trips")return viewTrips(m);
  if(view==="settings")return viewSettings(m);
}

/* ---------------- Aufnahmen ---------------- */
async function viewClips(m){
  m.innerHTML="";
  m.append(el("h2","title","Aufnahmen"));
  const info=el("div","sub","lädt…");m.append(info);
  const nasrow=el("div","sub nasrow","NAS-Archiv: lädt…");m.append(nasrow);
  refreshNasStatus(nasrow);
  const bar=el("div","saverow");
  const bulkbtn=el("button","btn sm","🔓 Alle entschlüsseln + Metadaten erzeugen");
  const bulkmsg=el("span","note","");
  const syncbtn=el("button","btn sm ghost","🔄 Jetzt synchronisieren");
  const syncmsg=el("span","note","");
  bar.append(bulkbtn,bulkmsg,syncbtn,syncmsg);m.append(bar);
  const grid=el("div","clipgrid");m.append(grid);
  let clips;try{clips=await jget("api/clips");}catch(e){return;}
  const enc=clips.filter(c=>c.encrypted).length;
  info.textContent=`${clips.length} Clips · ${enc} verschlüsselt`;
  let nasClips={};try{nasClips=(await jget("api/nas/sync_status")).clips||{};}catch(e){}
  if(!clips.length){info.textContent="Keine Aufnahmen gefunden.";return;}
  syncbtn.onclick=async()=>{
    syncbtn.disabled=true;syncmsg.textContent="starte Archivierung…";
    try{await jpost("api/sync",{});}catch(e){}
    syncmsg.textContent="Archivierung läuft (Auto trennt kurz die USB-Verbindung)…";
    setTimeout(async()=>{
      await jpost("api/nas/sync_status/refresh",{});
      setTimeout(()=>{syncbtn.disabled=false;syncmsg.textContent="✓ ausgelöst";toast("Sync ausgelöst, Status aktualisiert sich gleich");viewClips(m);},15000);
    },20000);
  };
  bulkbtn.onclick=async()=>{
    bulkbtn.disabled=true;bulkmsg.textContent="startet…";
    let st;try{st=await jpost("api/bulk_prepare",{});}catch(e){bulkbtn.disabled=false;bulkmsg.textContent="✗ Fehler";return;}
    const poll=async()=>{
      try{st=await jget("api/bulk_prepare");}catch(e){return;}
      bulkmsg.textContent=st.total?`${st.done} / ${st.total}…`:"nichts zu tun";
      if(st.running){setTimeout(poll,1500);}
      else{
        bulkbtn.disabled=false;
        bulkmsg.textContent=st.errors&&st.errors.length?`fertig, ${st.errors.length} Fehler`:(st.total?"✓ fertig":"nichts zu tun");
        toast("Entschlüsselung abgeschlossen");
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
    b.append(el("span","badge "+(c.encrypted?"enc":"plain"),c.encrypted?"🔒 verschlüsselt":"offen"));
    // Every segment of an event folder carries has_event, but only one of
    // them (is_trigger) is the segment the trigger actually happened in --
    // give that one a distinct 🎯 badge instead of the same "Event" label
    // on every segment (ported from Te_FITI's trigger-segment marking).
    if(c.is_trigger){
      const at=c.event_at!=null?` bei ${Math.floor(c.event_at/60)}:${String(Math.round(c.event_at%60)).padStart(2,"0")}`:"";
      b.append(el("span","badge trigger",(c.reason?c.reason+" ":"")+"🎯 Auslöser"+at));
    }else if(c.has_event){
      b.append(el("span","badge event",(c.reason||"Event")+" (anderes Segment)"));
    }
    if(c.encrypted){
      const kk=c.cams_keyed||0,kt=c.cams_encrypted||0;
      const keyCls=kt===0?"locked":kk===0?"locked":kk<kt?"keypartial":"keyok";
      const keyTxt=kk===0?"🔑 kein Schlüssel":kk<kt?`🔑 ${kk}/${kt} Kameras`:"🔑 Schlüssel vorhanden";
      b.append(el("span","badge "+keyCls,keyTxt));
    }
    b.append(el("span","badge "+(nasClips[c.id]?"nasok":"nasno"),nasClips[c.id]?"☁️ auf NAS":"☁️ noch nicht"));
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
    nasrow.append(el("span",null,`NAS-Archiv: wird übertragen… ${p.done||0} / ${p.total} Dateien (${pct}%) `));
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
    nasrow.append(el("span",null,`NAS-Archiv: Übertragung unterbrochen (Netzwerk) bei ${p.done||0} / ${p.total} Dateien -- wird automatisch erneut versucht `));
    const rl=el("a",null,"jetzt prüfen");
    rl.onclick=()=>refreshNasStatus(nasrow);
    nasrow.append(rl);
    return;
  }
  let s;try{s=await jget("api/nas/sync_status");}catch(e){return;}
  nasrow.innerHTML="";
  if(s.ok===null){nasrow.append(el("span",null,"NAS-Archiv: noch nicht geprüft "));}
  else if(!s.ok){nasrow.append(el("span",null,"NAS-Archiv: nicht erreichbar ("+(s.error||"Fehler")+") "));}
  else{nasrow.append(el("span",null,`NAS-Archiv: ${s.percent}% archiviert (${s.on_nas}/${s.total} Clips) `));}
  const rl=el("a",null,"jetzt prüfen");
  rl.onclick=async()=>{nasrow.querySelector("span").textContent="NAS-Archiv: prüfe…";await jpost("api/nas/sync_status/refresh",{});setTimeout(()=>refreshNasStatus(nasrow),20000);};
  nasrow.append(rl);
}
/* ---------------- Player: synced multi-cam, event-seek, GPS map, HUD ---------------- */
const CAMS=[["front","Front","a-front"],["back","Heck","a-back"],
  ["left_repeater","Links","a-left"],["right_repeater","Rechts","a-right"],
  ["left_pillar","Links (Säule)","a-lp"],["right_pillar","Rechts (Säule)","a-rp"]];
const REASON_LABELS={
  user_interaction_dashcam_icon_tapped:"Dashcam-Taste",
  user_interaction_dashcam_panel_save:"manuell gespeichert",
  sentry_aware_object_detection:"Sentry: Objekt erkannt",
  sentry_aware_accel_detection:"Sentry: Erschütterung",
  sentry_aware_alarm_state:"Sentry: Alarm",
  honk:"Hupe"};
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
    <span class="note" id="pstatus">lädt…</span>
    <label class="tc hidden" id="tc_hud"><input type="checkbox" id="t_hud" checked> HUD</label>
    <label class="tc hidden" id="tc_map"><input type="checkbox" id="t_map"> Karte</label>
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
  try{res=await jpost("api/prepare",{id:c.id});}catch(e){status.textContent="✗ Verbindungsfehler";return;}
  if(!res||!res.cameras){status.textContent="✗ "+(res&&res.error?res.error:"Fehler");return;}
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
      const dl=el("a","iconbtn dlcam","⬇");dl.href=info.url;dl.download=cam+".mp4";dl.title="Kamera herunterladen";
      const fs=el("button","iconbtn fscam","⛶");fs.title="Vollbild";fs.onclick=(e)=>{e.stopPropagation();(v.requestFullscreen||v.webkitEnterFullscreen||function(){}).call(v);};
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
  if(!any){grid.append(el("div","note","Kein Video verfügbar – kein Schlüssel für diesen Clip."));return;}
  if(res.errors&&res.errors.length)toast("Fehler: "+res.errors.join(", "));

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

/* ---------------- Dateien ---------------- */
async function viewFiles(m,path){
  m.innerHTML="";
  m.append(el("h2","title","Dateien"));
  const crumbs=el("div","crumbs");m.append(crumbs);
  const bar=el("div","saverow");
  const up=el("label","btn sm","⬆️ Hochladen");const fin=el("input");fin.type="file";fin.className="hidden";fin.multiple=true;up.append(fin);
  const mk=el("button","btn sm ghost","➕ Ordner");
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
      const lc=el("button","iconbtn","🔔");lc.title="Als LockChime festlegen (überschreibt Boombox/LockChime.wav)";
      lc.onclick=async e=>{e.stopPropagation();if(confirm("„"+ent.name+"“ als LockChime festlegen? Überschreibt Boombox/LockChime.wav.")){
        const r=await jpost("api/files/lockchime",{path:rel});
        if(r.ok){toast("LockChime gesetzt: "+ent.name);viewFiles($("#main"),path);}else{toast("✗ "+(r.error||"Fehler"));}
      }};
      act.append(lc);
    }
    const rn=el("button","iconbtn","✏️");rn.title="Umbenennen";rn.onclick=async e=>{e.stopPropagation();const n=prompt("Neuer Name",ent.name);if(n){await jpost("api/files/rename",{path:rel,name:n});viewFiles($("#main"),path);}};
    const del=el("button","iconbtn","🗑️");del.title="Löschen";del.onclick=async e=>{e.stopPropagation();if(confirm("Löschen: "+ent.name+"?")){await jpost("api/files/delete",{path:rel});viewFiles($("#main"),path);}};
    act.append(rn,del);it.append(act);
    it.onclick=()=>{if(ent.dir)viewFiles($("#main"),rel);else if(ent.image)lightbox(rel,ent.name);else if(ent.audio)audioPlayer(rel,ent.name);};
    list.append(it);
  });
  mk.onclick=async()=>{const n=prompt("Ordnername");if(n){await jpost("api/files/mkdir",{path:(path?path+"/":"")+n});viewFiles($("#main"),path);}};
  fin.onchange=async()=>{
    for(const f of fin.files){
      toast("Lade "+f.name+"…");
      await api("api/files/upload?path="+encodeURIComponent(path)+"&name="+encodeURIComponent(f.name),{method:"PUT",body:f});
    }
    toast("Upload fertig");viewFiles($("#main"),path);
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

/* ---------------- Videos ---------------- */
async function viewVideos(m){
  m.append(el("h2","title","Videos"));
  m.append(el("div","note","Dateien landen hier über die SMB-Freigabe „Videos“ (\\\\"+location.hostname+"\\Videos). MKV & Co. werden beim ersten Abspielen automatisch in ein browserkompatibles Format umgewandelt -- das kann beim ersten Mal etwas dauern, ist danach aber sofort da."));
  const playerWrap=el("div","card");playerWrap.style.display="none";
  playerWrap.innerHTML=`<video id="videoplayer" controls style="width:100%;max-height:70vh;background:#000;border-radius:10px"></video>
    <div class="saverow" style="margin-top:10px"><span class="note" id="videoplayer_cap"></span></div>`;
  m.append(playerWrap);
  const list=el("div","filelist");m.append(list);

  async function refresh(){
    list.innerHTML="lädt…";
    let data;try{data=await jget("api/videos");}catch(e){list.textContent="✗ Fehler beim Laden";return;}
    const vids=data.videos||[];
    list.innerHTML="";
    if(!vids.length){list.append(el("div","note","Noch keine Videos im Ordner. Einfach per SMB draufkopieren."));return;}
    for(const v of vids){
      const it=el("div","fileitem");
      it.innerHTML=`<span class="ic">🎞️</span>`;
      const nm=el("div","nm",v.name);it.append(nm);
      it.append(el("div","sz",human(v.size)+(v.ready?"":" · wird beim Abspielen vorbereitet")));
      const act=el("div","act");
      const play=el("button","iconbtn","▶️");play.title="Abspielen";
      play.onclick=()=>playVideo(v.name);
      act.append(play);
      const del=el("button","iconbtn","🗑️");del.title="Vorbereitete Kopie löschen (bei Bedarf neu erzeugen)";
      del.onclick=async e=>{e.stopPropagation();await jpost("api/videos/delete_cache",{name:v.name});toast("Zurückgesetzt");refresh();};
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
    cap.textContent=name+" -- wird vorbereitet…";
    try{
      const r=await jpost("api/videos/prepare",{name});
      if(!r.ok){cap.textContent="✗ "+(r.error||"Fehler");return;}
      let st=r;
      while(st.state==="working"){
        await new Promise(res=>setTimeout(res,2000));
        st=await jget("api/videos/status?name="+encodeURIComponent(name));
      }
      if(st.state!=="done"){cap.textContent="✗ "+(st.error||"Fehler bei der Vorbereitung");return;}
      cap.textContent=name;
      vid.src="media/videos/"+encodeURIComponent(name);
      vid.play().catch(()=>{});
    }catch(e){cap.textContent="✗ Verbindungsfehler";}
  }

  refresh();
}

/* ---------------- Diagnose ---------------- */
async function viewDiag(m){
  m.append(el("h2","title","Diagnose"));
  const stats=el("div","stats");m.append(stats);
  const logcard=el("div","card");m.append(logcard);
  let s;try{s=await jget("api/status");}catch(e){return;}
  const d=s.diag||{};
  const items=[["Temperatur",d.temp||"–"],["Uptime",d.uptime||"–"],["WLAN",d.wifi_ssid||"–"],
    ["USB am Auto",d.gadget_active?"aktiv":"—"],["Clips",s.clips],["verschlüsselt",s.encrypted]];
  items.forEach(([k,v])=>{const c=el("div","stat");c.append(el("div","k",k));c.append(el("div","v",String(v)));stats.append(c);});
  const actions=el("div","saverow");
  const rb=el("button","btn sm","♻️ Neustart");rb.onclick=async()=>{if(confirm("Pi neu starten?")){await jpost("api/reboot",{});toast("Startet neu…");}};
  const td=el("button","btn sm ghost","🔀 Laufwerke togglen");td.onclick=async()=>{await jpost("api/toggle_drives",{});toast("getoggelt");};
  actions.append(rb,td);m.insertBefore(actions,logcard);
  logcard.append(el("h3",null,"Logs"));
  const sel=el("select");["archiveloop","setup","sync","retention"].forEach(w=>{const o=el("option",null,w);o.value=w;sel.append(o);});
  const box=el("div","logbox","lädt…");
  const load=async()=>{const r=await jget("api/log?which="+sel.value);box.textContent=r.text||"(leer)";box.scrollTop=box.scrollHeight;};
  sel.onchange=load;const f=el("div","field");f.append(sel);logcard.append(f,box);load();
}

/* ---------------- Einstellungen ---------------- */
function fld(label,id,type,val,ph){return `<div class="field"><label>${label}</label><input id="${id}" type="${type||'text'}" value="${val==null?'':String(val).replace(/"/g,'&quot;')}" ${ph?`placeholder="${ph}"`:''}></div>`;}
function chk(label,id,on){return `<label class="checkline"><input type="checkbox" id="${id}" ${on?'checked':''}> ${label}</label>`;}
/* ---------------- Fahrzeug (BLE) ---------------- */
async function viewBle(m){
  m.append(el("h2","title","Fahrzeug (BLE)"));
  let c;try{c=await jget("api/settings");}catch(e){c={};}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Fahrzeug</h3>
      ${fld("Fahrzeug-VIN","ble_vin","text",c.tesla_ble_vin)}
      <div class="saverow"><button class="btn sm ghost" id="blevinsave">VIN speichern</button><span class="note" id="blevinmsg"></span></div>
    </div>
    <div class="card"><h3>Auto wach halten</h3>
      <div class="note">Sendet alle 5 Minuten per BLE einen <code>wake</code>-Befehl ans Auto, damit es nicht einschläft -- z.&nbsp;B. während einer längeren Aufräum-/Reinigungsaktion. (<code>keep-accessory-power</code> allein reicht dafür nicht: laut Tesla gilt das nicht für den von Dashcam/USB genutzten Datenport -- das haben wir in der Praxis auch so beobachtet, deshalb der periodische Nudge statt eines einmaligen Befehls.) Schaltet sich nach der eingestellten Zeit automatisch wieder aus, "Jetzt beenden" geht jederzeit vorzeitig. Braucht den gekoppelten <b>Wachhalten</b>-Schlüssel unten.</div>
      <div class="saverow"><span class="note" id="keepawake_status">lädt…</span></div>
      <div class="saverow" style="flex-wrap:wrap">
        <input type="number" id="keepawake_hours" value="24" min="1" max="72" style="width:70px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <span class="note">Stunden</span>
        <button class="btn sm" id="keepawake_on">Einschalten</button>
        <button class="btn sm ghost" id="keepawake_off" style="display:none">Jetzt beenden</button>
      </div>
    </div>
    <div class="card"><h3>BLE-Programme</h3>
      <div class="note">Die offiziellen Tesla-Kommandozeilenwerkzeuge (<code>tesla-control</code>, <code>tesla-keygen</code>), mit denen BLE-Schlüssel erzeugt und gekoppelt werden.</div>
      <div class="saverow" style="flex-wrap:wrap">
        <button class="btn sm ghost" id="bleinstall">BLE-Programme installieren</button>
        <span class="note" id="bleinstallmsg"></span>
      </div>
    </div>
    <div class="card"><h3>Schlüssel &amp; Rollen</h3>
      <div class="note">BLE-Schlüssel werden mit einer <b>Rolle</b> gekoppelt, die festlegt, was der Schlüssel darf. Statt wie früher immer mit vollem "Owner"-Zugriff zu koppeln, lassen sich hier gezielt eingeschränkte Rollen für getrennte Zwecke koppeln (jede Rolle = ein eigener, unabhängiger Schlüssel).</div>
      <div class="ble-row">
        <div><b>Wachhalten</b> <span class="note">(Rolle: charging_manager)</span></div>
        <div class="saverow" style="flex-wrap:wrap">
          <button class="btn sm ghost" id="blepair_awake">Koppeln</button>
          <span class="note" id="blemsg_awake">–</span>
        </div>
      </div>
      <div class="note">Die Rolle <code>vehicle_monitor</code> wird hier nicht mehr angeboten. Falls sie vorher schon gekoppelt wurde, bleibt der Schlüssel bis auf Weiteres auf dem Auto eingetragen — unsere eingeschränkten Schlüssel können sich nicht selbst entfernen. Entfernen geht nur über die Tesla-App (Sicherheit &amp; Fahrzeugzugriff → Schlüssel) oder am Touchscreen.</div>
      <div class="note warn">⚠ Sicherheitshinweis: Jeder gekoppelte private Schlüssel liegt unverschlüsselt als Datei auf dem Stick (<code>/root/.ble/&lt;name&gt;/key_private.pem</code>); wer physischen Zugriff auf den Stick bekommt, könnte ihn kopieren und (nur in Bluetooth-Reichweite des Autos) im Rahmen seiner Rolle missbrauchen. Empfehlung: <b>PIN-to-Drive</b> im Auto aktivieren und bei Verlust/Diebstahl des Sticks alle BLE-Schlüssel sofort in der Tesla-App entfernen.</div>
    </div>
    <div class="card" id="ble_reads_card" style="display:none"><h3>Sensoren (lesen)</h3>
      <div class="note">Nur Befehle, die für diesen Schlüssel getestet und bestätigt erlaubt sind. Jeder Wert wird erst beim Klick auf "Lesen" wirklich vom Auto abgefragt.</div>
      <div id="ble_reads_list"></div>
    </div>
    <div class="card" id="ble_actions_card" style="display:none"><h3>Befehle (auslösen)</h3>
      <div class="note">Sendet echte Befehle ans Auto. Befehle, die das Fahrzeug mit einem Rechte-Fehler ablehnen, verschwinden automatisch aus dieser Liste.</div>
      <div class="note">Kein Handschuhfach-Befehl hier: Teslas offizielles Kommandowerkzeug (<code>tesla-control</code>) kennt keinen "Glovebox"-Befehl -- nur Kofferraum/Frunk. Das Handschuhfach lässt sich nur über ein rohes, unsigniertes CAN-Signal auslösen, nicht über diesen authentifizierten Kanal -- dafür gibt es den Bereich "⚠ Experimentell: Rohbefehle" unter <b>CAN-Bus</b>.</div>
      <div id="ble_actions_list"></div>
      <div class="saverow"><button class="btn sm ghost" id="ble_reset_unavailable">Ausgeblendete Befehle erneut versuchen</button><span class="note" id="ble_reset_msg"></span></div>
    </div>`;
  m.append(box);
  $("#blevinsave").onclick=async()=>{
    const v=$("#ble_vin").value.trim();
    $("#blevinmsg").textContent="speichere…";
    try{
      const r=await jpost("api/settings",{tesla_ble_vin:v});
      $("#blevinmsg").textContent=r.ok?"✓ gespeichert":"✗ "+(r.error||"Fehler");
    }catch(e){$("#blevinmsg").textContent="✗ Verbindungsfehler";}
  };
  $("#bleinstall").onclick=async()=>{
    $("#bleinstallmsg").textContent="installiere… (kann eine Weile dauern)";
    try{
      const r=await jpost("api/ble/install",{});
      $("#bleinstallmsg").textContent=r.ok?(r.already?"✓ bereits installiert":"✓ installiert"):"✗ "+(r.error||"Fehler");
    }catch(e){
      $("#bleinstallmsg").textContent="✗ Verbindungsfehler – bitte erneut versuchen";
    }
  };
  function wireBlePair(id,name,role){
    $("#blepair_"+id).onclick=async()=>{
      $("#blemsg_"+id).textContent="koppele…";
      try{
        const r=await jpost("api/ble/pair",{name,role});
        if(!r.ok){$("#blemsg_"+id).textContent="✗ "+(r.error||"Fehler");return;}
        $("#blemsg_"+id).textContent="Anfrage gesendet – jetzt am Auto Schlüsselkarte an die Konsole halten und am Bildschirm bestätigen";
        pollBlePaired(id,name,40,3000,role);
      }catch(e){
        $("#blemsg_"+id).textContent="✗ Verbindungsfehler – bitte erneut versuchen";
      }
    };
  }
  async function pollBlePaired(id,name,triesLeft,delayMs,role){
    if(triesLeft<=0)return;
    let r;
    try{r=await jget("api/ble/status?name="+name);}catch(e){return;}
    if(r.paired){$("#blemsg_"+id).textContent="✓ gekoppelt";loadBleCommands(id);return;}
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
    $("#blereadmsg_"+readId).textContent="lese…";
    try{
      const r=await jpost("api/ble/read",{name:id,id:readId});
      if(!r.ok){$("#blereadmsg_"+readId).textContent="✗ "+(r.error||"Fehler");return;}
      $("#blereadmsg_"+readId).textContent="✓";
      bleValues[readId]=r.values||{};
      const entries=Object.entries(r.values||{});
      $("#blereadvals_"+readId).innerHTML=entries.length?`<table class="probe"><tbody>${entries.map(([k,v])=>
        `<tr><td>${k}</td><td class="note">${v}</td></tr>`).join("")}</tbody></table>`:"";
      Object.keys(BLE_ACTION_STATUS).concat(Object.keys(BLE_ACTION_PREFILL)).forEach(applyActionStatus);
    }catch(e){$("#blereadmsg_"+readId).textContent="✗ Verbindungsfehler";}
  }
  async function loadBleCommands(id){
    let cmds;try{cmds=await jget("api/ble/commands");}catch(e){return;}
    const readsList=$("#ble_reads_list"),actionsList=$("#ble_actions_list");
    readsList.innerHTML=cmds.reads.map(c=>`
      <div class="ble-row">
        <div>${c.label}</div>
        <div class="saverow"><button class="btn sm ghost" id="bleread_${c.id}">Lesen</button><span class="note" id="blereadmsg_${c.id}">lädt…</span></div>
        <div id="blereadvals_${c.id}" style="width:100%"></div>
      </div>`).join("");
    actionsList.innerHTML=cmds.actions.map(c=>{
      const hasStatus=c.id in BLE_ACTION_STATUS, noStatus=(c.id==="keep_accessory_power_on"||c.id==="keep_accessory_power_off");
      return `
      <div class="ble-row">
        <div>${c.label}${noStatus?' <span class="note">(kein Lesebefehl für Status vorhanden)</span>':''}</div>
        <div class="saverow">
          ${(c.id==="charging_set_limit"||c.id==="charging_set_amps")?`<input type="number" id="bleval_${c.id}" style="width:80px" placeholder="${c.id==='charging_set_limit'?'%':'A'}">`:""}
          <button class="btn sm ghost" id="bleact_${c.id}">Ausführen</button>
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
        $("#bleactmsg_"+c.id).textContent="sende…";
        const body={name:id,id:c.id};
        const valEl=$("#bleval_"+c.id);
        if(valEl&&valEl.value)body.value=valEl.value;
        try{
          const r=await jpost("api/ble/exec",body);
          const msg=r.ok?"✓ "+(r.detail||"OK"):"✗ "+(r.error||r.detail||"Fehler");
          if(!r.ok&&/INSUFFICIENT_PRIVILEGES|UNAUTHORIZED/i.test(r.error||r.detail||"")){
            toast(c.label+": vom Auto abgelehnt, wird ausgeblendet");
            loadBleCommands(id);
            return;
          }
          $("#bleactmsg_"+c.id).textContent=msg;
        }catch(e){$("#bleactmsg_"+c.id).textContent="✗ Verbindungsfehler";}
      };
    });
    $("#ble_reset_unavailable").onclick=async()=>{
      $("#ble_reset_msg").textContent="setze zurück…";
      try{await jpost("api/ble/reset_unavailable",{});$("#ble_reset_msg").textContent="✓";loadBleCommands(id);}
      catch(e){$("#ble_reset_msg").textContent="✗ Verbindungsfehler";}
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
      $("#blemsg_"+id).textContent=r.paired?"✓ gekoppelt":"noch nicht gekoppelt (Status-Check unzuverlässig -- Befehle unten trotzdem versuchen)";
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
      statusEl.textContent=`aktiv – noch ${h}h ${mn}min`;
      onBtn.style.display="none";offBtn.style.display="";hoursInp.disabled=true;
    }else{
      statusEl.textContent="aus";
      onBtn.style.display="";offBtn.style.display="none";hoursInp.disabled=false;
    }
    if(document.body.contains(statusEl))setTimeout(refreshKeepAwake,30000);
  }
  $("#keepawake_on").onclick=async()=>{
    $("#keepawake_status").textContent="schalte ein…";
    try{
      const r=await jpost("api/keepawake/start",{hours:Number($("#keepawake_hours").value)||24});
      if(!r.ok)toast("✗ "+(r.error||"Fehler"));
      refreshKeepAwake();
    }catch(e){toast("✗ Verbindungsfehler");}
  };
  $("#keepawake_off").onclick=async()=>{
    $("#keepawake_status").textContent="schalte aus…";
    try{
      const r=await jpost("api/keepawake/stop",{});
      if(!r.ok)toast("✗ "+(r.error||r.detail||"Fehler"));
      refreshKeepAwake();
    }catch(e){toast("✗ Verbindungsfehler");}
  };
  refreshKeepAwake();
}

/* ---------------- CAN-Bus ---------------- */
async function viewCanbus(m){
  m.append(el("h2","title","CAN-Bus"));
  let c;try{c=await jget("api/settings");}catch(e){c={};}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>OBD-Dongle</h3>
      ${fld("Bluetooth-Adresse (MAC)","canbus_mac","text",c.canbus_mac,"z. B. 01:1D:A5:02:2C:CB")}
      <div class="saverow"><button class="btn sm ghost" id="canbusmacsave">Adresse speichern</button><span class="note" id="canbusmacmsg"></span></div>
      <div class="note">BLE-OBD-Dongle (z. B. UniCarScan) am OBD-Port des Autos. Der Hub verbindet sich bei Bedarf kurz per Bluetooth, liest den CAN-Bus einige Sekunden mit und trennt danach wieder &mdash; kein Dauer-Polling, kein Pairing nötig.</div>
    </div>
    <div class="card"><h3>Live-Werte</h3>
      <div class="saverow" style="flex-wrap:wrap">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="canbus_monitor"><span>Kontinuierlich überwachen</span>
        </label>
        <span class="note" id="canbusmonitormsg"></span>
      </div>
      <div class="note">Liest alle paar Sekunden neu, solange aktiv, und zeigt den zuletzt bekannten Status jeder Adresse. Blockiert dabei zeitweise andere BLE-Aktionen (Wachhalten, Fahrzeugbefehle) mit, da der Pi nur einen Bluetooth-Adapter hat -- für längere Beobachtung gedacht, nicht dauerhaft nebenbei laufen lassen.</div>
      <div class="saverow">
        <input type="number" id="canbusdur" style="width:70px" value="5" min="2" max="15"> <span class="note">Sekunden</span>
        <button class="btn sm ghost" id="canbusread">Jetzt lesen</button><span class="note" id="canbusmsg">–</span>
      </div>
      <div id="canbusvals"></div>
    </div>
    <div class="card"><h3>Unbekannte CAN-IDs</h3>
      <div class="note">Alles, was der Hub noch nicht kennt -- roher Inhalt der zuletzt gesehenen Nachricht je ID. Nützlich zum Muster-Suchen: z. B. während des Lesens eine Tür öffnen/schließen oder blinken und schauen, welche ID sich ändert.</div>
      <div id="canbusunknown"></div>
    </div>
    <div class="card"><h3>⚠ Experimentell: Rohbefehle</h3>
      <div class="note warn">Schreibt unsignierte Rohdaten direkt auf den Fahrzeug-CAN-Bus -- anders als die Befehle unter "Fahrzeug (BLE)" prüft das Auto hier weder Rolle noch Signatur, es nimmt den Frame einfach an (falls das Gateway ihn überhaupt durchlässt). Nicht gegen ein echtes Fahrzeug verifiziert, keine Zähler/Prüfsumme wie in echten Tesla-Frames üblich -- kann wirkungslos bleiben oder unerwartetes Verhalten auslösen. Nur verwenden, wenn du weißt, was die gesendeten Bytes bedeuten.</div>
      <div class="saverow"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">
        <input type="checkbox" id="canbus_ack"><span>Ich weiß, was ich tue</span>
      </label></div>
      <div class="ble-row">
        <div>Handschuhfach öffnen <span class="note">(0x3B3, UI_gloveboxRequest)</span></div>
        <div class="saverow"><button class="btn sm ghost" id="canbus_glovebox" disabled>Senden</button><span class="note" id="canbus_glovebox_msg"></span></div>
      </div>
      <div class="ble-row">
        <div>Beliebiger Frame</div>
        <div class="saverow" style="flex-wrap:wrap">
          <input type="text" id="canbus_raw_id" placeholder="CAN-ID (hex, z. B. 3B3)" style="width:170px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
          <input type="text" id="canbus_raw_data" placeholder="Daten (hex, max. 8 Byte, z. B. 01000000)" style="width:230px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
          <button class="btn sm ghost" id="canbus_raw_send" disabled>Senden</button>
        </div>
        <span class="note" id="canbus_raw_msg"></span>
      </div>
    </div>`;
  m.append(box);
  $("#canbusmacsave").onclick=async()=>{
    const v=$("#canbus_mac").value.trim();
    $("#canbusmacmsg").textContent="speichere…";
    try{
      const r=await jpost("api/settings",{canbus_mac:v});
      $("#canbusmacmsg").textContent=r.ok?"✓ gespeichert":"✗ "+(r.error||"Fehler");
    }catch(e){$("#canbusmacmsg").textContent="✗ Verbindungsfehler";}
  };
  function renderCanbusValues(values,unknown_frames,unknown_count){
    const entries=Object.entries(values||{});
    $("#canbusvals").innerHTML=entries.length?`<table class="probe"><tbody>${entries.map(([k,v])=>
      `<tr><td>${k}</td><td class="note">${v}</td></tr>`).join("")}</tbody></table>`:'<div class="note">keine bekannten Signale dekodiert</div>';
    const uf=Object.entries(unknown_frames||{});
    const more=(unknown_count||0)-uf.length;
    $("#canbusunknown").innerHTML=uf.length?`<table class="probe"><tbody>${uf.map(([id,bytes])=>
      `<tr><td>0x${id}</td><td class="note">${bytes}</td></tr>`).join("")}</tbody></table>${more>0?`<div class="note">… und ${more} weitere</div>`:""}`:'<div class="note">keine</div>';
  }
  $("#canbusread").onclick=async()=>{
    $("#canbusread").disabled=true;$("#canbusmsg").textContent="verbinde & lese…";
    const dur=parseInt($("#canbusdur").value,10)||5;
    try{
      const r=await jpost("api/canbus/read",{duration:dur});
      if(!r.ok){$("#canbusmsg").textContent="✗ "+(r.error||"Fehler");$("#canbusread").disabled=false;return;}
      $("#canbusmsg").textContent=`✓ ${r.can_ids_seen} CAN-IDs gesehen, ${Object.keys(r.values||{}).length} bekannte Werte`;
      renderCanbusValues(r.values,r.unknown_frames,r.unknown_count);
    }catch(e){$("#canbusmsg").textContent="✗ Verbindungsfehler";}
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
    $("#canbusmonitormsg").textContent=r.error?("⚠ "+r.error):`aktiv – ${r.can_ids_seen} Adressen gesehen, Zyklus #${r.cycles}`;
    setTimeout(pollCanbusMonitor,3000);
  }
  $("#canbus_monitor").onchange=async(e)=>{
    if(e.target.checked){
      $("#canbusread").disabled=true;
      $("#canbusmonitormsg").textContent="starte…";
      try{
        const r=await jpost("api/canbus/monitor/start",{});
        if(!r.ok){$("#canbusmonitormsg").textContent="✗ "+(r.error||"Fehler");e.target.checked=false;$("#canbusread").disabled=false;return;}
        $("#canbusmonitormsg").textContent="aktiv…";
        pollCanbusMonitor();
      }catch(err){$("#canbusmonitormsg").textContent="✗ Verbindungsfehler";e.target.checked=false;$("#canbusread").disabled=false;}
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
      $("#canbusmonitormsg").textContent="aktiv…";
      pollCanbusMonitor();
    }
  })();
  $("#canbus_ack").onchange=(e)=>{
    const ok=e.target.checked;
    $("#canbus_glovebox").disabled=!ok;
    $("#canbus_raw_send").disabled=!ok;
  };
  $("#canbus_glovebox").onclick=async()=>{
    $("#canbus_glovebox").disabled=true;$("#canbus_glovebox_msg").textContent="sende…";
    try{
      const r=await jpost("api/canbus/write_action",{id:"glovebox_open",confirm:$("#canbus_ack").checked});
      $("#canbus_glovebox_msg").textContent=r.ok?`✓ gesendet (Antwort: ${r.dongle_response||"–"})`:"✗ "+(r.error||"Fehler");
    }catch(e){$("#canbus_glovebox_msg").textContent="✗ Verbindungsfehler";}
    $("#canbus_glovebox").disabled=!$("#canbus_ack").checked;
  };
  $("#canbus_raw_send").onclick=async()=>{
    $("#canbus_raw_send").disabled=true;$("#canbus_raw_msg").textContent="sende…";
    const can_id=$("#canbus_raw_id").value.trim();
    const data=$("#canbus_raw_data").value.trim();
    try{
      const r=await jpost("api/canbus/write_raw",{can_id,data,confirm:$("#canbus_ack").checked});
      $("#canbus_raw_msg").textContent=r.ok?`✓ gesendet (Antwort: ${r.dongle_response||"–"})`:"✗ "+(r.error||"Fehler");
    }catch(e){$("#canbus_raw_msg").textContent="✗ Verbindungsfehler";}
    $("#canbus_raw_send").disabled=!$("#canbus_ack").checked;
  };
}

/* ---------------- Fahrten & Log ---------------- */
const TRIP_EVENT_ICONS={wifi:"📶",usb:"🔌",temp:"🌡️",trip:"🚗",ble:"🔵"};
async function viewTrips(m){
  m.append(el("h2","title","Fahrten & Log"));
  let c={};try{c=await jget("api/settings");}catch(e){}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Blackbox-Modus</h3>
      <div class="note">Zeichnet automatisch Position/Route auf, sobald eine Fahrt erkannt wird (Schaltstellung ≠ Parken), und beendet die Aufzeichnung, wenn wieder geparkt wird. Braucht einen gekoppelten BLE-Schlüssel.</div>
      ${chk("Fahrten automatisch aufzeichnen","trip_blackbox_enabled",c.blackbox_enabled==='true')}
      <div class="saverow"><span class="note" id="trip_bbmsg"></span><span class="note" id="trip_active_status">lädt…</span></div>
    </div>
    <div class="card"><h3>Fahrten (GPX-Export)</h3>
      <div id="trips_list" class="note">lädt…</div>
      ${chk("Automatisch aufs NAS übertragen","trip_sync_enabled",c.sync_trips_enabled!=='false')}
      <div class="saverow"><span class="note" id="trip_syncenmsg"></span></div>
      <div class="saverow"><button class="btn sm ghost" id="tripsyncbtn">Jetzt aufs NAS übertragen</button><span class="note" id="trips_syncstatus">lädt…</span></div>
    </div>
    <div class="card"><h3>Ereignis-Log</h3>
      <div class="note">Wichtige Ereignisse. Mit gekoppeltem BLE detaillierter (Fahrt-Start/-Ende, Verriegelung, Ladezustand), ohne BLE nur WLAN-/USB-Verbindungswechsel und Temperatur-Warnungen.</div>
      <div id="events_list" class="note">lädt…</div>
    </div>
    <div class="card"><h3>Temperatur</h3>
      <div class="saverow"><span id="temp_current">lädt…</span></div>
      <div class="saverow"><a href="api/temperature/download" class="btn sm ghost" download>Log herunterladen</a></div>
    </div>`;
  m.append(box);

  $("#trip_blackbox_enabled").onchange=async(e)=>{
    $("#trip_bbmsg").textContent="speichere…";
    try{
      const r=await jpost("api/settings",{blackbox_enabled:e.target.checked});
      $("#trip_bbmsg").textContent=r.ok?"✓ gespeichert":"✗ "+(r.error||"Fehler");
    }catch(err){$("#trip_bbmsg").textContent="✗ Verbindungsfehler";}
  };
  $("#trip_sync_enabled").onchange=async(e)=>{
    $("#trip_syncenmsg").textContent="speichere…";
    try{
      const r=await jpost("api/settings",{sync_trips_enabled:e.target.checked});
      $("#trip_syncenmsg").textContent=r.ok?"✓ gespeichert":"✗ "+(r.error||"Fehler");
    }catch(err){$("#trip_syncenmsg").textContent="✗ Verbindungsfehler";}
  };

  try{
    const tr=await jget("api/blackbox/trips");
    $("#trip_active_status").textContent=tr.active?"🔴 Fahrt wird gerade aufgezeichnet":"⚪ Keine aktive Fahrt";
    const list=$("#trips_list");
    if(!tr.trips||!tr.trips.length){list.textContent="Noch keine aufgezeichneten Fahrten.";}
    else{
      list.innerHTML=`<table class="probe"><tbody>${tr.trips.map(t=>`
        <tr>
          <td>${(t.start||"").replace("T"," ").slice(0,16)}</td>
          <td class="note">${t.distance_km!=null?t.distance_km+" km":"–"} · ${t.points} Punkte</td>
          <td><a href="api/blackbox/export?trip=${encodeURIComponent(t.trip_id)}" class="btn sm ghost" download>GPX</a></td>
        </tr>`).join("")}</tbody></table>`;
    }
  }catch(e){$("#trips_list").textContent="✗ Fehler beim Laden";}

  const refreshTripSyncStatus=async()=>{
    let s;try{s=await jget("api/nas/trips_status");}catch(e){return;}
    const el2=$("#trips_syncstatus");
    if(!el2)return;
    if(!s.t){el2.textContent="noch nicht synchronisiert";}
    else if(!s.ok){el2.textContent="✗ "+(s.error||"Fehler");}
    else{el2.textContent="✓ übertragen ("+s.uploaded+" neu, "+new Date(s.t*1000).toLocaleTimeString()+")";}
  };
  refreshTripSyncStatus();
  $("#tripsyncbtn").onclick=async()=>{
    $("#trips_syncstatus").textContent="übertrage…";
    try{await jpost("api/nas/sync_trips",{});}catch(e){}
    setTimeout(refreshTripSyncStatus,4000);
  };

  try{
    const ev=await jget("api/events?limit=100");
    const list=$("#events_list");
    if(!ev.events||!ev.events.length){list.textContent="Noch keine Ereignisse.";}
    else{
      list.innerHTML=`<table class="probe"><tbody>${ev.events.map(e=>`
        <tr><td>${(TRIP_EVENT_ICONS[e.category]||"•")}</td>
        <td class="note">${(e.ts||"").replace("T"," ")}</td>
        <td>${e.message}</td></tr>`).join("")}</tbody></table>`;
    }
  }catch(e){$("#events_list").textContent="✗ Fehler beim Laden";}

  try{
    const s=await jget("api/status");
    $("#temp_current").textContent="Aktuell: "+((s.diag||{}).temp||"–");
  }catch(e){$("#temp_current").textContent="✗ Fehler beim Laden";}
}

async function viewSettings(m){
  m.append(el("h2","title","Einstellungen"));
  let c;try{c=await jget("api/settings");}catch(e){return;}
  let login={logged_in:false,has_refresh:false};
  try{login=(await jget("api/status")).login||login;}catch(e){}
  const box=el("div");box.innerHTML=`
    <div class="card"><h3>Tesla-Konto (für Schlüssel-Abruf)</h3>
      <div class="note">Damit verschlüsselte Aufnahmen automatisch entschlüsselt werden können, muss der Hub sich einmalig bei Tesla anmelden und einen Schlüssel-Abruf-Token holen. Ohne diesen Login bleiben verschlüsselte Clips dauerhaft gesperrt.</div>
      <div class="saverow"><span class="note" id="teslastatus">${login.logged_in?"✓ eingeloggt"+(login.has_refresh?" (bleibt automatisch gültig)":""):"✗ nicht eingeloggt"}</span></div>
      <div class="saverow"><button class="btn sm" id="teslaloginbtn">Bei Tesla einloggen</button></div>
      <div class="note">Öffnet die Tesla-Anmeldeseite in einem neuen Tab. Nach dem Login zeigt der Browser eine leere/Fehler-Seite unter <code>dashcam.tesla.com/callback?...</code> — die komplette Adresse aus der Adresszeile hier einfügen:</div>
      ${fld("Callback-URL nach Login","s_tesla_callback","text","","https://dashcam.tesla.com/callback?code=...")}
      <div class="saverow"><button class="btn sm ghost" id="teslaexchange">Bestätigen</button><span class="note" id="teslamsg"></span></div>
    </div>
    <div class="card"><h3>Verbindung / NAS</h3>
      ${fld("Archiv-Server (NAS-IP)","s_archive_server","text",c.archive_server)}
      ${fld("Share + Pfad","s_share_name","text",c.share_name)}
      ${fld("Benutzer","s_share_user","text",c.share_user)}
      ${fld("Passwort","s_share_password","password","",c.share_password_set?"•••• unverändert":"")}
      <div class="saverow"><button class="btn sm" id="nastest">Verbindung testen</button><span class="note" id="nasmsg"></span></div>
      ${chk("RecentClips archivieren","s_archive_recentclips",c.archive_recentclips==='true')}
      ${chk("SavedClips archivieren","s_archive_savedclips",c.archive_savedclips==='true')}
      ${chk("SentryClips archivieren","s_archive_sentryclips",c.archive_sentryclips==='true')}
    </div>
    <div class="card"><h3>Netzwerk</h3>
      ${fld("WLAN-SSID","s_ssid","text",c.ssid)}
      ${fld("WLAN-Passwort","s_wifipass","password","",c.wifipass_set?"•••• unverändert":"")}
      ${fld("Access-Point SSID","s_ap_ssid","text",c.ap_ssid)}
      ${fld("Access-Point Passwort","s_ap_pass","password","",c.ap_pass_set?"•••• unverändert":"")}
      <div class="note">SSID/Passwort hier oben eintragen und mit dem großen "Speichern" unten sichern, bevor der Fallback unten eingeschaltet wird.</div>
    </div>
    <div class="card"><h3>Access-Point-Fallback</h3>
      <div class="note">Aus: Access Point läuft wie gewohnt dauerhaft parallel zum Heim-WLAN. An: nur eingeschaltet, wenn das Heim-WLAN gerade nicht erreichbar ist (Prüfung alle 30s), sonst aus.</div>
      <div class="saverow"><span class="note" id="apfallback_status">lädt…</span></div>
      <div class="saverow">
        <button class="btn sm" id="apfallback_on">Einschalten</button>
        <button class="btn sm ghost" id="apfallback_off" style="display:none">Ausschalten</button>
      </div>
      <div class="note warn">⚠ Auf diesem Pi kann gleichzeitiger AP+WLAN-Betrieb die WLAN-Verbindung kurz stören (Chip-Limitierung, beim Testen beobachtet). Vor Verlassen des Hauses einmal in Ruhe testen, nicht blind verlassen.</div>
      <div class="note" id="apusb_note" style="display:none">Externer USB-WLAN-Adapter erkannt (<code id="apusb_device"></code>) -- behebt das Chip-Problem oben komplett: der Access Point läuft dann als eigenes Funkgerät, ohne sich mit dem Heim-WLAN die Antenne zu teilen.</div>
      <div class="saverow" id="apusb_row" style="display:none">
        <span class="note" id="apusb_status">lädt…</span>
        <button class="btn sm" id="apusb_on">Dauerhaft auf USB-Adapter verlagern</button>
        <button class="btn sm ghost" id="apusb_off" style="display:none">Zurück auf Onboard-Chip</button>
      </div>
    </div>
    <div class="card"><h3>Handy-Hotspot (WLAN)</h3>
      <div class="note">Speichert einen Handy-Hotspot zusätzlich zum Heim-WLAN als bekanntes Netzwerk -- praktisch unterwegs (z. B. Werkstattbesuch), wenn kein Heim-WLAN in Reichweite ist. Heim-WLAN wird bevorzugt und übernimmt automatisch wieder, sobald es erreichbar ist.</div>
      ${fld("Hotspot-SSID","s_hotspot_ssid","text",c.hotspot_ssid)}
      ${fld("Hotspot-Passwort","s_hotspot_pass","password","",c.hotspot_pass_set?"•••• unverändert":"")}
      <div class="note">SSID/Passwort hier eintragen und mit dem großen "Speichern" unten sichern, bevor unten eingeschaltet wird.</div>
      <div class="saverow"><span class="note" id="hotspot_status">lädt…</span></div>
      <div class="saverow">
        <button class="btn sm" id="hotspot_on">Einschalten</button>
        <button class="btn sm ghost" id="hotspot_off" style="display:none">Ausschalten</button>
      </div>
    </div>
    <div class="card"><h3>WireGuard-VPN (nach Hause)</h3>
      <div class="note">Baut unterwegs (z. B. über den Handy-Hotspot oben) eine verschlüsselte VPN-Verbindung zu einem WireGuard-Server zu Hause auf -- für Fernzugriff auf den Hub, ohne einen Port im Heimnetz nach außen öffnen zu müssen. Den öffentlichen Schlüssel unten in die Peer-Konfiguration des Heim-Servers eintragen, dann hier Peer-Daten eintragen, speichern und einschalten.</div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input type="file" id="wg_qr_file" accept="image/*" style="flex:1;min-width:220px">
        <button class="btn sm ghost" id="wg_qr_import">QR-Code auslesen</button>
      </div>
      <div class="note" id="wg_qr_msg"></div>
      <div class="note">Screenshot/Foto des WireGuard-QR-Codes hochladen und auslesen -- füllt alle Felder unten (inkl. privatem Schlüssel) automatisch aus. Danach unten prüfen und mit dem großen "Speichern" sichern. Alternativ von Hand ausfüllen.</div>
      ${fld("Peer Public-Key (Heim-Server)","s_wg_peer_pubkey","text",c.wg_peer_pubkey)}
      ${fld("Endpoint (Host:Port)","s_wg_endpoint","text",c.wg_endpoint,"vpn.example.com:51820")}
      ${fld("Erlaubte IPs (AllowedIPs)","s_wg_allowed_ips","text",c.wg_allowed_ips||"0.0.0.0/0")}
      ${fld("Tunnel-Adresse des Hubs (CIDR)","s_wg_address","text",c.wg_address,"10.10.10.2/24")}
      ${fld("Keepalive (Sek., 0=aus)","s_wg_keepalive","number",c.wg_keepalive||25)}
      ${fld("Preshared-Key (optional)","s_wg_psk","password","",c.wg_psk_set?"•••• gesetzt":"")}
      ${fld("Privater Schlüssel (aus QR-Code, i.d.R. nicht von Hand nötig)","s_wg_privkey","password","",c.wg_privkey_set?"•••• gesetzt":"")}
      ${fld("DNS (optional)","s_wg_dns","text",c.wg_dns)}
      <div class="note">Peer-Daten hier eintragen und mit dem großen "Speichern" unten sichern, bevor unten eingeschaltet wird.</div>
      <div class="saverow"><span class="note" id="wg_status">lädt…</span></div>
      <div class="saverow">
        <button class="btn sm" id="wg_on">Einschalten</button>
        <button class="btn sm ghost" id="wg_off" style="display:none">Ausschalten</button>
      </div>
      <div class="note">Öffentlicher Schlüssel dieses Hubs (in die Peer-Config des Heim-Servers eintragen): <code id="wg_own_pubkey">–</code></div>
    </div>
    <div class="card"><h3>Auto wachhalten</h3>
      ${fld("TeslaFi API-Token","s_teslafi_api_token","password","",c.teslafi_api_token_set?"•••• gesetzt":"")}
      ${fld("Tessie API-Token","s_tessie_api_token","password","",c.tessie_api_token_set?"•••• gesetzt":"")}
      ${fld("BLE Fahrzeug-VIN","s_tesla_ble_vin","text",c.tesla_ble_vin)}
      <div class="note">BLE-Schlüssel koppeln, testen und den Kopplungsstatus einsehen: Menüpunkt <b>„Fahrzeug (BLE)“</b> links.</div>
    </div>
    <div class="card"><h3>Benachrichtigungen</h3>
      ${chk("Pushover aktiv","s_pushover_enabled",c.pushover_enabled==='true')}
      ${fld("Pushover User-Key","s_pushover_user_key","password","",c.pushover_user_key_set?"•••• gesetzt":"")}
      ${fld("Pushover App-Key","s_pushover_app_key","password","",c.pushover_app_key_set?"•••• gesetzt":"")}
      ${chk("Telegram aktiv","s_telegram_enabled",c.telegram_enabled==='true')}
      ${fld("Telegram Chat-ID","s_telegram_chat_id","text",c.telegram_chat_id)}
      ${fld("Telegram Bot-Token","s_telegram_bot_token","password","",c.telegram_bot_token_set?"•••• gesetzt":"")}
    </div>
    <div class="card"><h3>Home Assistant (MQTT)</h3>
      ${chk("Als Gerät in Home Assistant anmelden","s_mqtt_enabled",c.mqtt_enabled==='true')}
      ${fld("MQTT-Server (Host)","s_mqtt_host","text",c.mqtt_host,"192.168.1.10")}
      ${fld("Port","s_mqtt_port","number",c.mqtt_port||1883)}
      ${fld("Benutzer","s_mqtt_user","text",c.mqtt_user)}
      ${fld("Passwort","s_mqtt_password","password","",c.mqtt_password_set?"•••• unverändert":"")}
      <div class="note">Meldet den Hub per MQTT Discovery automatisch als Gerät „TeslaCam Hub" in Home Assistant an (Sensoren: Aufnahmen, verschlüsselte Aufnahmen, NAS-Archivierung %, Pi-Temperatur, WLAN, USB am Auto, Tresor entsperrt). Kein manuelles Einrichten in HA nötig, sofern der MQTT-Integration dort bereits eingerichtet ist.</div>
    </div>
    <div class="card"><h3>Aufbewahrung & Sync</h3>
      <div class="note"><b>Kamera-Aufnahmen:</b> laufen über die normale teslausb-Archivierung (siehe „Verbindung / NAS" oben) und werden <b>nur in eine Richtung</b> übertragen: vom Stick zum NAS. Danach werden sie vom Stick entfernt, um Platz zu schaffen. Es kommt nichts vom NAS zurück.</div>
      <div class="note" style="margin-bottom:14px"><b>Music/LightShow/Boombox:</b> laufen über ein separates Verfahren und werden <b>in beide Richtungen</b> abgeglichen: Änderungen auf dem NAS (z. B. dort hinzugefügte Musik) werden auf den Stick übertragen, und Änderungen auf dem Stick zum NAS. Gelöscht wird dabei nirgends automatisch — eine auf einer Seite entfernte Datei bleibt auf der anderen Seite bestehen.</div>
      ${chk("Music/LightShow/Boombox automatisch bei WLAN synchronisieren (beidseitig)","s_sync_all_content",c.sync_all_content==='true')}
      ${fld("Sync-Pfad auf dem NAS","s_sync_media_path","text",c.sync_media_path,"Tesla_Video/Sonstiges")}
      <div class="note">Erster Teil (vor dem ersten <code>/</code>) muss ein bereits vorhandener Freigabename auf dem NAS sein (z. B. <code>Tesla_Video</code>, gleicher Server/Zugang wie oben bei „Verbindung / NAS"). Alles danach (z. B. <code>Sonstiges</code>) sowie die Ordner <code>Music/</code>, <code>LightShow/</code>, <code>Boombox/</code> werden automatisch angelegt, falls sie noch nicht existieren. „Jetzt synchronisieren" speichert den Pfad automatisch mit.</div>
      <div class="saverow"><button class="btn sm ghost" id="mediasync">Jetzt synchronisieren (beidseitig)</button><span class="note" id="mediasyncmsg"></span></div>
      <div class="field"><label>Aufnahmen auf dem Stick löschen</label>
        <select id="s_retention_mode">
          <option value="off"${c.retention_mode==='off'||!c.retention_mode?' selected':''}>Aus</option>
          <option value="time"${c.retention_mode==='time'?' selected':''}>Nach Zeitraum</option>
          <option value="space"${c.retention_mode==='space'?' selected':''}>Rollierend nach Speicher</option>
        </select></div>
      ${fld("Aufbewahrung (Tage)","s_retention_days","number",c.retention_days)}
      ${fld("Mind. freien Speicher halten (GB)","s_retention_free_gb","number",c.retention_free_gb)}
      <div class="note">Gelöscht wird nur, was bereits auf dem NAS gesichert ist.</div>
    </div>
    <div class="card"><h3>Backup & Wiederherstellung</h3>
      <div class="note">Exportiert/importiert die komplette Konfigurationsdatei (<code>teslausb_setup_variables.conf</code>) inkl. aller Passwörter/Tokens im Klartext -- bewusst getrennt von der normalen Speichern-Funktion oben, die Passwörter aus Sicherheitsgründen nie wieder anzeigt. Gedacht als Sicherheitsnetz vor größeren Eingriffen (z. B. Stick neu flashen mit größerer Root-Partition): vorher exportieren, danach importieren statt jedes Feld von Hand neu einzutippen.</div>
      <div class="note warn">⚠ Die exportierte Datei enthält alle Zugangsdaten im Klartext (WLAN, NAS, MQTT, Access-Point, ...). Sicher aufbewahren, nirgends unverschlüsselt hochladen/teilen.</div>
      <div class="saverow"><a href="api/backup/export" class="btn sm ghost" download>Einstellungen exportieren</a></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input type="file" id="backup_import_file" accept=".conf,text/plain" style="flex:1;min-width:220px">
        <button class="btn sm ghost" id="backup_import_btn">Einstellungen importieren</button>
      </div>
      <div class="note" id="backup_import_msg"></div>
    </div>
    <div class="card"><h3>Rohschlüssel für externe Instanz</h3>
      <div class="note warn">⚠ Sicherheits-Kompromiss: standardmäßig sind die Schlüssel-Sidecar-Dateien auf dem NAS (<code>*.key.json</code>) ohne Tresor-Passwort nutzlos. Diese Option schreibt zusätzlich <b>unverschlüsselte</b> Schlüssel (<code>*.rawkey.json</code>) neben die Videos, damit ein separates System die Clips direkt lesen kann, ohne das Tresor-Passwort zu kennen. Nur aktivieren, wenn das NAS selbst vertrauenswürdig/abgesichert ist.</div>
      <div class="note">Schutz dagegen, dass Rohschlüssel versehentlich auf ein falsches/vertauschtes NAS geschrieben werden: beim ersten Mal wird ein zufälliges Kopplungs-Token sowohl hier als auch in einer Datei auf dem NAS (<code>HUB-NAS-KOPPLUNG.json</code>) hinterlegt. Stimmen die Tokens bei einem späteren Lauf nicht überein (z. B. weil ein anderes NAS unter demselben Namen erreichbar ist), werden <b>keine</b> Rohschlüssel geschrieben.</div>
      ${chk("Unverschlüsselte Schlüssel für externe Instanz auf NAS ablegen","s_nas_raw_keys",c.nas_raw_keys==='true')}
      <div class="saverow" style="flex-wrap:wrap">
        <span class="note" id="pairingstatus">Kopplungsstatus: lädt…</span>
        <button class="btn sm ghost" id="rawkeypush">Jetzt übertragen</button>
        <button class="btn sm ghost" id="pairingreset">Kopplung zurücksetzen</button>
      </div>
      <div class="note" id="rawkeymsg"></div>
    </div>
    <div class="card"><h3>SMB-Freigabe</h3>
      <div class="note">Stellt <code>TeslaCam</code> (RecentClips/SavedClips/SentryClips, nur lesend) als Netzwerkfreigabe bereit, z. B. zum Durchsuchen/Kopieren vom PC aus. Standardmäßig an, passwortgeschützt (Benutzer <code>pi</code>).</div>
      <div class="note">Neben jedem Video, dessen Schlüssel im Tresor bekannt ist, liegt automatisch eine <code>*.mp4.key.json</code>-Datei -- verschlüsselt mit dem Tresor-Passwort, nutzlos ohne dieses. So lassen sich Video + Schlüssel gemeinsam per SMB kopieren. Erscheint spätestens 60s nach dem Entsperren des Tresors, wird nicht rückwirkend für schon vom Gerät gelöschte Videos nachgetragen.</div>
      ${chk("SMB-Freigabe aktiv","s_samba_enabled",c.samba_enabled!=='false')}
      <div class="saverow"><span class="note" id="sambastatus">lädt…</span></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_samba_pw_new" type="password" placeholder="neues SMB-Passwort (min. 8 Zeichen)" style="flex:1;min-width:220px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="sambapwchange">SMB-Passwort setzen</button>
      </div>
      <div class="note">Unabhängig vom Tresor- und SSH-Passwort. Beim allerersten Aktivieren (per <code>hub/install.sh</code>) wird automatisch ein zufälliges Passwort vergeben und einmalig im Install-Log ausgegeben -- hier direkt ein eigenes setzen.</div>
      <div class="note" id="sambapwmsg"></div>
    </div>
    <div class="card"><h3>Sicherheit & System</h3>
      <div class="note">Grundprinzip: der Stick soll bei Diebstahl wertlos sein. Entschlüsselungs-Schlüssel und Tesla-Token liegen nie unverschlüsselt auf dem Stick, sondern nur verschlüsselt im Tresor; das eigentliche Video wird beim Ansehen nur kurz im RAM entschlüsselt, nie dauerhaft gespeichert. Die beiden Einstellungen unten sichern die zwei verbleibenden Angriffsflächen ab: den Fernzugriff (SSH) und den Zustand "Tresor gerade entsperrt".</div>
      ${chk("SSH-Passwort-Login abschalten","s_ssh_disable_password",c.ssh_disable_password==='true')}
      <div class="note">Warum: ohne das ist SSH per Passwort aus dem ganzen (W)LAN erreichbar und damit anfällig für automatisiertes Passwort-Raten. Mit Häkchen ist nur noch Login per SSH-Schlüssel möglich. <b>Achtung:</b> vorher unbedingt einen eigenen SSH-Schlüssel auf dem Pi hinterlegen (<code>~/.ssh/authorized_keys</code>) — sonst sperrst du dich selbst aus SSH aus und kommst nur noch per Bildschirm+Tastatur direkt am Pi wieder rein.</div>
      ${fld("Tresor automatisch sperren nach (Min, 0=aus)","s_vault_autolock_min","number",c.vault_autolock_min)}
      <div class="note">Warum: der Tresor hält Schlüssel/Token nur entschlüsselt im RAM, solange er offen ist. Je länger er offen bleibt (z. B. weil du das Browser-Tab offen gelassen hast), desto länger könnte jemand mit Zugriff auf das laufende Gerät diese Klartext-Schlüssel im Speicher abgreifen. Automatisches Sperren nach Inaktivität begrenzt dieses Zeitfenster.</div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_pw_old" type="password" placeholder="aktuelles Passwort" style="flex:1;min-width:160px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <input id="s_pw_new" type="password" placeholder="neues Passwort" style="flex:1;min-width:160px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="pwchange">Tresor-Passwort ändern</button>
      </div>
      <div class="note" id="pwmsg"></div>
      <div class="saverow" style="flex-wrap:wrap;gap:10px 16px">
        <input id="s_ssh_pw_new" type="password" placeholder="neues SSH-Passwort (min. 8 Zeichen)" style="flex:1;min-width:220px;padding:10px 12px;background:var(--bg2);border:1px solid var(--line);border-radius:10px;color:var(--text)">
        <button class="btn sm ghost" id="sshpwchange">SSH-Passwort setzen</button>
      </div>
      <div class="note">Setzt das Linux-Login-Passwort des Benutzers <code>pi</code> für den SSH-Zugang neu — unabhängig vom Tresor-Passwort (bewusst getrennt: das Tresor-Passwort wird nirgends im Klartext gespeichert und könnte sich unabhängig ändern, eine Kopplung wäre riskant). Wirkt sofort, ohne Neustart.</div>
      <div class="note" id="sshpwmsg"></div>
      ${fld("Zeitzone","s_time_zone","text",c.time_zone,"Europe/Berlin")}
      ${fld("Hostname","s_teslausb_hostname","text",c.teslausb_hostname)}
    </div>
    <div class="saverow"><button class="btn primary" style="width:auto" id="savebtn">Speichern</button><span class="note" id="savemsg"></span></div>`;
  m.append(box);
  $("#teslaloginbtn").onclick=async()=>{
    $("#teslamsg").textContent="hole Login-Link…";
    try{const r=await jget("api/tesla/login_url");window.open(r.url,"_blank");$("#teslamsg").textContent="Tab geöffnet – nach Login die Adresse hier einfügen.";}
    catch(e){$("#teslamsg").textContent="✗ Fehler beim Abrufen des Login-Links";}
  };
  $("#teslaexchange").onclick=async()=>{
    const cb=$("#s_tesla_callback").value.trim();
    if(!cb){$("#teslamsg").textContent="Bitte zuerst die Callback-URL einfügen";return;}
    $("#teslamsg").textContent="prüfe…";
    try{
      const r=await jpost("api/tesla/exchange",{callback:cb});
      if(r.ok){$("#teslamsg").textContent="✓ eingeloggt";$("#teslastatus").textContent="✓ eingeloggt"+(r.refresh?" (bleibt automatisch gültig)":"");toast("Tesla-Login erfolgreich");}
      else{$("#teslamsg").textContent="✗ "+(r.error||"Fehler");}
    }catch(e){$("#teslamsg").textContent="✗ Verbindungsfehler";}
  };
  $("#nastest").onclick=async()=>{$("#nasmsg").textContent="Teste…";
    const r=await jget("api/nas/test");$("#nasmsg").textContent=r.ok?("✓ OK"+(r.writable?" (schreibbar)":" (nur lesbar)")):("✗ "+(r.error||"Fehler"));};
  $("#pwchange").onclick=async()=>{
    const oldp=$("#s_pw_old").value,newp=$("#s_pw_new").value;
    if(!oldp||!newp){$("#pwmsg").textContent="✗ bitte beide Felder ausfüllen";return;}
    if(newp.length<8){$("#pwmsg").textContent="✗ neues Passwort sollte mind. 8 Zeichen haben";return;}
    $("#pwmsg").textContent="ändere…";
    const r=await jpost("api/vault/change_pass",{old:oldp,new:newp});
    if(r.ok){$("#pwmsg").textContent="✓ Passwort geändert";$("#s_pw_old").value="";$("#s_pw_new").value="";toast("Tresor-Passwort geändert");}
    else{$("#pwmsg").textContent="✗ "+(r.error||"Fehler");}
  };
  $("#sshpwchange").onclick=async()=>{
    const newp=$("#s_ssh_pw_new").value;
    if(!newp||newp.length<8){$("#sshpwmsg").textContent="✗ Passwort sollte mind. 8 Zeichen haben";return;}
    $("#sshpwmsg").textContent="setze…";
    try{
      const r=await jpost("api/system/ssh_password",{password:newp});
      if(r.ok){$("#sshpwmsg").textContent="✓ SSH-Passwort gesetzt";$("#s_ssh_pw_new").value="";toast("SSH-Passwort geändert");}
      else{$("#sshpwmsg").textContent="✗ "+(r.error||"Fehler");}
    }catch(e){$("#sshpwmsg").textContent="✗ Verbindungsfehler";}
  };
  $("#sambapwchange").onclick=async()=>{
    const newp=$("#s_samba_pw_new").value;
    if(!newp||newp.length<8){$("#sambapwmsg").textContent="✗ Passwort sollte mind. 8 Zeichen haben";return;}
    $("#sambapwmsg").textContent="setze…";
    try{
      const r=await jpost("api/system/samba_password",{password:newp});
      if(r.ok){$("#sambapwmsg").textContent="✓ SMB-Passwort gesetzt";$("#s_samba_pw_new").value="";toast("SMB-Passwort geändert");}
      else{$("#sambapwmsg").textContent="✗ "+(r.error||"Fehler");}
    }catch(e){$("#sambapwmsg").textContent="✗ Verbindungsfehler";}
  };
  $("#backup_import_btn").onclick=async()=>{
    const file=$("#backup_import_file").files[0];
    if(!file){$("#backup_import_msg").textContent="✗ bitte zuerst eine Datei auswählen";return;}
    if(!confirm("Alle aktuellen Einstellungen mit dieser Datei überschreiben?"))return;
    $("#backup_import_msg").textContent="importiere…";
    try{
      const text=await file.text();
      const r=await jpost("api/backup/import",{content:text});
      if(r.ok){$("#backup_import_msg").textContent="✓ importiert -- Seite neu laden, danach Hub/Pi neu starten, damit alles greift";toast("Einstellungen importiert");}
      else{$("#backup_import_msg").textContent="✗ "+(r.error||"Fehler");}
    }catch(e){$("#backup_import_msg").textContent="✗ Verbindungsfehler";}
  };
  async function refreshSambaStatus(){
    const el=$("#sambastatus");
    if(!el)return;
    let r;try{r=await jget("api/samba/status");}catch(e){setTimeout(refreshSambaStatus,15000);return;}
    if(!r.installed)el.textContent="nicht installiert -- hub/install.sh erneut ausführen";
    else el.textContent=(r.active?"✓ aktiv":"aus")+" -- "+r.share;
    if(document.body.contains(el))setTimeout(refreshSambaStatus,15000);
  }
  refreshSambaStatus();
  async function refreshPairingStatus(){
    try{
      const r=await jget("api/nas/raw_keys/pairing");
      $("#pairingstatus").textContent=r.paired?("Kopplungsstatus: ✓ gekoppelt (Token "+r.token_prefix+"…)"):"Kopplungsstatus: noch nicht gekoppelt (wird beim ersten Übertragen angelegt)";
    }catch(e){$("#pairingstatus").textContent="Kopplungsstatus: unbekannt";}
  }
  refreshPairingStatus();
  $("#rawkeypush").onclick=async()=>{
    $("#rawkeymsg").textContent="übertrage…";
    const r=await jpost("api/nas/raw_keys/push",{});
    $("#rawkeymsg").textContent=r.ok?`✓ ${r.written||0} Rohschlüssel geschrieben`:"✗ "+(r.error||(r.errors&&r.errors[0])||"Fehler");
    refreshPairingStatus();
  };
  $("#pairingreset").onclick=async()=>{
    if(!confirm("Kopplung wirklich zurücksetzen? Danach wird beim nächsten Übertragen eine neue Kopplung mit dem aktuell erreichbaren NAS angelegt."))return;
    $("#rawkeymsg").textContent="setze zurück…";
    const r=await jpost("api/nas/raw_keys/reset_pairing",{});
    $("#rawkeymsg").textContent=r.ok?"✓ Kopplung zurückgesetzt":"✗ "+(r.error||"Fehler");
    refreshPairingStatus();
  };
  $("#mediasync").onclick=async()=>{
    const p=$("#s_sync_media_path").value.trim();
    if(!p){$("#mediasyncmsg").textContent="✗ bitte zuerst Sync-Pfad eintragen";return;}
    $("#mediasyncmsg").textContent="speichere Pfad…";
    const sr=await jpost("api/settings",{sync_media_path:p});
    if(!sr.ok){$("#mediasyncmsg").textContent="✗ "+(sr.error||"Pfad konnte nicht gespeichert werden");return;}
    $("#mediasyncmsg").textContent="synchronisiere…";
    let before=0;try{before=(await jget("api/nas/media_status")).t||0;}catch(e){}
    await jpost("api/nas/sync_media",{});
    const poll=async()=>{
      let st;try{st=await jget("api/nas/media_status");}catch(e){return;}
      if(!st.t||st.t<=before){setTimeout(poll,1500);return;}
      $("#mediasyncmsg").textContent=st.ok?`✓ fertig (${st.copied}/3 Ordner)`:"✗ "+(st.error||"Fehler");
    };
    setTimeout(poll,2000);
  };
  $("#savebtn").onclick=async()=>{
    const fields=["archive_server","share_name","share_user","ssid","ap_ssid","tesla_ble_vin",
      "telegram_chat_id","retention_days","retention_free_gb","vault_autolock_min","time_zone","teslausb_hostname","sync_media_path",
      "mqtt_host","mqtt_port","mqtt_user","hotspot_ssid",
      "wg_peer_pubkey","wg_endpoint","wg_allowed_ips","wg_address","wg_keepalive","wg_dns"];
    const secrets=["share_password","wifipass","ap_pass","teslafi_api_token","tessie_api_token",
      "pushover_user_key","pushover_app_key","telegram_bot_token","mqtt_password","hotspot_pass","wg_psk","wg_privkey"];
    const bools=["archive_recentclips","archive_savedclips","archive_sentryclips","sync_all_content",
      "ssh_disable_password","pushover_enabled","telegram_enabled","mqtt_enabled","nas_raw_keys","samba_enabled"];
    const body={};
    fields.forEach(f=>body[f]=($("#s_"+f)||{}).value||"");
    secrets.forEach(f=>{const v=($("#s_"+f)||{}).value;if(v)body[f]=v;});
    bools.forEach(f=>body[f]=($("#s_"+f)||{}).checked||false);
    body.retention_mode=$("#s_retention_mode").value;
    $("#savemsg").textContent="Speichern…";
    const r=await jpost("api/settings",body);
    $("#savemsg").textContent=r.ok?"✓ gespeichert (greift nach Archiv-Neustart/Reboot)":"✗ "+(r.error||"Fehler");
    if(r.ok)toast("Gespeichert");
  };
  async function refreshApFallback(){
    const statusEl=$("#apfallback_status");
    if(!statusEl)return;
    let r;try{r=await jget("api/ap_fallback/status");}catch(e){setTimeout(refreshApFallback,15000);return;}
    const onBtn=$("#apfallback_on"),offBtn=$("#apfallback_off");
    if(r.enabled){
      onBtn.style.display="none";offBtn.style.display="";
      statusEl.textContent=r.ap_broadcasting?"aktiv -- AP sendet gerade (Heim-WLAN nicht erreichbar)":
        r.home_wifi_connected?"bereit (Heim-WLAN verbunden)":"bereit (Heim-WLAN-Status unklar)";
    }else{
      onBtn.style.display="";offBtn.style.display="none";
      statusEl.textContent="aus";
    }
    if(document.body.contains(statusEl))setTimeout(refreshApFallback,15000);
  }
  $("#apfallback_on").onclick=async()=>{
    $("#apfallback_on").disabled=true;
    $("#apfallback_status").textContent="schalte ein…";
    try{
      const r=await jpost("api/settings",{ap_fallback_only:true});
      if(!r.ok)$("#apfallback_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#apfallback_status").textContent="✗ Verbindungsfehler";}
    $("#apfallback_on").disabled=false;
    refreshApFallback();
  };
  $("#apfallback_off").onclick=async()=>{
    $("#apfallback_off").disabled=true;
    $("#apfallback_status").textContent="schalte aus…";
    try{
      const r=await jpost("api/settings",{ap_fallback_only:false});
      if(!r.ok)$("#apfallback_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#apfallback_status").textContent="✗ Verbindungsfehler";}
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
        statusEl.textContent="✓ aktiv auf "+r.usb_device;
      }else{
        onBtn.style.display="";offBtn.style.display="none";
        statusEl.textContent="noch auf Onboard-Chip";
      }
    }
    if(document.body.contains(row))setTimeout(refreshApUsb,15000);
  }
  $("#apusb_on").onclick=async()=>{
    $("#apusb_on").disabled=true;
    $("#apusb_status").textContent="verlagere…";
    try{
      const r=await jpost("api/settings",{ap_on_usb:true});
      if(!r.ok)$("#apusb_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#apusb_status").textContent="✗ Verbindungsfehler";}
    $("#apusb_on").disabled=false;
    refreshApUsb();
  };
  $("#apusb_off").onclick=async()=>{
    $("#apusb_off").disabled=true;
    $("#apusb_status").textContent="schalte aus…";
    try{
      const r=await jpost("api/settings",{ap_on_usb:false});
      if(!r.ok)$("#apusb_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#apusb_status").textContent="✗ Verbindungsfehler";}
    $("#apusb_off").disabled=false;
    refreshApUsb();
  };
  refreshApUsb();
  async function refreshHotspot(){
    const statusEl=$("#hotspot_status");
    if(!statusEl)return;
    let r;try{r=await jget("api/hotspot/status");}catch(e){setTimeout(refreshHotspot,15000);return;}
    const onBtn=$("#hotspot_on"),offBtn=$("#hotspot_off");
    if(r.enabled){
      onBtn.style.display="none";offBtn.style.display="";
      statusEl.textContent=r.connected_now?"✓ verbunden":"bereit (nicht verbunden)";
    }else{
      onBtn.style.display="";offBtn.style.display="none";
      statusEl.textContent="aus";
    }
    if(document.body.contains(statusEl))setTimeout(refreshHotspot,15000);
  }
  $("#hotspot_on").onclick=async()=>{
    $("#hotspot_on").disabled=true;
    $("#hotspot_status").textContent="schalte ein…";
    try{
      const r=await jpost("api/settings",{hotspot_enabled:true});
      if(!r.ok)$("#hotspot_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#hotspot_status").textContent="✗ Verbindungsfehler";}
    $("#hotspot_on").disabled=false;
    refreshHotspot();
  };
  $("#hotspot_off").onclick=async()=>{
    $("#hotspot_off").disabled=true;
    $("#hotspot_status").textContent="schalte aus…";
    try{
      const r=await jpost("api/settings",{hotspot_enabled:false});
      if(!r.ok)$("#hotspot_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#hotspot_status").textContent="✗ Verbindungsfehler";}
    $("#hotspot_off").disabled=false;
    refreshHotspot();
  };
  refreshHotspot();
  $("#wg_qr_import").onclick=async()=>{
    const file=($("#wg_qr_file").files||[])[0];
    if(!file){$("#wg_qr_msg").textContent="✗ bitte zuerst ein Bild auswählen";return;}
    $("#wg_qr_msg").textContent="lese QR-Code…";
    try{
      const dataUrl=await new Promise((resolve,reject)=>{
        const fr=new FileReader();
        fr.onload=()=>resolve(String(fr.result||""));
        fr.onerror=()=>reject(new Error("read failed"));
        fr.readAsDataURL(file);
      });
      const r=await jpost("api/wireguard/import_qr",{image:dataUrl});
      if(!r.ok){$("#wg_qr_msg").textContent="✗ "+(r.error||"Fehler beim Lesen");return;}
      const cfg=r.config||{};
      if(cfg.peer_pubkey)$("#s_wg_peer_pubkey").value=cfg.peer_pubkey;
      if(cfg.endpoint)$("#s_wg_endpoint").value=cfg.endpoint;
      if(cfg.allowed_ips)$("#s_wg_allowed_ips").value=cfg.allowed_ips;
      if(cfg.address)$("#s_wg_address").value=cfg.address;
      if(cfg.keepalive)$("#s_wg_keepalive").value=cfg.keepalive;
      if(cfg.psk)$("#s_wg_psk").value=cfg.psk;
      if(cfg.privkey)$("#s_wg_privkey").value=cfg.privkey;
      if(cfg.dns)$("#s_wg_dns").value=cfg.dns;
      $("#wg_qr_msg").textContent="✓ Felder ausgefüllt -- unten prüfen und mit „Speichern“ sichern";
      toast("QR-Code gelesen");
    }catch(e){$("#wg_qr_msg").textContent="✗ Fehler beim Lesen der Datei";}
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
        ("✓ aktiv"+(r.handshake?" -- letzter Handshake "+r.handshake:"")+(r.transfer?" ("+r.transfer+")":"")):
        "wird gestartet…";
    }else{
      onBtn.style.display="";offBtn.style.display="none";
      statusEl.textContent="aus";
    }
    if(document.body.contains(statusEl))setTimeout(refreshWg,15000);
  }
  $("#wg_on").onclick=async()=>{
    $("#wg_on").disabled=true;
    $("#wg_status").textContent="schalte ein…";
    try{
      const r=await jpost("api/settings",{wg_enabled:true});
      if(!r.ok)$("#wg_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#wg_status").textContent="✗ Verbindungsfehler";}
    $("#wg_on").disabled=false;
    refreshWg();
  };
  $("#wg_off").onclick=async()=>{
    $("#wg_off").disabled=true;
    $("#wg_status").textContent="schalte aus…";
    try{
      const r=await jpost("api/settings",{wg_enabled:false});
      if(!r.ok)$("#wg_status").textContent="✗ "+(r.error||"Fehler");
    }catch(e){$("#wg_status").textContent="✗ Verbindungsfehler";}
    $("#wg_off").disabled=false;
    refreshWg();
  };
  refreshWg();
}

boot();
