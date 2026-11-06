// Run with: node tests/test_bear.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function setup() {
  let clock=0;
  const handlers = {};
  const gl = new Proxy({}, {get: (_, key) => key.startsWith('get') ? () => true : () => {}});
  const elements = {};
  for(const id of ['bear-stage','bear-canvas','bear-caption','bear-motion','log','input-text','send-button','input-form']) {
    elements[id]={querySelectorAll:()=>[],dataset:{touchEnabled:"true"},value:'書きかけ',disabled:false,addEventListener:(type,fn)=>{handlers[`${id}:${type}`]=fn;}};
  }
  const canvas=elements['bear-canvas'];
  Object.assign(canvas,{width:600,height:300,clientWidth:600,clientHeight:300,
    getContext:()=>gl,getBoundingClientRect:()=>({left:0,top:0,width:600,height:300}),setPointerCapture:()=>{}});
  const sent=[];
  elements['input-form'].requestSubmit=()=>{sent.push(elements['input-text'].value);elements['input-text'].value='';};
  const context=vm.createContext({CustomEvent:class {constructor(type, options){this.type=type;this.detail=options.detail;}},
    document:{dispatchEvent:event=>sent.push(event.detail.part),getElementById:id=>elements[id],querySelectorAll:()=>[],addEventListener:(type,fn)=>{handlers[`document:${type}`]=fn;}},
    matchMedia:()=>({matches:false,addEventListener:()=>{}}),devicePixelRatio:1,
    MutationObserver:class {observe(){}},ResizeObserver:class {observe(){}},
    performance:{now:()=>clock},requestAnimationFrame:()=>1,cancelAnimationFrame:()=>{}});
  let source=fs.readFileSync('web/bear.js','utf8');
  source=source.replace(/  refresh\(\);\n\}\)\(\);\s*$/, '  yaw=0; draw(0); globalThis.api={pick,touch,pose(name,time=0){motion=typeof name==="string"?{arm:name}:name;started=0;duration=6000;draw(time);return shapes;},reaction(){return touchMotion;}};\n})();');
  vm.runInContext(source,context);
  return {api:context.api,elements,sent,handlers,setTime:time=>{clock=time;}};
}
test('nearest visible body parts are picked; background is not',()=>{
  const {api}=setup();
  assert.equal(api.pick(300,110),'頭');
  assert.equal(api.pick(300,190),'おなか');
  assert.equal(api.pick(265,255),'右足');
  assert.equal(api.pick(335,255),'左足');
  assert.equal(api.pick(260,180),'右腕');
  assert.equal(api.pick(340,180),'左腕');
  assert.equal(api.pick(10,10),null);
});
test('touch records independently while text input is busy, and blocks disconnected or ended sessions',()=>{
  const {api,elements,sent}=setup();
  elements['input-text'].disabled=true;
  elements['send-button'].disabled=true;
  api.touch('頭');
  assert.deepEqual(sent,['頭']);
  assert.equal(elements['input-text'].value,'書きかけ');
  elements['bear-stage'].dataset.touchEnabled='false';
  api.touch('おなか');
  assert.equal(sent.length,1);
});
test('tap submits once; rotation and pointer cancellation do not submit',()=>{
  const {handlers,sent}=setup();
  const down=handlers['bear-canvas:pointerdown'], move=handlers['bear-canvas:pointermove'];
  const up=handlers['bear-canvas:pointerup'];
  const event=(x,y)=>({isPrimary:true,button:0,pointerId:1,clientX:x,clientY:y});
  down(event(300,110));up(event(300,110));
  assert.equal(sent.length,1);
  down(event(300,110));move(event(330,110));up(event(330,110));
  assert.equal(sent.length,1);
  down(event(300,110));handlers['bear-canvas:pointercancel']();up(event(300,110));
  assert.equal(sent.length,1);
});

test('both arms remain joined to the torso in resting and raised poses',()=>{
  const {api}=setup();
  for(const pose of ['', 'right_hand_up', 'left_hand_up', 'right_hand_bye', 'left_hand_chin', 'right_hand_mouth', 'left_hand_point', 'onegai']) {
    const shapes=api.pose(pose);
    for(const part of ['右腕','左腕']) {
      const arm=shapes.filter(s=>s.part===part);
      assert.ok(arm.length>0);
      assert.ok(arm.some(s=>{
        const [x,y,z]=s.center;
        return (x/.60)**2+((y-1.37)/.43)**2+((z+.12)/.48)**2 < 1;
      }),`${pose}: ${part} must overlap the chest`);
      assert.ok(arm.every(s=>s.center.every(Number.isFinite) && s.scale.every(v=>Number.isFinite(v) && v>0)));
    }
  }
});

test('tilt rotates the whole head about the neck, including face and tap volumes',()=>{
  const {api}=setup();
  const upright=api.pose({},600);
  const tilted=api.pose({neck:'tilt'},600);
  const before=upright.filter(s=>s.part==='頭');
  const after=tilted.filter(s=>s.part==='頭');
  // The two outer ears cease to be level; translating a head cannot do this.
  assert.ok(Math.abs(after[1].center[1]-after[3].center[1])>.2);
  const distance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
  for(let i=0;i<before.length;i++) {
    assert.ok(Math.abs(distance(before[0].center,before[i].center)-distance(after[0].center,after[i].center))<1e-9);
    assert.ok(Math.abs(after[i].lean-.24)<1e-9);
  }
  assert.deepEqual(tilted.filter(s=>s.part!=='頭'),upright.filter(s=>s.part!=='頭'));
  const center=after[0].center;
  const depth=6.5-center[2];
  assert.equal(api.pick(300+center[0]*3.3/depth*150,150-(center[1]-1.65)*3.3/depth*150),'頭');
});

test('nod pitches the whole head forward and returns, without translating it vertically',()=>{
  const {api}=setup();
  const upright=api.pose({},850).filter(s=>s.part==='頭');
  const down=api.pose({neck:'nod'},850).filter(s=>s.part==='頭');
  assert.ok(down[0].center[2]>upright[0].center[2]+.1);
  assert.ok(down[6].center[1]<upright[6].center[1]-.1); // nose lowers as head bows
  for(const shape of down) assert.ok(Math.abs(shape.pitch-.30)<1e-9);
  const returned=api.pose({neck:'nod'},1700).filter(s=>s.part==='頭');
  assert.ok(Math.abs(returned[0].pitch)<1e-9);
});
test('onegai brings both hands together below the chin',()=>{
  const {api}=setup();
  const shapes=api.pose('onegai',600);
  for(const part of ['右腕','左腕']) {
    const arm=shapes.filter(s=>s.part===part);
    const tip=arm[arm.length-1].center;
    assert.ok(tip[1]>1.8 && tip[2]>.75);
    assert.ok(Math.abs(tip[0])<.15);
  }
});

test('recorded touches select robot-style gestures with a shared five-second cooldown',()=>{
  const {api,handlers,setTime}=setup();
  const recorded=part=>handlers['document:pooh-touch-recorded']({detail:{part}});
  recorded('右腕');
  const right=api.reaction();
  assert.ok(right.neck || right.arm);
  assert.ok(['left_hand_chin',''].includes(right.arm));
  setTime(4999);recorded('左腕');
  assert.equal(api.reaction(),right);
  setTime(5000);recorded('左腕');
  const left=api.reaction();
  assert.ok(left.neck || left.arm);
  assert.ok(['right_hand_mouth',''].includes(left.arm));
  setTime(10000);recorded('頭');
  const head=api.reaction();
  assert.ok(head.neck || head.arm);
  assert.ok(['left_hand_chin','right_hand_mouth','onegai',''].includes(head.arm));
  for(const [i,part] of ['おなか','左足','右足'].entries()) {
    setTime(15000+i*5000);recorded(part);
    const response=api.reaction();
    assert.notEqual(response,head);
    assert.ok(response.neck || response.arm);
    assert.ok(['left_hand_chin','right_hand_mouth','onegai',''].includes(response.arm));
  }
});
