const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
async function downloadText(download){const chunks=[];for await(const chunk of await download.createReadStream())chunks.push(chunk);return Buffer.concat(chunks).toString('utf8')}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8766');
 await page.getByLabel('Пароль',{exact:true}).fill('local-test-password-only');
 await page.getByRole('button',{name:'Войти',exact:true}).click();
 await page.getByRole('button',{name:'Создать ключи'}).waitFor();
 await page.getByLabel('Количество',{exact:true}).fill('2');
 await page.getByRole('button',{name:'Создать ключи'}).click();
 await page.getByLabel('Созданные ключи').waitFor();
 const keys=(await page.getByLabel('Созданные ключи').inputValue()).split('\n');
 if(keys.length!==2)throw Error('Batch not issued');
 let downloading=page.waitForEvent('download');
 await page.getByRole('button',{name:'Скачать новые ключи CSV',exact:true}).click();
 const keysCsv=await downloadText(await downloading);
 if(!keys.every(key=>keysCsv.includes(key)))throw Error('Keys CSV incomplete');
 const key=keys[0],prefix=key.slice(0,10)+'…';
 for(const machine of ['a','b']){
  const activation=await page.request.post('http://127.0.0.1:8766/v1/activate',{data:{key,machine_id:machine.repeat(64)}});
  if(!activation.ok())throw Error('Activation failed');
 }
 if((await page.request.post('http://127.0.0.1:8766/v1/activate',{data:{key,machine_id:'c'.repeat(64)}})).status()!==403)throw Error('Device limit not enforced');
 await page.getByRole('button',{name:'Обновить',exact:true}).click();
 const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:prefix,exact:true})});
 await row.getByRole('cell',{name:'2 / 2',exact:true}).waitFor();
 const before=await row.locator('td').nth(2).textContent();
 page.once('dialog',d=>d.accept('30'));
 await row.getByRole('button',{name:'Продлить',exact:true}).click();
 await page.waitForFunction(({prefix,before})=>Array.from(document.querySelectorAll('#rows tr')).find(tr=>tr.firstChild.textContent===prefix)?.children[2].textContent!==before,{prefix,before});
 await row.getByRole('button',{name:'Устройства',exact:true}).click();
 await page.getByText('b'.repeat(64),{exact:true}).waitFor();
 await page.screenshot({path:'artifacts/admin-devices.png'});
 page.once('dialog',d=>d.accept());
 await page.locator('.activation').filter({hasText:'b'.repeat(64)}).getByRole('button',{name:'Освободить активацию'}).click();
 await page.getByText('b'.repeat(64),{exact:true}).waitFor({state:'detached'});
 await page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await row.getByRole('cell',{name:'1 / 2',exact:true}).waitFor();
 if((await page.request.post('http://127.0.0.1:8766/v1/check',{data:{key,machine_id:'b'.repeat(64)}})).status()!==403)throw Error('Detached device still authorized');
 page.once('dialog',d=>d.accept());
 await row.getByRole('button',{name:'Отозвать',exact:true}).click();
 await row.getByRole('cell',{name:'Отозван',exact:true}).waitFor();
 if((await page.request.post('http://127.0.0.1:8766/v1/check',{data:{key,machine_id:'a'.repeat(64)}})).status()!==403)throw Error('Revoked key still authorized');
 downloading=page.waitForEvent('download');
 await page.getByRole('button',{name:'Экспорт списка CSV',exact:true}).click();
 const inventory=await downloadText(await downloading);
 if(inventory.includes(key)||!inventory.includes(key.slice(0,10)))throw Error('Inventory CSV contains full keys or missing row');
 await page.getByText('Журнал последних событий',{exact:true}).click();
 await page.screenshot({path:'artifacts/admin.png',fullPage:true});
 await page.emulateMedia({colorScheme:'dark'});await page.screenshot({path:'artifacts/admin-dark.png',fullPage:true});
 await page.getByRole('button',{name:'Выйти',exact:true}).click();
 await page.getByRole('heading',{name:'Вход администратора'}).waitFor();
 if(await page.getByLabel('Созданные ключи').inputValue())throw Error('Logout did not clear keys');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Admin UI passed: login, batch of 2 keys, keys/inventory CSV, 2-device limit, renewal, detach, revoke, audit, dark theme, logout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
