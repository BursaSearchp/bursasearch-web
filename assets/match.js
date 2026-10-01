(function(){
var box=document.getElementById('mchk');if(!box)return;
var go=box.getAttribute('data-go')||'seo_site',pre=box.getAttribute('data-c')||'',title=box.getAttribute('data-t')||'Which could you get?';
var D=null,wait=[],busy=0;
function load(cb){if(D){cb();return}wait.push(cb);if(busy)return;busy=1;
 fetch('/assets/match.json').then(function(r){return r.json()}).then(function(j){D=j;var w=wait;wait=[];w.forEach(function(f){f()})})
 .catch(function(){busy=0})}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function nm(s){return s.toLowerCase().replace(/[^a-z0-9 ]/g,'')}
var Q=[{k:'u'},
 {k:'l',q:'What will you study?',o:[['u','Undergraduate degree'],['p',"Master's or PhD"]]},
 {k:'i',q:'What is your household income?',o:[['0','Under £25,000'],['25000','£25,000 to £43,000'],['43000','Over £43,000'],['-1','Not sure']]},
 {k:'c',q:'Do any of these apply to you?',o:[['c','Care-experienced'],['e','Estranged from my family'],['r',"I'm a carer"],['d','Disabled or long-term condition'],['f','Refugee or asylum seeker'],['n','None of these']]}];
if(pre)Q.pop();
var A={},U=null,step=0,bar=document.getElementById('ctatext'),barOrig=bar?bar.textContent:'';
function head(){var h='<div class="hd"><b>'+esc(title)+'</b><span>'+(step+1)+' of '+Q.length+'</span></div><div class="prog">';
 for(var i=0;i<Q.length;i++)h+='<i'+(i<=step?' class="on"':'')+'></i>';return h+'</div>'}
function ask(){var q=Q[step],h=head();
 if(q.k==='u')h+='<label class="qq" for="mq">Where are you studying, or hoping to?</label><input id="mq" type="search" autocomplete="off" placeholder="Type your university"><ul class="fres" id="mr"></ul>'
  +'<button type="button" class="opt" data-v="none">Not decided yet</button><p class="fine">Quick web estimate for UK students. The app is more accurate.</p>';
 else{h+='<p class="qq">'+esc(q.q)+'</p>';q.o.forEach(function(o){h+='<button type="button" class="opt" data-v="'+esc(o[0])+'">'+esc(o[1])+'</button>'});
  h+='<button type="button" class="back">Back</button>'}
 box.className='chk mchk';box.innerHTML=h;if(q.k==='u')bind()}
function bind(){var inp=document.getElementById('mq');if(!inp)return;
 inp.addEventListener('focus',function(){load(function(){})});
 inp.addEventListener('input',suggest);
 inp.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();var b=box.querySelector('#mr .pick');if(b)b.click()}})}
function suggest(){var inp=document.getElementById('mq'),o=document.getElementById('mr');if(!inp)return;var q=nm(inp.value).trim();
 if(q.length<2){o.innerHTML='';return}
 load(function(){if(nm(inp.value).trim()!==q)return;var m=[];
  D.u.forEach(function(x,i){if((' '+nm(x[0]+' '+x[2])).indexOf(' '+q)>=0)m.push(i)});
  m.sort(function(a,b){return D.u[a][0].length-D.u[b][0].length});m=m.slice(0,6);
  o.innerHTML=m.length?m.map(function(i){return '<li><button type="button" class="pick" data-i="'+i+'">'+esc(D.u[i][0])+'</button></li>'}).join('')
   :'<li class="nil">Not in our list yet. Choose “Not decided yet” to see national grants.</li>'})}
function next(){step++;if(step<Q.length)ask();else load(result)}
box.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
 if(t.classList.contains('pick')){U=D.u[+t.getAttribute('data-i')];next()}
 else if(t.classList.contains('opt')){if(Q[step].k==='u')U=0;else A[Q[step].k]=t.getAttribute('data-v');next()}
 else if(t.classList.contains('back')){step=Math.max(0,step-1);ask()}
 else if(t.classList.contains('again')){A={};U=null;step=0;if(bar)bar.textContent=barOrig;ask()}});
function href(){return box.getAttribute('data-href')||'/go/'+go+'/'}
function result(){var c=pre||A.c,here=0,top=0,nat=0;
 function fits(r){if(r[0]!=='a'&&r[0]!==A.l)return false;var lo=+A.i;if(r[1]>0&&lo>=0&&lo>=r[1])return false;if(r[2]&&r[2].indexOf(c)<0)return false;return true}
 if(U)U[4].forEach(function(r){if(fits(r)&&!r[3]){here++;if(r[4]>top)top=r[4]}});
 D.n.forEach(function(r){if(fits(r))nat++});
 var s=U?U[3]:'',h='<div class="nums">';
 if(U&&here)h+='<div><b>'+here+'</b><span>'+esc(s)+' funds you may get</span></div>';
 h+='<div><b>'+(U&&here?'+':'')+nat+'</b><span>national &amp; charity grants worth checking</span></div></div>';
 if(U&&!here)h+='<p class="top">None of '+esc(s)+'’s own funds match these answers, but these grants might.</p>';
 if(top>=100)h+='<p class="top">Biggest '+esc(s)+' award you may get: <b>£'+top.toLocaleString('en-GB')+'</b></p>';
 h+='<a class="wbtn" href="'+esc(href())+'">Get accurate matches in the app</a>';
 if(U&&location.pathname!=='/bursaries/'+U[1]+'/')h+='<a class="alt" href="/bursaries/'+U[1]+'/">See every '+esc(s)+' fund</a>';
 h+='<p class="small">'+(U?'':'University funds depend on where you study. The app checks all 144. ')+'A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p>'
  +'<button type="button" class="again">Change answers</button>';
 box.className='res';box.innerHTML=h;
 if(bar)bar.textContent=(U?here+' '+s+' funds + ':'')+nat+' national grants to check';
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
bind();
})();
