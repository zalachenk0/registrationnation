const fs=require('fs'), path=require('path');
const sharp=require('C:/Users/blasn/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
let s='';const esc=t=>t.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function t(x,y,v,z=26,c='#1a3a5c',w=400){s+=`<text x="${x}" y="${y}" font-family="Arial,sans-serif" font-size="${z}" fill="${c}" font-weight="${w}">${esc(v)}</text>`;}
function box(x,y,w,h,lines,color='#f1f5f9'){s+=`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" fill="${color}" stroke="#c4d2df" stroke-width="2"/>`;lines.forEach((a,i)=>t(x+18,y+34+i*34,a,25));}
function a(x1,x2,y,label,dash=false){s+=`<path d="M${x1} ${y}H${x2}" fill="none" stroke="#1a3a5c" stroke-width="3" ${dash?'stroke-dasharray="9 7"':''} marker-end="url(#arr)"/>`;t(Math.min(x1,x2)+15,y-17,label,26);}
function start(title,subtitle,heads){s=`<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><defs><marker id="arr" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0 0L10 5L0 10" fill="#1a3a5c"/></marker></defs><rect width="1920" height="1080" fill="white"/>`;t(65,53,'REGISTRATION NATION / TECHNICAL WORKFLOW',22,'#d66328',700);t(65,115,title,47,'#1a3a5c',700);t(65,159,subtitle,27,'#536a80');heads.forEach((h,i)=>{const x=245+i*475;s+=`<rect x="${x-185}" y="192" width="370" height="74" rx="8" fill="#1a3a5c"/><path d="M${x} 266V930" stroke="#c4d2df" stroke-width="2" stroke-dasharray="8 8"/>`;t(x-163,238,h,28,'white',700);});}
async function save(name){s+='</svg>';const svg=s;fs.writeFileSync(path.join(__dirname,name+'.svg'),svg);await sharp(Buffer.from(svg)).png().toFile(path.join(__dirname,name+'.png'));}
(async()=>{
start('Document capture and extraction','One synchronous HTTP request captures or receives the image, extracts fields, and returns JSON.',['React / Chromium','Flask API :5000','Camera adapter','Document processors']);
a(245,720,322,'1  POST camera scan');
t(270,359,'/api/camera/scan/{kind}',24);t(75,406,'kind = license | insurance',24,'#536a80');
a(720,1195,412,'2  capture(temp JPEG)');
box(1005,446,385,111,['Serialize with camera lock','rpicam / libcamera / OpenCV']);
a(1195,720,593,'3  Image saved at temp path',true);
a(720,1670,651,'4  process_license(path) OR process_insurance(path)');
box(1430,686,445,165,['License: crop / preprocess','PDF417 decode → AAMVA parse','Insurance: OpenCV → Tesseract','Choose extraction with most fields']);
box(65,457,365,126,['Upload alternative:','POST /api/scan/{kind}','multipart field: image']);
a(1670,720,897,'5  Normalize dates; add full VIN for insurance',true);
a(720,245,967,'6  JSON → React state',true);
t(790,947,'{ success, recommendation, fields }',25,'#536a80');
t(65,1026,'UI: idle → scanning → done / error',25,'#1a3a5c',700);
t(790,1026,'API deletes the temporary image in finally after processing.',25,'#536a80');
await save('technical-01-document-scanning');
start('Review, print submission and completion','The current final action submits a demo print job. No payment gateway or registration database is connected.',['React / Chromium','Flask API :5000','Print service','CUPS / printer']);
box(65,284,365,126,['Review and edit React state','Generate REG- confirmation','Set printing = true']);
a(245,720,466,'1  POST print payload');t(265,503,'/api/print/registration',24);
box(530,515,380,126,['2  Validate required values','registrant, license_plate,','confirmation_number']);
a(720,1195,684,'3  print_registration_mockup');
box(990,706,410,92,['Build mockup; write temp text','No card data in print payload']);
a(1195,1670,839,'4  Run lp (20s timeout)');
a(1670,1195,905,'5  Job accepted',true);
a(1195,720,959,'6  Job output; remove temp file',true);
a(720,245,959,'7  JSON → submitted = true',true);
t(65,1010,'Failure: 400 missing fields / 503 printer error. UI stays at Payment and enables retry.',26,'#b45309');
t(65,1053,'Success confirms job submission. Start Next Customer clears form state. Physical output is not monitored.',26,'#1a3a5c');
await save('technical-02-print-workflow');
console.log('Technical diagrams created.');
})();
