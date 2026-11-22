'use strict';
const strings={en:{login:'Sign In',register:'Create Account',reset:'Reset Password',resend:'Resend Verification',intro:'Your private customer space. Employee access is separate.',site:'Public site code',name:'Full name',nameHint:'e.g., Walid Salama',email:'Email',password:'Password',passwordHint:'Your password',newPasswordHint:'Choose a strong password',passwordRule:'Use at least 12 characters.',portal:'Customer Portal',boundary:'Customer Portal · No employee access',refresh:'Refresh Session',logout:'Logout',logoutAll:'Logout All',privateFiles:'Private Files',reloadFiles:'Reload files',drop:'Drop a file here or select a file',limits:'PNG, JPEG, WebP up to 5 MB · PDF up to 10 MB',noFile:'No file selected',upload:'Upload',noFiles:'No private files yet.',download:'Download',loading:'Loading your account…',signedIn:'Signed in successfully.',checkEmail:'Check your email for the next step. If this address is eligible, a link will arrive shortly.',signedOut:'Signed out successfully.',refreshed:'Session refreshed.',uploaded:'File uploaded successfully.',failed:'The request could not be completed. Please try again.',checkFields:'Please check the highlighted fields.',required:'This field is required.',invalidEmail:'Enter a valid email address.',invalidSite:'Select an active public site.',passwordShort:'Use at least 12 characters.',invalid:'Check this value and try again.',authFailed:'Sign in failed. Check the site, email and password, and confirm your email first.',expired:'Your session has expired. Please sign in again.',rateLimit:'Too many attempts. Please wait before trying again.',network:'Connection interrupted. Please try again.',fileType:'Select a PNG, JPEG, WebP image or PDF file.',imageSize:'The image must be 5 MB or smaller.',pdfSize:'The PDF must be 10 MB or smaller.',oneFile:'Select one file at a time.',downloadFailed:'Unable to download this file. Please try again.',filesFailed:'Unable to load files. Use Reload files to try again.',siteNumber:'Site',kb:'KB',mb:'MB',bytes:'bytes'},ar:{login:'تسجيل الدخول',register:'إنشاء حساب',reset:'استعادة كلمة المرور',resend:'إعادة إرسال التأكيد',intro:'مساحتك الخاصة كعميل. صلاحيات الموظفين منفصلة عن هذا الحساب.',site:'كود الموقع العام',name:'الاسم الكامل',nameHint:'مثال: وليد سلامة',email:'البريد الإلكتروني',password:'كلمة المرور',passwordHint:'أدخل كلمة المرور',newPasswordHint:'اختر كلمة مرور قوية',passwordRule:'استخدم 12 حرفًا على الأقل.',portal:'بوابة العميل',boundary:'بوابة العميل · بدون صلاحيات موظفين',refresh:'تجديد الجلسة',logout:'تسجيل الخروج',logoutAll:'الخروج من كل الأجهزة',privateFiles:'الملفات الخاصة',reloadFiles:'تحديث الملفات',drop:'اسحب ملفًا هنا أو اضغط لاختياره',limits:'PNG وJPEG وWebP حتى 5 MB · PDF حتى 10 MB',noFile:'لم يتم اختيار ملف',upload:'رفع الملف',noFiles:'لا توجد ملفات خاصة حتى الآن.',download:'تنزيل',loading:'جارٍ تحميل حسابك…',signedIn:'تم تسجيل الدخول بنجاح.',checkEmail:'راجع بريدك للخطوة التالية. إذا كان العنوان مستوفيًا للشروط، ستصلك رسالة بالرابط.',signedOut:'تم تسجيل الخروج بنجاح.',refreshed:'تم تجديد الجلسة.',uploaded:'تم رفع الملف بنجاح.',failed:'تعذر تنفيذ الطلب. حاول مرة أخرى.',checkFields:'راجع الحقول المحددة باللون الأحمر.',required:'هذا الحقل مطلوب.',invalidEmail:'أدخل بريدًا إلكترونيًا صحيحًا.',invalidSite:'اختر موقعًا عامًا نشطًا.',passwordShort:'استخدم 12 حرفًا على الأقل.',invalid:'راجع هذه القيمة وحاول مرة أخرى.',authFailed:'تعذر الدخول. راجع الموقع والبريد وكلمة المرور، وتأكد من تأكيد بريدك أولًا.',expired:'انتهت الجلسة. سجّل الدخول مرة أخرى.',rateLimit:'محاولات كثيرة. انتظر قليلًا ثم حاول مرة أخرى.',network:'انقطع الاتصال. حاول مرة أخرى.',fileType:'اختر صورة PNG أو JPEG أو WebP، أو ملف PDF.',imageSize:'يجب ألا يزيد حجم الصورة عن 5 MB.',pdfSize:'يجب ألا يزيد حجم PDF عن 10 MB.',oneFile:'اختر ملفًا واحدًا في كل مرة.',downloadFailed:'تعذر تنزيل الملف. حاول مرة أخرى.',filesFailed:'تعذر تحميل الملفات. اضغط تحديث الملفات للمحاولة.',siteNumber:'الموقع',kb:'KB',mb:'MB',bytes:'بايت'}};
const $=id=>document.getElementById(id),params=new URLSearchParams(location.search);
let lang=params.get('lang')==='ar'?'ar':'en',mode='login',access='',csrf='',busy=false,customer=null,selectedFile=null,uploadKey='',rows=[],notice=null;
const errors=new Map();const t=key=>strings[lang][key]||strings[lang].invalid;
function showNotice(key,kind='info'){notice={key,kind};renderNotice()}
function renderNotice(){const el=$('result');el.hidden=!notice;if(notice){el.textContent=t(notice.key);el.className='alert '+notice.kind;el.setAttribute('role',notice.kind==='error'?'alert':'status')}}
function setError(field,key){if(!$(field))return;errors.set(field,key);$(field).classList.add('invalid');$(field).setAttribute('aria-invalid','true');$(field+'-error').textContent=t(key)}
function clearError(field){errors.delete(field);if($(field)){$(field).classList.remove('invalid');$(field).removeAttribute('aria-invalid')}if($(field+'-error'))$(field+'-error').textContent=''}
function clearErrors(){for(const field of [...errors.keys()])clearError(field)}
function firstError(){const first=[...errors.keys()].map($).find(el=>el&&!el.disabled);if(first){first.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});first.focus({preventScroll:true})}}
function errorKey(field,codes){const code=Array.isArray(codes)?String(codes[0]):String(codes||'');if(['required','blank','null'].includes(code))return 'required';if(field==='email'&&code==='invalid')return 'invalidEmail';return 'invalid'}
class RequestError extends Error{constructor(key,status=0){super(key);this.key=key;this.status=status}}
async function request(path,method='GET',data,headers={},quiet=false){
 const opts={method,credentials:'same-origin',headers:{...headers}};
 if(method!=='GET')opts.headers['X-CSRFToken']=csrf;
 if(access)opts.headers.Authorization='Bearer '+access;
 if(data instanceof FormData)opts.body=data;else if(data!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data)}
 let response;try{response=await fetch(path,opts)}catch(_){throw new RequestError('network')}
 let body;try{body=await response.json()}catch(_){throw new RequestError('failed',response.status)}
 if(!response.ok){if(!quiet){for(const field of Object.keys(body.field_errors||{})){if($(field+'-error'))setError(field,errorKey(field,(body.field_error_codes||{})[field]))}firstError()}
 let key=errors.size?'checkFields':response.status===429?'rateLimit':response.status===401?(customer?'expired':'authFailed'):'failed';
 if(customer&&response.status===401){access='';customer=null;rows=[];renderState()}
 throw new RequestError(key,response.status)}return body
}
function controls(){document.querySelectorAll('button').forEach(b=>b.disabled=busy);$('upload').disabled=busy||!selectedFile;$('account').setAttribute('aria-busy',String(busy));$('member').setAttribute('aria-busy',String(busy))}
async function run(fn){if(busy)return;busy=true;controls();clearErrors();try{await fn()}catch(e){showNotice(e.key||'failed','error')}finally{busy=false;controls()}}
function applyMode(){const register=mode==='register',password=register||mode==='login';$('name-field').hidden=!register;$('display_name').disabled=!register;$('display_name').required=register;$('password-field').hidden=!password;$('password').disabled=!password;$('password').required=password;$('password').autocomplete=register?'new-password':'current-password';$('password').placeholder=t(register?'newPasswordHint':'passwordHint');$('password-help').hidden=!register;const key=mode==='password-reset/request'?'reset':mode==='resend-verification'?'resend':mode;$('auth-title').textContent=t(key);$('auth-submit').textContent=t(key);document.querySelectorAll('[data-mode]').forEach(b=>{const active=b.dataset.mode===mode;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;if(active)$('account').setAttribute('aria-labelledby',b.id)})}
function renderState(){if(!customer)clearFolders();$('business-data').replaceChildren();$('loading').hidden=true;$('auth-view').hidden=!!customer;$('member').hidden=!customer;if(customer){$('who').textContent=customer.display_name+' · '+customer.email;$('active-site').textContent=customer.siteCode||t('siteNumber')+' #'+customer.space_id}else{$('who').textContent='';$('active-site').textContent='';$('assets').replaceChildren();$('empty-files').hidden=true;selectedFile=null;uploadKey='';$('file').value=''}renderFiles();controls()}
function size(bytes){if(bytes>=1024*1024)return (bytes/(1024*1024)).toLocaleString(lang,{maximumFractionDigits:1})+' '+t('mb');if(bytes>=1024)return (bytes/1024).toLocaleString(lang,{maximumFractionDigits:1})+' '+t('kb');return bytes.toLocaleString(lang)+' '+t('bytes')}
function chosen(){$('chosen-file').textContent=selectedFile?selectedFile.name+' · '+size(selectedFile.size):t('noFile')}
function renderFiles(){const list=$('assets');list.replaceChildren();$('empty-files').hidden=true;for(const row of rows){const li=document.createElement('li');li.className='file-row';const info=document.createElement('div');info.className='file-info';const type=document.createElement('span');type.className='file-type';type.textContent=({'application/pdf':'PDF','image/png':'PNG','image/jpeg':'JPEG','image/webp':'WebP'})[row.mime]||'FILE';const detail=document.createElement('div');detail.className='file-detail';const name=document.createElement('div');name.className='file-name';name.textContent=row.name;const meta=document.createElement('div');meta.className='file-size';meta.textContent=size(Number(row.size)||0);detail.append(name,meta);info.append(type,detail);const button=document.createElement('button');button.type='button';button.className='link-button';button.textContent=t('download');button.setAttribute('aria-label',t('download')+' '+row.name);button.disabled=busy;button.addEventListener('click',()=>run(()=>download(row)));li.append(info,button);list.append(li)}chosen()}
function setLanguage(next){$('business-data').replaceChildren();lang=next;document.documentElement.lang=lang;document.documentElement.dir=lang==='ar'?'rtl':'ltr';document.title='ERPi Pro · '+t('portal');const url=new URL(location.href);url.searchParams.set('lang',lang);history.replaceState(null,'',url);document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));document.querySelectorAll('[data-placeholder]').forEach(el=>el.placeholder=t(el.dataset.placeholder));$('language').textContent=lang==='ar'?'English':'العربية';$('language').setAttribute('aria-label',lang==='ar'?'Switch to English':'التبديل إلى العربية');$('member').setAttribute('aria-label',t('portal'));document.querySelector('[role=tablist]').setAttribute('aria-label',t('portal'));for(const [field,key] of errors)$(field+'-error').textContent=t(key);applyMode();if(customer)$('active-site').textContent=customer.siteCode||t('siteNumber')+' #'+customer.space_id;renderFiles();if(customer)files().catch(()=>showNotice("failed","error"));renderNotice()}
function siteMemo(data,code){try{sessionStorage.setItem('erpi.customer.site',JSON.stringify({id:data.space_id,code}))}catch(_){}}
async function loadMe(code){const d=await request('/api/customer/v1/me');customer=d;if(code){d.siteCode=code;siteMemo(d,code)}else{try{const memo=JSON.parse(sessionStorage.getItem('erpi.customer.site')||'null');if(memo&&memo.id===d.space_id)d.siteCode=memo.code}catch(_){}}renderState()}
const fileApi='/api/customer/v1/media',folderList=$('folder-list'),states=new Map();
let folderData=[],folderVersion=0,filterTimer;
Object.assign(strings.en,{newFolder:'New folder',createFolder:'Create folder',fileSearch:'Search files'});
Object.assign(strings.ar,{newFolder:'مجلد جديد',createFolder:'إنشاء المجلد',fileSearch:'بحث في الملفات'});
const local=(en,ar)=>lang==='ar'?ar:en;
function releasePreviews(){for(const img of folderList.querySelectorAll('img'))URL.revokeObjectURL(img.src)}
function clearFolders(){folderVersion++;releasePreviews();folderList.replaceChildren();states.clear();folderData=[];$('folder-search').value='';$('folder-name').value=''}
async function folderRequest(id,action,data){
 if(action==='DELETE'){const r=await fetch(fileApi+'/folders/'+id,{method:'DELETE',headers:{Authorization:'Bearer '+access,'X-CSRFToken':csrf,'Content-Type':'application/json'},body:JSON.stringify(data)});if(!r.ok)throw new RequestError('failed')}
 else await request(fileApi+'/folders'+(id?'/'+id:''),action,data);
 await files()
}
async function files(){
 const generation=++folderVersion;
 const d=await request(fileApi+'/folders');
 if(generation!==folderVersion||!customer)return;
 folderData=[{id:'unfiled',name:local('General / Unfiled','عام / غير مصنف')},...d.results];
 await renderFolders()
}
async function renderFolders(){
 const version=++folderVersion;releasePreviews();folderList.replaceChildren();$('empty-files').hidden=true;
 for(const f of folderData){
 const state=states.get(f.id)||{page:1,size:10,open:f.id==='unfiled'};states.set(f.id,state);
 const box=element('section'),toggle=element('button',(state.open?'− ':'+ ')+(f.id==='unfiled'?local('General / Unfiled','عام / غير مصنف'):f.name)+(f.count===undefined?'':' ('+f.count+')'));
 toggle.type='button';toggle.setAttribute('aria-expanded',String(state.open));toggle.onclick=()=>run(async()=>{state.open=!state.open;await renderFolders()});box.className='folder-card';box.append(toggle);folderList.append(box);
 if(!state.open)continue;
 if(f.id!=='unfiled'){
 box.append(actionButton(local('Rename','تعديل الاسم'),async()=>{const name=prompt(local('Folder name','اسم المجلد'),f.name);if(name?.trim())await folderRequest(f.id,'PATCH',{name,revision:f.revision})}),
 actionButton(local('Delete folder','حذف المجلد'),async()=>{if(confirm(local('Move its files to General and delete the folder?','نقل الملفات إلى عام وحذف المجلد؟')))await folderRequest(f.id,'DELETE',{revision:f.revision})}))}
 const data=await request(fileApi+'?'+new URLSearchParams({folder:f.id,q:$('folder-search').value,page:String(state.page),page_size:String(state.size)}));
 if(version!==folderVersion||!customer)return;
 state.page=data.page;const max=Math.max(1,Math.ceil(data.count/state.size));
 for(const row of data.results){
 const item=element('div');item.className='file-row';box.append(item);
 if(row.mime.startsWith('image/')){
 const image=element('img');image.width=image.height=64;image.alt='';item.append(image);
 fetch(fileApi+'/'+row.id+'/thumbnail',{headers:{Authorization:'Bearer '+access}}).then(async r=>{if(r.ok){const b=await r.blob();if(version===folderVersion&&image.isConnected)image.src=URL.createObjectURL(b)}}).catch(()=>{})}
 item.append(element('b',row.mime.split('/')[1].toUpperCase().replace('JPEG','JPG')),element('span',row.name+' · '+size(row.size)+' · '+new Date(row.created_at).toLocaleString(lang)),actionButton(t('download'),()=>download(row)));
 const move=element('select');move.setAttribute('aria-label',local('Move to folder','نقل إلى مجلد'));
 for(const dest of folderData){const op=element('option',dest.name);op.value=dest.id;move.append(op)}move.value=f.id;
 move.onchange=()=>run(async()=>{await request(fileApi+'/'+row.id,'PATCH',{folder_id:move.value==='unfiled'?null:move.value,revision:row.revision});await files()});item.append(move)}
 const controls=element('div');controls.className='actions';const prev=actionButton(local('Previous','السابق'),async()=>{state.page=Math.max(1,state.page-1);await renderFolders()}),next=actionButton(local('Next','التالي'),async()=>{state.page=Math.min(max,state.page+1);await renderFolders()});
 const sizes=element('select');for(const n of [10,25,50]){const op=element('option',String(n));op.value=n;sizes.append(op)}sizes.value=String(state.size);sizes.onchange=()=>run(async()=>{state.size=Number(sizes.value);state.page=1;await renderFolders()});
 const label=element('label',local('Rows per page','صفوف الصفحة'));label.append(sizes);controls.append(prev,element('span',local('Page','صفحة')+' '+data.page+' / '+max),next,label);box.append(controls)
 }}
$('folder-create').onclick=()=>run(async()=>{const name=$('folder-name').value.trim();if(name){await folderRequest('','POST',{name});$('folder-name').value=''}});
$('folder-search').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{for(const s of states.values())s.page=1;renderFolders().catch(()=>showNotice('failed','error'))},220)};
async function refresh(){const perform=()=>request('/api/public/v1/auth/refresh','POST',{}, {},true);const data=navigator.locks?await navigator.locks.request('erpi.customer.refresh',perform):await perform();access=data.access;await loadMe()}
function validateAuth(){for(const input of $('account').querySelectorAll('input:not(:disabled)')){if(input.required&&!input.value.trim())setError(input.id,'required');else if(input.type==='email'&&!input.validity.valid)setError(input.id,'invalidEmail');else if(mode==='register'&&input.id==='password'&&input.value.length<12)setError('password','passwordShort')}firstError();return !errors.size}
$('account').addEventListener('submit',e=>{e.preventDefault();run(async()=>{if(!validateAuth()){showNotice('checkFields','error');return}const code=$('space').value.trim();const data=Object.fromEntries(new FormData($('account')));data.space=code;const d=await request('/api/public/v1/auth/'+mode,'POST',data);if(d.access){access=d.access;$('password').value='';await loadMe(code);showNotice('signedIn','success');try{await files()}catch(e){showNotice(e.key||'filesFailed','error')}}else showNotice('checkEmail','success')})});
document.querySelectorAll('[data-mode]').forEach(b=>{b.addEventListener('click',()=>{if(busy)return;mode=b.dataset.mode;$('password').value='';clearErrors();notice=null;renderNotice();applyMode()});b.addEventListener('keydown',e=>{if(!['ArrowRight','ArrowLeft','Home','End'].includes(e.key))return;e.preventDefault();const tabs=[...document.querySelectorAll('[data-mode]')];const i=tabs.indexOf(b),step=(e.key==='ArrowRight'?1:-1)*(lang==='ar'?-1:1);const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+step+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus()})});
$('language').addEventListener('click',()=>setLanguage(lang==='ar'?'en':'ar'));
$('refresh').addEventListener('click',()=>run(async()=>{await refresh();showNotice('refreshed','success')}));
for(const action of ['logout','logout-all'])$(action).addEventListener('click',()=>run(async()=>{await request('/api/public/v1/auth/'+action,'POST',{});access='';customer=null;rows=[];renderState();showNotice('signedOut','success')}));
$('files').addEventListener('click',()=>run(files));
function selectFile(file){clearError('file');selectedFile=null;uploadKey='';if(file){const isPdf=file.type==='application/pdf'||file.name.toLowerCase().endsWith('.pdf');const image=['image/png','image/jpeg','image/webp'].includes(file.type)||/\.(png|jpe?g|webp)$/i.test(file.name);if(!isPdf&&!image)setError('file','fileType');else if(file.size>(isPdf?10:5)*1024*1024)setError('file',isPdf?'pdfSize':'imageSize');else{selectedFile=file;uploadKey=crypto.randomUUID()}}chosen();controls()}
$('file').addEventListener('change',()=>selectFile($('file').files[0]));
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,e=>{e.preventDefault();if(!busy)$('dropzone').classList.add('dragging')});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('dragging')});
$('dropzone').addEventListener('drop',e=>{if(busy)return;if(e.dataTransfer.files.length!==1){setError('file','oneFile');return}selectFile(e.dataTransfer.files[0])});
$('upload').addEventListener('click',()=>run(async()=>{if(!selectedFile){setError('file','required');firstError();return}const form=new FormData();form.append('file',selectedFile);await request('/api/customer/v1/media','POST',form,{'Idempotency-Key':uploadKey});selectedFile=null;uploadKey='';$('file').value='';chosen();showNotice('uploaded','success');await files()}));
async function download(row){let r;try{r=await fetch('/api/customer/v1/media/'+encodeURIComponent(row.id)+'/download',{credentials:'same-origin',headers:{Authorization:'Bearer '+access}})}catch(_){throw new RequestError('network')}if(!r.ok)throw new RequestError('downloadFailed');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download=row.name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000)}
document.querySelectorAll('#account input').forEach(input=>input.addEventListener('input',()=>clearError(input.id)));
const businessPath='/api/customer/v1/business/';
const caption=(en,ar)=>lang==='ar'?ar:en;
function element(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el}
function actionButton(title,action){const b=element('button',title);b.type='button';b.onclick=()=>run(action);return b}
function statusText(key){const labels={trial:'تجريبي',active:'نشط',past_due:'متأخر',suspended:'معلق',scheduled:'مجدول',confirmed:'مؤكد',completed:'مكتمل',no_show:'غياب',cancelled:'ملغي',paid:'مدفوع',issued:'صادرة',partially_paid:'مدفوعة جزئياً',void:'ملغاة'};return lang==='ar'?(labels[key]||key):key.replaceAll('_',' ')}
async function printDocument(kind,id){
 const win=window.open('','_blank');if(!win)throw new RequestError('failed');win.opener=null;
 try{const r=await fetch(businessPath+kind+'/'+id+'/print?lang='+lang,{headers:{Authorization:'Bearer '+access}});
 if(!r.ok)throw new RequestError('failed');const doc=new DOMParser().parseFromString(await r.text(),'text/html');
 win.document.replaceChild(win.document.importNode(doc.documentElement,true),win.document.documentElement);win.focus();win.print()}catch(e){win.close();throw e}
}
function booking(section,resources){
 const res=element('select'),day=element('input'),slots=element('div');day.type='date';day.required=true;
 res.onchange=day.onchange=()=>slots.replaceChildren();
 for(const r of resources){const op=element('option',r.name+' · '+r.timezone);op.value=r.id;res.append(op)}
 const rl=element('label',caption('Resource','المورد')),dl=element('label',caption('Date at resource','التاريخ عند المورد'));rl.append(res);dl.append(day);
 section.append(rl,dl,actionButton(caption('Find available slots','عرض المواعيد المتاحة'),async()=>{
 slots.replaceChildren();if(!day.reportValidity())return;
 const resource=Number(res.value),result=await request(businessPath+'resources/'+resource+'/slots?date='+day.value);
 if(!result.results.length)slots.append(element('p',caption('No available slots','لا توجد مواعيد متاحة')));
 for(const start of result.results){const key=crypto.randomUUID(),zone=resources.find(r=>r.id===resource).timezone;
 slots.append(actionButton(new Date(start).toLocaleTimeString(lang,{timeZone:zone,hour:'2-digit',minute:'2-digit'}),async()=>{
 await request(businessPath+'appointments','POST',{resource_id:resource,start},{'Idempotency-Key':key});await loadBusiness()}))}
 }),slots)
}
async function loadBusiness(){
 const root=$('business-data');root.replaceChildren();
 for(const [kind,en,ar] of [['appointments','My appointments','مواعيدي'],['invoices','My invoices','فواتيري'],['receipts','My receipts','إيصالاتي'],['resources','Book an appointment','حجز موعد']]){
 let rows;try{rows=(await request(businessPath+kind)).results}catch(e){if(e.status===404)continue;throw e}
 const section=element('section');section.append(element('h3',caption(en,ar)));root.append(section);
 if(!rows.length){section.append(element('p',caption('No records','لا توجد سجلات')));continue}
 if(kind==='resources'){booking(section,rows);continue}
 for(const row of rows){const item=element('p',kind==='appointments'?row.resource__name+' · '+new Date(row.start).toLocaleString(lang)+' · '+statusText(row.status):
 row.number+' · '+(row.amount??row.total)+' '+(row.currency??row.invoice__currency)+(row.status?' · '+statusText(row.status):''));
 if(kind!=='appointments')item.append(actionButton('PDF',async()=>{
 const res=await fetch(businessPath+kind+'/'+row.id+'/download?lang='+lang,{headers:{Authorization:'Bearer '+access}});
 if(!res.ok)throw new RequestError('downloadFailed');const url=URL.createObjectURL(await res.blob());const a=element('a');a.href=url;a.download=row.number+'.pdf';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000)}));
 if(kind!=='appointments')item.append(' ',actionButton(caption('Print','طباعة'),()=>printDocument(kind,row.id)));section.append(item)}
 }
 try {
 const contracts=await request('/api/customer/v1/subscriptions');
 const panel=element('section');panel.append(element('h3',caption('My subscriptions','اشتراكاتي')));root.append(panel);
 for(const c of contracts.results){
 const row=element('p',(lang==='ar'?c.version__plan__name_ar:c.version__plan__name_en)+' · '+statusText(c.status)+' · '+c.next_date);
 const detail=element('div');row.append(actionButton(caption('Billing history','سجل الفوترة'),async()=>{
 const result=await request('/api/customer/v1/subscriptions/'+c.id);detail.replaceChildren();
 if(result.pending_cancellation)detail.append(element('p',caption('Cancellation under review','طلب الإلغاء قيد المراجعة')));
 for(const cycle of result.cycles)detail.append(element('p',cycle.start+' → '+cycle.end+' · '+(cycle.invoice__number??caption('Covered by credit','مغطاة بالرصيد الدائن'))+' · '+(cycle.invoice__total??0)));
 }));row.append(detail);
 if(!c.cancel_on && c.status!=='cancelled'){const key=crypto.randomUUID();row.append(actionButton(caption('Request cancellation','طلب إلغاء'),async()=>{
 const reason=window.prompt(caption('Reason for cancellation','سبب الإلغاء'));if(!reason)return;
 await request('/api/customer/v1/subscriptions/'+c.id+'/cancellation-requests','POST',{reason,revision:c.revision},{'Idempotency-Key':key});await loadBusiness()}))}
 panel.append(row)}
 }catch(e){if(e.status!==404)throw e}
 if(!root.children.length)root.append(element('p',caption('No business services linked in this site.','لا توجد خدمات مرتبطة بحسابك في هذا الموقع.')))
}
$('business-open').onclick=()=>run(loadBusiness);
strings.en.business='My appointments, subscriptions and billing';strings.ar.business='مواعيدي واشتراكاتي وفواتيري';

$('space').value=params.get('space')||'';setLanguage(lang);
run(async()=>{const d=await request('/api/public/v1/auth/csrf');csrf=d.csrf_token;try{await refresh();await files()}catch(e){if(!customer&&e.status===401){notice=null;renderNotice()}else throw e}finally{renderState()}});
