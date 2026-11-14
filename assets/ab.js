(function(){
var B=window.BSAB||{a:'',id:'w0',hex:'0000'},go=document.documentElement.getAttribute('data-go')||'seo_site';
var off=false;try{off=localStorage.getItem('bs_noab')==='1'}catch(e){}
if(navigator.globalPrivacyControl||navigator.doNotTrack==='1')off=true;
if(/bot|crawl|spider|slurp|lighthouse|headless|preview|facebookexternalhit/i.test(navigator.userAgent||''))off=true;
var t0=Date.now(),maxs=0,r=document.referrer||'';
var ref=!r?'direct':/google\./.test(r)?'google':/bing\.|duckduckgo|yahoo|ecosia/.test(r)?'search':/bursasearch\.com/.test(r)?'site':'other';
var U='https://firestore.googleapis.com/v1/projects/fresher-forge-v2-cmw4ev/databases/(default)/documents/preAuthEvents';
function log(ev,x){if(off)return;var f={event:{stringValue:ev},device:{stringValue:B.id},platform:{stringValue:'web'},
 ts:{timestampValue:new Date().toISOString()},ab:{stringValue:B.a},pg:{stringValue:go},path:{stringValue:location.pathname.slice(0,120)},ref:{stringValue:ref}};
 if(x)for(var k in x){var v=x[k];f[k]=typeof v==='number'?{integerValue:String(Math.round(v))}:{stringValue:String(v).slice(0,80)}}
 try{fetch(U,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({fields:f})})}catch(e){}}
window.bsLog=log;
function tag(a){var h=a.getAttribute('href')||'';if(h.indexOf('/go/')!==0||h.indexOf('?a=')>0)return;
 var m=h.match(/^\/go\/([^\/?#]+)/),ch=m?m[1]:go;if(ch==='seo_site'&&a.hasAttribute('data-p'))ch=go;a.setAttribute('href','/go/'+ch+'/?a='+B.hex)}
[].forEach.call(document.querySelectorAll('a[href^="/go/"]'),tag);
document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a');if(!a)return;var h=a.getAttribute('href')||'';
 if(h.indexOf('/go/')===0){tag(a);log('web_tap',{place:a.getAttribute('data-p')||'link',ms:Date.now()-t0})}
 else if(/apps\.apple|play\.google/.test(a.href))log('web_tap',{place:'store_direct',ms:Date.now()-t0})},true);
[].forEach.call(document.querySelectorAll('[data-d]'),function(el){var n=Math.ceil((new Date(el.getAttribute('data-d')+'T23:59:00')-Date.now())/864e5);
 if(n>=0){var s=document.createElement('span');s.className='dd';s.textContent=' · '+n+' day'+(n===1?'':'s')+' left';var rm=el.querySelector('.rm');rm?el.insertBefore(s,rm):el.appendChild(s)}});
addEventListener('scroll',function(){var h=document.documentElement,p=(h.scrollTop+innerHeight)/h.scrollHeight;if(p>maxs)maxs=p},{passive:true});
log('web_view');
var gone=false;function bye(){if(gone)return;gone=true;log('web_leave',{ms:Date.now()-t0,scroll:Math.round(Math.min(maxs,1)*100)})}
document.addEventListener('visibilitychange',function(){if(document.visibilityState==='hidden')bye()});addEventListener('pagehide',bye);
var o=document.getElementById('abopt');if(o)o.addEventListener('click',function(e){e.preventDefault();try{localStorage.setItem('bs_noab','1')}catch(x){}off=true;o.textContent='Turned off'});
})();
