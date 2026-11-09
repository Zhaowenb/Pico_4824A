const guide=window.INTERFERENCE_GUIDE||[];
const manualThemeKey="waveguard-ui-theme";
function applyManualTheme(theme,persist=false){WaveGuardUI.appearance({theme},persist);}
const cards=document.getElementById("experimentCards");
const toc=document.getElementById("experimentToc");
guide.forEach((item,index)=>{
  const number=String(index+1).padStart(2,"0");
  const link=document.createElement("a");link.href=`#experiment-${number}`;link.textContent=`${number} ${item.title}`;toc.append(link);
  const card=document.createElement("article");card.className="experiment-card";card.id=`experiment-${number}`;
  const badge=document.createElement("span");badge.className="tag";badge.textContent=`实验 ${number}`;card.append(badge);
  const title=document.createElement("h3");title.textContent=item.title;card.append(title);
  const aim=document.createElement("p");aim.className="aim";aim.textContent=item.aim;card.append(aim);
  const makeList=(heading,items)=>{const h=document.createElement("h4");h.textContent=heading;card.append(h);const ol=document.createElement("ol");ol.className="steps";items.forEach(s=>{const li=document.createElement("li");li.textContent=s;ol.append(li)});card.append(ol)};
  makeList("接线与准备",item.before);makeList("采集与核对",item.during);
  for(const [label,value,kind] of [["计算与图","readout","readout"],["结果如何解释","interpret","interpret"],["注意事项","caution","caution"]]){const p=document.createElement("p");p.className=`guide-note ${kind}`;const strong=document.createElement("strong");strong.textContent=`${label}：`;p.append(strong,document.createTextNode(item[value]));card.append(p)}
  const action=document.createElement("a");action.className="button jump";action.href=`/interference#exp-${index+1}`;action.textContent=`打开采集页 · 实验 ${number}`;card.append(action);
  cards.append(card);
});
document.querySelectorAll("[data-ui-theme-choice]").forEach(button=>button.addEventListener("click",()=>applyManualTheme(button.dataset.uiThemeChoice,true)));
applyManualTheme(localStorage.getItem(manualThemeKey)||"light");

WaveGuardUI.mountShell({shell:document.querySelector(".layout"),controls:document.querySelector(".toc"),main:document.querySelector(".layout>main"),intro:document.querySelector(".manual-intro"),documentMode:true});WaveGuardUI.activate("interference");
