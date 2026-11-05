import {test,expect} from '@playwright/test';

test('typing a person in Listen to Slack turns on their messages, and a refusal is shown',async({page})=>{
 const request=page.request;
 const owner={username:'desktop-tester',password:'desktop-test-password-2026'};
 await request.post('/api/auth/setup',{data:owner});
 expect((await request.post('/api/auth/login',{data:owner})).ok()).toBeTruthy();
 const t=(await (await request.get('/api/tenants')).json())[0]?.id??(await (await request.post('/api/tenants',{data:{name:'Slack workspace'}})).json()).id;
 await request.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 const a=await (await request.post(`/api/t/${t}/projects`,{data:{name:'Dmtest'}})).json();
 await request.post(`/api/t/${t}/projects/${a.id}/sessions`,{data:{name:'Dm work'}});
 await page.goto('/');
 await page.locator('.project-row',{hasText:'Dmtest'}).locator('.project-main').click();
 await page.locator('.session-list>button',{hasText:'Dm work'}).click({button:'right'});
 await page.getByRole('menuitem',{name:'Listen to Slack…'}).click();
 const person=page.getByLabel('Channel or person');
 const anyone=page.getByRole('switch');
 await expect(anyone).not.toBeChecked();
 await person.fill('@Nelson');
 await expect(anyone).toBeChecked();
 await anyone.uncheck();
 await person.fill('@Nelsonn');
 await expect(anyone).not.toBeChecked();
 await page.screenshot({path:'test-results/slack-dm-modal.png'});
 await page.getByRole('button',{name:'Listen',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Slack MCP server');
});
