const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn,spawnSync}=require('child_process');
const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..');
fs.mkdirSync(path.join(root,'data'),{recursive:true});
const data=fs.mkdtempSync(path.join(root,'data','preview-check-'));
const python=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const child=spawn(python,['-m','client.preview','--no-browser','--data-dir',data],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
let browser;
(async()=>{
 const url=await new Promise((resolve,reject)=>{
  let buffer='';const timeout=setTimeout(()=>reject(Error('Preview startup timed out')),30000);
  child.on('exit',code=>{clearTimeout(timeout);reject(Error('Preview exited: '+code))});
  child.stdout.on('data',chunk=>{buffer+=chunk.toString();const newline=buffer.indexOf('\n');if(newline>=0){try{resolve(JSON.parse(buffer.slice(0,newline)).url);clearTimeout(timeout)}catch(e){reject(e)}}});
  child.stderr.on('data',chunk=>{if(chunk.toString().includes('Traceback'))reject(Error(chunk.toString()))});
 });
 browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:')&&!r.url().startsWith('blob:'))errors.push('Unexpected network host')});
 await page.goto(url);
 const reopened=spawnSync(python,['-m','client.preview','--no-browser','--data-dir',data],{cwd:root,windowsHide:true,encoding:'utf8',timeout:10000});
 if(reopened.status!==0||JSON.parse(reopened.stdout).url!==url)throw Error('Preview did not reuse its running instance');
 await page.getByRole('heading',{name:'Добавьте первую книгу'}).waitFor();
 if(page.url().includes('#session='))throw Error('Session fragment not removed');
 for(const [filename,title] of [['night-keeper.fb2','Ночной смотритель'],['lighthouse.epub','Письма с маяка'],['forest-route.pdf','Маршрут через лес']]){
  await page.locator('input[type=file]').first().setInputFiles(path.join(root,'samples',filename));
  await page.getByRole('heading',{name:title,exact:true}).waitFor();
 }
 await page.screenshot({path:path.join(root,'artifacts/preview-library.png'),fullPage:true});
 const endpoint=new URL(page.url()).origin;
 if((await page.request.get(endpoint+'/projects')).status()!==401)throw Error('Public preview exposed API');
 if((await page.request.post(endpoint+'/preview-session/stop')).status()!==401)throw Error('Unauthenticated preview shutdown allowed');
 await page.getByRole('button',{name:'Голос и озвучка'}).click();
 await page.getByText('В этой сборке модуль озвучки ещё не установлен.',{exact:false}).waitFor();
 if(await page.getByRole('button',{name:'Озвучить книгу',exact:true}).isEnabled())throw Error('Preview bypassed licensing');
 if(errors.length)throw Error(errors.join('\n'));
 const stopped=spawnSync(python,['-m','client.preview','--stop','--data-dir',data],{cwd:root,windowsHide:true,encoding:'utf8',timeout:10000});
 if(stopped.status!==0)throw Error('Preview shutdown command failed');
 console.log('Production preview passed: automatic local session, static assets, 3 book formats, private API, synthesis gate, no external requests, instance reuse, authenticated shutdown');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();child.kill()});
