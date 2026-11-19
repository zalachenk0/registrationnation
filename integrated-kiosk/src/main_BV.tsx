// ============================================================================
// File:         main_BV.tsx
// Written by:   BV (Blas Vargas); CG (adds the touch keyboard back and the
//               finger-drag scrolling, tagged [CG])
// What it does: Starts the React app.
// Team-written: Logic written by BV (Blas Vargas); CG (lines tagged [CG]).
//               Outside code is used only through the third-party sources
//               listed below.
// Third-party:  React; React DOM
// ============================================================================
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App_BV_CG'
import { TouchKeyboard } from './TouchKeyboard_BV_CG'  // [CG] on-screen keyboard
import './index_BV.css'
import { enableDragScroll } from './DragScroll_CG'  // [CG] drag-to-scroll for when Chromium treats the touchscreen as a mouse

enableDragScroll()  // [CG]

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
    <TouchKeyboard />
  </React.StrictMode>,
)
