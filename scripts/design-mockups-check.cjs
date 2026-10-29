const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/korna/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path=require('path');
const fs=require('fs');
(async()=>{
 const output=path.resolve(__dirname,'../artifacts/design-studio-v2');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>{errors.push(String(e));console.error(e)});
 const report=[];
 for(const variant of ['a','b','c'])for(const screen of ['home','studio']){
  await page.goto(`http://127.0.0.1:8310/?variant=${variant}&screen=${screen}`);
  await page.locator('#app').screenshot({path:path.join(output,`${variant}-${screen}.png`)});
  report.push({variant,screen,...await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,actions:[...document.querySelectorAll('.primary')].map(e=>({label:e.textContent,bottom:e.getBoundingClientRect().bottom}))}))});
 }
 await page.getByRole('button',{name:'Слушать пробу',exact:true}).click();
 await page.getByRole('dialog').waitFor();
 await page.locator('audio').evaluate(a=>a.play());
 await page.waitForFunction(()=>document.querySelector('audio').currentTime>0);
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Сохранить MP3',exact:true}).click();
 await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Светлая тема',exact:true}).click();
 await page.screenshot({path:path.join(output,'c-studio-light.png'),fullPage:true});
 await page.setViewportSize({width:640,height:1000});
 for(const variant of ['a','b','c']){
  await page.goto(`http://127.0.0.1:8310/?variant=${variant}&screen=studio`);
  const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  if(!fits)throw Error('Overflow at 640px '+variant);
 }
 await page.goto('http://127.0.0.1:8310/?variant=a');
 await page.getByRole('textbox',{name:'Найти книгу'}).fill('несуществующая');
 await page.getByText('Книга не найдена. Попробуйте другое название.').waitFor();
 if(errors.length)throw Error(errors.join('\n'));
 fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({screens:report,errors,audioPlayback:true,dialogs:true,search:true,responsive640:true},null,2));
 await browser.close();console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exit(1)});
