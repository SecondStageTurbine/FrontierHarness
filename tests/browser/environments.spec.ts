import {test,expect,request as http} from '@playwright/test';
import {spawn,type ChildProcess} from 'node:child_process';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

// A second Frontier, standing in for another machine, on its own port and data folder.
const OTHER='http://127.0.0.1:8011';
let other:ChildProcess;
test.beforeAll(async()=>{
 other=spawn('.venv\\Scripts\\python',['-m','uvicorn','tests.browser_server:app','--host','127.0.0.1','--port','8011'],{env:{...process.env,HARNESS_TEST_DATA_DIR:mkdtempSync(join(tmpdir(),'frontier-other-'))},stdio:'ignore'});
 for(let i=0;i<100;i++){try{const r=await fetch(`${OTHER}/api/health`);if(r.ok)return}catch{/* starting */}await new Promise(r=>setTimeout(r,200))}
 throw new Error('The other Frontier did not start.');
});
test.afterAll(()=>{other?.kill()});

test('another machine is paired from Settings, and its project runs from this window',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon'))errors.push('console: '+m.text())});
 // The other machine: a user, a workspace, an agent and a project of its own.
 const there=await http.newContext({baseURL:OTHER});
 await there.post('/api/auth/setup',{data:{username:'studio-owner',password:'studio-password-2026'}});
 const t=(await (await there.post('/api/tenants',{data:{name:'Studio workspace'}})).json()).id;
 await there.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 await there.post(`/api/t/${t}/projects`,{data:{name:'Render farm'}});
 const code=(await (await there.post('/api/remote/pair')).json()).token;

 // This machine pairs it with the link, then switches to it.
 expect((await page.request.post('/api/auth/login',{data:{username:'desktop-tester',password:'desktop-test-password-2026'}})).ok()).toBeTruthy();
 await page.goto('/');
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.locator('.desktop-settings nav button',{hasText:'Environments'}).click();
 await page.getByLabel('Name',{exact:true}).fill('Studio');
 await page.getByLabel('Pairing link',{exact:true}).fill(`${OTHER}/pair?code=${code}`);
 await page.getByRole('button',{name:'Pair',exact:true}).click();
 const row=page.locator('.env-row',{hasText:'Studio'});
 await expect(row).toContainText('ready');
 await row.getByRole('button',{name:'Switch to it'}).click();

 // Everything now comes from the other machine: its workspace, its project, its agent's reply, streamed live.
 await expect(page.locator('.env-banner')).toContainText('Studio');
 await expect(page.locator('.project-list')).toContainText('Render farm');
 await page.locator('.project-list>button',{hasText:'Render farm'}).click();
 await page.getByLabel('Agent',{exact:true}).selectOption({label:'Claude'});
 await page.getByLabel('Ask Frontier',{exact:true}).fill('Build it over there.');
 await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.agent-turn').last()).toContainText('Done.',{timeout:15000});
 // The conversation lives on the other machine.
 const [project]=await (await there.get(`/api/t/${t}/projects`)).json();
 const [session]=await (await there.get(`/api/t/${t}/projects/${project.id}/sessions`)).json();
 const full=await (await there.get(`/api/t/${t}/projects/${project.id}/sessions/${session.id}`)).json();
 expect(full.messages.map((m:{content:string})=>m.content)).toEqual(['Build it over there.','Done.']);
 await page.screenshot({path:'test-results/environment.png'});
 await page.getByRole('button',{name:'Back to this computer'}).click();
 await expect(page.locator('.env-banner')).toHaveCount(0);
 await expect(page.locator('.project-list')).not.toContainText('Render farm');
 expect(errors).toEqual([]);
});
