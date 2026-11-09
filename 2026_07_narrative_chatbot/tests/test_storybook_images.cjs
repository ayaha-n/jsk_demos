const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8');
const retry=source.slice(source.indexOf('function retryFailedStoryImages('),source.indexOf('function appendStoryPage('));
const load=source.slice(source.indexOf('async function loadOwnStorybook('),source.indexOf('// Auto-open once per session:'));
test('an unchanged book retries broken images without rebuilding pages or touching healthy images',async()=>{
 const broken={src:'https://example.test/storybook/generated/picture.png',complete:true,naturalWidth:0};
 const healthy={src:'https://example.test/storybook/healthy.png',complete:true,naturalWidth:512};
 const pending={src:'https://example.test/storybook/loading.png',complete:false,naturalWidth:0};
 const book={title:'絵本',pages:[{illustration:'picture.png',illustration_url:broken.src}]};
 const context=vm.createContext({URL,location:{href:'https://example.test/'},
  endedSessionId:'session',ownStorybookLoading:false,ownStorybookSnapshot:JSON.stringify(book),
  storybookLayout:{value:'landscape'},storybookPdf:{},storybookStatus:{},storybookProgress:{},
  storybookViewer:{querySelectorAll:()=>[broken,healthy,pending],replaceChildren:()=>assert.fail('must preserve pages')},
  accessToken:()=>'',authenticatedHeaders:()=>({}),crowd:undefined,
  fetch:async()=>({ok:true,json:async()=>book}),appendStoryPage:()=>assert.fail('must preserve pages')});
 vm.runInContext(retry+'\n'+load,context);
 await vm.runInContext('loadOwnStorybook(true)',context);
 assert.ok(new URL(broken.src).searchParams.has('_retry'));
 assert.equal(healthy.src,'https://example.test/storybook/healthy.png');
 assert.equal(pending.src,'https://example.test/storybook/loading.png');
 assert.equal(context.ownStorybookLoading,false);
});
