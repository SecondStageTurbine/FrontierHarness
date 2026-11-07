import {test,expect} from '@playwright/test';

test('a web link in an agent reply asks for a new window, so the desktop app sends it to the browser',async({page})=>{
 const request=page.request;
 const owner={username:'desktop-tester',password:'desktop-test-password-2026'};
 await request.post('/api/auth/setup',{data:owner});
 expect((await request.post('/api/auth/login',{data:owner})).ok()).toBeTruthy();
 const t=(await (await request.get('/api/tenants')).json())[0]?.id??(await (await request.post('/api/tenants',{data:{name:'Links workspace'}})).json()).id;
 await request.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 const p=await (await request.post(`/api/t/${t}/projects`,{data:{name:'Linktest'}})).json();
 await request.post(`/api/t/${t}/projects/${p.id}/sessions`,{data:{name:'Link work'}});
 await page.goto('/');
 await page.locator('.project-row',{hasText:'Linktest'}).locator('.project-main').click();
 await page.locator('.session-list>button',{hasText:'Link work'}).click();
 await page.getByLabel('Agent',{exact:true}).selectOption({label:'Claude'});
 await page.getByLabel('Ask Frontier',{exact:true}).fill('link me');
 await page.getByRole('button',{name:'Send',exact:true}).click();
 const web=page.locator('.agent-turn a',{hasText:'the docs'});
 await expect(web).toHaveAttribute('target','_blank',{timeout:15000});
 await expect(web).toHaveAttribute('href','https://example.com/docs');
 // A link inside the page is not a web address, and stays where it is.
 await expect(page.locator('.agent-turn a',{hasText:'this section'})).not.toHaveAttribute('target','_blank');
});
