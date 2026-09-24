import {test,expect,devices} from '@playwright/test';

// A phone reaching Frontier through remote access: the layout, pairing by QR code, and notifications.
test.use({...devices['iPhone 13'],browserName:'chromium',channel:'chrome'});

test('a phone gets a one-column layout, pairs by QR code, and opens a conversation from a notification link',async({page,browser})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon'))errors.push('console: '+m.text())});
 expect((await page.request.post('/api/auth/login',{data:{username:'desktop-tester',password:'desktop-test-password-2026'}})).ok()).toBeTruthy();
 const t=(await (await page.request.get('/api/tenants')).json())[0].id;
 const project=(await (await page.request.get(`/api/t/${t}/projects`)).json())[0];
 const session=(await (await page.request.post(`/api/t/${t}/projects/${project.id}/sessions`,{data:{name:'From the phone'}})).json());
 const noOverflow=()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1);

 // A notification's link opens its conversation, and the address bar is left clean.
 await page.goto(`/?t=${t}&p=${project.id}&s=${session.id}`);
 await expect(page.locator('.session-header')).toContainText('From the phone');
 expect(new URL(page.url()).search).toBe('');
 await expect(page.locator('.project-sidebar')).toHaveCount(0);  // Closed by default on a phone.
 expect(await noOverflow()).toBeTruthy();

 // The composer fits and sends.
 await page.getByLabel('Agent',{exact:true}).selectOption({label:'Claude'});
 await page.getByLabel('Ask Frontier',{exact:true}).fill('Hello from the phone.');
 await page.getByRole('button',{name:'Send',exact:true}).tap();
 await expect(page.locator('.agent-turn').last()).toContainText('Done.',{timeout:15000});
 expect(await noOverflow()).toBeTruthy();
 await page.screenshot({path:'test-results/phone-conversation.png'});

 // Projects and sessions in a drawer, which closes once a session is picked.
 await page.getByRole('button',{name:'Show project sidebar'}).tap();
 await expect(page.locator('.project-sidebar')).toBeVisible();
 await page.screenshot({path:'test-results/phone-drawer.png'});
 await page.locator('.session-list>button').first().tap();
 await expect(page.locator('.project-sidebar')).toHaveCount(0);

 // A panel takes the whole screen.
 await page.getByRole('button',{name:'Open changes',exact:true}).tap();
 await expect(page.locator('.context-inspector')).toBeVisible();
 const box=await page.locator('.context-inspector').boundingBox();
 expect(Math.round(box!.width)).toBe(page.viewportSize()!.width);
 await page.getByRole('button',{name:'Close contextual panel'}).tap();

 // Settings → Remote access: pairing code and push notifications. Remote access is shown as listening.
 await page.route('**/api/remote',r=>r.request().method()==='GET'?r.fulfill({json:{enabled:true,listening:true,addresses:['192.168.1.20'],port:8001,has_password:true,username:'desktop-tester'}}):r.continue());
 await page.getByRole('button',{name:'Show project sidebar'}).tap();
 await page.getByRole('button',{name:'Settings',exact:true}).tap();
 await page.locator('.desktop-settings nav button',{hasText:'Remote access'}).tap();
 await page.getByRole('button',{name:'Show pairing code'}).tap();
 await expect(page.getByRole('img',{name:'Pairing code for your phone'})).toBeVisible();
 await page.getByRole('switch',{name:/Send push notifications/}).click();
 await expect(page.getByRole('img',{name:'Topic to subscribe to in ntfy'})).toBeVisible();
 await expect(page.locator('.push-topic')).toContainText('https://ntfy.sh/frontier-');
 expect(await noOverflow()).toBeTruthy();
 await page.screenshot({path:'test-results/phone-pairing.png',fullPage:true});
 await page.request.put('/api/push',{data:{enabled:false}});

 // The pairing link signs a fresh phone in, once.
 const code=(await (await page.request.post('/api/remote/pair')).json()).token;
 const phone=await browser.newContext({...devices['iPhone 13']});
 const fresh=await phone.newPage();
 await fresh.goto(`/pair?code=${code}`);
 await expect(fresh.locator('.session-header, .workspace-empty').first()).toBeVisible({timeout:15000});
 const reuse=await (await browser.newContext()).newPage();
 await reuse.goto(`/pair?code=${code}`);
 await expect(reuse.locator('body')).toContainText('expired or was already used');
 expect(errors).toEqual([]);
});
