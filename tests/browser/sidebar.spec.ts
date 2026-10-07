import {test,expect} from '@playwright/test';

test('a project row folds its sessions open underneath and starts a new session in place',async({page})=>{
 const request=page.request;
 // The browser server is shared across specs: set up the owner if this runs first, else sign in as it.
 const owner={username:'desktop-tester',password:'desktop-test-password-2026'};
 await request.post('/api/auth/setup',{data:owner});
 expect((await request.post('/api/auth/login',{data:owner})).ok()).toBeTruthy();
 const t=(await (await request.get('/api/tenants')).json())[0]?.id??(await (await request.post('/api/tenants',{data:{name:'Sidebar workspace'}})).json()).id;
 await request.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 const a=await (await request.post(`/api/t/${t}/projects`,{data:{name:'Alpha'}})).json();
 const b=await (await request.post(`/api/t/${t}/projects`,{data:{name:'Beta'}})).json();
 await request.post(`/api/t/${t}/projects/${a.id}/sessions`,{data:{name:'Alpha work'}});
 await request.post(`/api/t/${t}/projects/${b.id}/sessions`,{data:{name:'Beta work'}});
 await page.goto('/');
 const row=(name:string)=>page.locator('.project-row',{hasText:name});
 const sessions=page.locator('.project-sessions .session-list>button');

 // Opening a project's caret shows its sessions directly under that project.
 await row('Alpha').locator('.project-main').click();
 await expect(row('Alpha').getByRole('button',{name:'Hide sessions'})).toBeVisible();
 await expect(page.locator('.project-row:has-text("Alpha") + .project-sessions')).toContainText('Alpha work');
 await row('Alpha').getByRole('button',{name:'Hide sessions'}).click();
 await expect(sessions).toHaveCount(0);

 // Another project's caret switches to it and opens its sessions.
 await row('Beta').getByRole('button',{name:'Show sessions'}).click();
 await expect(page.locator('.project-row:has-text("Beta") + .project-sessions')).toContainText('Beta work');
 await page.locator('.project-sidebar').screenshot({path:'test-results/sidebar.png'});

 // The row's plus starts a new session in that project.
 await row('Alpha').getByRole('button',{name:'New session in Alpha'}).click();
 await expect(row('Alpha')).toHaveClass(/active/);
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // Right-click → Pin to top keeps a project first in the list; Unpin puts it back.
 const last=(await page.locator('.project-list .project-main > span:not(.sidebar-status)').allTextContents()).at(-1)!;
 await row(last).locator('.project-main').click({button:'right'});
 await page.getByRole('menuitem',{name:'Pin to top'}).click();
 await expect(page.locator('.project-list .project-row').first()).toContainText(last);
 await expect(row(last).getByLabel('Pinned')).toBeVisible();
 await row(last).locator('.project-main').click({button:'right'});
 await page.getByRole('menuitem',{name:'Unpin'}).click();
 await expect(row(last).getByLabel('Pinned')).toHaveCount(0);
});
