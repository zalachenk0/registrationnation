const fs = require('fs');
const path = require('path');
const sharp = require('sharp');
const esc = s => s.replaceAll('&','&amp;').replaceAll('<','&lt;');
let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0 0L10 5L0 10" fill="#1a3a5c"/></marker></defs><rect width="1920" height="1080" fill="#ffffff"/>`;
function text(x,y,s,size=28,color='#1a3a5c',weight=400){svg+=`<text x="${x}" y="${y}" font-family="Arial, sans-serif" font-size="${size}" font-weight="${weight}" fill="${color}">${esc(s)}</text>`;}
function line(d,dashed=false){svg+=`<path d="${d}" fill="none" stroke="#1a3a5c" stroke-width="4" ${dashed?'stroke-dasharray="10 8"':''} marker-end="url(#a)"/>`;}
function node(x,y,n,title,lines,complete=false){let color=complete?'#307044':'#1a3a5c'; svg+=`<rect x="${x}" y="${y}" width="520" height="222" rx="14" fill="${complete?'#eef7ef':'#f3f6fa'}" stroke="${complete?'#8db99a':'#c9d5e1'}" stroke-width="2"/><circle cx="${x+43}" cy="${y+43}" r="24" fill="${color}"/>`;text(x+35,y+53,n,27,'#ffffff',700);text(x+82,y+53,title,31,color,700);lines.forEach((s,i)=>text(x+30,y+105+i*39,s,27,'#40566d'));}
text(80,72,'REGISTRATION NATION',23,'#d66328',700);
text(80,139,'Vehicle registration kiosk user flow',53,'#1a3a5c',700);
text(80,191,'Staff signs in once, then each customer follows the five-step journey.',29,'#536a80');
node(80,265,'1','Personal information',['Scan license or upload an image','Review name, birth date and address','Enter email and phone']);
node(700,265,'2',"Driver’s license",['Confirm the prefilled license details','Check number, state, expiry and class','Correct any missing or misread data']);
node(1320,265,'3','Insurance & vehicle',['Scan insurance or upload an image','Review vehicle details and expiry','Enter remaining policy data and plate']);
node(1320,587,'4','Review & confirm',['Check all information together','Use Edit to correct individual fields','Select Proceed to Payment']);
node(700,587,'5','Payment',['Review the displayed $71.00 total','Tap, insert, or swipe card on reader','Select Pay $71.00 & Print']);
node(80,587,'✓','Confirmation & output',['Print service accepts the demo job','View the confirmation number','Collect the demo sticker / receipt'],true);
line('M600 376H683');line('M1220 376H1303');line('M1580 487V570');line('M1320 698H1237');line('M700 698H617');
line('M80 698H38V376H63',true);
text(81,555,'Start Next Customer clears the form and returns to step 1',27,'#536a80');
svg+='<line x1="80" y1="858" x2="1840" y2="858" stroke="#c9d5e1" stroke-width="2"/>';
text(80,905,'SCAN RECOVERY',23,'#d66328',700);text(80,948,'Try Again opens image upload.',27);text(80,987,'Review and edit extracted values.',27);
text(725,905,'PRINT RECOVERY',23,'#d66328',700);text(725,948,'An error keeps the user at payment.',27);text(725,987,'Retry reprints only \u2014 card isn\'t charged again.',27);
text(1360,905,'DEMO SCOPE',23,'#d66328',700);text(1360,948,'Real Stripe charge (test mode).',27);text(1360,987,'Printed output is a registration mockup.',27);
svg+='</svg>';
fs.writeFileSync(path.join(__dirname,'kiosk-user-flow.svg'),svg);
sharp(Buffer.from(svg)).png().toFile(path.join(__dirname,'kiosk-user-flow.png')).then(()=>console.log('Created 1920 x 1080 PNG and SVG flow diagram.'));
