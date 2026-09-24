import {useState} from 'react';
import {ArrowRight,Sparkles} from 'lucide-react';

/** Curated starting points, by the kind of work. Picking one fills the composer to edit before sending. */
export const STARTERS:{category:string;prompts:{title:string;text:string}[]}[]=[
 {category:'Build',prompts:[
  {title:'Add a feature with tests',text:'Add <feature>. Follow the conventions already in this project, add tests for the new behaviour, run them, and tell me how to try it.'},
  {title:'Add an API endpoint',text:'Add an endpoint that <does what>, with input validation, error handling that matches the rest of the API, and tests. Run the tests.'},
  {title:'Build a UI screen',text:'Build a screen for <purpose> using the components and styles this project already has. Check it in a browser at desktop and phone widths and fix anything that looks wrong.'},
  {title:'Prototype an idea',text:'Build a quick working prototype of <idea> in a new folder, with the smallest set of dependencies, and a one-line command to run it.'}]},
 {category:'Fix',prompts:[
  {title:'Fix a bug',text:'This is wrong: <what happens>. It should <what should happen>. Reproduce it, find the root cause, fix it, and add a test that would have caught it.'},
  {title:'Make the tests pass',text:'Run the test suite. For each failure, find out whether the test or the code is wrong and fix the right one. Do not weaken assertions to make them pass.'},
  {title:'Fix a build error',text:'The build fails. Run it, read the error, fix the cause, and run it again until it succeeds.'},
  {title:'Speed something up',text:'<Thing> is slow. Measure it first, find where the time goes, fix the biggest cause, and measure again to show the difference.'}]},
 {category:'Review',prompts:[
  {title:'Review my changes',text:'Review the uncommitted changes for bugs, security problems and missing tests. List findings most severe first, with file and line, and do not change anything yet.'},
  {title:'Security audit',text:'Audit this project for security problems: committed secrets, injection, missing authorisation, unsafe dependencies. Report each with its location and the fix.'},
  {title:'Find dead code',text:'Find code in this project that nothing uses any more, confirm each is really unused, and list it. Do not delete anything yet.'}]},
 {category:'Explore',prompts:[
  {title:'Explain this project',text:'Read this project and explain its architecture: entry points, main components, how a request flows through it, and where to make the most common changes.'},
  {title:'How does this work?',text:'Explain how <feature> works in this project, end to end, citing the files and functions involved.'},
  {title:'Plan a change',text:'I want to <goal>. Read the relevant code and write a step-by-step plan: files to change, risks, and how to test it. Do not change anything yet.'}]},
 {category:'Tests',prompts:[
  {title:'Add missing tests',text:'Find the most important behaviour in this project that has no tests, add tests for it, and run them.'},
  {title:'Set up testing',text:'This project has no automated tests. Add the standard test setup for its language and framework, one meaningful test, and a command to run them.'}]},
 {category:'Docs & Git',prompts:[
  {title:'Update the README',text:'Update the README so it matches what the project does now: what it is, how to install and run it, and one working example.'},
  {title:'Write a commit message',text:'Read the staged changes and write a clear commit message for them. Do not commit.'},
  {title:'Release notes',text:'Write release notes for the changes since the last tag, grouped into features, fixes and anything users must do to upgrade.'}]},
];

export function StarterPrompts({onPick,onSkills}:{onPick:(text:string)=>void;onSkills?:()=>void}){
 const [category,setCategory]=useState(STARTERS[0].category);
 const current=STARTERS.find(s=>s.category===category)!;
 return <div className="starter-prompts">
  <div className="starter-tabs" role="tablist" aria-label="Starter prompts">{STARTERS.map(s=><button key={s.category} role="tab" aria-selected={s.category===category} className={s.category===category?'selected':''} onClick={()=>setCategory(s.category)}>{s.category}</button>)}
   {onSkills&&<button className="starter-skills" onClick={onSkills}><Sparkles size={12}/>Skills catalog</button>}</div>
  <div className="suggestion-grid">{current.prompts.map(p=><button key={p.title} title={p.text} onClick={()=>onPick(p.text)}><Sparkles size={15}/><strong>{p.title}</strong><ArrowRight size={13}/></button>)}</div>
 </div>;
}
