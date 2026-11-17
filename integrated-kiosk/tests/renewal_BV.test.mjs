import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import React, { act } from 'react';
import { JSDOM } from 'jsdom';

const transpile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const load = text => import('data:text/javascript;base64,' + Buffer.from(text).toString('base64'));
const { renewalError } = await load(transpile(readFileSync(new URL('../src/renewal_BV.ts', import.meta.url), 'utf8')));
globalThis.__timerReact = React;
const timerSource = readFileSync(new URL('../src/CompletionTimer_BV.tsx', import.meta.url), 'utf8')
  .replace('import { useEffect, useRef, useState } from "react";', 'const React = globalThis.__timerReact; const { useEffect, useRef, useState } = React;');
const { CompletionTimer } = await load(transpile(timerSource));
delete globalThis.__timerReact;

test('coverage start date and policy type may be blank; required fields and VIN remain validated', () => {
  const personal = { firstName:'Jane', lastName:'Sample', dateOfBirth:'1990-05-13', email:'jane@example.com',
    phone:'830-555-0123', address:'100 Main St', city:'New Braunfels', state:'Texas', zip:'78130', county:'Comal' };
  const license = { licenseNumber:'12345678', licenseState:'Texas', expirationDate:'2030-12-31',
    licenseClass:'C', vehicleYear:'2019', vehicleMake:'Honda', vehicleModel:'Accord', licensePlate:'ABC123',
    vin:'1HGCM82633A004352' };
  const insurance = { provider:'Example', policyNumber:'ABC123', policyHolder:'Sample', endDate:'2027-12-31', startDate:'', coverageType:'' };
  assert.equal(renewalError(personal,license,insurance),'');
  assert.match(renewalError({...personal, phone:'123'}, license, insurance), /10-digit/);
  assert.match(renewalError({...personal, dateOfBirth:'2025-02-29'}, license, insurance), /valid dates/);
  assert.match(renewalError(personal,license,{...insurance,endDate:''}),/required/);
  assert.match(renewalError(personal,{...license,vin:'BAD'},insurance),/17-character/);
});

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url:'http://127.0.0.1:5000' });
  const previous = { window:globalThis.window, document:globalThis.document, act:globalThis.IS_REACT_ACT_ENVIRONMENT, setInterval:globalThis.setInterval, clearInterval:globalThis.clearInterval, now:Date.now };
  globalThis.window=dom.window; globalThis.document=dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const intervals=new Map(); let next=0, now=0, resets=0;
  globalThis.setInterval=fn=>{intervals.set(++next,fn);return next;};
  globalThis.clearInterval=id=>intervals.delete(id); Date.now=()=>now;
  const { createRoot }=await import('react-dom/client');
  const root=createRoot(document.getElementById('root'));
  await act(async()=>root.render(React.createElement(CompletionTimer,{onReset:()=>resets++})));
  let unmounted=false;
  const unmount=async()=>{if(!unmounted){await act(async()=>root.unmount());unmounted=true;}};
  t.after(async()=>{await unmount(); globalThis.window=previous.window;globalThis.document=previous.document;globalThis.IS_REACT_ACT_ENVIRONMENT=previous.act;globalThis.setInterval=previous.setInterval;globalThis.clearInterval=previous.clearInterval;Date.now=previous.now;dom.window.close();});
  return { get resets(){return resets;}, get text(){return document.body.textContent;}, intervals, unmount,
    advance:async(ms)=>{await act(async()=>{now+=ms;for(const fn of [...intervals.values()])fn();});},
    click:async()=>{await act(async()=>document.querySelector('button').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true})));},
  };
}

test('completion resets once at 20 seconds, never before', async t=>{
  const f=await mount(t);assert.match(f.text,/20 seconds/);
  await f.advance(19999);assert.equal(f.resets,0);
  await f.advance(1);assert.equal(f.resets,1);
  await f.advance(60000);assert.equal(f.resets,1);
});

test('Wait stops the reset; resuming grants a fresh 20 seconds',async t=>{
  const f=await mount(t);await f.advance(19000);await f.click();
  assert.match(f.text,/Reset paused/);await f.advance(60000);assert.equal(f.resets,0);
  await f.click();assert.match(f.text,/20 seconds/);
  await f.advance(19999);assert.equal(f.resets,0);await f.advance(1);assert.equal(f.resets,1);
});

test('leaving the completion screen cancels its timer',async t=>{
  const f=await mount(t);await f.unmount();await f.advance(30000);
  assert.equal(f.resets,0);assert.equal(f.intervals.size,0);
});
