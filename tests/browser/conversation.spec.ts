import {test,expect} from '@playwright/test';

test('one conversation, any agent: selection, switching, team mode, rewind, snooze, project settings',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('favicon'))errors.push('console: '+m.text())});
 const request=page.request;
 await request.post('/api/auth/setup',{data:{username:'desktop-tester',password:'desktop-test-password-2026'}});
 const t=(await (await request.post('/api/tenants',{data:{name:'Desktop test workspace'}})).json()).id;
 // Two agents to switch between, and one API-key model that must never be offered as an agent.
 await request.post(`/api/t/${t}/models`,{data:{name:'Claude',provider:'claude_cli',model_name:'sonnet'}});
 await request.post(`/api/t/${t}/models`,{data:{name:'Codex',provider:'codex_cli',model_name:'gpt-5.6-sol'}});
 await request.post(`/api/t/${t}/models`,{data:{name:'Keyed',provider:'custom_openai',model_name:'test-model',base_url:'http://127.0.0.1:9999/v1',api_key:'k'}});

 await page.goto('/');
 await page.getByRole('button',{name:'Create your first project',exact:true}).click();
 await expect(page.getByLabel('Or clone a repository',{exact:true})).toBeVisible();
 await page.getByLabel('Project name',{exact:true}).fill('Authentication project');
 await page.getByRole('button',{name:'Create project',exact:true}).click();
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // The agent selector offers agents only: an API key reaches a model, not an agent. Adaptive is the default.
 const agent=page.getByLabel('Agent',{exact:true});
 await expect(agent).toBeVisible();
 expect(await agent.locator('option').allTextContents()).toEqual(['Adaptive','Claude','Codex']);
 expect(await agent.locator('optgroup').allInnerTexts()).toHaveLength(2);
 await expect(agent).toHaveValue('adaptive');
 await expect(page.locator('.composer-footnote')).toContainText('least expensive agent');

 // What the agent may do is chosen per turn and defaults to editing files, not running commands.
 const mode=page.getByLabel('What the agent may do',{exact:true});
 await expect(mode).toHaveValue('edit');
 await agent.selectOption({label:'Claude'});
 await mode.selectOption('read');
 await expect(page.locator('.composer-footnote')).toContainText('cannot change anything');
 await mode.selectOption('edit');

 // Team mode: Adaptive cannot lead; a chosen agent can, and the footnote says what it will do.
 const team=page.getByRole('button',{name:'Toggle team mode',exact:true});
 await agent.selectOption('adaptive');
 await team.click();
 await expect(page.locator('.composer-error')).toContainText('Adaptive cannot lead');
 await page.getByRole('button',{name:'Dismiss error',exact:true}).click();
 await agent.selectOption({label:'Claude'});
 await team.click();
 await expect(team).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.composer-footnote')).toContainText('Claude will lead');
 await team.click();
 await expect(team).toHaveAttribute('aria-pressed','false');

 // The composer draft survives a reload, and the conversation is empty until a turn is sent.
 await page.getByLabel('Ask Frontier',{exact:true}).fill('Explain this project.');
 await page.reload();
 await expect(page.getByLabel('Ask Frontier',{exact:true})).toHaveValue('Explain this project.');
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // One turn with the scripted agent, then Edit from here rewinds the conversation into the composer.
 await page.getByLabel('Agent',{exact:true}).selectOption({label:'Claude'});
 await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.agent-turn')).toContainText('Done.',{timeout:15000});
 await page.locator('.user-message').hover();
 await page.getByRole('button',{name:'Edit from here',exact:true}).click();
 await page.getByRole('button',{name:'Confirm',exact:true}).click();
 await expect(page.getByLabel('Ask Frontier',{exact:true})).toHaveValue('Explain this project.');
 await expect(page.getByRole('heading',{name:'What are we working on?'})).toBeVisible();

 // Another turn, then the session menu: snooze hides the session under its own list.
 await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.agent-turn')).toContainText('Done.',{timeout:15000});
 // Selecting text in a reply offers to cite it, and the citation becomes a chip under the composer.
 await page.locator('.agent-turn .inline-build').first().evaluate(el=>{const r=document.createRange();r.selectNodeContents(el);const s=window.getSelection()!;s.removeAllRanges();s.addRange(r)});
 await page.locator('.conversation-thread').dispatchEvent('mouseup');
 await page.getByRole('button',{name:'Cite in composer',exact:true}).click();
 await expect(page.locator('.context-chips')).toContainText('reply from Claude');
 await page.locator('.context-chips button').first().click();
 const row=page.locator('.session-list>button').first();
 await row.click({button:'right'});
 const menu=page.getByRole('menu');
 await expect(menu.getByRole('menuitem',{name:'Remember this session'})).toBeVisible();
 await menu.getByRole('menuitem',{name:'Snooze 1 hour'}).click();
 await expect(page.locator('.archived-toggle',{hasText:'Snoozed (1)'})).toBeVisible();
 await page.locator('.archived-toggle',{hasText:'Snoozed (1)'}).click();
 await expect(page.locator('.session-list>button').first()).toBeVisible();

 // Project settings from the project's own menu: memory saved and read back.
 await page.locator('.project-list>button').first().click({button:'right'});
 await page.getByRole('menuitem',{name:'Project settings…'}).click();
 await page.getByLabel('Notes every agent is given',{exact:true}).fill('- Uses tabs.');
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await expect(page.locator('.toast')).toContainText('Project settings saved');
 await page.locator('.project-list>button').first().click({button:'right'});
 await page.getByRole('menuitem',{name:'Project settings…'}).click();
 await expect(page.getByLabel('Notes every agent is given',{exact:true})).toHaveValue('- Uses tabs.');
 await page.keyboard.press('Escape');

 // Settings: every page is there, the workspace rules save, and the tools table renders.
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 const tabs=await page.locator('.desktop-settings nav button').allTextContents();
 expect(tabs).toEqual(['General','Agents & Providers','MCP & Skills','Automations','Usage','Remote access','Workspaces','Security','Developer']);
 await page.getByLabel('Rules for every agent',{exact:true}).fill('Always write tests.');
 await page.getByRole('button',{name:'Save workspace settings',exact:true}).click();
 await expect.poll(async()=>(await (await request.get('/api/tenants')).json())[0].rules).toBe('Always write tests.');
 // Interface size and spellcheck are per-device preferences applied at once.
 await page.getByLabel('Interface size',{exact:true}).selectOption('110');
 await expect.poll(async()=>page.evaluate(()=>(document.body.style as CSSStyleDeclaration&{zoom:string}).zoom)).toBe('110%');
 await page.getByLabel('Interface size',{exact:true}).selectOption('100');
 // The MCP catalog fills the add-server form.
 await page.locator('.desktop-settings nav button',{hasText:'MCP & Skills'}).click();
 await page.getByRole('button',{name:'Catalog',exact:true}).click();
 await page.locator('.mcp-catalog>button',{hasText:'Playwright browser'}).click();
 await expect(page.getByRole('dialog').getByLabel('Command',{exact:true})).toHaveValue('npx');
 await page.getByRole('dialog').getByRole('button',{name:'Save server',exact:true}).click();
 await expect(page.locator('.mcp-card')).toContainText('playwright');
 await page.locator('.desktop-settings nav button',{hasText:'Agents & Providers'}).click();
 await expect(page.getByRole('heading',{name:'Agents & providers'})).toBeVisible();
 await expect(page.locator('.tool-versions, .error-text').first()).toBeVisible({timeout:20000});
 expect(errors).toEqual([]);
});
