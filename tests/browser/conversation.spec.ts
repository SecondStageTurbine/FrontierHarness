import {test,expect} from '@playwright/test';

test('one conversation, any agent: selection, switching and persistence',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const request=page.request;
 await request.post('/api/auth/setup',{data:{username:'desktop-tester',password:'desktop-test-password-2026'}});
 const t=(await (await request.post('/api/tenants',{data:{name:'Desktop test workspace'}})).json()).id;
 // Two agents to switch between, and one API-key model that must never be offered as an agent.
 await request.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 await request.post(`/api/t/${t}/models`,{data:{name:'Codex',provider:'codex_cli',model_name:'gpt-5.6-sol'}});
 await request.post(`/api/t/${t}/models`,{data:{name:'Keyed',provider:'custom_openai',model_name:'test-model',base_url:'http://127.0.0.1:9999/v1',api_key:'k'}});

 await page.goto('/');
 await page.getByRole('button',{name:'Create your first project',exact:true}).click();
 await page.getByLabel('Project name',{exact:true}).fill('Authentication project');
 await page.getByRole('button',{name:'Create project',exact:true}).click();
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // The agent selector offers agents only: an API key reaches a model, not an agent.
 const agent=page.getByLabel('Agent',{exact:true});
 await expect(agent).toBeVisible();
 const options=await agent.locator('option').allTextContents();
 // Adaptive is a selection like any other, offered first; the agents follow, grouped by
 // provider so a model named after its provider is not printed twice.
 expect(options).toEqual(['Adaptive','Claude','Codex']);
 expect(await agent.locator('optgroup').allInnerTexts()).toHaveLength(2);

 // What the agent may do is chosen per turn and defaults to editing files, not running commands.
 const mode=page.getByLabel('What the agent may do',{exact:true});
 await expect(mode).toHaveValue('edit');
 await mode.selectOption('read');
 await expect(page.locator('.composer-footnote')).toContainText('cannot change anything');

 // With Adaptive selected the footnote says what it will do instead of what the agent may do.
 await agent.selectOption('adaptive');
 await expect(page.locator('.composer-footnote')).toContainText('least expensive agent');

 // Selecting a different agent says, before anything is sent, what the next turn costs.
 await agent.selectOption({label:'Codex'});
 await page.getByLabel('Ask Frontier',{exact:true}).fill('Explain this project.');
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeEnabled();

 // The composer draft survives a reload, and the conversation is empty until a turn is sent.
 await page.reload();
 await expect(page.getByLabel('Ask Frontier',{exact:true})).toHaveValue('Explain this project.');
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // Settings hold agents and workspaces; the workflow engine's pages are gone.
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 const tabs=await page.locator('.desktop-settings nav button').allTextContents();
 expect(tabs).toEqual(['General','Agents & Providers','Workspaces','Security','Developer']);
 await page.locator('.desktop-settings nav button',{hasText:'Agents & Providers'}).click();
 await expect(page.getByRole('heading',{name:'Agents & providers'})).toBeVisible();
 expect(errors).toEqual([]);
});
