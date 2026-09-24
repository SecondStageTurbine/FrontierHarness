/** Turning a VS Code colour theme into Frontier's colours. */
export type CustomTheme={name:string;dark:boolean;vars:Record<string,string>};
type VsTheme={name?:string;type?:string;colors?:Record<string,string>};

export const THEME_VARS=['--background','--foreground','--surface','--card','--elevated','--border','--muted','--subtle','--primary','--primary-bg','--primary-foreground','--success','--warning','--danger','--accent-surface','--grid'];

/** JSON with comments and trailing commas, as theme files are written. */
export function parseJsonc(text:string){
 const stripped=text.replace(/"(?:\\.|[^"\\])*"|\/\/[^\n]*|\/\*[\s\S]*?\*\//g,m=>m.startsWith('"')?m:'');
 return JSON.parse(stripped.replace(/,(\s*[}\]])/g,'$1'));
}

function rgb(hex:string){
 const h=hex.replace('#','');
 const full=h.length<=4?h.slice(0,3).split('').map(c=>c+c).join(''):h.slice(0,6);
 const n=parseInt(full,16);
 return Number.isNaN(n)||full.length!==6?null:[n>>16&255,n>>8&255,n&255];
}
const hex=(c:number[])=>'#'+c.map(v=>Math.round(Math.max(0,Math.min(255,v))).toString(16).padStart(2,'0')).join('');
/** Blend a toward b by t, both opaque hex colours. */
function blend(a:string,b:string,t:number){const x=rgb(a),y=rgb(b);return x&&y?hex(x.map((v,i)=>v+(y[i]-v)*t)):a}
const opaque=(c?:string)=>c&&rgb(c)?hex(rgb(c)!):undefined;
const luminance=(c:string)=>{const x=rgb(c);return x?(0.2126*x[0]+0.7152*x[1]+0.0722*x[2])/255:0};

export function fromVsCode(theme:VsTheme,fallbackName='Imported theme'):CustomTheme{
 const c=theme.colors||{};
 const pick=(...keys:string[])=>keys.map(k=>c[k]).find(v=>typeof v==='string'&&v.startsWith('#'));
 const background=opaque(pick('editor.background','sideBar.background'))||'#1e1e1e';
 const dark=theme.type?!/light/i.test(theme.type):luminance(background)<0.5;
 const foreground=opaque(pick('editor.foreground','foreground'))||(dark?'#d4d4d4':'#1f1f1f');
 const lift=(t:number)=>blend(background,dark?'#ffffff':'#000000',t);
 const primary=opaque(pick('textLink.foreground','focusBorder','button.background','activityBarBadge.background'))||(dark?'#8885ff':'#6054cf');
 const vars:Record<string,string>={
  '--background':background,
  '--foreground':foreground,
  '--surface':opaque(pick('sideBar.background','editorGroupHeader.tabsBackground','panel.background'))||lift(.03),
  '--card':opaque(pick('editorWidget.background','notifications.background','dropdown.background'))||lift(.05),
  '--elevated':opaque(pick('input.background','dropdown.background','list.hoverBackground'))||lift(.09),
  '--border':opaque(pick('panel.border','editorGroup.border','sideBar.border','widget.border','input.border'))||lift(.14),
  '--muted':opaque(pick('descriptionForeground','tab.inactiveForeground','sideBarTitle.foreground'))||blend(foreground,background,.3),
  '--subtle':opaque(pick('editorLineNumber.foreground','disabledForeground'))||blend(foreground,background,.5),
  '--primary':primary,
  '--primary-bg':opaque(pick('button.background','activityBarBadge.background'))||primary,
  '--primary-foreground':opaque(pick('button.foreground','activityBarBadge.foreground'))||'#ffffff',
  '--success':opaque(pick('terminal.ansiGreen','gitDecoration.addedResourceForeground'))||(dark?'#71d6ad':'#187a58'),
  '--warning':opaque(pick('terminal.ansiYellow','editorWarning.foreground'))||(dark?'#e8b86d':'#936018'),
  '--danger':opaque(pick('terminal.ansiRed','editorError.foreground','errorForeground'))||(dark?'#ef9399':'#b53c49'),
  '--accent-surface':blend(background,primary,dark?.18:.1),
  '--grid':lift(.1),
 };
 return {name:theme.name||fallbackName,dark,vars};
}
