/* Standalone, dependency-free WebGL bear. Existing chat DOM is the adapter;
 * Touches are independent observations; the seated silhouette references
 * assets/stuffed_pooh2.stl and the palette references bear-reference.png. */
"use strict";
(() => {
  const host = document.getElementById("bear-stage");
  const canvas = document.getElementById("bear-canvas");
  // Older running servers retain their startup HTML. Remove obsolete UI
  // there too, so refreshing the updated script is enough.
  host.querySelectorAll(".bear-toolbar, .bear-touch-controls, [data-bear-touch]").forEach(node=>node.remove());
  // Some GPU/driver combinations cannot create a multisampled context.
  const gl = canvas.getContext("webgl", { alpha: true, antialias: true })
    || canvas.getContext("webgl", { alpha: true, antialias: false });
  if (!gl) {
    host.hidden = true;
    return;
  }
  function shader(type, source) {
    const s = gl.createShader(type);
    gl.shaderSource(s, source); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, `
    attribute vec3 p, vertexNormal; uniform bool isMesh; uniform vec3 center, scale; uniform float yaw, aspect, lean, pitch;
    varying mediump vec3 normal, surface;
    void main() {
      mat3 limb = mat3(cos(lean),sin(lean),0., -sin(lean),cos(lean),0., 0.,0.,1.);
      vec3 n = limb * normalize(isMesh ? vertexNormal : p / scale);
      mat3 neck = mat3(1.,0.,0., 0.,cos(pitch),sin(pitch), 0.,-sin(pitch),cos(pitch));
      vec3 v = neck * (limb * (p * scale)) + center;
      n = neck * n;
      mat3 r = mat3(cos(yaw),0.,-sin(yaw), 0.,1.,0., sin(yaw),0.,cos(yaw));
      surface = v; v = r * v; normal = r * n;
      float depth = 6.5 - v.z;
      gl_Position = vec4(v.x * 3.3 / aspect, (v.y - 1.65) * 3.3, -v.z * .12 * depth, depth);
    }`));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, `
    precision mediump float; uniform vec3 color; varying mediump vec3 normal, surface;
    void main() {
      float diffuse = max(dot(normalize(normal), normalize(vec3(-.6,1.,1.5))),0.);
      float light = .67 + .30 * diffuse;
      // Fine warm grain suggests the reference's soft illustrated fur.
      float grain = fract(sin(dot(floor(surface * 180.), vec3(12.9,78.2,37.7))) * 437.5);
      gl_FragColor = vec4(color * light * (.975 + .045 * grain), 1.);
    }`));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const vertices = [];
  const point = (a,b) => [Math.sin(a)*Math.cos(b), Math.cos(a), Math.sin(a)*Math.sin(b)];
  for (let i=0;i<24;i++) for (let j=0;j<32;j++) {
    const a=i*Math.PI/24, b=j*Math.PI*2/32, da=Math.PI/24, db=Math.PI*2/32;
    vertices.push(...point(a,b), ...point(a+da,b), ...point(a+da,b+db),
      ...point(a,b), ...point(a+da,b+db), ...point(a,b+db));
  }
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program,"p");
  gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,3,gl.FLOAT,false,0,0);
  const normalPosition = gl.getAttribLocation(program,"vertexNormal");
  gl.enableVertexAttribArray(normalPosition);
  gl.vertexAttribPointer(normalPosition,3,gl.FLOAT,false,0,0);
  const uniforms = Object.fromEntries(["center","scale","color","yaw","aspect","lean","pitch","isMesh"].map(k=>[k,gl.getUniformLocation(program,k)]));
  gl.enable(gl.DEPTH_TEST);
  // Palette and unclothed, round silhouette from assets/storybook/bear-reference.png.
  const gold=[1,.73,.36], black=[.20,.115,.065], cream=[1,.94,.76];
  let shapes = [], part = null;
  let headAngle=0, headPitch=0, headPivot=[0,1.61,0];
  let motion = {}, started = 0, duration = 0, yaw = -.12, drag = null;
  let animation = null;
  let touchMotion=null, touchStarted=0;
  let lastTouchAction=-Infinity;
  const touchDuration=6000;
  // Match pooh_body/scripts/proximity_listener_array_timer.py: do not move
  // the touched hand, choose a nonempty neck/arm reaction, and throttle
  // actions for five seconds. Recording is independent of this cooldown.
  function reactToTouch(bodyPart) {
    const now=performance.now();
    if(now-lastTouchAction<5000) return;
    let arms;
    if(["頭","おなか","右足","左足"].includes(bodyPart)) arms=["left_hand_chin","right_hand_mouth","onegai",""];
    else if(bodyPart==="右腕") arms=["left_hand_chin",""];
    else if(bodyPart==="左腕") arms=["right_hand_mouth",""];
    else return;
    const choices=[];
    for(const neck of ["tilt",""]) for(const arm of arms) {
      if(neck || arm) choices.push({neck,arm});
    }
    const eyebrows=["normal","happy","surprised","","",""];
    touchMotion={...choices[Math.floor(Math.random()*choices.length)],
      eyebrow:eyebrows[Math.floor(Math.random()*eyebrows.length)]};
    touchStarted=lastTouchAction=now;
    refresh();
  }
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  function sphere(center, scale, color, lean=0) {
    if(part==="頭" && headAngle!==0) {
      const x=center[0]-headPivot[0], y=center[1]-headPivot[1];
      const c=Math.cos(headAngle), s=Math.sin(headAngle);
      center=[headPivot[0]+c*x-s*y,headPivot[1]+s*x+c*y,center[2]];
      lean+=headAngle;
    }
    const pitch=part==="頭" ? headPitch : 0;
    if(pitch!==0) {
      const y=center[1]-headPivot[1],z=center[2]-headPivot[2];
      const c=Math.cos(pitch),s=Math.sin(pitch);
      center=[center[0],headPivot[1]+c*y-s*z,headPivot[2]+s*y+c*z];
    }
    gl.uniform1i(uniforms.isMesh,0);
    gl.uniform1f(uniforms.pitch,pitch);
    shapes.push({center, scale, part, lean, pitch});
    gl.uniform1f(uniforms.lean,lean);
    gl.uniform3fv(uniforms.center, center); gl.uniform3fv(uniforms.scale,scale);
    gl.uniform3fv(uniforms.color,color); gl.drawArrays(gl.TRIANGLES,0,vertices.length/3);
  }
  const limbBuffer=gl.createBuffer();
  const limbCache=new Map();
  // A continuous curved plush limb: one soft surface from shoulder/hip to
  // the rounded tip, rather than a separate ball attached to an oval.
  function plushLimb(start, bend, end, width, depth, floor=null) {
    let origin=start;
    bend=bend.map((v,i)=>v-origin[i]);end=end.map((v,i)=>v-origin[i]);
    start=[0,0,0];
    const key=JSON.stringify([...bend,...end,width,depth].map(v=>Math.round(v*100)/100));
    let geometry=limbCache.get(key);
    if(!geometry) {
    const volumes=[];
    const point=(a,b)=>{
      const u=(1-Math.cos(a))/2, v=1-u;
      const center=start.map((p,i)=>v*v*p+2*v*u*bend[i]+u*u*end[i]);
      const tangent=start.map((p,i)=>2*v*(bend[i]-p)+2*u*(end[i]-bend[i]));
      const length=Math.hypot(...tangent) || 1;
      const axis=tangent.map(v=>v/length);
      const projected=[1-axis[0]*axis[0],-axis[0]*axis[1],-axis[0]*axis[2]];
      const span=Math.hypot(...projected)||1;
      const x=projected.map(v=>v/span);
      const z=[axis[1]*x[2]-axis[2]*x[1],axis[2]*x[0]-axis[0]*x[2],axis[0]*x[1]-axis[1]*x[0]];
      const taper=1-.19*u;
      const r=Math.sin(a)*taper;
      return center.map((p,i)=>p+r*(x[i]*width*Math.cos(b)+z[i]*depth*Math.sin(b)));
    };
    const sample=(a,b)=>{
      const p=point(a,b), eps=.001;
      const da=point(Math.min(Math.PI-.0001,a+eps),b).map((v,i)=>v-point(Math.max(.0001,a-eps),b)[i]);
      const db=point(a,b+eps).map((v,i)=>v-point(a,b-eps)[i]);
      let n=[da[1]*db[2]-da[2]*db[1],da[2]*db[0]-da[0]*db[2],da[0]*db[1]-da[1]*db[0]];
      const length=Math.hypot(...n)||1;
      n=n.map(v=>-v/length);
      return [...p,...n];
    };
    const rows=20, columns=24, samples=[];
    for(let i=0;i<=rows;i++) {
      const a=Math.max(.0001,Math.min(Math.PI-.0001,i*Math.PI/rows));
      const row=[];
      for(let j=0;j<=columns;j++) row.push(sample(a,j*Math.PI*2/columns));
      samples.push(row);
      // Closely spaced interior volumes follow the curved surface for taps.
      if(i>0 && i<rows) {
        const u=(1-Math.cos(a))/2,v=1-u,r=Math.sin(a)*(1-.19*u);
        volumes.push({center:start.map((p,k)=>v*v*p+2*v*u*bend[k]+u*u*end[k]),
          scale:[Math.max(.025,width*r),Math.max(.025,width*r),Math.max(.025,depth*r)]});
      }
    }
    const mesh=[];
    for(let i=0;i<rows;i++) for(let j=0;j<columns;j++) {
      mesh.push(...samples[i][j],...samples[i+1][j],...samples[i+1][j+1],
        ...samples[i][j],...samples[i+1][j+1],...samples[i][j+1]);
    }
    let minY=Infinity;
    for(let i=1;i<mesh.length;i+=6) minY=Math.min(minY,mesh[i]);
    geometry={mesh:new Float32Array(mesh),volumes,minY};
    if(limbCache.size>=80) limbCache.delete(limbCache.keys().next().value);
    limbCache.set(key,geometry);
    }
    // Set the actual mesh's lowest point on the same floor as the rump.
    // Move the whole curved leg, including its attachment and tap volumes.
    if(floor!==null) origin=[origin[0],floor-geometry.minY,origin[2]];
    for(const volume of geometry.volumes) shapes.push({
      center:volume.center.map((v,i)=>v+origin[i]),scale:volume.scale,part,lean:0});
    gl.bindBuffer(gl.ARRAY_BUFFER,limbBuffer);
    gl.bufferData(gl.ARRAY_BUFFER,geometry.mesh,gl.DYNAMIC_DRAW);
    gl.vertexAttribPointer(position,3,gl.FLOAT,false,24,0);
    gl.vertexAttribPointer(normalPosition,3,gl.FLOAT,false,24,12);
    gl.uniform1i(uniforms.isMesh,1);
    gl.uniform1f(uniforms.lean,0);
    gl.uniform1f(uniforms.pitch,0);
    gl.uniform3fv(uniforms.center,origin);gl.uniform3fv(uniforms.scale,[1,1,1]);
    gl.uniform3fv(uniforms.color,gold);
    gl.drawArrays(gl.TRIANGLES,0,geometry.mesh.length/6);
    gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
    gl.vertexAttribPointer(position,3,gl.FLOAT,false,0,0);
    gl.vertexAttribPointer(normalPosition,3,gl.FLOAT,false,0,0);
  }
  function draw(now) {
    animation = null;
    shapes = []; part = null;
    const moving = !reduced.matches;
    const t = moving ? now/1000 : 0;
    const active = moving && now-started < duration;
    const touchActive=moving && touchMotion && now-touchStarted<touchDuration;
    const m = touchActive ? touchMotion : active ? motion : {};
    const actionStarted=touchActive ? touchStarted : started;
    const actionDuration=touchActive ? touchDuration : duration;
    const breathe = moving ? Math.sin(t*1.8)*.025 : 0;
    const w = Math.round(canvas.clientWidth * Math.min(devicePixelRatio || 1,2));
    const h = Math.round(canvas.clientHeight * Math.min(devicePixelRatio || 1,2));
    if (canvas.width!==w || canvas.height!==h) { canvas.width=w; canvas.height=h; }
    gl.viewport(0,0,w,h); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.uniform1f(uniforms.yaw,yaw); gl.uniform1f(uniforms.aspect,w/Math.max(h,1));
    // A seated, pear-shaped plush: no scan pedestal or stray scan fragments.
    for(const side of [-1,1]) {
      part=side===-1 ? "右足" : "左足";
      plushLimb([side*.43,.70,-.32], [side*.70,.16,.44],
        [side*.34,.29,1.05], .30,.275,.21);
    }
    part="おなか";
    sphere([0,.94+breathe,-.10],[.79,.73+breathe,.64],gold);
    sphere([0,1.37+breathe,-.12],[.60,.43,.48],gold);

    part="頭";
    // Rotate the entire head about the neck, easing in and back to upright.
    const ramp=Math.max(0,Math.min(1,(now-actionStarted)/400,(actionDuration-(now-actionStarted))/400));
    headAngle=m.neck==="tilt" ? .24*(.5-.5*Math.cos(Math.PI*ramp)) : 0;
    headPivot=[0,1.61+breathe,0];
    const nodPhase=(now-actionStarted)/1700*Math.PI*2;
    headPitch=m.neck==="nod" ? .30*(.5-.5*Math.cos(nodPhase))*ramp : 0;
    const headY=2.17+breathe;
    sphere([0,headY,0],[.76,.68,.57],gold);
    for(const side of [-1,1]) {
      sphere([side*.55,headY+.51,0],[.24,.26,.17],gold);
      sphere([side*.55,headY+.51,.15],[.14,.17,.045],gold);
    }
    sphere([0,headY-.20,.48],[.43,.28,.26],gold);
    sphere([0,headY-.12,.72],[.16,.105,.10],black);
    // Brief eyelid closure, suppressed when motion is paused.
    const blink = moving && t%5.3>5.12;
    for(const side of [-1,1]) {
      sphere([side*.27,headY+.08,.51],[.059,blink?.012:.078,.038],black);
      if(!blink) sphere([side*.27-.013,headY+.105,.548],[.014,.018,.009],cream);
      const browY = m.eyebrow==="surprised" ? .29 : m.eyebrow==="sad" ? .20 : .23;
      sphere([side*.27,headY+browY,.50],[.085,.014,.018],black);
    }
    // A soft upward mouth curve, rather than a flat line.
    for(let i=-4;i<=4;i++) {
      const x=i*.032;
      sphere([x,headY-.37+x*x*2,.706],[.025,.016,.02],black);
    }
    for(const side of [-1,1]) {
      part=side===-1 ? "右腕" : "左腕";
      const name=m.arm || "";
      const selected=name.startsWith(side===-1 ? "right" : "left");
      let hand=[side*.29,1.02+breathe,.64];
      if(selected && /up|bye/.test(name)) hand=[side*.99,1.92+Math.sin(t*8)*(/bye/.test(name)?.12:.025),.22];
      if(selected && /chin|mouth/.test(name)) hand=[side*.28,1.89,.77];
      if(selected && /point/.test(name)) hand=[side*1.22,1.48,.44];
      if(/onegai|onagai/.test(name)) hand=[side*.12,1.89+breathe,.80];
      // Keep the anchor inside the chest in every pose. Starting on the
      // outer shoulder made the tapered end detach when the arm pointed up.
      const shoulder=[side*.43,1.44+breathe,-.08];
      // Broad at the shoulder, gently curved inward, with a rounded mitten tip.
      const bend=[side*.67,(shoulder[1]+hand[1])/2,hand[2]*.60];
      plushLimb(shoulder,bend,[hand[0],hand[1]-.07,hand[2]],.28,.255);

    }
    if(moving && !document.hidden) animation=requestAnimationFrame(draw);
  }
  function refresh() { if(animation===null) animation=requestAnimationFrame(draw); }
  document.addEventListener("pooh-touch-recorded",event=>{
    reactToTouch(event.detail.part);
  });
  reduced.addEventListener("change",refresh);
  document.addEventListener("visibilitychange",()=>{
    if(document.hidden && animation!==null) {cancelAnimationFrame(animation);animation=null;}
    else refresh();
  });
  new ResizeObserver(refresh).observe(host);
  // Match the perspective in the vertex shader, then intersect every visible
  // ellipsoid. The nearest surface wins, including raised arms over the face.
  function pick(clientX, clientY) {
    const rect=canvas.getBoundingClientRect();
    if(!rect.width || !rect.height) return null;
    const x=(clientX-rect.left)/rect.width*2-1;
    const y=1-(clientY-rect.top)/rect.height*2;
    const c=Math.cos(yaw), s=Math.sin(yaw);
    const dx=x*(canvas.width/canvas.height)/3.3, dy=y/3.3;
    const origin=[-s*6.5,1.65,c*6.5];
    const direction=[c*dx+s,dy,s*dx-c];
    let nearest=Infinity, hit=null;
    for(const shape of shapes) {
      const local=origin.map((v,i)=>v-shape.center[i]);
      const lc=Math.cos(shape.lean), ls=Math.sin(shape.lean);
      const unrotate=v=>{
        const pitch=shape.pitch || 0,pc=Math.cos(pitch),ps=Math.sin(pitch);
        const y=pc*v[1]+ps*v[2],z=-ps*v[1]+pc*v[2];
        return [lc*v[0]+ls*y,-ls*v[0]+lc*y,z];
      };
      const o=unrotate(local).map((v,i)=>v/shape.scale[i]);
      const d=unrotate(direction).map((v,i)=>v/shape.scale[i]);
      const a=d.reduce((sum,v)=>sum+v*v,0);
      const b=2*d.reduce((sum,v,i)=>sum+v*o[i],0);
      const q=o.reduce((sum,v)=>sum+v*v,0)-1;
      const discriminant=b*b-4*a*q;
      if(discriminant<0) continue;
      const distance=(-b-Math.sqrt(discriminant))/(2*a);
      if(distance>0 && distance<nearest) {nearest=distance;hit=shape.part;}
    }
    return hit;
  }
  function touch(bodyPart) {
    if(host.dataset.touchEnabled!=="true") {
      return;
    }
    document.dispatchEvent(new CustomEvent("pooh-touch",{detail:{part:bodyPart}}));
  }
  canvas.addEventListener("pointerdown",e=>{
    if(!e.isPrimary || e.button!==0 || drag) return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,lastX:e.clientX,moved:false};
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove",e=>{
    if(!drag || drag.id!==e.pointerId) return;
    if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>8) drag.moved=true;
    if(drag.moved) {yaw+=(e.clientX-drag.lastX)*.008;refresh();}
    drag.lastX=e.clientX;
  });
  canvas.addEventListener("pointerup",e=>{
    if(!drag || drag.id!==e.pointerId) return;
    const tapped=!drag.moved && Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<=8;
    drag=null;
    if(tapped) {const hit=pick(e.clientX,e.clientY);if(hit) touch(hit);}
  });
  canvas.addEventListener("pointercancel",()=>{drag=null;});
  canvas.addEventListener("lostpointercapture",()=>{drag=null;});
  canvas.addEventListener("keydown",e=>{
    if(e.key==="ArrowLeft" || e.key==="ArrowRight") {e.preventDefault();yaw+=e.key==="ArrowLeft"?-.15:.15;refresh();}
    if(e.key==="Home") {e.preventDefault();yaw=-.12;refresh();}
  });
  canvas.addEventListener("webglcontextlost",e=>{
    e.preventDefault(); if(animation!==null) cancelAnimationFrame(animation);
    animation=null;
  });
  const labels={眉:"eyebrow",腕:"arm",首:"neck",鼻:"nose"};
  // Observe only the current conversation, never the past-log/book dialogs.
  new MutationObserver(records=>{
    let latest=null;
    for(const record of records) for(const node of record.addedNodes) {
      if(node.nodeType===1 && node.matches(".message.pooh")) latest=node;
    }
    if(!document.querySelector("#log .message.pooh")) {motion={};duration=0;touchMotion=null;lastTouchAction=-Infinity;}
    if(latest) {
      motion={};
      const detail=latest.querySelector(".details")?.textContent || "";
      const line=detail.split("\n").find(s=>s.startsWith("動作: ")) || "";
      for(const match of line.matchAll(/(眉|腕|首|鼻) ([a-z_]+)/g)) motion[labels[match[1]]]=match[2];
      started=performance.now(); duration=6000;
    }
    refresh();
  }).observe(document.getElementById("log"),{childList:true});
  refresh();
})();
