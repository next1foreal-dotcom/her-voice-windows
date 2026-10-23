// Browser-only fixture, served at /ui-driver.js by scripts/ui-smoke.mjs.
// These callbacks simulate UI state only; they never capture audio or call an API.
import {createUI} from './ui.js';
let state={connection:'disconnected',state:'idle',mode:'live',messages:[],health:{app_name:'HER Voice',engine:{state:'ready'},provider_ready:true},settings:{captions:true,bargeIn:true}};
const render = patch => { state={...state,...patch}; ui.render(state); };
const ui=createUI({
 onStart:()=>render({connection:'connected',state:'listening'}),
 onStop:()=>render({connection:'disconnected',state:'idle',paused:false}),
 onPause:()=>render({paused:!state.paused,state:state.paused?'listening':'paused'}),
 onInterrupt:()=>render({state:'interrupted',caption:{user:'',assistant:''}}),
 onSettingsChange:settings=>render({settings:{...state.settings,...settings}}),
 onModeChange:mode=>render({mode,connection:'disconnected',state:'idle',paused:false}),
 onClearHistory:()=>render({messages:[],connection:'disconnected',state:'idle',paused:false}),
 onSend:text=>render({state:'thinking',caption:{user:text,assistant:''},messages:[...state.messages,{id:Date.now(),role:'user',text,status:'complete'}]}),
});
window.fixture={ui,render,get state(){return state}};
const q=new URLSearchParams(location.search);const initial=q.get('state')||'idle';
render({state:initial,connection:initial==='idle'?'disconnected':'connected',paused:initial==='paused'});
if(q.has('messages'))render({messages:[{id:1,role:'user',text:'最近有好多想做的事，卻不知道從哪裡開始。',status:'complete'},{id:2,role:'assistant',text:'先不用急著把每件事排好順序。\n\n我們可以從最讓你在意的那一件開始。你現在腦中，第一個浮現的是什麼？',status:'complete'}],caption:{user:'最近有好多想做的事。',assistant:'我們從最讓你在意的那一件開始。'},metrics:{firstAudioMs:820}});
if(q.has('error'))render({connection:'error',error:'無法連接本機語音服務，請確認服務已啟動。'});
