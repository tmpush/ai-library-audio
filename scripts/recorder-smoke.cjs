const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn,spawnSync}=require('child_process');
const fs=require('fs'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),data=fs.mkdtempSync(path.join(root,'data','recorder-check-'));
const python=path.join(root,'.venv-tts','Scripts','python.exe');
const child=spawn(python,['-X','utf8','-m','client.preview','--no-browser','--with-samples','--data-dir',data],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
let browser;
(async()=>{
 const url=await new Promise((resolve,reject)=>{let buffer='';const timer=setTimeout(()=>reject(Error('startup timeout')),30000);child.stdout.on('data',chunk=>{buffer+=chunk;const end=buffer.indexOf('\n');if(end>=0){try{resolve(JSON.parse(buffer.slice(0,end)).url);clearTimeout(timer)}catch(e){reject(e)}}});child.on('exit',code=>reject(Error('preview exited '+code)));});
 browser=await chromium.launch({headless:true,channel:'msedge',args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--use-file-for-fake-audio-capture='+path.join(root,'data/tts-check/reference.wav')]});
 const page=await browser.newPage({viewport:{width:1200,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.testTracks=[];const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async options=>{if(window.testDenied)throw new DOMException('denied','NotAllowedError');const stream=await original(options);window.testTracks.push(...stream.getTracks());if(window.testDelay)await new Promise(r=>setTimeout(r,1500));return stream}});
 await page.goto(url);await page.getByRole('button',{name:'Голос и озвучка'}).click();
 await page.getByText('Записать мой голос с микрофона',{exact:true}).click();
 const begin=()=>page.getByRole('button',{name:'Начать запись',exact:true}).click();
 const stopped=async()=>page.waitForFunction(()=>window.testTracks.every(t=>t.readyState==='ended'),null,{timeout:2000});
 await page.evaluate(()=>window.testDenied=true);await begin();await page.getByRole('alert').filter({hasText:'Доступ к микрофону не разрешён'}).waitFor();await page.evaluate(()=>window.testDenied=false);
 await begin();await page.getByRole('button',{name:'Остановить запись',exact:true}).waitFor();await page.waitForTimeout(700);await page.getByRole('button',{name:'Остановить запись',exact:true}).click();await page.getByRole('alert').filter({hasText:'короче 10 секунд'}).waitFor();await stopped();
 await begin();await page.getByRole('button',{name:'Остановить запись',exact:true}).waitFor();await page.waitForTimeout(12000);await page.getByRole('button',{name:'Остановить запись',exact:true}).click();await page.getByLabel('Моя запись').waitFor();await stopped();
 await page.getByLabel('Имя записанного голоса').fill('Проверка записи — синтетический источник');
 const response=page.waitForResponse(r=>r.url().endsWith('/voices')&&r.request().method()==='POST');await page.getByRole('button',{name:'Сохранить мой голос',exact:true}).click();const saved=await(await response).json();assert(saved.id&&saved.duration>=10&&saved.sample_rate===24000&&saved.channels===1,JSON.stringify(saved));assert(saved.transcript.startsWith('Сегодня я читаю'));
 await page.getByText('Голос сохранён и выбран для озвучки.',{exact:true}).waitFor();assert.equal(await page.locator('.render-controls select').inputValue(),saved.id);
 await page.setViewportSize({width:640,height:900});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)),'overflow');await page.screenshot({path:path.join(root,'artifacts/recorder-smoke.png'),fullPage:true});
 await page.getByRole('button',{name:'Записать заново',exact:true}).click();await page.getByRole('button',{name:'Остановить запись',exact:true}).waitFor();await page.getByText('Записать мой голос с микрофона',{exact:true}).click();await stopped();
 await page.getByText('Записать мой голос с микрофона',{exact:true}).click();await page.evaluate(()=>window.testDelay=true);await begin();await page.getByRole('button',{name:'Отменить запрос',exact:true}).click();await page.waitForTimeout(1800);await stopped();await page.evaluate(()=>window.testDelay=false);
 await begin();await page.getByRole('button',{name:'Остановить запись',exact:true}).waitFor();await page.getByLabel('Моя запись').waitFor({timeout:35000});await stopped();const duration=await page.getByLabel('Моя запись').evaluate(a=>a.duration);assert(duration<=30&&duration>=29,'auto stop duration '+duration);
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(root,'artifacts/recorder-check.json'),JSON.stringify({passed:true,synthetic_input_only:true,duration:saved.duration,auto_stop_duration:duration,checks:['permission denial','short clip rejection','real MediaRecorder to PCM WAV','local voice save and selection','640px layout','close and cancelled permission stop tracks','30s auto stop']},null,2));
 console.log('PASS: recorder, WAV save, auto stop, cancellation, layout; synthetic input only');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();spawnSync(python,['-m','client.preview','--stop','--data-dir',data],{cwd:root,windowsHide:true,timeout:10000});child.kill()});


