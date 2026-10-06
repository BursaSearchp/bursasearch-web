(function(){
var NAT=[["u",0,"",1],["a",0,"",1],["p",25000,"",1],["u",25000,"",1],["u",0,"",1],["u",0,"",1],["a",25000,"",1],["u",0,"",1],["a",0,"",1],["u",0,"",1],["p",0,"",1],["p",0,"",1],["p",0,"",1],["p",0,"",1],["a",25000,"",1],["p",25000,"",1],["a",0,"",1],["u",0,"",1],["u",25000,"",0],["u",0,"d",1],["u",0,"",1],["p",0,"",1],["u",25000,"",0],["p",0,"",1],["a",25000,"",1],["a",0,"",1],["u",0,"",1],["u",25000,"",1],["u",0,"",1],["u",0,"",1],["u",25000,"",1],["u",0,"",1],["u",0,"",1],["u",0,"",1],["u",25000,"",1],["u",0,"",1],["u",0,"",1],["p",0,"",1],["p",0,"",1],["u",25000,"",1],["u",0,"",1],["a",0,"",1],["a",0,"",1],["u",0,"",1],["a",0,"",1],["a",25000,"",1],["a",25000,"",1],["p",25000,"",0],["u",90000,"",0],["p",0,"",1],["u",0,"c",0],["a",0,"",1],["p",0,"",1],["a",0,"",1],["u",25000,"",1],["p",0,"",0],["p",25000,"",0],["u",25000,"",1],["u",0,"",1],["u",0,"",1],["u",25000,"",1],["p",0,"",1],["u",25000,"",1],["p",0,"",1],["a",25000,"",1],["u",0,"",1],["u",0,"",1],["a",0,"f",1],["a",25000,"",1],["u",0,"",1],["u",0,"",1],["u",0,"",1],["u",0,"",1],["u",25000,"",1],["u",0,"",1],["a",25000,"",1],["u",0,"",1],["a",25000,"",1],["u",25000,"",1],["a",0,"f",0],["u",0,"",1],["u",0,"",1],["p",25000,"",0],["a",0,"",1],["p",0,"",1],["u",25000,"",1],["u",25000,"",1],["u",25000,"",0],["u",25000,"",1],["p",25000,"",1],["p",25000,"",1],["p",25000,"",1],["u",0,"c",0],["p",25000,"",1],["a",25000,"",1],["a",0,"",1],["a",25000,"",1],["a",25000,"",1],["a",0,"d",0],["p",0,"",1],["a",25000,"",0],["a",0,"",1],["u",0,"",1]];
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
var fb=document.getElementById('fchk');
if(fb){var FQ=JSON.parse(fb.getAttribute('data-q')),fs=0,fok=true;
 var fask=function(){var q=FQ[fs],h='<div class="hd"><b>Am I eligible?</b><span>'+(fs+1)+' of '+FQ.length+'</span></div><div class="prog">';
  for(var i=0;i<FQ.length;i++)h+='<i'+(i<=fs?' class="on"':'')+'></i>';
  h+='</div><p class="qq">'+esc(q[0])+'</p><div class="yn"><button type="button" class="opt" data-v="y">Yes</button><button type="button" class="opt" data-v="n">No</button></div>';
  if(q[1])h+='<button type="button" class="opt" data-v="u">Not sure</button>';
  fb.className='chk';fb.innerHTML=h};
 var fres=function(){var go=fb.getAttribute('data-go'),off=fb.getAttribute('data-off'),n=+fb.getAttribute('data-n'),u=fb.getAttribute('data-uni');fb.className='res';
  fb.innerHTML=(fok?'<p class="big">You may be eligible</p><p>Check the full rules on the official page, then apply there. People who fit this usually qualify for other funds too.</p>'
    +'<a class="wbtn" href="/go/'+esc(go)+'/">Find my other funds in the free app</a>'
   :'<p class="big">Probably not this one</p><p>'+(n?n+' other funds at '+esc(u)+', plus national grants, may still fit you.':'National and charity grants may still fit you.')+'</p>'
    +'<a class="wbtn" href="/go/'+esc(go)+'/">See which fit in the free app</a>')
   +'<p class="small">A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p><button type="button" class="again">Change answers</button>'};
 fb.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
  if(t.classList.contains('opt')){if(t.getAttribute('data-v')==='n')fok=false;fs++;if(fs<FQ.length)fask();else fres()}
  else if(t.classList.contains('again')){fs=0;fok=true;fask()}});
}
var box=document.getElementById('chk'); if(!box) return;
var go=box.getAttribute('data-go')||'seo_site', uni=box.getAttribute('data-uni')||'University';
var Q=[
 {k:'l',q:'What will you study?',o:[['u','Undergraduate degree'],['p',"Master's or PhD"]]},
 {k:'i',q:'What is your household income?',o:[['0','Under £25,000'],['25000','£25,000 to £43,000'],['43000','Over £43,000'],['-1','Not sure']]},
 {k:'c',q:'Do any of these apply to you?',o:[['c','Care-experienced'],['e','Estranged from my family'],['r',"I'm a carer"],['d','Disabled or long-term condition'],['f','Refugee or asylum seeker'],['n','None of these']]}
];
var A={},step=0,groups=[].slice.call(document.querySelectorAll('.grp[data-g]')),orig=groups.map(function(g){return g.innerHTML});
var bar=document.getElementById('ctatext'),barOrig=bar?bar.textContent:'';
function fits(l,cap,circ){if(l!=='a'&&l!==A.l)return false;var lo=+A.i;if(cap>0&&lo>=0&&lo>=cap)return false;if(circ&&circ.indexOf(A.c)<0)return false;return true}
function ask(){var q=Q[step],h='<div class="hd"><b>Which could you get?</b><span>'+(step+1)+' of '+Q.length+'</span></div><div class="prog">';
 for(var i=0;i<Q.length;i++)h+='<i'+(i<=step?' class="on"':'')+'></i>';
 h+='</div><p class="qq">'+esc(q.q)+'</p>';
 q.o.forEach(function(o){h+='<button type="button" class="opt" data-v="'+esc(o[0])+'">'+esc(o[1])+'</button>'});
 h+=step?'<button type="button" class="back">Back</button>':'<p class="fine">Quick web estimate for UK students. The app is more accurate.</p>';
 box.className='chk';box.innerHTML=h}
box.addEventListener('click',function(e){var t=e.target.closest('button');if(!t)return;
 if(t.classList.contains('opt')){A[Q[step].k]=t.getAttribute('data-v');step++;if(step<Q.length)ask();else result()}
 else if(t.classList.contains('back')){step=Math.max(0,step-1);ask()}
 else if(t.classList.contains('again')){A={};step=0;groups.forEach(function(g,i){g.innerHTML=orig[i]});if(bar)bar.textContent=barOrig;ask()}});
function result(){var here=0;
 groups.forEach(function(g,gi){if(g.getAttribute('data-g')==='h')return;g.innerHTML=orig[gi];
  var rows=[].slice.call(g.querySelectorAll('.row[data-l]')),yes=[],no=[];
  rows.forEach(function(r){var ok=fits(r.getAttribute('data-l'),+r.getAttribute('data-i'),r.getAttribute('data-c'));
   if(ok&&r.getAttribute('data-x')!=='1')here++;(ok?yes:no).push(r)});
  var list=g.querySelector('.list');list.innerHTML='';
  yes.forEach(function(r){list.appendChild(r)});
  if(!yes.length){var p=document.createElement('p');p.className='none';p.textContent='None of these match your answers.';list.appendChild(p)}
  if(no.length){var d=document.createElement('details');d.className='more';d.innerHTML='<summary><span>'+no.length+(no.length===1?' doesn\'t':' don\'t')+' match your answers</span></summary>';
   no.forEach(function(r){r.classList.add('no');d.appendChild(r)});list.appendChild(d)}
  var sm=g.querySelector('h2 small');if(sm)sm.textContent=yes.length+' of '+rows.length});
 var nat=0;NAT.forEach(function(n){if(fits(n[0],n[1],n[2]))nat++});
 box.className='res';
 box.innerHTML='<div class="nums"><div><b>'+here+'</b><span>'+esc(uni)+' funds you may get</span></div>'
  +(nat?'<div><b>+'+nat+'</b><span>national &amp; charity grants worth checking</span></div>':'')+'</div>'
  +'<a class="wbtn" href="/go/'+esc(go)+'/">Get accurate matches in the app</a>'
  +'<p class="small">A quick estimate only. The app checks your course, region, fee status and more, so its matches are far more accurate.</p>'
  +'<button type="button" class="again">Change answers</button>';
 if(bar)bar.textContent=here+' '+uni+' funds'+(nat?' + '+nat+' national grants':'')+' to check';
 if(window.innerWidth<900)box.scrollIntoView({behavior:'smooth',block:'start'})}
})();
