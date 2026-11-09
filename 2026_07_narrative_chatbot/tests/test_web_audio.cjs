// Exercise the actual browser audio queue without a browser/TTS server.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup() {
 const sent=[],events={};
 class Audio {
  addEventListener(name,callback){events[name]=callback;}
  play(){return Promise.resolve();}
 }
 const context=vm.createContext({Audio,WebSocket:{OPEN:1},socket:{readyState:1,send:text=>sent.push(JSON.parse(text))},
  document:{addEventListener(){}},updateControls(){}});
 const source=fs.readFileSync('web/app.js','utf8');
 const queue=source.slice(source.indexOf('let audioElement = null;'),source.indexOf('function appendOutput('));
 vm.runInContext(queue+'\nglobalThis.api={queueAudio,get queue(){return audioQueue;}};',context);
 return {api:context.api,events,sent};
}
test('audio completion acknowledges exactly the finished line, including playback errors',()=>{
 const {api,events,sent}=setup();
 api.queueAudio('/first.wav','first');api.queueAudio('/second.wav','second');
 assert.equal(sent.length,0);
 events.ended();assert.deepEqual(sent,[{type:'playback_finished',playback_id:'first'}]);
 assert.equal(api.queue.length,1);
 events.error();assert.equal(sent[1].playback_id,'second');assert.equal(api.queue.length,0);
});
test('lines without a playback token keep the existing queue behavior',()=>{
 const {api,events,sent}=setup();api.queueAudio('/birthday.wav');events.ended();
 assert.equal(sent.length,0);assert.equal(api.queue.length,0);
});
